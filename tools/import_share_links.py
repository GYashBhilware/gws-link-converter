#!/usr/bin/env python3
"""Load sharing-link -> item GUID mappings into <INDEX_DATASET>.share_links.

Input CSV with header: url,item_guid[,source]
  url        a SharePoint/OneDrive sharing link (any ?e=... query is ignored)
  item_guid  the SharePoint item unique ID (listItemUniqueId) it points to
Typical sources: a Microsoft Graph export, a SharePoint admin report, or the
built-in harvester. Re-importing the same rows is harmless (latest row wins).

Usage: python3 -m tools.import_share_links links.csv [--source import] [--dry-run]
Env:   GCP_PROJECT, BQ_DATASET, BQ_LOCATION (source deploy.env)
"""

import argparse
import csv
import re
import sys

from app import bq, config
from app.normalize import Kind, normalize

_GUID = re.compile(r"^\{?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}?$")
BATCH = 500

_INSERT = """
INSERT INTO `{t}` (share_key, item_guid, source, owner, item_type, web_url, recorded_at)
SELECT k, g, @source, o, ty, u, CURRENT_TIMESTAMP()
FROM UNNEST(@keys) k WITH OFFSET i
JOIN UNNEST(@guids) g WITH OFFSET i2 ON i = i2
JOIN UNNEST(@owners) o WITH OFFSET i3 ON i = i3
JOIN UNNEST(@types) ty WITH OFFSET i4 ON i = i4
JOIN UNNEST(@urls) u WITH OFFSET i5 ON i = i5
"""


def parse_rows(rows):
  """-> (good [(key, guid, owner, type, url)], errors [(line, reason)])."""
  good, errors = [], []
  for n, r in enumerate(rows, start=2):
    url, guid = (r.get("url") or "").strip(), (r.get("item_guid") or "").strip()
    norm = normalize(url)
    m = _GUID.match(guid)
    if norm.kind != Kind.SHARE:
      errors.append((n, f"not a sharing link: {url[:80]}"))
    elif not m:
      errors.append((n, f"bad item_guid: {guid[:60]}"))
    else:
      s = norm.share
      good.append((s.share_key, m.group(1).lower(), s.owner, s.item_type, url))
  return good, errors


def main():
  p = argparse.ArgumentParser()
  p.add_argument("csv")
  p.add_argument("--source", default="import")
  p.add_argument("--dry-run", action="store_true")
  a = p.parse_args()
  with open(a.csv, newline="", encoding="utf-8-sig") as f:
    good, errors = parse_rows(csv.DictReader(f))
  for line, why in errors[:20]:
    print(f"  line {line}: {why}", file=sys.stderr)
  print(f"{len(good)} valid rows, {len(errors)} rejected")
  if a.dry_run or not good:
    return
  if not bq.PROJECT:
    raise SystemExit("GCP_PROJECT is not set (source deploy.env first).")
  table = f"{bq.PROJECT}.{config.index_dataset()}.share_links"
  sql = _INSERT.replace("{t}", table)
  for i in range(0, len(good), BATCH):
    chunk = good[i:i + BATCH]
    cols = list(zip(*chunk))
    bq.query(sql, {"keys": list(cols[0]), "guids": list(cols[1]), "owners": list(cols[2]),
                   "types": list(cols[3]), "urls": list(cols[4]), "source": a.source},
             dry_run_first=False)
  print(f"inserted {len(good)} rows into {table}")


if __name__ == "__main__":
  main()
