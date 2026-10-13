"""Minimal Microsoft Graph client (stdlib only) for collecting sharing links.

App-only (client credentials) auth with an Azure app registration that has
*read-only* application permissions: Sites.Read.All (or Files.Read.All), with
admin consent. Secrets come from the environment and are never logged:

  GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
  pass


def share_id(url: str) -> str:
  """Graph 'encoded sharing URL': u! + unpadded base64url (see /shares API docs)."""
  b = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
  return "u!" + b


class Graph:

  def __init__(self, tenant=None, client_id=None, secret=None, opener=None, sleep=time.sleep):
    self.tenant = tenant or os.environ.get("GRAPH_TENANT_ID", "")
    self.client_id = client_id or os.environ.get("GRAPH_CLIENT_ID", "")
    self._secret = secret or os.environ.get("GRAPH_CLIENT_SECRET", "")
    if not (self.tenant and self.client_id and self._secret):
      raise GraphError("Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET.")
    self._open = opener or urllib.request.urlopen
    self._sleep = sleep
    self._token, self._exp = None, 0.0

  # -- transport ------------------------------------------------------------
  def _token_value(self):
    if self._token and time.time() < self._exp:
      return self._token
    body = urllib.parse.urlencode({
        "client_id": self.client_id, "client_secret": self._secret,
        "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data=body,
        method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    d = self._call(req, auth=False)
    self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600)) - 120
    return self._token

  def _call(self, req, auth=True, attempts=6):
    for i in range(attempts):
      if auth:
        req.add_header("Authorization", "Bearer " + self._token_value())
      try:
        with self._open(req, timeout=60) as r:
          raw = r.read()
          return json.loads(raw) if raw else {}
      except urllib.error.HTTPError as e:
        if e.code in (429, 503, 504) and i < attempts - 1:  # throttled: honor Retry-After
          wait = int((e.headers or {}).get("Retry-After") or 2 ** i)
          self._sleep(min(wait, 120))
          continue
        detail = e.read().decode(errors="replace")[:300] if hasattr(e, "read") else ""
        raise GraphError(f"HTTP {e.code} {req.full_url.split('?')[0]}: {detail}") from None
    raise GraphError("unreachable")

  def get(self, path_or_url):
    url = path_or_url if path_or_url.startswith("https://") else GRAPH + path_or_url
    return self._call(urllib.request.Request(url, method="GET"))

  def pages(self, path_or_url):
    """Yields items across @odata.nextLink pages (guards against repeating links)."""
    url, seen = path_or_url, set()
    while url:
      if url in seen:
        raise GraphError(f"paging loop at {url.split('?')[0]}")
      seen.add(url)
      d = self.get(url)
      yield from d.get("value", [])
      url = d.get("@odata.nextLink")

  # -- domain helpers ---------------------------------------------------------
  def resolve_share(self, url):
    """Sharing link -> {'item_guid', 'name'} while SharePoint is online."""
    d = self.get(f"/shares/{share_id(url)}/driveItem?$select=id,name,sharepointIds")
    guid = (d.get("sharepointIds") or {}).get("listItemUniqueId")
    return {"item_guid": guid.lower() if guid else None, "name": d.get("name")}

  def drive_id(self, host, scope, owner):
    """OneDrive (scope personal) or site default library -> drive id."""
    rel = f"personal/{owner}" if scope == "personal" else f"sites/{owner}"
    site = self.get(f"/sites/{host}:/{urllib.parse.quote(rel)}?$select=id")
    return self.get(f"/sites/{site['id']}/drive?$select=id")["id"]

  def shared_items(self, drive_id):
    """Every item in the drive that has sharing on it (delta walk of the whole drive)."""
    for it in self.pages(f"/drives/{drive_id}/root/delta?$select=id,name,shared,deleted"):
      if it.get("shared") and not it.get("deleted"):
        yield it

  def item_guid(self, drive_id, item_id):
    d = self.get(f"/drives/{drive_id}/items/{item_id}?$select=id,sharepointIds")
    g = (d.get("sharepointIds") or {}).get("listItemUniqueId")
    return g.lower() if g else None

  def link_urls(self, drive_id, item_id):
    """Sharing-link URLs on one item (permissions that are links, not direct grants)."""
    return [p["link"]["webUrl"]
            for p in self.pages(f"/drives/{drive_id}/items/{item_id}/permissions")
            if (p.get("link") or {}).get("webUrl")]
