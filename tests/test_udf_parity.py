"""Integration test: SQL UDF normalize_uri must agree with app/normalize.py.

Runs against BigQuery (needs gcloud auth). Skipped if RUN_BQ_TESTS != 1.
"""

import json
import os
import pathlib
import unittest
import urllib.parse

from app import bq
from app.normalize import normalize

DS = os.environ.get("BQ_DATASET", "migration_lookup")
VECTORS = json.loads((pathlib.Path(__file__).parent / "vectors.json").read_text())


def _variants(uri):
  """Logged raw URI plus browser-style encodings of it."""
  scheme_host, _, path = uri.partition(".com")
  enc = scheme_host + ".com" + urllib.parse.quote(path)
  return [uri, enc, enc + "/", uri.upper().replace("HTTPS://", "https://")]


@unittest.skipUnless(os.environ.get("RUN_BQ_TESTS") == "1", "set RUN_BQ_TESTS=1")
class UdfParityTest(unittest.TestCase):

  def test_udf_matches_python(self):
    inputs = [v for u in VECTORS["logged_source_uris"] for v in _variants(u)]
    inputs += ["https://x-my.sharepoint.com/personal/a//b/Ünïcode%20ﬁle.pdf"]
    rows, _ = bq.query(
        f"SELECT u, `{bq.PROJECT}.{DS}.normalize_uri`(u) AS k "
        "FROM UNNEST(@inputs) AS u WITH OFFSET o ORDER BY o",
        {"inputs": inputs})
    self.assertEqual(len(rows), len(inputs))
    for r in rows:
      with self.subTest(u=r["u"]):
        self.assertEqual(r["k"], normalize(r["u"]).key)


if __name__ == "__main__":
  unittest.main()
