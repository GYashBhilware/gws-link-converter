"""Unit tests for app/normalize.py (stdlib unittest; run: python3 -m unittest)."""

import json
import pathlib
import unittest

from app.normalize import Kind, normalize

HOST = "https://contoso-my.sharepoint.com"
ROOT = HOST + "/personal/alan_contoso_com/Documents"
VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "vectors.json").read_text()
)


class RealLoggedUrisTest(unittest.TestCase):
  """Every source_uri seen in the export must normalize to itself (lowercased)."""

  def test_logged_uris_are_fixed_points(self):
    for uri in VECTORS["logged_source_uris"]:
      with self.subTest(uri=uri):
        n = normalize(uri)
        self.assertEqual(n.kind, Kind.PATH)
        self.assertEqual(n.key, uri.rstrip("/").lower())

  def test_idempotent(self):
    for uri in VECTORS["logged_source_uris"]:
      with self.subTest(uri=uri):
        k = normalize(uri).key
        self.assertEqual(normalize(k).key, k)


class VariantsTest(unittest.TestCase):
  """Browser / Office variants of the same file map to the logged key."""

  expected = (ROOT + "/last mont.pdf").lower()

  def assertKey(self, raw):
    n = normalize(raw)
    self.assertEqual(n.kind, Kind.PATH, n.reason)
    self.assertEqual(n.key, self.expected)

  def test_percent_encoded(self):
    self.assertKey(ROOT + "/last%20mont.pdf")

  def test_double_encoded(self):
    self.assertKey(ROOT + "/last%2520mont.pdf")

  def test_query_fragment_trailing_slash_whitespace_case(self):
    self.assertKey("  " + ROOT.upper().replace("HTTPS", "https") + "/LAST MONT.PDF/?web=1#x ")

  def test_no_scheme(self):
    self.assertKey(ROOT.removeprefix("https://") + "/last mont.pdf")

  def test_office_direct_path_link(self):
    self.assertKey(
        HOST + "/:b:/r/personal/alan_contoso_com/Documents/last%20mont.pdf?csf=1&web=1"
    )

  def test_onedrive_view_id_param(self):
    self.assertKey(
        HOST + "/my?id=%2Fpersonal%2Falan_contoso_com%2FDocuments%2Flast%20mont.pdf"
        .replace("/my?", "/personal/alan_contoso_com/_layouts/15/onedrive.aspx?")
    )

  def test_library_view_id_param(self):
    raw = (
        ROOT + "/Forms/AllItems.aspx?id=%2Fpersonal%2Falan_contoso_com"
        "%2FDocuments%2Flast%20mont.pdf&parent=%2Fpersonal"
    )
    self.assertKey(raw)


class FolderTest(unittest.TestCase):

  def test_modern_onedrive_my_view_exact_user_url(self):
    # Regression: modern OneDrive address-bar URL (/my?viewid=...&id=...).
    raw = ("https://contoso-my.sharepoint.com/my?viewid=0a1b2c3d%2Da6a5%2D458f%2Da8e2"
           "%2D39989427a6ff&id=%2Fpersonal%2Falan%5Fcontoso%5Fcom%2FDocuments%2F"
           "Quarterly%20Report%20Final%2Epdf"
           "&parent=%2Fpersonal%2Falan%5Fcontoso%5Fcom%2FDocuments")
    n = normalize(raw)
    self.assertEqual(n.kind, Kind.PATH)
    self.assertEqual(n.key, (ROOT + "/Quarterly Report Final.pdf").lower())

  def test_modern_onedrive_my_view_folder(self):
    raw = HOST + "/my?id=%2Fpersonal%2Falan%5Fcontoso%5Fcom%2FDocuments%2FMeetings"
    self.assertEqual(normalize(raw).key, (ROOT + "/Meetings").lower())

  def test_id_param_not_a_path_is_ignored(self):
    # An id that isn't a server-relative path must not be treated as one.
    n = normalize(ROOT + "/report.pdf?id=12345")
    self.assertEqual(n.key, (ROOT + "/report.pdf").lower())

  def test_library_root_view(self):
    n = normalize(ROOT + "/Forms/AllItems.aspx")
    self.assertEqual(n.key, ROOT.lower())

  def test_folder_with_trailing_slash(self):
    self.assertEqual(normalize(ROOT + "/Meetings/").key, (ROOT + "/Meetings").lower())


class SpecialKindsTest(unittest.TestCase):

  def test_sourcedoc_guid(self):
    n = normalize(
        HOST + "/personal/alan_contoso_com/_layouts/15/Doc.aspx?"
        "sourcedoc=%7B11111111-2222-4333-8444-555555555555%7D&file=x.docx&action=default"
    )
    self.assertEqual(n.kind, Kind.SOURCEDOC)
    self.assertEqual(n.guid, "11111111-2222-4333-8444-555555555555")

  def test_sharing_links_unsupported(self):
    for raw in (
        HOST + "/:b:/g/personal/alan_contoso_com/EQ8sw4ZWeT1DnEfwlpaF6Hk?e=AbC123",
        HOST + "/:x:/s/site/EabcDEF?e=1",
        HOST + "/:f:/p/alan/EsXyz",
    ):
      with self.subTest(raw=raw):
        n = normalize(raw)
        self.assertEqual(n.kind, Kind.UNSUPPORTED)
        self.assertIn("sharing link", n.reason)

  def test_invalid(self):
    for raw in ("", "   ", "https://drive.google.com/file/d/x", "not a url",
                "ftp://contoso-my.sharepoint.com/x",
                "https://evil.com/contoso.sharepoint.com/x",
                "https://sharepoint.com.evil.com/x"):
      with self.subTest(raw=raw):
        self.assertEqual(normalize(raw).kind, Kind.INVALID)


if __name__ == "__main__":
  unittest.main()
