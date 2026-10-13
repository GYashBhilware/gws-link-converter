"""CSV validation for tools/import_share_links.py (no BigQuery)."""

import unittest

from tests.test_sharelink import USER_LINK, USER_TOKEN
from tools.import_share_links import parse_rows


class ParseRowsTest(unittest.TestCase):

  def test_valid_and_rejected_rows(self):
    rows = [
        {"url": USER_LINK + "?e=abc", "item_guid": "{1A2B3C4D-5E6F-4A7B-8C9D-0E1F2A3B4C5D}"},
        {"url": "https://contoso-my.sharepoint.com/personal/a/Documents/x.pdf",
         "item_guid": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"},
        {"url": USER_LINK, "item_guid": "not-a-guid"},
    ]
    good, errors = parse_rows(rows)
    self.assertEqual(good, [("contoso-my.sharepoint.com/" + USER_TOKEN,
                             "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                             "yash_contoso_com", "x", USER_LINK + "?e=abc")])
    self.assertEqual([e[0] for e in errors], [3, 4])


if __name__ == "__main__":
  unittest.main()
