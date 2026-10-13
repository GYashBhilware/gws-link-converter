#!/usr/bin/env python3
"""Post-install health check: is everything the installer built really working?

  python3 -m tools.verify [--config deploy.env]

Read-only. Prints PASS / WARN / FAIL per check (same format as preflight) and
exits 1 if anything FAILs. Also run automatically at the end of ./install.sh,
and on demand with ./install.sh verify.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

from app import bq
from tools.preflight import BQAPI, Report, _gcloud, _try, load_env


def _json(rc_out_err):
  rc, out, err = rc_out_err
  if rc != 0:
    return None, (err or out)[-200:]
  try:
    return json.loads(out or "null"), None
  except ValueError:
    return None, "unparseable gcloud output"


def check(cfg_path):
  env = load_env(cfg_path)
  g = env.get
  rep = Report()
  project, region, service = g("PROJECT_ID"), g("REGION"), g("SERVICE_NAME")
  prefix = g("NAME_PREFIX", "link-converter")
  job = g("JOB_NAME") or f"{prefix}-sync"
  dataset = g("INDEX_DATASET", "migration_lookup")
  sched_region = g("SCHEDULER_REGION") or region
  schedules = [g("SCHEDULE_NAME") or f"{job}-frequent",
               g("CATCHUP_SCHEDULE_NAME") or f"{job}-nightly-catchup"]
  domains = [d for d in re.split(r"[,\s]+", g("ALLOWED_DOMAINS", "")) if d]
  P = f"--project={project}"
  print(f"Health check for {cfg_path} ({project} / {region} / {service})")

  # 1. Cloud Run service
  svc, err = _json(_gcloud("run", "services", "describe", service, P, f"--region={region}",
                           "--format=json"))
  url = None
  if not svc:
    rep.add("FAIL", "Cloud Run service exists", err)
  else:
    conds = {c["type"]: c for c in svc.get("status", {}).get("conditions", [])}
    ready = conds.get("Ready", {}).get("status") == "True"
    url = svc.get("status", {}).get("url")
    rev = svc.get("status", {}).get("latestReadyRevisionName")
    rep.add("PASS" if ready else "FAIL", "Cloud Run service ready",
            f"{rev} {url}" if ready else conds.get("Ready", {}).get("message", "not ready"))
    ann = svc.get("metadata", {}).get("annotations", {})
    rep.add("PASS" if ann.get("run.googleapis.com/iap-enabled") == "true" else "FAIL",
            "Google sign-in (IAP) enabled on the service")
    envs = {e["name"]: e.get("value", "")
            for e in svc["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    want = {"BQ_SOURCE_TABLE": g("EXPORT_TABLE"), "BQ_DATASET": dataset, "AUTH_MODE": "iap"}
    wrong = [k for k, v in want.items() if envs.get(k) != v]
    rep.add("FAIL" if wrong else "PASS", "Service settings match deploy.env",
            ("different: " + ", ".join(wrong)) if wrong else "")

  # 2. Unauthenticated access is blocked (sent to Google sign-in)
  if url:
    code = None
    try:
      req = urllib.request.Request(url + "/", method="GET")
      opener = urllib.request.build_opener(_NoRedirect())
      code = opener.open(req, timeout=20).status
    except urllib.error.HTTPError as e:
      code = e.code
    except Exception as e:  # noqa: BLE001
      rep.add("WARN", "Anonymous access blocked", f"could not reach {url}: {e}")
    if code is not None:
      rep.add("PASS" if code in (302, 401, 403) else "FAIL", "Anonymous access blocked",
              f"HTTP {code}" + ("" if code in (302, 401, 403) else " - app reachable without sign-in!"))

  # 3. IAP policy allows the configured domains
  pol, err = _json(_gcloud("iap", "web", "get-iam-policy", "--resource-type=cloud-run",
                           f"--region={region}", f"--service={service}", P, "--format=json"))
  if pol is None:
    rep.add("WARN", "Sign-in policy (IAP) for your domains", err)
  else:
    members = {m for b in pol.get("bindings", []) if b.get("role") == "roles/iap.httpsResourceAccessor"
               for m in b.get("members", [])}
    present = [d for d in domains if f"domain:{d}" in members]
    # Secondary domains of the same tenant are folded into the primary binding by IAM.
    rep.add("PASS" if present else "FAIL", "Sign-in policy (IAP) for your domains",
            ", ".join(sorted(m for m in members if m.startswith("domain:"))) or "no domain bindings")

  # 4. Sync job + last run
  rc, _, err = _gcloud("run", "jobs", "describe", job, P, f"--region={region}", "--format=value(name)")
  rep.add("PASS" if rc == 0 else "FAIL", "Sync job exists", job if rc == 0 else err[-200:])
  ex, err = _json(_gcloud("run", "jobs", "executions", "list", f"--job={job}", P,
                          f"--region={region}", "--limit=1", "--format=json"))
  if ex:
    st = ex[0].get("status", {})
    ok, failed = st.get("succeededCount", 0), st.get("failedCount", 0)
    when = st.get("completionTime", "running")
    rep.add("PASS" if ok else ("FAIL" if failed else "WARN"), "Last sync run",
            f"{ex[0]['metadata']['name']} {'succeeded' if ok else 'failed' if failed else 'running'} {when}")
  else:
    rep.add("WARN", "Last sync run", "no runs yet" if ex == [] else err)

  # 5. Schedules
  for s in schedules:
    sj, err = _json(_gcloud("scheduler", "jobs", "describe", s, P, f"--location={sched_region}",
                            "--format=json"))
    if not sj:
      rep.add("FAIL", f"Schedule {s}", err)
    else:
      rep.add("PASS" if sj.get("state") == "ENABLED" else "FAIL", f"Schedule {s}",
              f"{sj.get('schedule')} {sj.get('state')}")

  # 6. Index dataset tables + freshness
  bq.PROJECT, bq.LOCATION = project, g("BQ_LOCATION", "US")
  bq._API = f"{BQAPI}/{project}"
  missing = [t for t in ("migration_events", "share_links", "sync_state", "uri_mapping_current")
             if _try(lambda t=t: bq._request("GET", f"{BQAPI}/{project}/datasets/{dataset}/tables/{t}"))[0]
             is None]
  rep.add("FAIL" if missing else "PASS", "Lookup tables exist",
          ("missing: " + ", ".join(missing)) if missing else f"{project}.{dataset}")
  if not missing:
    try:
      rows, _ = bq.query(f"SELECT watermark_usec, UNIX_SECONDS(last_run_at) AS last_run, "
                         f"(SELECT COUNT(*) FROM `{project}.{dataset}.uri_mapping_current`) AS mappings "
                         f"FROM `{project}.{dataset}.sync_state` WHERE name='activity'",
                         dry_run_first=False)
      if not rows or rows[0]["last_run"] is None:
        rep.add("WARN", "Lookup data freshness", "no completed sync yet")
      else:
        age = int((time.time() - rows[0]["last_run"]) / 60)
        rep.add("PASS" if age <= 30 else "WARN", "Lookup data freshness",
                f"last sync {age} min ago, {rows[0]['mappings']} migrated items searchable")
    except bq.BigQueryError as e:
      rep.add("WARN", "Lookup data freshness", str(e)[:200])

  # 7. Alerts (only if configured)
  if g("ALERT_EMAIL"):
    rc, out, err = _gcloud("alpha", "monitoring", "policies", "list", P,
                           "--format=value(displayName)")
    names = [n for n in out.splitlines() if n.startswith(f"{job}: ")] if rc == 0 else []
    rep.add("PASS" if len(names) >= 2 else "WARN", "Email alerts configured",
            f"{len(names)} policies for {g('ALERT_EMAIL')}" if rc == 0 else err[-200:])
  return rep


class _NoRedirect(urllib.request.HTTPRedirectHandler):

  def redirect_request(self, *a, **k):  # report 302 instead of following it
    return None


def main():
  args = sys.argv[1:]
  path = args[1] if len(args) > 1 and args[0] == "--config" else "deploy.env"
  rep = check(path)
  counts = {lvl: sum(r[0] == lvl for r in rep.rows) for lvl in ("PASS", "WARN", "FAIL")}
  print(f"Health check: {'FAILED' if rep.failed else 'OK'} "
        f"({counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail)")
  sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
  main()
