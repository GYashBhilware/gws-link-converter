"""Cloud Run job entrypoint: one incremental sync run (see sql/sync.sql).

Usage: python -m tools.sync_job [--overlap-hours 2]
  Scheduled every 5 min with the default overlap; nightly with --overlap-hours 72
  as a catch-up for very late export rows.
"""

import argparse
import json
import os
import sys
import time

from app import bq
from app import config
from tools.run_sql import render


def main():
  p = argparse.ArgumentParser()
  p.add_argument("--overlap-hours", type=float,
                 default=float(os.environ.get("SYNC_OVERLAP_HOURS", "2")))
  a = p.parse_args()
  config.check()
  ds = config.index_dataset()
  src = config.source_table()
  t0 = time.time()
  try:
    rows, stats = bq.query(render("sync", ds, src, a.overlap_hours), dry_run_first=False)
  except Exception as e:  # noqa: BLE001
    print(json.dumps({"severity": "ERROR", "event": "sync_failed", "error": str(e)[:1500],
                      "overlap_hours": a.overlap_hours}), flush=True)
    sys.exit(1)
  r = rows[0] if rows else {}
  print(json.dumps({"severity": "INFO", "event": "sync_ok", "overlap_hours": a.overlap_hours,
                    "seconds": round(time.time() - t0, 1), "bytes": stats["bytes"], **r},
                   default=str), flush=True)


if __name__ == "__main__":
  main()
