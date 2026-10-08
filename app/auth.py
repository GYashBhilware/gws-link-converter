"""Who is calling? Verifies the IAP-signed JWT and restricts to allowed Workspace domains.

AUTH_MODE=iap (default): require a valid `x-goog-iap-jwt-assertion` header
  (signed by IAP, audience = IAP_AUDIENCE) for a user in ALLOWED_DOMAIN (list separated by "," or ";").
AUTH_MODE=dev: local development only; refused when running on Cloud Run.
"""

import os
import re

from fastapi import HTTPException, Request
from google.auth.transport import requests as ga_requests
from google.oauth2 import id_token

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"
_session = ga_requests.Request()


def _cfg():
  return {
      "mode": os.environ.get("AUTH_MODE", "iap"),
      "audience": os.environ.get("IAP_AUDIENCE", ""),
      "domains": [d.strip().lower() for d in
                  re.split(r"[,;]", os.environ.get("ALLOWED_DOMAIN", ""))
                  if d.strip()],
      "dev_user": os.environ.get("DEV_USER", "dev@example.com"),
  }


def check_config():
  """Fail fast at startup on unsafe/incomplete auth configuration."""
  c = _cfg()
  if c["mode"] == "dev" and (os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB")):
    raise RuntimeError("AUTH_MODE=dev is not allowed on Cloud Run")
  if c["mode"] == "iap" and not c["audience"]:
    raise RuntimeError("IAP_AUDIENCE must be set when AUTH_MODE=iap")
  if c["mode"] == "iap" and not c["domains"]:
    raise RuntimeError("ALLOWED_DOMAIN must list at least one Workspace domain when AUTH_MODE=iap")
  if c["mode"] not in ("iap", "dev"):
    raise RuntimeError(f"Unknown AUTH_MODE {c['mode']!r}")


def verify_iap_jwt(token: str, audience: str) -> dict:
  claims = id_token.verify_token(token, _session, audience=audience, certs_url=IAP_CERTS_URL)
  if claims.get("iss") != IAP_ISSUER:
    raise ValueError("wrong issuer")
  return claims


def current_user(request: Request) -> str:
  c = _cfg()
  if c["mode"] == "dev":
    return c["dev_user"]
  token = request.headers.get("x-goog-iap-jwt-assertion")
  if not token:
    raise HTTPException(401, "Missing IAP credentials. Open the app through its HTTPS URL.")
  try:
    claims = verify_iap_jwt(token, c["audience"])
  except Exception:  # noqa: BLE001 - any verification failure is a 401
    raise HTTPException(401, "Invalid IAP credentials.") from None
  email = (claims.get("email") or "").lower()
  hd = claims.get("hd", "").lower()
  if hd not in c["domains"] or email.rpartition("@")[2] not in c["domains"]:
    raise HTTPException(403, f"Only {', '.join(c['domains'])} accounts may use this app.")
  return email
