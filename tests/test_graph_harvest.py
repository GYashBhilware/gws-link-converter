"""Microsoft Graph client + harvester with a fake HTTP layer (no network, no secrets)."""

import io
import json
import unittest
import urllib.error
import urllib.parse

from tests.test_sharelink import USER_LINK
from tools import graph as graph_mod
from tools.graph import Graph, GraphError, share_id
from tools.harvest_share_links import harvest, parse_root

GUID = "1A2B3C4D-5E6F-4A7B-8C9D-0E1F2A3B4C5D"


class _Resp(io.BytesIO):
  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False


class FakeGraphHTTP:
  """Routes Graph URLs to canned JSON; can throttle the first N calls of a route."""

  def __init__(self, routes, throttle=None):
    self.routes, self.throttle = routes, dict(throttle or {})
    self.calls = []

  def __call__(self, req, timeout=None):
    url = req.full_url
    self.calls.append((req.get_method(), url, dict(req.header_items())))
    if "login.microsoftonline.com" in url:
      assert b"client_secret=s3cr3t" in req.data
      return _Resp(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
    path = url.replace(graph_mod.GRAPH, "")
    for prefix, n in list(self.throttle.items()):
      if path.startswith(prefix) and n > 0:
        self.throttle[prefix] = n - 1
        raise urllib.error.HTTPError(url, 429, "Too Many", {"Retry-After": "1"}, io.BytesIO(b""))
    for prefix, body in self.routes.items():
      if path.startswith(prefix):
        return _Resp(json.dumps(body).encode())
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"error":"nf"}'))


def make(routes, throttle=None):
  http = FakeGraphHTTP(routes, throttle)
  sleeps = []
  g = Graph("tenant", "client", "s3cr3t", opener=http, sleep=sleeps.append)
  return g, http, sleeps


ROUTES = {
    "/sites/contoso-my.sharepoint.com:/personal/yash_contoso_com": {"id": "SITE1"},
    "/sites/SITE1/drive": {"id": "DRIVE1"},
    "/drives/DRIVE1/root/delta": {"value": [
        {"id": "A", "name": "Budget.xlsx", "shared": {"scope": "organization"}},
        {"id": "B", "name": "private.docx"},
        {"id": "C", "name": "gone.pdf", "shared": {}, "deleted": {}},
    ], "@odata.nextLink": graph_mod.GRAPH + "/pages/DRIVE1/2"},
    "/pages/DRIVE1/2": {"value": [
        {"id": "D", "name": "Plan.pdf", "shared": {"scope": "users"}}]},
    "/drives/DRIVE1/items/A/permissions": {"value": [
        {"id": "p1", "link": {"type": "view", "scope": "organization", "webUrl": USER_LINK}},
        {"id": "p2", "grantedToV2": {"user": {"email": "x@contoso.com"}}},  # direct grant, no link
    ]},
    "/drives/DRIVE1/items/A?": {"id": "A", "sharepointIds": {"listItemUniqueId": GUID}},
    "/drives/DRIVE1/items/D/permissions": {"value": []},
}


class GraphClientTest(unittest.TestCase):

  def test_share_id_encoding(self):
    sid = share_id("https://x.sharepoint.com/:x:/g/a")
    self.assertTrue(sid.startswith("u!"))
    self.assertNotIn("=", sid)

  def test_missing_credentials(self):
    with self.assertRaises(GraphError):
      Graph("", "", "", opener=lambda *a, **k: None)

  def test_resolve_share_and_token_reuse(self):
    g, http, _ = make({f"/shares/{share_id(USER_LINK)}/driveItem":
                       {"name": "Budget.xlsx", "sharepointIds": {"listItemUniqueId": GUID}}})
    self.assertEqual(g.resolve_share(USER_LINK)["item_guid"], GUID.lower())
    g.resolve_share(USER_LINK)
    self.assertEqual(sum("login.microsoftonline" in u for _, u, _ in http.calls), 1)
    self.assertTrue(all(h.get("Authorization") == "Bearer tok"
                        for _, u, h in http.calls if "graph.microsoft.com" in u))

  def test_throttling_honors_retry_after(self):
    g, _, sleeps = make(ROUTES, throttle={"/drives/DRIVE1/items/A/permissions": 2})
    self.assertEqual(g.link_urls("DRIVE1", "A"), [USER_LINK])
    self.assertEqual(sleeps, [1, 1])

  def test_paging_loop_is_detected(self):
    g, _, _ = make({"/loop": {"value": [{"id": 1}], "@odata.nextLink": graph_mod.GRAPH + "/loop"}})
    with self.assertRaisesRegex(GraphError, "paging loop"):
      list(g.pages("/loop"))

  def test_errors_are_reported_without_secret(self):
    g, _, _ = make({})
    with self.assertRaises(GraphError) as cm:
      g.get("/nope")
    self.assertNotIn("s3cr3t", str(cm.exception))


class HarvestTest(unittest.TestCase):

  def test_harvest_collects_link_to_guid(self):
    g, _, _ = make(ROUTES)
    rows, stats = harvest(g, [("contoso-my.sharepoint.com", "personal",
                               "yash_contoso_com")], log=lambda m: None)
    self.assertEqual(rows, [(USER_LINK, GUID.lower())])
    self.assertEqual(stats, {"drives": 1, "shared_items": 2, "links": 1, "errors": 0})

  def test_unknown_drive_counts_error_and_continues(self):
    g, _, _ = make(ROUTES)
    rows, stats = harvest(g, [("contoso-my.sharepoint.com", "personal", "nobody"),
                              ("contoso-my.sharepoint.com", "personal",
                               "yash_contoso_com")], log=lambda m: None)
    self.assertEqual(stats["errors"], 1)
    self.assertEqual(len(rows), 1)

  def test_harvested_rows_load_and_resolve_the_user_link(self):
    # End-to-end: harvest -> CSV-import validation -> repository lookup.
    from app.repository import Repository
    from tests.test_repository import FakeBQ, row
    from tools.import_share_links import parse_rows
    g, _, _ = make(ROUTES)
    rows, _ = harvest(g, [("contoso-my.sharepoint.com", "personal",
                           "yash_contoso_com")], log=lambda m: None)
    good, errors = parse_rows([{"url": u, "item_guid": gd} for u, gd in rows])
    self.assertEqual(errors, [])
    shares = {k: gd for k, gd, *_ in good}
    fake = FakeBQ(index=[row("/Budget.xlsx", guid=GUID.lower())], shares=shares)
    [r] = Repository(fake).lookup([USER_LINK + "?e=zz"])
    self.assertEqual((r.status, r.match), ("FOUND", "share-harvested"))

  def test_parse_root(self):
    self.assertEqual(parse_root("https://C-my.sharepoint.com/personal/a_b_com/Documents/x"),
                     ("c-my.sharepoint.com", "personal", "a_b_com"))
    self.assertEqual(parse_root("https://c.sharepoint.com/sites/Fin/Shared Documents/x")[1:],
                     ("sites", "Fin"))
    self.assertIsNone(parse_root("https://c.sharepoint.com/"))


if __name__ == "__main__":
  unittest.main()
