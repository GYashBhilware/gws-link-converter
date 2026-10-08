#!/usr/bin/env python3
"""Idempotently add or remove dataset-level access entries (BigQuery REST).

Usage: python3 -m tools.grant_dataset <project> <dataset> <READER|WRITER> <sa-email> [...]
       python3 -m tools.grant_dataset --remove <project> <dataset> <sa-email> [...]
"""

import sys

from app import bq


def main():
  args = sys.argv[1:]
  remove = bool(args) and args[0] == "--remove"
  if remove:
    project, dataset, *emails = args[1:]
    role = None
  else:
    project, dataset, role, *emails = args
  url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset}"
  ds = bq._request("GET", url)
  access = ds.get("access", [])
  if remove:
    new = [a for a in access if a.get("userByEmail") not in emails]
    changed = len(new) != len(access)
    access = new
  else:
    changed = False
    for email in emails:
      if not any(a.get("role") == role and a.get("userByEmail") == email for a in access):
        access.append({"role": role, "userByEmail": email})
        changed = True
  if changed:
    bq._request("PATCH", url, {"access": access, "etag": ds["etag"]})
  verb = "removed" if remove else "added"
  print(f"{project}.{dataset}: {role or 'ALL'} {emails} ({verb if changed else 'no change'})")


if __name__ == "__main__":
  main()
