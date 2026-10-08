#!/usr/bin/env python3
"""Drop the app's index dataset: python3 -m tools.run_sql_drop <project> <dataset>.

Refuses to drop a dataset that does not look like the app's index (must contain
the `sync_state` and `migration_events` tables), so a typo can't delete the
Workspace export.
"""

import sys

from app import bq

API = "https://bigquery.googleapis.com/bigquery/v2/projects"


def main():
  project, dataset = sys.argv[1:3]
  for t in ("sync_state", "migration_events"):
    bq._request("GET", f"{API}/{project}/datasets/{dataset}/tables/{t}")  # 404 -> abort
  bq._request("DELETE", f"{API}/{project}/datasets/{dataset}?deleteContents=true")
  print(f"dropped {project}.{dataset}")


if __name__ == "__main__":
  main()
