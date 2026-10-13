#!/usr/bin/env python3
"""Discovery + preflight checks for install.sh (uses the caller's gcloud credentials).

  python3 -m tools.preflight discover-datasets <export-project>
      -> JSON list of datasets that contain an `activity` table
  python3 -m tools.preflight table-info <project.dataset.table>
      -> JSON: exists, location, has data_migration fields
  python3 -m tools.preflight org-domain <project>
      -> primary Workspace domain of the project's organization ('' if none)
  python3 -m tools.preflight check [--config deploy.env]
      -> PASS/WARN/FAIL report; exit 1 if anything FAILs. Read-only.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

from app import bq

CRM = "https://cloudresourcemanager.googleapis.com"
BQAPI = "https://bigquery.googleapis.com/bigquery/v2/projects"

# Permissions the installer needs in the app project (Owner has all of them).
INSTALL_PERMS = [
    "serviceusage.services.enable", "iam.serviceAccounts.create",
    "resourcemanager.projects.setIamPolicy", "run.services.create", "run.jobs.create",
    "cloudscheduler.jobs.create", "bigquery.datasets.create", "bigquery.jobs.create",
    "storage.buckets.create", "artifactregistry.repositories.create",
    "iap.webServices.setIamPolicy", "iam.serviceAccounts.actAs",
]


def _get(url):
  return bq._request("GET", url)


def _short(err):
  """'HTTP 404: {json...}' -> 'HTTP 404: Not found: Dataset p:x' (readable one-liner)."""
  m = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', err)
  code = err.split(":", 1)[0]
  return f"{code}: {m.group(1)}" if m else err.replace("\n", " ")[:200]


def _try(fn, attempts=4):
  """Call fn; retry rate-limit (429) errors with backoff; return (result, error)."""
  for i in range(attempts):
    try:
      return fn(), None
    except bq.BigQueryError as e:
      if "HTTP 429" in str(e) and i < attempts - 1:
        time.sleep(5 * (i + 1))
        continue
      return None, _short(str(e))


def parse_table(t):
  parts = t.split(".")
  if len(parts) != 3:
    raise ValueError("must be project.dataset.table")
  return parts


def discover_datasets(project):
  out = []
  page = ""
  while True:
    res = _get(f"{BQAPI}/{project}/datasets?maxResults=1000&all=false" +
               (f"&pageToken={page}" if page else ""))
    for d in res.get("datasets", []):
      ds = d["datasetReference"]["datasetId"]
      info, err = _try(lambda: _get(f"{BQAPI}/{project}/datasets/{ds}/tables/activity"))
      if info:
        out.append({"dataset": ds, "location": d.get("location", ""),
                    "rows": int(info.get("numRows", 0))})
    page = res.get("nextPageToken")
    if not page:
      return out


def table_info(table):
  p, d, t = parse_table(table)
  info, err = _try(lambda: _get(f"{BQAPI}/{p}/datasets/{d}/tables/{t}"))
  if not info:
    return {"exists": False, "error": err}
  fields = {f["name"]: f for f in info.get("schema", {}).get("fields", [])}
  dm = {f["name"] for f in fields.get("data_migration", {}).get("fields", [])}
  return {"exists": True, "location": info.get("location", ""),
          "rows": int(info.get("numRows", 0)),
          "partitioned": "timePartitioning" in info,
          "has_data_migration": {"source_uri", "target_uri"} <= dm,
          "has_status": "status" in fields}


def org_domain(project):
  proj, err = _try(lambda: _get(f"{CRM}/v3/projects/{project}"))
  if not proj:
    return ""
  parent = proj.get("parent", "")
  # Walk up folders to the organization.
  for _ in range(10):
    if parent.startswith("organizations/"):
      org, _e = _try(lambda: _get(f"{CRM}/v3/{parent}"))
      return (org or {}).get("displayName", "")
    if parent.startswith("folders/"):
      f, _e = _try(lambda: _get(f"{CRM}/v3/{parent}"))
      parent = (f or {}).get("parent", "")
    else:
      return ""
  return ""


def load_env(path):
  """Parse a deploy.env written by install.sh (KEY=shell-quoted value)."""
  env = {}
  for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    k, v = line.split("=", 1)
    vals = shlex.split(v) if v else [""]
    env[k.strip()] = vals[0] if vals else ""
  return env


def _swap(level_detail, name):
  return level_detail[0], name, level_detail[1]


class Report:

  def __init__(self):
    self.rows = []

  def add(self, level, name, detail=""):
    detail = " ".join(str(detail or "").split())  # one line, for logs
    self.rows.append((level, name, detail))
    out = os.environ.get("RESULTS_FILE")
    if out:  # machine-readable record for install.sh's summary
      with open(out, "a", encoding="utf-8") as f:
        f.write(f"{os.environ.get('RESULTS_STAGE', 'check')}\t{level}\t{name}\t{detail}\n")
    color = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}[level]
    print(f"  {color}{level}\033[0m  {name}" + (f" - {detail}" if detail else ""), flush=True)

  @property
  def failed(self):
    return any(r[0] == "FAIL" for r in self.rows)


def _gcloud(*args, timeout=60):
  """Runs gcloud non-interactively. --quiet + closed stdin: gcloud must never stop at a
  prompt (e.g. "API not enabled, enable and retry? (y/N)" in a brand-new project)."""
  try:
    r = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
  except subprocess.TimeoutExpired:
    return 124, "", f"gcloud {' '.join(args[:3])} timed out after {timeout}s"
  return r.returncode, r.stdout.strip(), r.stderr.strip()


_REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]{1,2}$")


def check_region(project, region):
  """-> (level, detail). The Cloud Run API is often not enabled yet before install."""
  if not _REGION_RE.match(region or ""):
    return "FAIL", f"{region!r} is not a region name (e.g. us-central1, europe-west1)"
  rc, out, err = _gcloud("run", "regions", "list", f"--project={project}",
                         "--format=value(locationId)")
  if rc == 0 and out:
    ok = region in out.split()
    return ("PASS", region) if ok else ("FAIL", f"{region} is not a Cloud Run region")
  if "SERVICE_DISABLED" in err or "has not been used" in err or "not enabled" in err:
    return "WARN", (f"{region} - Cloud Run API not enabled yet; the installer enables it "
                    "and the region is verified then")
  return "WARN", f"{region} - could not list regions ({(err or 'no output')[:120]})"


def check_service(project, region, service):
  """-> (level, detail) for an existing service with the same name."""
  rc, img, err = _gcloud("run", "services", "describe", service, f"--project={project}",
                         f"--region={region}",
                         "--format=value(spec.template.spec.containers[0].image)")
  if rc == 0:
    return "WARN", f"{service} exists and will be REPLACED (current image: {img})"
  return "PASS", f"{service} will be created"


def check(cfg_path):
  env = load_env(cfg_path)
  rep = Report()
  g = env.get
  print(f"Preflight for {cfg_path}")

  # Identity
  rc, acct, _ = _gcloud("config", "get-value", "account")
  try:
    bq.access_token()
    rep.add("PASS", "gcloud credentials", acct)
  except Exception as e:  # noqa: BLE001
    rep.add("FAIL", "gcloud credentials", f"{e}. Run: gcloud auth login")
    return rep

  # Required values + formats
  for k in ("PROJECT_ID", "REGION", "SERVICE_NAME", "EXPORT_TABLE", "ALLOWED_DOMAINS"):
    if not g(k):
      rep.add("FAIL", f"{k} set", "missing in config")
  if rep.failed:
    return rep
  project = g("PROJECT_ID")
  prefix = g("NAME_PREFIX", "link-converter")
  if not re.fullmatch(r"[a-z][a-z0-9-]{4,19}", prefix):
    rep.add("FAIL", "NAME_PREFIX", f"{prefix!r}: 5-20 chars, lowercase letters/digits/-")
  for k in ("SERVICE_NAME", "JOB_NAME"):
    v = g(k, "")
    if v and not re.fullmatch(r"[a-z]([a-z0-9-]{0,47}[a-z0-9])?", v):
      rep.add("FAIL", k, f"{v!r}: lowercase letters/digits/-, max 49 chars")
  if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", g("INDEX_DATASET", "migration_lookup")):
    rep.add("FAIL", "INDEX_DATASET", "letters, digits, underscore only")
  for k, dflt in (("SYNC_CRON", "*/5 * * * *"), ("CATCHUP_CRON", "17 2 * * *")):
    if len(g(k, dflt).split()) != 5:
      rep.add("FAIL", k, "cron needs 5 fields")
  email = g("ALERT_EMAIL", "")
  if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
    rep.add("FAIL", "ALERT_EMAIL", email)
  domains = [d for d in re.split(r"[,\s]+", g("ALLOWED_DOMAINS")) if d]
  bad = [d for d in domains if not re.fullmatch(r"([a-z0-9-]+\.)+[a-z]{2,}", d.lower())]
  if bad:
    rep.add("FAIL", "ALLOWED_DOMAINS", f"invalid: {bad}")

  # Project
  proj, err = _try(lambda: _get(f"{CRM}/v3/projects/{project}"))
  if not proj:
    rep.add("FAIL", "project accessible", err[:200])
    return rep
  rep.add("PASS", "project accessible", f"{project} ({proj.get('name', '')})")
  orgd = org_domain(project)
  if orgd:
    rep.add("PASS", "project is in an organization", orgd)
  else:
    rep.add("FAIL", "project is in an organization",
            "IAP sign-in for Workspace users needs the project under the customer's organization")
  if orgd and domains and orgd.lower() not in [d.lower() for d in domains]:
    rep.add("WARN", "allowed domains", f"org primary domain {orgd} is not in ALLOWED_DOMAINS "
            "(fine if these are its secondary domains)")
  rc, out, _ = _gcloud("billing", "projects", "describe", project, "--format=value(billingEnabled)")
  if rc == 0:
    rep.add("PASS" if out == "True" else "FAIL", "billing enabled", out)
  else:
    rep.add("WARN", "billing enabled", "could not verify (Cloud Billing API)")
  perms, err = _try(lambda: bq._request(
      "POST", f"{CRM}/v1/projects/{project}:testIamPermissions", {"permissions": INSTALL_PERMS}))
  if perms is not None:
    missing = sorted(set(INSTALL_PERMS) - set(perms.get("permissions", [])))
    rep.add("FAIL" if missing else "PASS", "installer permissions in project",
            ("missing " + ", ".join(missing) + " (Owner role recommended)") if missing else acct)
  else:
    rep.add("WARN", "installer permissions in project", err[:200])

  # Export table
  try:
    sp, sd, st = parse_table(g("EXPORT_TABLE"))
  except ValueError as e:
    rep.add("FAIL", "EXPORT_TABLE", str(e))
    return rep
  ti = table_info(g("EXPORT_TABLE"))
  if not ti["exists"]:
    rep.add("FAIL", "export table exists", (ti.get("error") or "")[:200])
    return rep
  rep.add("PASS", "export table exists", f"{g('EXPORT_TABLE')} ({ti['location']}, {ti['rows']} rows)")
  rep.add("PASS" if ti["has_data_migration"] else "FAIL", "export has data_migration fields",
          "" if ti["has_data_migration"] else
          "enable Workspace BigQuery export (Admin console > Reporting > BigQuery Export)")
  loc = g("BQ_LOCATION", "US")
  if ti["location"].lower() != loc.lower():
    rep.add("FAIL", "BQ_LOCATION matches export", f"config {loc}, export {ti['location']}")
  if sp != project:
    perms2, err2 = _try(lambda: bq._request(
        "POST", f"{CRM}/v1/projects/{sp}:testIamPermissions",
        {"permissions": ["bigquery.datasets.update", "bigquery.tables.getData"]}))
    ok = perms2 and len(perms2.get("permissions", [])) == 2
    rep.add("PASS" if ok else "WARN", "can grant read access on export project",
            sp if ok else f"need bigquery.datasets.update in {sp} ({(err2 or '')[:120]})")

  # Index dataset (created by schema step) must be co-located with the export.
  ds = g("INDEX_DATASET", "migration_lookup")
  info, _e = _try(lambda: _get(f"{BQAPI}/{project}/datasets/{ds}"))
  if info:
    same = info.get("location", "").lower() == ti["location"].lower()
    rep.add("PASS" if same else "FAIL", "index dataset location",
            f"{project}.{ds} exists in {info.get('location')}" +
            ("" if same else f"; must be {ti['location']}"))
  else:
    rep.add("PASS", "index dataset", f"{project}.{ds} will be created in {ti['location']}")

  # Migration events present (cheap column scan of last 30 days).
  if ti["has_data_migration"]:
    sql = (f"SELECT COUNT(*) n, COUNTIF(event_name IN ('CREATE_FILE', 'CREATE_FOLDER') "
           f"AND STARTS_WITH(data_migration.target_uri, 'https://')) mapped "
           f"FROM `{g('EXPORT_TABLE')}` WHERE record_type = 'data_migration'" +
           (" AND _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 31 DAY)"
            if ti["partitioned"] else ""))
    old = bq.LOCATION, bq.PROJECT, bq._API
    bq.LOCATION, bq.PROJECT = ti["location"], project
    bq._API = f"{BQAPI}/{project}"
    try:
      rows, _s = bq.query(sql)
      n, mapped = rows[0]["n"], rows[0]["mapped"]
      rep.add("PASS" if mapped else "WARN", "migration events (last 30 days)",
              f"{n} migration log rows, {mapped} files/folders with a Drive link" +
              ("" if mapped else " - app will fill in once migrations run"))
    except bq.BigQueryError as e:
      rep.add("WARN", "migration events (last 30 days)", str(e)[:200])
    finally:
      bq.LOCATION, bq.PROJECT, bq._API = old

  # Cloud Run region + existing service
  region, service = g("REGION"), g("SERVICE_NAME")
  rep.add(*_swap(check_region(project, region), "Cloud Run region"))
  rep.add(*_swap(check_service(project, region, service), "Cloud Run service"))
  return rep


def main():
  if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
  cmd, args = sys.argv[1], sys.argv[2:]
  if cmd == "discover-datasets":
    print(json.dumps(discover_datasets(args[0])))
  elif cmd == "table-info":
    print(json.dumps(table_info(args[0])))
  elif cmd == "org-domain":
    print(org_domain(args[0]))
  elif cmd == "check":
    path = args[1] if len(args) > 1 and args[0] == "--config" else "deploy.env"
    rep = check(path)
    print("Preflight: " + ("FAILED - fix the items above and re-run." if rep.failed else "OK"))
    sys.exit(1 if rep.failed else 0)
  else:
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
  main()
