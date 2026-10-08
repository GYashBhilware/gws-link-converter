-- Incremental, idempotent sync from the Workspace export into {ds}.
-- Placeholders: {project} {ds} {src} {overlap_usec}
-- * First run (no watermark) = full backfill.
-- * Each run re-reads `overlap_usec` before the watermark to catch late rows.
-- * Partitions follow US Pacific dates, so the partition filter keeps a 1-day margin.
-- * MERGEs are insert-if-absent on natural keys, so re-reading never duplicates.
-- * Watermark advances only inside the same committed transaction.

DECLARE wm INT64 DEFAULT (
  SELECT watermark_usec FROM `{project}.{ds}.sync_state` WHERE name = 'activity');
DECLARE lo INT64 DEFAULT GREATEST(IFNULL(wm, 0) - {overlap_usec}, 0);
DECLARE lo_part TIMESTAMP DEFAULT
  TIMESTAMP_SUB(TIMESTAMP_TRUNC(TIMESTAMP_MICROS(lo), DAY), INTERVAL 1 DAY);
DECLARE new_wm INT64;
DECLARE inserted INT64 DEFAULT 0;

CREATE TEMP TABLE src_window AS
SELECT *
FROM `{src}`
WHERE _PARTITIONTIME >= lo_part
  AND time_usec >= lo
  AND record_type = 'data_migration';

SET new_wm = (SELECT MAX(time_usec) FROM src_window);

BEGIN TRANSACTION;

MERGE `{project}.{ds}.migration_events` T
USING (
  SELECT
    event_id,
    TIMESTAMP_MICROS(time_usec) AS event_time,
    event_name,
    IF(event_name = 'CREATE_FOLDER', 'FOLDER', 'FILE') AS item_type,
    status.event_status AS event_status,
    data_migration.source_uri AS source_uri,
    `{project}.{ds}.normalize_uri`(data_migration.source_uri) AS source_key,
    LOWER(REGEXP_EXTRACT(data_migration.source_identifier,
          r'/(?:files|folders)/([0-9a-fA-F-]{36})')) AS source_item_guid,
    data_migration.source_identifier AS source_identifier,
    data_migration.source_type AS source_type,
    data_migration.source_name AS source_root,
    NULLIF(data_migration.target_uri, '') AS target_uri,
    REGEXP_EXTRACT(data_migration.target_identifier, r'^(?:files|folders)/(.+)$') AS target_id,
    NULLIF(data_migration.target_type, '') AS target_type,
    NULLIF(data_migration.migration_error_code, '') AS warning_code,
    NULLIF(data_migration.migration_error_title, '') AS warning_title,
    email AS migrated_by,
    data_migration.migration_type AS migration_type,
    data_migration.execution_id AS execution_id
  FROM src_window
  WHERE event_type = 'MIGRATION'
    AND event_name IN ('CREATE_FILE', 'CREATE_FOLDER')
    AND data_migration.source_type LIKE 'Sharepoint%'
    AND IFNULL(data_migration.source_uri, '') != ''
    AND event_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY time_usec) = 1
) S
ON T.event_id = S.event_id
WHEN NOT MATCHED THEN INSERT (
  event_id, event_time, event_name, item_type, event_status, source_uri, source_key,
  source_item_guid, source_identifier, source_type, source_root, target_uri, target_id,
  target_type, warning_code, warning_title, migrated_by, migration_type, execution_id,
  synced_at)
VALUES (
  S.event_id, S.event_time, S.event_name, S.item_type, S.event_status, S.source_uri,
  S.source_key, S.source_item_guid, S.source_identifier, S.source_type, S.source_root,
  S.target_uri, S.target_id, S.target_type, S.warning_code, S.warning_title,
  S.migrated_by, S.migration_type, S.execution_id, CURRENT_TIMESTAMP());

SET inserted = @@row_count;

MERGE `{project}.{ds}.migration_executions` T
USING (
  SELECT data_migration.execution_id AS execution_id,
         REGEXP_EXTRACT(data_migration.target_uri, r'WaveId: ([^,\s]+)') AS wave_id,
         REGEXP_EXTRACT(data_migration.target_uri, r'ExecutionType: ([^,\s]+)') AS execution_type,
         TIMESTAMP_MICROS(time_usec) AS started_at
  FROM src_window
  WHERE event_name = 'START_MIGRATION' AND data_migration.execution_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY data_migration.execution_id ORDER BY time_usec) = 1
) S
ON T.execution_id = S.execution_id
WHEN NOT MATCHED THEN INSERT (execution_id, wave_id, execution_type, started_at, synced_at)
VALUES (S.execution_id, S.wave_id, S.execution_type, S.started_at, CURRENT_TIMESTAMP());

MERGE `{project}.{ds}.migration_waves` T
USING (
  SELECT REGEXP_EXTRACT(data_migration.target_uri, r'WaveId: ([^,\s]+)') AS wave_id,
         data_migration.target_identifier AS batch_name,
         TIMESTAMP_MICROS(time_usec) AS created_at
  FROM src_window
  WHERE event_name = 'START_MIGRATION_SETUP'
    AND REGEXP_CONTAINS(IFNULL(data_migration.target_uri, ''), r'WaveId: ')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY wave_id ORDER BY time_usec DESC) = 1
) S
ON T.wave_id = S.wave_id
WHEN MATCHED AND IFNULL(T.batch_name, '') != IFNULL(S.batch_name, '') THEN
  UPDATE SET batch_name = S.batch_name, synced_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (wave_id, batch_name, created_at, synced_at)
VALUES (S.wave_id, S.batch_name, S.created_at, CURRENT_TIMESTAMP());

MERGE `{project}.{ds}.sync_state` T
USING (SELECT 'activity' AS name) S
ON T.name = S.name
WHEN MATCHED THEN UPDATE SET
  watermark_usec = GREATEST(IFNULL(T.watermark_usec, 0), IFNULL(new_wm, 0)),
  last_run_at = CURRENT_TIMESTAMP(), last_inserted = inserted
WHEN NOT MATCHED THEN INSERT (name, watermark_usec, last_run_at, last_inserted)
VALUES ('activity', new_wm, CURRENT_TIMESTAMP(), inserted);

COMMIT TRANSACTION;

SELECT wm AS old_watermark_usec, new_wm AS window_max_usec, lo AS window_start_usec,
       inserted AS events_inserted,
       (SELECT COUNT(*) FROM src_window) AS window_rows;
