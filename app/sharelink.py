"""Parse SharePoint / OneDrive *sharing* links (Outlook "modern attachments").

A sharing link looks like

  https://<tenant>-my.sharepoint.com/:x:/g/personal/<owner>/<TOKEN>[?e=...]
  https://<tenant>.sharepoint.com/:w:/s/<site>/<TOKEN>[?e=...]

It carries no file path, only an opaque share token. Two ways to resolve it:

1. Offline: some token formats embed the SharePoint item's unique ID (a GUID),
   the same GUID the Workspace migration log records in
   `data_migration.source_identifier`. `decode_token` extracts *candidate*
   GUIDs from the token layouts we have verified. Microsoft does not document
   the format, so candidates are only ever used to *look up* rows that really
   exist in the migration log; nothing is fabricated from them.
2. Harvested: links collected from Microsoft Graph while SharePoint is still
   online are stored in the `share_links` table, keyed by `share_key`.
"""

import base64
import binascii
import dataclasses
import re
import uuid

# /:<type>:/<mode>/<path...>/<token>
#   type : w word, x excel, p powerpoint, b pdf, f folder, o onenote, u other, v video, i image
#   mode : g = full web path follows (e.g. personal/<owner>, sites/<site>)
#          s = SharePoint site (sites/ implied), t = Teams site (teams/ implied)
#          (r = direct path link, handled by app/normalize.py, never a token)
_SHARE = re.compile(
    r"^/:(?P<type>[a-z]):/(?P<mode>[a-qs-z])/(?P<rest>(?:[^/]+/)*?[^/]+)/"
    r"(?P<token>[A-Za-z0-9_-]{16,})/?$"
)

ITEM_TYPES = {
    "w": "Word document", "x": "Excel workbook", "p": "PowerPoint presentation",
    "b": "PDF", "f": "folder", "o": "OneNote notebook", "v": "video", "i": "image",
    "t": "text file", "u": "file",
}

# Verified token layouts: first byte -> (byte offset of the 16-byte GUID).
# 0x21: modern "IQ..." tokens (35 bytes: 21 00 | GUID | 17 bytes), seen on links
#       created by "Copy link" and Outlook in 2026. Add layouts here only after
#       verifying them against real migration-log GUIDs (see tests).
_LAYOUTS = {0x21: 2}


@dataclasses.dataclass(frozen=True)
class ShareLink:
  host: str  # lowercase host
  item_type: str  # one-letter Office type code (x, w, b, f, ...)
  scope: str  # personal | sites | teams
  owner: str  # OneDrive owner (e.g. alan_contoso_com) or site name
  token: str  # case-sensitive share token
  share_key: str  # stable lookup key for the share_links table
  guid_candidates: tuple[str, ...]  # lowercase GUIDs to try against the migration log
  token_format: str  # e.g. "0x21/35" (first byte / decoded length) or "undecodable"

  @property
  def item_label(self) -> str:
    return ITEM_TYPES.get(self.item_type, "file")


def share_key(host: str, token: str) -> str:
  """Lookup key: the token identifies the link within a tenant host. Case is kept."""
  return f"{host.lower()}/{token}"


def decode_token(token: str) -> tuple[tuple[str, ...], str]:
  """Returns (candidate GUIDs, format label). Empty candidates if layout unknown."""
  try:
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
  except (binascii.Error, ValueError):
    return (), "undecodable"
  fmt = f"0x{raw[0]:02x}/{len(raw)}" if raw else "empty"
  off = _LAYOUTS.get(raw[0]) if raw else None
  if off is None or len(raw) < off + 16:
    return (), fmt
  g = raw[off:off + 16]
  # .NET Guid.ToByteArray() order (little-endian first 3 groups) first, then RFC order.
  cands = (str(uuid.UUID(bytes_le=g)), str(uuid.UUID(bytes=g)))
  return tuple(dict.fromkeys(cands)), fmt


def parse(host: str, path: str) -> ShareLink | None:
  """Parses the path of a sharepoint.com URL; None if it is not a sharing link."""
  m = _SHARE.match(path)
  if not m:
    return None
  mode, segs = m["mode"].lower(), m["rest"].split("/")
  if mode in ("s", "t"):
    scope, owner = ("sites" if mode == "s" else "teams"), segs[0]
  elif len(segs) >= 2:
    scope, owner = segs[0].lower(), segs[1]
  else:
    scope, owner = "unknown", segs[0]
  cands, fmt = decode_token(m["token"])
  return ShareLink(
      host=host.lower(), item_type=m["type"].lower(), scope=scope,
      owner=owner, token=m["token"], share_key=share_key(host, m["token"]),
      guid_candidates=cands, token_format=fmt)
