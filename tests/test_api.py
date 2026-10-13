"""API + auth tests (FastAPI TestClient, fake BigQuery). Run with the venv:
.venv/bin/python -m unittest tests.test_api
"""

import importlib
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from tests.test_repository import ROOT, FakeBQ, row


def make_client(env):
  with mock.patch.dict(os.environ, env, clear=False):
    for k in ("K_SERVICE", "CLOUD_RUN_JOB"):
      if k not in env:
        os.environ.pop(k, None)
    import app.main as main
    main = importlib.reload(main)
    main.repo._query = FakeBQ(index=[row("/last mont.pdf"),
                                     row("/Meetings", item="FOLDER")],
                              folder=[row("/Meetings/a.pdf")])
    return TestClient(main.app), main


DEV = {"AUTH_MODE": "dev", "DEV_USER": "tester@contoso.com"}
IAP = {"AUTH_MODE": "iap", "IAP_AUDIENCE": "/projects/1/locations/r/services/s",
       "ALLOWED_DOMAIN": "contoso.com;demo.contoso.com"}


class ApiDevModeTest(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.env = mock.patch.dict(os.environ, DEV)
    cls.env.start()
    cls.client, cls.main = make_client(DEV)

  @classmethod
  def tearDownClass(cls):
    cls.env.stop()

  def test_healthz_and_headers(self):
    r = self.client.get("/healthz")
    self.assertEqual(r.json(), {"ok": True})
    self.assertIn("default-src 'self'", r.headers["content-security-policy"])
    self.assertEqual(r.headers["x-content-type-options"], "nosniff")

  def test_index_and_static(self):
    self.assertIn("link finder", self.client.get("/").text)
    self.assertEqual(self.client.get("/static/app.js").status_code, 200)

  def test_lookup_found(self):
    r = self.client.get("/api/lookup", params={"url": ROOT + "/last%20mont.pdf"})
    self.assertEqual(r.status_code, 200)
    self.assertEqual(r.json()["status"], "FOUND")

  def test_lookup_unsupported_and_invalid(self):
    r = self.client.get("/api/lookup", params={
        "url": "https://contoso-my.sharepoint.com/:x:/g/personal/a/Eabc?e=1"})
    self.assertEqual(r.json()["status"], "UNSUPPORTED")
    self.assertEqual(self.client.get("/api/lookup", params={"url": "hello"}).json()["status"],
                     "INVALID")

  def test_lookup_modern_attachment_link(self):
    from tests.test_sharelink import USER_LINK
    r = self.client.get("/api/lookup", params={"url": USER_LINK + "?e=Ab12"})
    self.assertEqual(r.status_code, 200)
    body = r.json()
    self.assertEqual((body["status"], body["match"]), ("NOT_FOUND", "share"))
    self.assertEqual(body["share_owner"], "yash_contoso_com")
    self.assertIn("Excel workbook", body["message"])

  def test_lookup_missing_or_too_long(self):
    self.assertEqual(self.client.get("/api/lookup").status_code, 422)
    r = self.client.get("/api/lookup", params={"url": ROOT + "/" + "a" * 5000})
    self.assertEqual(r.status_code, 400)

  def test_bulk(self):
    r = self.client.post("/api/bulk", json={"urls": [ROOT + "/last mont.pdf", " ", ROOT + "/x"]})
    self.assertEqual([x["status"] for x in r.json()["results"]], ["FOUND", "NOT_FOUND"])
    self.assertEqual(self.client.post("/api/bulk", json={"urls": [" "]}).status_code, 400)
    self.assertEqual(self.client.post("/api/bulk", json={"urls": []}).status_code, 422)
    too_many = {"urls": [ROOT + f"/{i}" for i in range(self.main.MAX_BULK + 1)]}
    self.assertEqual(self.client.post("/api/bulk", json=too_many).status_code, 400)

  def test_folder(self):
    r = self.client.get("/api/folder", params={"url": ROOT + "/Meetings"}).json()
    self.assertEqual(r["folder"]["status"], "FOUND")
    self.assertEqual(len(r["children"]), 1)

  def test_me_custom_title(self):
    with mock.patch.dict(os.environ, {"APP_TITLE": "Contoso link finder"}):
      self.assertEqual(self.client.get("/api/me").json()["app_title"], "Contoso link finder")

  def test_me(self):
    r = self.client.get("/api/me").json()
    self.assertEqual(r["email"], "tester@contoso.com")

  def test_backend_error_is_502_without_details(self):
    from app.bq import BigQueryError

    def boom(*a, **k):
      raise BigQueryError("HTTP 403: secret internals")
    old = self.main.repo._query
    self.main.repo._query = boom
    try:
      r = self.client.get("/api/lookup", params={"url": ROOT + "/x.pdf"})
    finally:
      self.main.repo._query = old
    self.assertEqual(r.status_code, 502)
    self.assertNotIn("secret", r.text)


class AuthTest(unittest.TestCase):

  def setUp(self):
    self.env = mock.patch.dict(os.environ, IAP)
    self.env.start()
    self.client, self.main = make_client(IAP)

  def tearDown(self):
    self.env.stop()

  def get(self, headers=None):
    return self.client.get("/api/lookup", params={"url": ROOT + "/last mont.pdf"},
                           headers=headers or {})

  def test_missing_header_401(self):
    self.assertEqual(self.get().status_code, 401)
    self.assertEqual(self.client.get("/").status_code, 401)

  def test_healthz_needs_no_auth(self):
    self.assertEqual(self.client.get("/healthz").status_code, 200)

  def test_forged_token_401(self):
    forged = "eyJhbGciOiJFUzI1NiJ9.eyJlbWFpbCI6ImFAY29udG9zby5jb20ifQ.c2ln"
    with mock.patch("app.auth.id_token.verify_token", side_effect=ValueError("bad sig")):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": forged}).status_code, 401)

  def test_valid_same_domain_200(self):
    claims = {"iss": "https://cloud.google.com/iap", "email": "alan@contoso.com",
              "hd": "contoso.com"}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims) as v:
      r = self.get({"x-goog-iap-jwt-assertion": "tok"})
    self.assertEqual(r.status_code, 200)
    self.assertEqual(v.call_args.kwargs["audience"], IAP["IAP_AUDIENCE"])

  def test_second_allowed_domain_200(self):
    claims = {"iss": "https://cloud.google.com/iap", "email": "bo@demo.contoso.com",
              "hd": "demo.contoso.com"}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": "tok"}).status_code, 200)

  def test_lookalike_domain_403(self):
    claims = {"iss": "https://cloud.google.com/iap", "email": "eve@evil-contoso.com",
              "hd": "evil-contoso.com"}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": "tok"}).status_code, 403)

  def test_hd_email_mismatch_403(self):
    claims = {"iss": "https://cloud.google.com/iap", "email": "eve@gmail.com",
              "hd": "demo.contoso.com"}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": "tok"}).status_code, 403)

  def test_other_domain_403(self):
    claims = {"iss": "https://cloud.google.com/iap", "email": "eve@gmail.com", "hd": ""}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": "tok"}).status_code, 403)

  def test_wrong_issuer_401(self):
    claims = {"iss": "https://accounts.google.com", "email": "a@contoso.com",
              "hd": "contoso.com"}
    with mock.patch("app.auth.id_token.verify_token", return_value=claims):
      self.assertEqual(self.get({"x-goog-iap-jwt-assertion": "tok"}).status_code, 401)


class StartupConfigTest(unittest.TestCase):

  def test_dev_mode_refused_on_cloud_run(self):
    with mock.patch.dict(os.environ, {**DEV, "K_SERVICE": "gws-link-converter"}):
      from app import auth
      with self.assertRaises(RuntimeError):
        auth.check_config()

  def test_iap_without_audience_refused(self):
    with mock.patch.dict(os.environ, {"AUTH_MODE": "iap", "IAP_AUDIENCE": ""}):
      from app import auth
      with self.assertRaises(RuntimeError):
        auth.check_config()


if __name__ == "__main__":
  unittest.main()
