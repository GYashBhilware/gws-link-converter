"""Lookup logic: pasted SharePoint/OneDrive links -> Google Drive links.

Reads the synced index (`migration_events`) first. For anything not found
there, falls back to the raw Workspace export rows newer than the sync
watermark, so files migrated after the last sync run are still found.
All SQL is parameterized; user input never becomes SQL text.
"""

import dataclasses
import datetime as dt
import os
from typing import Callable

from app import bq
from app import config
from app.normalize import Kind, normalize

DS = config.index_dataset()
SRC = config.source_table()
MAX_BULK = int(os.environ.get("MAX_BULK", "500"))
_T = f"`{bq.PROJECT}.{DS}"
_OK = "('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')"

QueryFn = Callable[..., tuple[list[dict], dict]]

# Latest successful attempt per source (else latest attempt, to explain failures).
_INDEX_SQL = f"""
SELECT * EXCEPT (rn) FROM (
  SELECT e.source_key, e.source_uri, e.source_item_guid, e.item_type, e.target_uri,
         e.event_status, e.warning_code, e.warning_title, e.event_time AS migrated_at,
         e.migrated_by, x.execution_type, w.batch_name,
         COUNT(*) OVER (PARTITION BY e.source_key) AS attempts,
         ROW_NUMBER() OVER (
           PARTITION BY e.source_key
           ORDER BY (e.event_status IN {_OK} AND e.target_uri IS NOT NULL) DESC,
                    e.event_time DESC, e.event_id DESC) AS rn
  FROM {_T}.migration_events` e
  LEFT JOIN {_T}.migration_executions` x USING (execution_id)
  LEFT JOIN {_T}.migration_waves` w USING (wave_id)
  WHERE e.source_key IN UNNEST(@keys) OR e.source_item_guid IN UNNEST(@guids)
) WHERE rn = 1
"""

# Same shape, straight from the export, only for rows newer than the watermark.
_LIVE_SQL = f"""
SELECT * EXCEPT (rn) FROM (
  SELECT {_T}.normalize_uri`(a.data_migration.source_uri) AS source_key,
         a.data_migration.source_uri AS source_uri,
         LOWER(REGEXP_EXTRACT(a.data_migration.source_identifier,
               r'/(?:files|folders)/([0-9a-fA-F-]{{36}})')) AS source_item_guid,
         IF(a.event_name = 'CREATE_FOLDER', 'FOLDER', 'FILE') AS item_type,
         NULLIF(a.data_migration.target_uri, '') AS target_uri,
         a.status.event_status AS event_status,
         NULLIF(a.data_migration.migration_error_code, '') AS warning_code,
         NULLIF(a.data_migration.migration_error_title, '') AS warning_title,
         TIMESTAMP_MICROS(a.time_usec) AS migrated_at, a.email AS migrated_by,
         CAST(NULL AS STRING) AS execution_type, CAST(NULL AS STRING) AS batch_name,
         1 AS attempts,
         ROW_NUMBER() OVER (
           PARTITION BY a.data_migration.source_uri
           ORDER BY (a.status.event_status IN {_OK}
                     AND IFNULL(a.data_migration.target_uri, '') != '') DESC,
                    a.time_usec DESC) AS rn
  FROM `{SRC}` a
  WHERE a._PARTITIONTIME >= TIMESTAMP(@part_from)
    AND a.time_usec > @after_usec
    AND a.record_type = 'data_migration'
    AND a.event_type = 'MIGRATION'
    AND a.event_name IN ('CREATE_FILE', 'CREATE_FOLDER')
    AND a.data_migration.source_type LIKE 'Sharepoint%'
) WHERE rn = 1
  AND (source_key IN UNNEST(@keys) OR source_item_guid IN UNNEST(@guids))
""".replace("{{36}}", "{36}")

# Sharing links collected from Microsoft Graph (harvest) or resolved live while
# SharePoint was still online. share_key = "<host>/<token>" (see app/sharelink.py).
_SHARE_SQL = f"""
SELECT share_key, item_guid, source
FROM {_T}.share_links`
WHERE share_key IN UNNEST(@share_keys) AND item_guid IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY share_key ORDER BY recorded_at DESC) = 1
"""

_WATERMARK_SQL = f"SELECT watermark_usec, last_run_at FROM {_T}.sync_state` WHERE name = 'activity'"

_FOLDER_SQL = f"""
SELECT source_key, source_uri, item_type, target_uri, event_status, warning_title,
       migrated_at, batch_name
FROM {_T}.uri_mapping_current`
WHERE STARTS_WITH(source_key, @prefix)
ORDER BY item_type DESC, source_key
LIMIT @limit
"""


@dataclasses.dataclass
class Result:
  input: str
  status: str  # FOUND | MIGRATION_FAILED | NOT_FOUND | UNSUPPORTED | INVALID
  message: str | None = None
  target_uri: str | None = None
  source_uri: str | None = None
  item_type: str | None = None
  event_status: str | None = None
  warning: str | None = None
  migrated_at: str | None = None
  migrated_by: str | None = None
  batch_name: str | None = None
  attempts: int | None = None
  via: str | None = None  # index | live
  match: str | None = None  # path | sourcedoc | share-harvested | share-decoded
  share_owner: str | None = None  # sharing links: OneDrive owner / site name


def _iso(ts):
  if ts is None:
    return None
  return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def _is_success(row):
  return row["event_status"] in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS") and row["target_uri"]


class Repository:

  def __init__(self, query: QueryFn = bq.query):
    self._query = query

  def watermark(self):
    rows, _ = self._query(_WATERMARK_SQL, None, dry_run_first=False)
    return rows[0] if rows else {"watermark_usec": None, "last_run_at": None}

  def _fetch(self, keys, guids, live):
    if not keys and not guids:
      return []
    params = {"keys": sorted(keys) or [""], "guids": sorted(guids) or [""]}
    if not live:
      rows, _ = self._query(_INDEX_SQL, params, dry_run_first=False)
      return rows
    wm = self.watermark()["watermark_usec"] or 0
    # Partitions are US-Pacific days: start one day before the watermark's UTC day.
    day = dt.datetime.fromtimestamp(wm / 1e6, dt.timezone.utc).date() - dt.timedelta(days=1)
    params.update(after_usec=int(wm), part_from=day.isoformat() + " 00:00:00+00")
    rows, _ = self._query(_LIVE_SQL, params, dry_run_first=False)
    return rows

  def _harvested(self, share_keys):
    """share_key -> item_guid from the share_links table ({} if none / no table yet)."""
    if not share_keys:
      return {}
    try:
      rows, _ = self._query(_SHARE_SQL, {"share_keys": sorted(share_keys)}, dry_run_first=False)
    except bq.BigQueryError as e:
      if "Not found: Table" in str(e):  # schema step not yet run on this deployment
        return {}
      raise
    return {r["share_key"]: r["item_guid"].lower() for r in rows}

  def lookup(self, inputs: list[str]) -> list[Result]:
    inputs = inputs[:MAX_BULK]
    norm = [normalize(i) for i in inputs]
    keys = {n.key for n in norm if n.kind == Kind.PATH}
    guids = {n.guid for n in norm if n.kind == Kind.SOURCEDOC}
    shares = [n.share for n in norm if n.kind == Kind.SHARE]
    harvested = self._harvested({s.share_key for s in shares})
    for s in shares:
      if s.share_key in harvested:
        guids.add(harvested[s.share_key])
      guids.update(s.guid_candidates)

    found = {}  # ("k", key) / ("g", guid) -> (row, via)

    def absorb(rows, via):
      for r in rows:
        for ref in (("k", r["source_key"]), ("g", r.get("source_item_guid"))):
          if ref[1] is None:
            continue
          prev = found.get(ref)
          if prev is None or (_is_success(r) and not _is_success(prev[0])):
            found[ref] = (r, via)

    absorb(self._fetch(keys, guids, live=False), "index")
    # Live fallback for anything missing or only failed so far.
    miss_k = {k for k in keys if ("k", k) not in found or not _is_success(found[("k", k)][0])}
    miss_g = {g for g in guids if ("g", g) not in found or not _is_success(found[("g", g)][0])}
    if miss_k or miss_g:
      absorb(self._fetch(miss_k, miss_g, live=True), "live")

    out = []
    for raw, n in zip(inputs, norm):
      if n.kind in (Kind.INVALID, Kind.UNSUPPORTED):
        out.append(Result(raw, n.kind.value, message=n.reason))
        continue
      if n.kind == Kind.SHARE:
        hit, match = self._share_hit(n.share, harvested, found)
        if not hit:
          out.append(Result(raw, "NOT_FOUND", match="share", share_owner=n.share.owner,
                            message=self._share_miss_message(n.share, n.share.share_key in harvested)))
          continue
      else:
        hit = found.get(("k", n.key) if n.kind == Kind.PATH else ("g", n.guid))
        match = n.kind.value.lower()
      if not hit:
        out.append(Result(raw, "NOT_FOUND", message=(
            "No migration record found for this link. It may not have been "
            "migrated yet, or it was migrated by a different tool.")))
        continue
      r, via = hit
      ok = _is_success(r)
      out.append(Result(
          input=raw,
          status="FOUND" if ok else "MIGRATION_FAILED",
          message=None if ok else (
              f"Migration was attempted but did not succeed ({r['event_status']}"
              + (f": {r['warning_title']}" if r.get("warning_title") else "") + ")."),
          target_uri=r["target_uri"] if ok else None,
          source_uri=r["source_uri"], item_type=r["item_type"],
          event_status=r["event_status"],
          warning=r.get("warning_title") if ok else None,
          migrated_at=_iso(r["migrated_at"]), migrated_by=r.get("migrated_by"),
          batch_name=r.get("batch_name"), attempts=r.get("attempts"), via=via,
          match=match, share_owner=n.share.owner if n.kind == Kind.SHARE else None))
    return out

  @staticmethod
  def _share_hit(share, harvested, found):
    """Harvested mapping (authoritative) first, then decoded token candidates."""
    order = []
    if share.share_key in harvested:
      order.append((harvested[share.share_key], "share-harvested"))
    order += [(g, "share-decoded") for g in share.guid_candidates]
    best = None
    for g, match in order:
      hit = found.get(("g", g))
      if hit and (best is None or (_is_success(hit[0]) and not _is_success(best[0][0]))):
        best = (hit, match)
    return best if best else (None, None)

  @staticmethod
  def _share_miss_message(share, was_harvested):
    where = (f"{share.owner}'s OneDrive" if share.scope == "personal"
             else f"the {share.owner} site")
    base = f"Sharing link to a {share.item_label} in {where}. "
    if was_harvested:
      return base + ("The link is known, but the file it points to has no migration "
                     "record yet. It may not have been migrated.")
    if share.guid_candidates:
      return base + ("No migrated file matches it yet. If the owner's files have not "
                     "been migrated, try again after their migration.")
    return base + ("This link format can't be decoded offline. It will resolve once the "
                   "sharing-link inventory has been collected from SharePoint; meanwhile, "
                   "open it and paste the address-bar URL.")

  def folder(self, url: str, limit: int = 200) -> tuple[Result, list[dict]]:
    """The folder itself plus everything migrated beneath it."""
    head = self.lookup([url])[0]
    n = normalize(url)
    if n.kind != Kind.PATH:
      return head, []
    rows, _ = self._query(_FOLDER_SQL, {"prefix": n.key + "/", "limit": int(limit)},
                          dry_run_first=False)
    for r in rows:
      r["migrated_at"] = _iso(r["migrated_at"])
    return head, rows
