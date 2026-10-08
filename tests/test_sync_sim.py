"""Simulation test for sql/sync.sql continuous-update guarantees (B4).

Uses an isolated dataset `migration_lookup_simtest` with a fake export table
(same column names / Pacific-date ingestion partitions as the real export).
The real `100.activity` table is never written. Dataset is dropped afterwards.
Run: RUN_BQ_TESTS=1 python3 -m unittest -v tests.test_sync_sim
"""

import os
import time
import unittest

from app import bq
from tools.run_sql import render

DS = "migration_lookup_simtest"
SRC = f"{bq.PROJECT}.{DS}.fake_activity"
HOST = "https://t-my.sharepoint.com/personal/u/Documents/"
H = 3600 * 1_000_000


def run_sync(inject_failure=False):
  sql = render("sync", DS, SRC, 2)
  if inject_failure:
    sql = sql.replace("COMMIT TRANSACTION;",
                      "SELECT ERROR('injected failure');\nCOMMIT TRANSACTION;")
  rows, _ = bq.query(sql, dry_run_first=False)
  return rows[0] if rows else None


def insert_events(evts):
  """evts: list of (event_id, t_usec, event_name, status, src_path, target, exec_id)."""
  values = ",\n".join(
      "(TIMESTAMP(DATE(TIMESTAMP_MICROS({t}), 'America/Los_Angeles')), {t}, "
      "'data_migration', '{etype}', '{name}', '{eid}', 'admin@x.com', "
      "STRUCT('{status}' AS event_status), "
      "STRUCT('SharePoint Online Enterprise Migration' AS migration_type, "
      "'Sharepoint File' AS source_type, 'Google Drive File' AS target_type, "
      "'documentLibraries/l/files/00000000-0000-0000-0000-{eid12}' AS source_identifier, "
      "'files/{tgt}' AS target_identifier, '{src}' AS source_uri, '{tu}' AS target_uri, "
      "'{ex}' AS execution_id, 'root' AS source_name, CAST(NULL AS STRING) AS migration_error_code, "
      "CAST(NULL AS STRING) AS migration_error_title))".format(
          t=t, etype="MIGRATION_SETUP" if name.startswith("START") else "MIGRATION",
          name=name, eid=eid, eid12=eid.rjust(12, "0")[-12:], status=status,
          src=src, tgt=tgt or "", tu=tgt or "", ex=ex)
      for eid, t, name, status, src, tgt, ex in evts)
  bq.query(
      f"INSERT INTO `{SRC}` (_PARTITIONTIME, time_usec, record_type, event_type, "
      "event_name, event_id, email, status, data_migration) VALUES\n" + values,
      dry_run_first=False)


def mapping():
  rows, _ = bq.query(
      f"SELECT source_key, target_uri, batch_name FROM `{bq.PROJECT}.{DS}.uri_mapping_current`")
  return {r["source_key"]: r for r in rows}


def watermark():
  rows, _ = bq.query(
      f"SELECT watermark_usec FROM `{bq.PROJECT}.{DS}.sync_state` WHERE name='activity'")
  return rows[0]["watermark_usec"] if rows else None


@unittest.skipUnless(os.environ.get("RUN_BQ_TESTS") == "1", "set RUN_BQ_TESTS=1")
class SyncSimulationTest(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    bq.query(f"DROP SCHEMA IF EXISTS `{bq.PROJECT}.{DS}` CASCADE", dry_run_first=False)
    bq.query(render("schema", DS, SRC, 2), dry_run_first=False)
    bq.query(f"""
      CREATE TABLE `{SRC}` (
        time_usec INT64, record_type STRING, event_type STRING, event_name STRING,
        event_id STRING, email STRING,
        status STRUCT<event_status STRING>,
        data_migration STRUCT<migration_type STRING, source_type STRING,
          target_type STRING, source_identifier STRING, target_identifier STRING,
          source_uri STRING, target_uri STRING, execution_id STRING,
          source_name STRING, migration_error_code STRING, migration_error_title STRING>)
      PARTITION BY _PARTITIONDATE""", dry_run_first=False)

  @classmethod
  def tearDownClass(cls):
    if os.environ.get("KEEP_SIMTEST") != "1":
      bq.query(f"DROP SCHEMA IF EXISTS `{bq.PROJECT}.{DS}` CASCADE", dry_run_first=False)

  def test_continuous_update_scenarios(self):
    t0 = int(time.time() * 1e6) - 20 * H
    a, b, c = HOST + "a file.pdf", HOST + "b.pdf", HOST + "c.pdf"

    # 1. Backfill: setup events + a failed attempt then a success for A.
    insert_events([
        ("s1", t0, "START_MIGRATION_SETUP", "SUCCEEDED", "", "", ""),
        ("s2", t0 + 1, "START_MIGRATION", "SUCCEEDED", "", "", "ex1"),
        ("e1", t0 + H, "CREATE_FILE", "FAILED", a, None, "ex1"),
        ("e2", t0 + 2 * H, "CREATE_FILE", "SUCCEEDED", a, "T1", "ex1"),
    ])
    # setup rows need WaveId text in target_uri; patch them in the fake table.
    bq.query(f"""UPDATE `{SRC}` SET data_migration.target_uri = CASE event_id
                   WHEN 's1' THEN 'WaveId: w1' WHEN 's2' THEN 'WaveId: w1, ExecutionType: Full' END,
                 data_migration.target_identifier = IF(event_id='s1', 'Sim Batch', 'ExecutionId: ex1')
               WHERE event_id IN ('s1','s2')""", dry_run_first=False)
    r = run_sync()
    self.assertEqual(r["events_inserted"], 2)
    m = mapping()
    self.assertEqual(m[a.lower()]["target_uri"], "T1")
    self.assertEqual(m[a.lower()]["batch_name"], "Sim Batch")

    # 2. Idempotent re-run.
    self.assertEqual(run_sync()["events_inserted"], 0)

    # 3. Re-migration: newer success for A wins.
    insert_events([("e3", t0 + 5 * H, "CREATE_FILE", "SUCCEEDED", a, "T2", "ex1")])
    self.assertEqual(run_sync()["events_inserted"], 1)
    self.assertEqual(mapping()[a.lower()]["target_uri"], "T2")
    hist, _ = bq.query(f"SELECT COUNT(*) n FROM `{bq.PROJECT}.{DS}.migration_events` "
                       "WHERE source_key = @k", {"k": a.lower()})
    self.assertEqual(hist[0]["n"], 3)  # failed + T1 + T2 kept as history

    # 4. Late-arriving row: timestamp 1h BEFORE the watermark, arrives now.
    wm = watermark()
    insert_events([("e4", wm - H, "CREATE_FILE", "SUCCEEDED", b, "TB", "ex1")])
    self.assertEqual(run_sync()["events_inserted"], 1)
    self.assertEqual(mapping()[b.lower()]["target_uri"], "TB")

    # 5. Failed run: nothing committed, watermark unchanged; next run recovers.
    wm_before = watermark()
    insert_events([("e5", wm_before + H, "CREATE_FILE", "SUCCEEDED", c, "TC", "ex1")])
    with self.assertRaises(bq.BigQueryError):
      run_sync(inject_failure=True)
    self.assertEqual(watermark(), wm_before)
    self.assertNotIn(c.lower(), mapping())
    self.assertEqual(run_sync()["events_inserted"], 1)
    self.assertEqual(mapping()[c.lower()]["target_uri"], "TC")
    self.assertGreater(watermark(), wm_before)

    # 6. Browser-encoded lookup key matches (spaces in name).
    self.assertIn((HOST + "a file.pdf").lower(), mapping())


if __name__ == "__main__":
  unittest.main()
