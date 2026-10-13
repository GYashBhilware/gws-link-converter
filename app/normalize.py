"""Normalize SharePoint / OneDrive URLs into a canonical lookup key.

The Workspace Data Migration log stores `data_migration.source_uri` in a raw,
canonical form, e.g.

  https://tenant-my.sharepoint.com/personal/alan_x_com/Documents/last mont.pdf

Users paste many other shapes of the same item (percent-encoded, with query
strings, Office "view" links, OneDrive / library list views). `normalize()`
maps all of them onto the same key so lookups are a simple equality match.

The SQL UDF in sql/udf_normalize.sql implements the same rules for the
already-canonical logged URIs; tests/test_normalize.py checks both agree.
"""

import dataclasses
import enum
import re
import unicodedata
import urllib.parse

from app import sharelink


class Kind(enum.Enum):
  PATH = "PATH"  # Resolved to a canonical item path -> lookup by source_key.
  SOURCEDOC = "SOURCEDOC"  # Doc.aspx?sourcedoc={GUID} -> lookup by item GUID.
  SHARE = "SHARE"  # Sharing link /:x:/g/<scope>/<owner>/<token> -> see app/sharelink.py.
  UNSUPPORTED = "UNSUPPORTED"  # Recognized SharePoint URL we cannot interpret.
  INVALID = "INVALID"  # Not a SharePoint / OneDrive URL at all.


@dataclasses.dataclass(frozen=True)
class Normalized:
  kind: Kind
  key: str | None = None  # Canonical lowercase key (Kind.PATH).
  guid: str | None = None  # Lowercase item GUID without braces (SOURCEDOC).
  reason: str | None = None  # Human-readable explanation for non-PATH kinds.
  share: sharelink.ShareLink | None = None  # Parsed sharing link (Kind.SHARE).


_SP_HOST = re.compile(r"^[a-z0-9-]+(-my)?\.sharepoint(-df)?\.com$")
# Office short links: /:w:/r/..., /:x:/g/..., /:f:/s/..., /:b:/p/...
_SHORT = re.compile(r"^/:([a-z]):/([a-z])(/.*)$")
_GUID = re.compile(
    r"\{?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}?"
)
# Query params that carry the real item path in list / viewer pages.
_PATH_PARAMS = ("id", "RootFolder")


def _decode(s: str) -> str:
  # Repeatedly percent-decode (links are sometimes double-encoded), max 3x.
  for _ in range(3):
    decoded = urllib.parse.unquote(s)
    if decoded == s:
      break
    s = decoded
  return s


def _canon(host: str, path: str) -> str:
  path = _decode(path)
  path = unicodedata.normalize("NFC", path)
  path = re.sub(r"/{2,}", "/", path)
  path = path.rstrip("/")
  return f"https://{host}{path}".lower()


def normalize(raw: str) -> Normalized:
  """Returns the canonical lookup form of a pasted SharePoint/OneDrive URL."""
  if not raw or not raw.strip():
    return Normalized(Kind.INVALID, reason="Empty input.")
  text = raw.strip().strip("<>\"'")
  if "://" not in text:
    text = "https://" + text
  try:
    parts = urllib.parse.urlsplit(text)
  except ValueError:
    return Normalized(Kind.INVALID, reason="Not a valid URL.")
  host = (parts.hostname or "").lower()
  if parts.scheme.lower() not in ("http", "https") or not _SP_HOST.match(host):
    return Normalized(
        Kind.INVALID, reason="Not a SharePoint or OneDrive (sharepoint.com) URL."
    )

  path = parts.path or "/"
  query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)

  # 1. Office short links (/:w:/r/<path>?...).
  m = _SHORT.match(path.lower())
  if m:
    mode = m.group(2)
    if mode == "r":  # "r" = direct path link: the rest is the item path.
      path = path[len(path) - len(m.group(3)):]
    else:  # g / s / p ... = sharing links with an opaque token.
      share = sharelink.parse(host, path)
      if share:
        return Normalized(Kind.SHARE, share=share)
      return Normalized(
          Kind.UNSUPPORTED,
          reason=(
              "This looks like a SharePoint sharing link, but its format is not"
              " recognized. Open it in the browser and paste the address-bar URL"
              " instead."
          ),
      )

  # 2. Viewer links: /_layouts/15/Doc.aspx?sourcedoc={GUID}&file=...
  if "/_layouts/" in path.lower():
    sourcedoc = (query.get("sourcedoc") or [""])[0]
    g = _GUID.search(_decode(sourcedoc).lower())
    if g:
      return Normalized(Kind.SOURCEDOC, guid=g.group(1))
    for p in _PATH_PARAMS:
      if query.get(p):
        return Normalized(Kind.PATH, key=_canon(host, query[p][0]))
    return Normalized(
        Kind.UNSUPPORTED, reason="Unrecognized SharePoint viewer link."
    )

  # 3. Any view that carries the item path in a query param, e.g. the modern
  #    OneDrive UI  /my?viewid=...&id=/personal/u/Documents/file.pdf&parent=...
  #    and library views .../Forms/AllItems.aspx?id=...,  onedrive.aspx?id=...
  for p in _PATH_PARAMS:
    value = _decode((query.get(p) or [""])[0])
    if value.startswith("/"):
      return Normalized(Kind.PATH, key=_canon(host, value))
  if path.lower().endswith("/forms/allitems.aspx"):
    # Library root view without a folder id -> the library itself.
    return Normalized(Kind.PATH, key=_canon(host, path[: -len("/Forms/AllItems.aspx")]))

  # 4. Plain item path (the canonical case). Query and fragment are dropped.
  return Normalized(Kind.PATH, key=_canon(host, path))
