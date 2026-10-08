-- Schema for the migration link lookup. Placeholders: {project} {ds} {location}
-- Idempotent: safe to run repeatedly (CREATE ... IF NOT EXISTS / OR REPLACE).

CREATE SCHEMA IF NOT EXISTS `{project}.{ds}`
OPTIONS (location = '{location}',
         description = 'SharePoint/OneDrive -> Google Drive link lookup (gws-link-converter)');

-- Same rules as app/normalize.py for already-canonical logged URIs:
-- percent-decode (max 3x), NFC, collapse duplicate slashes in the path,
-- strip trailing slashes, lowercase. Logged URIs are raw paths, so '?' and '#'
-- are NOT stripped here (they can be literal characters in file names).
CREATE OR REPLACE FUNCTION `{project}.{ds}.normalize_uri`(u STRING)
RETURNS STRING LANGUAGE js AS r"""
  if (u === null || u === undefined) return null;
  let s = String(u).trim();
  for (let i = 0; i < 3; i++) {
    let d;
    try { d = decodeURIComponent(s); } catch (e) { break; }
    if (d === s) break;
    s = d;
  }
  s = s.normalize('NFC');
  const m = s.match(/^([a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\/]+)(.*)$/);
  if (m) s = m[1] + m[2].replace(/\/{2,}/g, '/');
  s = s.replace(/\/+$/, '');
  return s.toLowerCase();
""";

-- Every file/folder migration attempt (any status). Insert-only, keyed on event_id.
CREATE TABLE IF NOT EXISTS `{project}.{ds}.migration_events` (
  event_id          STRING NOT NULL,
  event_time        TIMESTAMP NOT NULL,
  event_name        STRING,      -- CREATE_FILE | CREATE_FOLDER
  item_type         STRING,      -- FILE | FOLDER
  event_status      STRING,      -- SUCCEEDED | SUCCEEDED_WITH_WARNINGS | FAILED | SKIPPED
  source_uri        STRING,
  source_key        STRING,      -- normalize_uri(source_uri)
  source_item_guid  STRING,      -- SharePoint item GUID from source_identifier
  source_identifier STRING,
  source_type       STRING,
  source_root       STRING,
  target_uri        STRING,
  target_id         STRING,      -- Drive file/folder id
  target_type       STRING,
  warning_code      STRING,
  warning_title     STRING,
  migrated_by       STRING,
  migration_type    STRING,
  execution_id      STRING,
  synced_at         TIMESTAMP
)
PARTITION BY DATE(event_time)
CLUSTER BY source_key, source_item_guid
OPTIONS (description = 'Data Migration CREATE_FILE/CREATE_FOLDER events copied from the Workspace export');

-- execution_id -> wave (from START_MIGRATION events).
CREATE TABLE IF NOT EXISTS `{project}.{ds}.migration_executions` (
  execution_id   STRING NOT NULL,
  wave_id        STRING,
  execution_type STRING,         -- Full | Delta
  started_at     TIMESTAMP,
  synced_at      TIMESTAMP
);

-- wave_id -> batch name (from START_MIGRATION_SETUP events), e.g. "OneDrive Migration".
CREATE TABLE IF NOT EXISTS `{project}.{ds}.migration_waves` (
  wave_id    STRING NOT NULL,
  batch_name STRING,
  created_at TIMESTAMP,
  synced_at  TIMESTAMP
);

-- Incremental sync bookkeeping (one row per source).
CREATE TABLE IF NOT EXISTS `{project}.{ds}.sync_state` (
  name           STRING NOT NULL,
  watermark_usec INT64,
  last_run_at    TIMESTAMP,
  last_inserted  INT64
);

-- What the app reads: the latest successful mapping per source item, plus batch info.
CREATE OR REPLACE VIEW `{project}.{ds}.uri_mapping_current` AS
SELECT
  e.source_key, e.source_uri, e.source_item_guid, e.item_type,
  e.target_uri, e.target_id, e.target_type,
  e.event_status, e.warning_code, e.warning_title,
  e.event_time AS migrated_at, e.migrated_by, e.execution_id,
  x.wave_id, x.execution_type, w.batch_name, e.event_id
FROM `{project}.{ds}.migration_events` e
LEFT JOIN `{project}.{ds}.migration_executions` x USING (execution_id)
LEFT JOIN `{project}.{ds}.migration_waves` w USING (wave_id)
WHERE e.event_status IN ('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')
  AND IFNULL(e.target_uri, '') != ''
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.source_key ORDER BY e.event_time DESC, e.event_id DESC) = 1;
