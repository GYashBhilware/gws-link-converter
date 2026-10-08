#!/usr/bin/env python3
"""Render and run a SQL file from sql/ against BigQuery.

Usage: python3 -m tools.run_sql <schema|sync> [--ds DATASET] [--src PROJECT.DATASET.TABLE]
       [--overlap-hours 2]
Defaults come from the environment (GCP_PROJECT, BQ_DATASET, BQ_SOURCE_TABLE).
"""

import argparse
import json
import pathlib

from app import bq
from app import config

ROOT = pathlib.Path(__file__).resolve().parent.parent


def render(name, ds, src, overlap_hours):
  # Plain placeholder replacement (not str.format): SQL/JS bodies contain braces.
  text = (ROOT / "sql" / f"{name}.sql").read_text()
  for k, v in {"{project}": bq.PROJECT, "{ds}": ds, "{src}": src,
               "{location}": bq.LOCATION,
               "{overlap_usec}": str(int(overlap_hours * 3600 * 1_000_000))}.items():
    text = text.replace(k, v)
  return text


def main():
  p = argparse.ArgumentParser()
  p.add_argument("name", choices=["schema", "sync"])
  p.add_argument("--ds", default=config.index_dataset())
  p.add_argument("--src", default=config.source_table())
  p.add_argument("--overlap-hours", type=float, default=2)
  a = p.parse_args()
  if not bq.PROJECT:
    raise SystemExit("GCP_PROJECT is not set (source deploy.env first).")
  if a.name == "sync" and not a.src:
    raise SystemExit("BQ_SOURCE_TABLE / --src is not set.")
  sql = render(a.name, a.ds, a.src, a.overlap_hours)
  # Scripts with CREATE SCHEMA can't be dry-run before the schema exists.
  rows, stats = bq.query(sql, dry_run_first=False)
  print(json.dumps({"stats": stats, "rows": rows}, default=str, indent=1))


if __name__ == "__main__":
  main()
