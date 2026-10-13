#!/usr/bin/env python3
"""Harvest SharePoint/OneDrive sharing links before SharePoint is decommissioned.

For every OneDrive/site that was migrated, walk all items that are shared, read
their sharing links (Outlook modern attachments, "Copy link") and record
link -> SharePoint item GUID. Output is a CSV compatible with
tools/import_share_links.py; with --load the rows go straight to BigQuery.

Run it repeatedly until cutover (re-runs are harmless; latest row wins), and once
more right after SharePoint is set to read-only.

  python3 -m tools.harvest_share_links --owners alan_contoso_com,yash_contoso_com \
      --host contoso-my.sharepoint.com --out links.csv [--load]
  python3 -m tools.harvest_share_links --from-index --out links.csv [--load]

Env: GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET (Azure app, read-only),
     and for --from-index / --load: GCP_PROJECT, BQ_DATASET, BQ_LOCATION.
"""

import argparse
import csv
import json
import re
import sys
import time

from tools.graph import Graph, GraphError

# Migrated roots from the migration log: https://host/personal/<owner>/... or /sites/<site>/...
_ROOT = re.compile(r"^https://([^/]+)/(personal|sites|teams)/([^/]+)/", re.I)

_ROOTS_SQL = """
SELECT DISTINCT REGEXP_EXTRACT(source_uri, r'^(https://[^/]+/(?:personal|sites|teams)/[^/]+/)') AS root
FROM `{t}` WHERE event_status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS')
"""


def roots_from_index():
  from app import bq, config
  rows, _ = bq.query(_ROOTS_SQL.replace("{t}", f"{bq.PROJECT}.{config.index_dataset()}.migration_events"),
                     dry_run_first=False)
  return sorted({r["root"] for r in rows if r["root"]})


def parse_root(root):
  m = _ROOT.match(root)
  return (m.group(1).lower(), m.group(2).lower(), m.group(3)) if m else None


def harvest(graph, targets, log=print):
  """targets: [(host, scope, owner)] -> (rows [(url, guid)], stats dict)."""
  rows, stats = [], {"drives": 0, "shared_items": 0, "links": 0, "errors": 0}
  for host, scope, owner in targets:
    try:
      drive = graph.drive_id(host, scope, owner)
    except GraphError as e:
      stats["errors"] += 1
      log(json.dumps({"event": "drive_error", "owner": owner, "error": str(e)[:200]}))
      continue
    stats["drives"] += 1
    for it in graph.shared_items(drive):
      try:
        urls = graph.link_urls(drive, it["id"])
        guid = graph.item_guid(drive, it["id"]) if urls else None
      except GraphError as e:
        stats["errors"] += 1
        log(json.dumps({"event": "item_error", "owner": owner, "error": str(e)[:200]}))
        continue
      stats["shared_items"] += 1
      for u in urls:
        if guid:
          rows.append((u, guid))
          stats["links"] += 1
    log(json.dumps({"event": "drive_done", "owner": owner, **stats}))
  return rows, stats


def main():
  p = argparse.ArgumentParser()
  g = p.add_mutually_exclusive_group(required=True)
  g.add_argument("--owners", help="comma-separated OneDrive owners (e.g. alan_contoso_com)")
  g.add_argument("--from-index", action="store_true", help="all migrated roots in the index")
  p.add_argument("--host", help="OneDrive host for --owners, e.g. contoso-my.sharepoint.com")
  p.add_argument("--out", default=f"share_links_{time.strftime('%Y%m%d_%H%M%S')}.csv")
  p.add_argument("--load", action="store_true", help="also insert into BigQuery share_links")
  a = p.parse_args()

  if a.owners:
    if not a.host:
      raise SystemExit("--host is required with --owners")
    targets = [(a.host.lower(), "personal", o.strip()) for o in a.owners.split(",") if o.strip()]
  else:
    targets = [t for t in map(parse_root, roots_from_index()) if t]
  print(f"harvesting {len(targets)} drive(s)", file=sys.stderr)

  rows, stats = harvest(Graph(), targets, log=lambda m: print(m, file=sys.stderr))
  with open(a.out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["url", "item_guid", "source"])
    w.writerows((u, gd, "harvest") for u, gd in rows)
  print(json.dumps({"event": "harvest_done", "csv": a.out, **stats}))

  if a.load and rows:
    import subprocess
    subprocess.run([sys.executable, "-m", "tools.import_share_links", a.out, "--source", "harvest"],
                   check=True)


if __name__ == "__main__":
  main()
