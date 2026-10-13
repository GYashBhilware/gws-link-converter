"""Sharing-link (Outlook modern attachment) parsing and token decoding."""

import unittest

from app import sharelink
from app.normalize import Kind, normalize

# Modern sharing link (Excel, OneDrive), token layout 0x21; synthetic GUID.
USER_LINK = ("https://contoso-my.sharepoint.com/:x:/g/personal/"
             "yash_contoso_com/IQBNPCsab157SoydDh8qO0xdAQIDBAUGBwgJCgsMDQ4PEBE")
USER_TOKEN = "IQBNPCsab157SoydDh8qO0xdAQIDBAUGBwgJCgsMDQ4PEBE"
# GUID embedded at bytes 2..18 of the decoded token (.NET byte order first).
USER_GUIDS = ("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "4d3c2b1a-6f5e-7b4a-8c9d-0e1f2a3b4c5d")


class DecodeTokenTest(unittest.TestCase):

  def test_user_token_layout(self):
    cands, fmt = sharelink.decode_token(USER_TOKEN)
    self.assertEqual(fmt, "0x21/35")
    self.assertEqual(cands, USER_GUIDS)

  def test_unknown_layout_gives_no_candidates(self):
    cands, fmt = sharelink.decode_token("EQ8sw4ZWeT1DnEfwlpaF6Hk")
    self.assertEqual(cands, ())
    self.assertTrue(fmt.startswith("0x"))

  def test_garbage_is_safe(self):
    for tok in ("!!!!", "", "A"):
      with self.subTest(tok=tok):
        cands, _ = sharelink.decode_token(tok)
        self.assertEqual(cands, ())


class ParseTest(unittest.TestCase):

  def test_user_link(self):
    n = normalize(USER_LINK)
    self.assertEqual(n.kind, Kind.SHARE)
    s = n.share
    self.assertEqual(s.owner, "yash_contoso_com")
    self.assertEqual(s.scope, "personal")
    self.assertEqual(s.item_type, "x")
    self.assertEqual(s.item_label, "Excel workbook")
    self.assertEqual(s.token, USER_TOKEN)  # case preserved
    self.assertEqual(s.guid_candidates, USER_GUIDS)

  def test_share_key_ignores_query_type_letter_and_host_case(self):
    variants = (
        USER_LINK,
        USER_LINK + "?e=AbC123",
        USER_LINK + "?e=AbC123&at=9",
        USER_LINK.replace("contoso-my", "CONTOSO-MY"),
        USER_LINK.replace("/:x:/g/", "/:u:/g/"),
        " <" + USER_LINK + "> ",  # pasted from an email with angle brackets
    )
    keys = {normalize(v).share.share_key for v in variants}
    self.assertEqual(keys, {"contoso-my.sharepoint.com/" + USER_TOKEN})

  def test_token_case_matters(self):
    a = normalize(USER_LINK).share.share_key
    b = normalize(USER_LINK.replace(USER_TOKEN, USER_TOKEN.swapcase())).share.share_key
    self.assertNotEqual(a, b)

  def test_site_and_teams_scopes(self):
    s = normalize("https://contoso.sharepoint.com/:w:/s/Finance/" + USER_TOKEN + "?e=x").share
    self.assertEqual((s.scope, s.owner, s.item_type), ("sites", "Finance", "w"))
    t = normalize("https://contoso.sharepoint.com/:b:/t/Ops/" + USER_TOKEN).share
    self.assertEqual(t.scope, "teams")

  def test_direct_r_links_stay_path_links(self):
    n = normalize("https://contoso-my.sharepoint.com/:x:/r/personal/a_b_com/Documents/x.xlsx?d=w1")
    self.assertEqual(n.kind, Kind.PATH)


if __name__ == "__main__":
  unittest.main()
