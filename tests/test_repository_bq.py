"""Integration tests for app/repository.py against real BigQuery (read-only).

Run: RUN_BQ_TESTS=1 python3 -m unittest -v tests.test_repository_bq
"""

import os
import unittest

from app import bq
from app import repository as repo_mod
from app.repository import Repository

ROOT = "https://contoso-my.sharepoint.com/personal/alan_contoso_com/Documents"
LAST_MONT_TARGET = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=drive_web"


@unittest.skipUnless(os.environ.get("RUN_BQ_TESTS") == "1", "set RUN_BQ_TESTS=1")
class RepositoryBQTest(unittest.TestCase):

  def test_index_lookup_known_pair_browser_encoded(self):
    [r] = Repository().lookup([ROOT + "/last%20mont.pdf"])
    self.assertEqual((r.status, r.via, r.target_uri), ("FOUND", "index", LAST_MONT_TARGET))
    self.assertEqual(r.batch_name, "OneDrive Migration")
    self.assertEqual(r.attempts, 2)  # Full (failed) + Delta (succeeded)

  def test_live_fallback_finds_unsynced_event(self):
    """Pretend the sync stopped before the Delta run: live path must still find it."""
    repo = Repository()
    delta_start_usec = 1791367000000000  # 2026-10-07 ~09:56 UTC, before the Delta events
    repo.watermark = lambda: {"watermark_usec": delta_start_usec, "last_run_at": None}
    rows = repo._fetch({(ROOT + "/last mont.pdf").lower()}, set(), live=True)
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]["target_uri"], LAST_MONT_TARGET)

  def test_live_query_scans_only_recent_partitions(self):
    wm = Repository().watermark()["watermark_usec"]
    import datetime as dt
    day = dt.datetime.fromtimestamp(wm / 1e6, dt.timezone.utc).date() - dt.timedelta(days=1)
    params = {"keys": ["x"], "guids": ["x"], "after_usec": wm,
              "part_from": day.isoformat() + " 00:00:00+00"}
    recent = bq._request("POST", bq._API + "/queries", {
        "query": repo_mod._LIVE_SQL, "useLegacySql": False, "dryRun": True,
        "location": bq.LOCATION, "parameterMode": "NAMED",
        "queryParameters": [bq._param(k, v) for k, v in params.items()]})
    params["part_from"] = "2000-01-01 00:00:00+00"
    full = bq._request("POST", bq._API + "/queries", {
        "query": repo_mod._LIVE_SQL, "useLegacySql": False, "dryRun": True,
        "location": bq.LOCATION, "parameterMode": "NAMED",
        "queryParameters": [bq._param(k, v) for k, v in params.items()]})
    rb, fb = int(recent["totalBytesProcessed"]), int(full["totalBytesProcessed"])
    print(f"live dry-run bytes: recent={rb} full={fb}")
    self.assertLess(rb, fb)

  def test_folder_listing(self):
    head, children = Repository().folder(ROOT)
    self.assertEqual(head.status, "FOUND")
    self.assertEqual(len(children), 11)  # everything under Documents/

  def test_failed_only_site_root(self):
    [r] = Repository().lookup(["https://contoso-my.sharepoint.com/"])
    self.assertEqual(r.status, "MIGRATION_FAILED")


if __name__ == "__main__":
  unittest.main()
