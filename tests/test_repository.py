"""Unit tests for app/repository.py with a fake BigQuery (no network)."""

import unittest

from app import repository as repo_mod
from app.repository import Repository

ROOT = "https://contoso-my.sharepoint.com/personal/alan_contoso_com/Documents"
K = lambda p: (ROOT + p).lower()  # noqa: E731


def row(path, status="SUCCEEDED", target="https://drive.google.com/file/d/T/view",
        guid=None, item="FILE", warning=None):
  return {"source_key": K(path), "source_uri": ROOT + path, "source_item_guid": guid,
          "item_type": item, "target_uri": target, "event_status": status,
          "warning_code": None, "warning_title": warning, "migrated_at": 1791367271.0,
          "migrated_by": "admin@contoso.com", "execution_type": "Delta",
          "batch_name": "OneDrive Migration", "attempts": 2}


class FakeBQ:

  def __init__(self, index=(), live=(), folder=(), shares=None):
    self.index, self.live, self.folder = list(index), list(live), list(folder)
    self.shares = shares  # None = share_links table does not exist yet
    self.calls = []

  def __call__(self, sql, params=None, **_):
    self.calls.append((sql, params))
    if sql is repo_mod._SHARE_SQL:
      if self.shares is None:
        from app.bq import BigQueryError
        raise BigQueryError('HTTP 404: {"message": "Not found: Table p:migration_lookup.share_links"}')
      return [{"share_key": k, "item_guid": g, "source": "harvest"}
              for k, g in self.shares.items() if k in params["share_keys"]], {}
    if sql is repo_mod._WATERMARK_SQL:
      return [{"watermark_usec": 1791367283448689, "last_run_at": 0.0}], {}
    if sql is repo_mod._FOLDER_SQL:
      return [dict(r) for r in self.folder if r["source_key"].startswith(params["prefix"])], {}
    src = self.index if sql is repo_mod._INDEX_SQL else self.live
    out = [dict(r) for r in src
           if r["source_key"] in params["keys"] or r["source_item_guid"] in params["guids"]]
    return out, {}


class LookupTest(unittest.TestCase):

  def test_found_from_index_with_encoded_input(self):
    fake = FakeBQ(index=[row("/last mont.pdf")])
    [r] = Repository(fake).lookup([ROOT + "/last%20mont.pdf?web=1"])
    self.assertEqual(r.status, "FOUND")
    self.assertEqual(r.via, "index")
    self.assertEqual(r.batch_name, "OneDrive Migration")
    self.assertEqual(len(fake.calls), 1)  # no live fallback needed

  def test_live_fallback_for_not_yet_synced(self):
    fake = FakeBQ(live=[row("/new.pdf")])
    [r] = Repository(fake).lookup([ROOT + "/new.pdf"])
    self.assertEqual((r.status, r.via), ("FOUND", "live"))
    live_sql, live_params = fake.calls[-1]
    self.assertIs(live_sql, repo_mod._LIVE_SQL)
    self.assertEqual(live_params["after_usec"], 1791367283448689)
    self.assertTrue(live_params["part_from"].startswith("2026-10-0"))

  def test_failed_only_reports_migration_failed(self):
    fake = FakeBQ(index=[row("/x.pdf", status="FAILED", target=None, warning="InternalPlatformError")])
    [r] = Repository(fake).lookup([ROOT + "/x.pdf"])
    self.assertEqual(r.status, "MIGRATION_FAILED")
    self.assertIsNone(r.target_uri)
    self.assertIn("InternalPlatformError", r.message)

  def test_live_success_beats_index_failure(self):
    fake = FakeBQ(index=[row("/x.pdf", status="FAILED", target=None)], live=[row("/x.pdf")])
    [r] = Repository(fake).lookup([ROOT + "/x.pdf"])
    self.assertEqual((r.status, r.via), ("FOUND", "live"))

  def test_not_found(self):
    [r] = Repository(FakeBQ()).lookup([ROOT + "/nope.pdf"])
    self.assertEqual(r.status, "NOT_FOUND")

  def test_sourcedoc_by_guid(self):
    g = "11111111-2222-4333-8444-555555555555"
    fake = FakeBQ(index=[row("/Slack.pdf", guid=g)])
    [r] = Repository(fake).lookup([
        "https://contoso-my.sharepoint.com/personal/alan_contoso_com/"
        "_layouts/15/Doc.aspx?sourcedoc=%7B11111111-2222-4333-8444-555555555555%7D"])
    self.assertEqual(r.status, "FOUND")

  def test_unsupported_and_invalid_do_not_query(self):
    fake = FakeBQ()
    res = Repository(fake).lookup([
        "https://contoso-my.sharepoint.com/:b:/g/personal/x/EQabc?e=1",
        "https://drive.google.com/file/d/abc", ""])
    self.assertEqual([r.status for r in res], ["UNSUPPORTED", "INVALID", "INVALID"])
    self.assertEqual(fake.calls, [])

  def test_bulk_preserves_order_and_caps(self):
    fake = FakeBQ(index=[row("/a.pdf"), row("/b.pdf")])
    inputs = [ROOT + "/b.pdf", ROOT + "/zzz.pdf", ROOT + "/a.pdf"]
    res = Repository(fake).lookup(inputs)
    self.assertEqual([r.input for r in res], inputs)
    self.assertEqual([r.status for r in res], ["FOUND", "NOT_FOUND", "FOUND"])
    many = Repository(FakeBQ()).lookup([ROOT + f"/{i}.pdf" for i in range(500)])
    self.assertEqual(len(many), repo_mod.MAX_BULK)

  def test_user_input_is_parameter_not_sql(self):
    evil = ROOT + "/x'; DROP TABLE t; --.pdf"
    fake = FakeBQ()
    Repository(fake).lookup([evil])
    for sql, params in fake.calls:
      self.assertNotIn("DROP TABLE", sql)
    self.assertIn(K("/x'; DROP TABLE t; --.pdf"), fake.calls[0][1]["keys"])


class FolderTest(unittest.TestCase):

  def test_folder_children(self):
    fake = FakeBQ(index=[row("/Meetings", item="FOLDER")],
                  folder=[row("/Meetings/a.pdf"), row("/Meetings2/b.pdf")])
    head, children = Repository(fake).folder(ROOT + "/Meetings/")
    self.assertEqual(head.status, "FOUND")
    self.assertEqual([c["source_key"] for c in children], [K("/Meetings/a.pdf")])


if __name__ == "__main__":
  unittest.main()
