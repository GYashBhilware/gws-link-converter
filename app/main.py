"""gws-link-converter: SharePoint/OneDrive link -> Google Drive link lookup."""

import dataclasses
import json
import logging
import pathlib
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth
from app import config
from app.bq import BigQueryError
from app.repository import MAX_BULK, Repository

STATIC = pathlib.Path(__file__).parent / "static"
MAX_URL_LEN = 4096

log = logging.getLogger("gws-link-converter")
logging.basicConfig(level=logging.INFO, format="%(message)s")

config.check()
auth.check_config()
app = FastAPI(title="gws-link-converter", docs_url=None, redoc_url=None, openapi_url=None)
repo = Repository()


@app.middleware("http")
async def security_headers(request: Request, call_next):
  resp = await call_next(request)
  resp.headers["Content-Security-Policy"] = (
      "default-src 'self'; img-src 'self' data:; style-src 'self'; "
      "script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
  resp.headers["X-Content-Type-Options"] = "nosniff"
  resp.headers["Referrer-Policy"] = "no-referrer"
  resp.headers["Cache-Control"] = "no-store"
  return resp


@app.exception_handler(BigQueryError)
async def bq_error(_request: Request, exc: BigQueryError):
  log.error(json.dumps({"severity": "ERROR", "event": "bigquery_error", "error": str(exc)[:500]}))
  return JSONResponse({"detail": "Lookup backend unavailable, please retry."}, status_code=502)


def _audit(user, action, results):
  log.info(json.dumps({
      "severity": "INFO", "event": action, "user": user,
      "count": len(results),
      "statuses": [r.status for r in results],
      "inputs": [r.input[:300] for r in results],
  }))


def _check_url(u: str):
  if len(u) > MAX_URL_LEN:
    raise HTTPException(400, f"URL longer than {MAX_URL_LEN} characters.")


class BulkRequest(BaseModel):
  urls: list[str] = Field(..., min_length=1)


@app.get("/healthz", include_in_schema=False)
def healthz():
  return {"ok": True}


@app.get("/", include_in_schema=False)
def index(_user: str = Depends(auth.current_user)):
  return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/me")
def me(user: str = Depends(auth.current_user)):
  wm = repo.watermark()
  last = wm.get("last_run_at")
  return {"email": user, "max_bulk": MAX_BULK, "app_title": config.app_title(),
          "index_last_synced": last,
          "index_age_seconds": int(time.time() - last) if last else None}


@app.get("/api/lookup")
def lookup(url: str, user: str = Depends(auth.current_user)):
  _check_url(url)
  results = repo.lookup([url])
  _audit(user, "lookup", results)
  return dataclasses.asdict(results[0])


@app.post("/api/bulk")
def bulk(body: BulkRequest, user: str = Depends(auth.current_user)):
  urls = [u for u in (x.strip() for x in body.urls) if u]
  if not urls:
    raise HTTPException(400, "No links provided.")
  if len(urls) > MAX_BULK:
    raise HTTPException(400, f"At most {MAX_BULK} links per request.")
  for u in urls:
    _check_url(u)
  results = repo.lookup(urls)
  _audit(user, "bulk_lookup", results)
  return {"results": [dataclasses.asdict(r) for r in results]}


@app.get("/api/folder")
def folder(url: str, user: str = Depends(auth.current_user)):
  _check_url(url)
  head, children = repo.folder(url)
  _audit(user, "folder", [head])
  return {"folder": dataclasses.asdict(head), "children": children}
