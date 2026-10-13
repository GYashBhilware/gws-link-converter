"""Sharing-link (modern attachment) lookups through Repository, fake BigQuery."""

import unittest

from app.repository import Repository
from tests.test_repository import FakeBQ, row
from tests.test_sharelink import USER_GUIDS, USER_LINK, USER_TOKEN

KEY = "contoso-my.sharepoint.com/" + USER_TOKEN
OTHER_GUID = "0f0f0f0f-1111-4222-8333-444444444444"


class ShareLookupTest(unittest.TestCase):

  def test_user_link_found_via_decoded_guid(self):
    fake = FakeBQ(index=[row("/Budget.xlsx", guid=USER_GUIDS[0])], shares={})
    [r] = Repository(fake).lookup([USER_LINK])
    self.assertEqual(r.status, "FOUND")
    self.assertEqual(r.match, "share-decoded")
    self.assertEqual(r.share_owner, "yash_contoso_com")
    self.assertTrue(r.target_uri.startswith("https://drive.google.com/"))
    # Both candidate byte orders were offered to the index query.
    index_params = [p for s, p in fake.calls if p and "guids" in p][0]
    self.assertTrue(set(USER_GUIDS) <= set(index_params["guids"]))

  def test_with_email_query_string(self):
    fake = FakeBQ(index=[row("/Budget.xlsx", guid=USER_GUIDS[0])], shares={})
    [r] = Repository(fake).lookup([USER_LINK + "?e=Xy12Ab"])
    self.assertEqual(r.status, "FOUND")

  def test_harvested_mapping_wins_over_decoded(self):
    fake = FakeBQ(index=[row("/Decoded.xlsx", guid=USER_GUIDS[0],
                             target="https://drive.google.com/file/d/DECODED/view"),
                         row("/Harvested.xlsx", guid=OTHER_GUID,
                             target="https://drive.google.com/file/d/HARVESTED/view")],
                  shares={KEY: OTHER_GUID})
    [r] = Repository(fake).lookup([USER_LINK])
    self.assertEqual((r.status, r.match), ("FOUND", "share-harvested"))
    self.assertIn("HARVESTED", r.target_uri)

  def test_live_fallback_for_share(self):
    fake = FakeBQ(live=[row("/New.xlsx", guid=USER_GUIDS[0])], shares={})
    [r] = Repository(fake).lookup([USER_LINK])
    self.assertEqual((r.status, r.via, r.match), ("FOUND", "live", "share-decoded"))

  def test_not_migrated_explains_owner_and_type(self):
    [r] = Repository(FakeBQ(shares={})).lookup([USER_LINK])
    self.assertEqual(r.status, "NOT_FOUND")
    self.assertIn("Excel workbook", r.message)
    self.assertIn("yash_contoso_com's OneDrive", r.message)

  def test_missing_share_table_is_tolerated(self):
    fake = FakeBQ(index=[row("/Budget.xlsx", guid=USER_GUIDS[0])], shares=None)
    [r] = Repository(fake).lookup([USER_LINK])
    self.assertEqual(r.status, "FOUND")

  def test_undecodable_token_without_harvest(self):
    link = ("https://contoso-my.sharepoint.com/:b:/g/personal/alan_contoso_com/"
            "EQ8sw4ZWeT1DnEfwlpaF6Hk?e=AbC123")
    [r] = Repository(FakeBQ(shares={})).lookup([link])
    self.assertEqual(r.status, "NOT_FOUND")
    self.assertIn("can't be decoded offline", r.message)

  def test_undecodable_token_resolved_by_harvest(self):
    link = ("https://contoso-my.sharepoint.com/:b:/g/personal/alan_contoso_com/"
            "EQ8sw4ZWeT1DnEfwlpaF6Hk?e=AbC123")
    key = "contoso-my.sharepoint.com/EQ8sw4ZWeT1DnEfwlpaF6Hk"
    fake = FakeBQ(index=[row("/a.pdf", guid=OTHER_GUID)], shares={key: OTHER_GUID.upper()})
    [r] = Repository(fake).lookup([link])
    self.assertEqual((r.status, r.match), ("FOUND", "share-harvested"))

  def test_bulk_mix_of_path_and_share_links(self):
    fake = FakeBQ(index=[row("/Budget.xlsx", guid=USER_GUIDS[0]), row("/last mont.pdf")],
                  shares={})
    from tests.test_repository import ROOT
    res = Repository(fake).lookup([ROOT + "/last mont.pdf", USER_LINK, "not a link"])
    self.assertEqual([r.status for r in res], ["FOUND", "FOUND", "INVALID"])
    self.assertEqual([r.match for r in res][:2], ["path", "share-decoded"])

  def test_no_share_query_when_no_share_links(self):
    from tests.test_repository import ROOT
    fake = FakeBQ(index=[row("/last mont.pdf")])
    Repository(fake).lookup([ROOT + "/last mont.pdf"])
    from app import repository as repo_mod
    self.assertFalse(any(s is repo_mod._SHARE_SQL for s, _ in fake.calls))


if __name__ == "__main__":
  unittest.main()
