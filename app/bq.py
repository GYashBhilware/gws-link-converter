"""Minimal BigQuery REST client (stdlib only) used by tools and the sync job.

Auth order: $BQ_ACCESS_TOKEN, then the GCE/Cloud Run metadata server, then
`gcloud auth print-access-token` (local development on the cloudtop).
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from app import config

PROJECT = config.project()
LOCATION = config.location()
MAX_BYTES = int(os.environ.get("BQ_MAX_BYTES", str(2 * 1024**3)))
_API = "https://bigquery.googleapis.com/bigquery/v2/projects/" + PROJECT


class BigQueryError(RuntimeError):
  pass


_token_cache = {"value": None, "expires": 0.0}


def _metadata_token():
  req = urllib.request.Request(
      "http://metadata.google.internal/computeMetadata/v1/instance/"
      "service-accounts/default/token",
      headers={"Metadata-Flavor": "Google"},
  )
  with urllib.request.urlopen(req, timeout=3) as r:
    d = json.load(r)
  return d["access_token"], d.get("expires_in", 300)


def access_token() -> str:
  if os.environ.get("BQ_ACCESS_TOKEN"):
    return os.environ["BQ_ACCESS_TOKEN"]
  if _token_cache["value"] and time.time() < _token_cache["expires"]:
    return _token_cache["value"]
  tok, ttl = None, 300
  if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
    tok, ttl = _metadata_token()
  else:
    tok = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True, timeout=30,
        stderr=subprocess.DEVNULL,
    ).strip()
    ttl = 600
  if not tok:
    raise BigQueryError("No access token (run: gcloud auth login --update-adc)")
  _token_cache.update(value=tok, expires=time.time() + ttl - 60)
  return tok


def _request(method, url, body=None):
  req = urllib.request.Request(
      url, method=method,
      data=json.dumps(body).encode() if body is not None else None,
      headers={"Authorization": "Bearer " + access_token(),
               "Content-Type": "application/json"},
  )
  try:
    with urllib.request.urlopen(req, timeout=120) as r:
      body = r.read()
      return json.loads(body) if body else {}  # DELETE returns 204 with no body
  except urllib.error.HTTPError as e:
    raise BigQueryError(f"HTTP {e.code}: {e.read().decode()[:1500]}") from e


def _cell(v, field):
  if v is None:
    return None
  if field.get("mode") == "REPEATED":
    return [_cell(x["v"], {**field, "mode": "NULLABLE"}) for x in v]
  t = field["type"]
  if t in ("RECORD", "STRUCT"):
    return {f["name"]: _cell(c["v"], f) for f, c in zip(field["fields"], v["f"])}
  if t in ("INTEGER", "INT64"):
    return int(v)
  if t in ("FLOAT", "FLOAT64"):
    return float(v)
  if t in ("BOOLEAN", "BOOL"):
    return v == "true"
  if t == "TIMESTAMP":
    return float(v)  # epoch seconds
  return v


def _param(name, value):
  if isinstance(value, list):
    return {"name": name,
            "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
            "parameterValue": {"arrayValues": [{"value": str(x)} for x in value]}}
  typ = "INT64" if isinstance(value, int) and not isinstance(value, bool) else "STRING"
  return {"name": name, "parameterType": {"type": typ},
          "parameterValue": {"value": None if value is None else str(value)}}


def query(sql, params=None, *, dry_run_first=True, max_rows=10000):
  """Runs SQL (scripts allowed). Returns (rows as dicts, stats dict)."""
  base = {"query": sql, "useLegacySql": False, "location": LOCATION}
  if params:
    base["parameterMode"] = "NAMED"
    base["queryParameters"] = [_param(k, v) for k, v in params.items()]
  if dry_run_first:
    dry = _request("POST", _API + "/queries", {**base, "dryRun": True})
    if int(dry.get("totalBytesProcessed", 0)) > MAX_BYTES:
      raise BigQueryError("Query exceeds byte cap: " + dry["totalBytesProcessed"])
  res = _request("POST", _API + "/queries",
                 {**base, "maxResults": max_rows, "timeoutMs": 60000,
                  "maximumBytesBilled": str(MAX_BYTES)})
  job = res["jobReference"]
  while not res.get("jobComplete"):
    time.sleep(1)
    res = _request(
        "GET", f"{_API}/queries/{job['jobId']}?location={job['location']}"
        f"&maxResults={max_rows}&timeoutMs=30000")
  if res.get("errors"):
    raise BigQueryError(json.dumps(res["errors"])[:1500])
  rows = []
  if "schema" in res:
    fields = res["schema"]["fields"]
    for r in res.get("rows", []):
      rows.append({f["name"]: _cell(c["v"], f) for f, c in zip(fields, r["f"])})
  stats = {
      "job_id": job["jobId"],
      "bytes": int(res.get("totalBytesProcessed", 0)),
      "dml_rows": int(res["numDmlAffectedRows"]) if "numDmlAffectedRows" in res else None,
  }
  return rows, stats


def job_children(job_id):
  """Child job statistics of a script (to read per-statement DML counts)."""
  res = _request("GET", f"{_API}/jobs?parentJobId={job_id}&allUsers=false"
                 "&projection=full&maxResults=50")
  out = []
  for j in res.get("jobs", []):
    q = j.get("statistics", {}).get("query", {})
    out.append({"type": q.get("statementType"),
                "dml": q.get("dmlStats"),
                "affected": q.get("numDmlAffectedRows")})
  return out
