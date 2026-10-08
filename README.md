# gws-link-converter

Web app for a Google Workspace tenant. Paste a SharePoint or OneDrive link and get the
Google Drive link of the same item, migrated by the Google Workspace **Data Migration**
service. The data comes from the Workspace audit log exported to BigQuery
(`<export project>.<dataset>.activity`, record `data_migration`).

* **Install for a customer:** see [CUSTOMER_GUIDE.md](CUSTOMER_GUIDE.md) - run `./install.sh`.
* All deployment values live in `deploy.env` (written by `install.sh`; template:
  `deploy.env.example`). Nothing customer-specific is hardcoded in the code.

| Script | Purpose |
|---|---|
| `install.sh` | Interactive installer: asks/auto-detects settings, writes `deploy.env`, preflight, deploy, first sync |
| `install.sh preflight` | Read-only checks of `deploy.env` against the project |
| `setup.sh [--config F] [steps]` | Idempotent provisioning steps: `apis iam schema service iap job schedule alerts` (`show` prints resolved settings) |
| `uninstall.sh [--config F] [--yes] [--delete-index]` | Removes what was installed (dry run by default) |
| `package.sh [ref]` | Builds a shareable zip from a git ref into `dist/` |

## How it works

```
Workspace Data Migration ──audit log──▶ BigQuery <export>.activity (near-real-time export)
                                              │  every 5 min: Cloud Scheduler ▶ Cloud Run job <JOB_NAME>
                                              ▼  (nightly CATCHUP_CRON: same job, 72 h catch-up window)
                         BigQuery migration_lookup.migration_events  (insert-only, key event_id)
                                              │
 browser ──IAP (domain)──▶ Cloud Run <SERVICE_NAME> ──▶ index lookup ──miss──▶ live lookup on
                                                                                  activity rows newer
                                                                                  than the watermark
```

* `sql/sync.sql`: idempotent incremental MERGE. It re-reads 2 h before the watermark,
  filters partitions with a 1-day margin (export partitions follow **US Pacific** dates),
  and advances the watermark only inside the committed transaction. The first run is the backfill.
* `app/normalize.py`: canonical key for pasted links. Supported link forms:
  * the plain path: `https://<t>-my.sharepoint.com/personal/<u>/Documents/file.pdf`
  * the browser address bar in OneDrive or SharePoint: `.../my?viewid=…&id=%2Fpersonal%2F…`,
    `.../Forms/AllItems.aspx?id=…`, `onedrive.aspx?id=…`
  * Office "path" links: `/:w:/r/...`
  * viewer links: `/_layouts/15/Doc.aspx?sourcedoc={GUID}` (matched by the SharePoint item GUID)
  * **Not supported:** sharing links with a token (`/:x:/g/…?e=…`, `/:b:/s/…`). Resolving these
    needs the Microsoft Graph API.
* Result statuses: `FOUND`, `MIGRATION_FAILED` (with the reason), `NOT_FOUND`, `UNSUPPORTED`, `INVALID`.
* "Migrated" means `CREATE_FILE` or `CREATE_FOLDER` with status `SUCCEEDED` or
  `SUCCEEDED_WITH_WARNINGS`. The latest successful event wins, and older attempts stay as history.

## BigQuery objects (`<PROJECT_ID>.<INDEX_DATASET>`, default `migration_lookup`)
| Object | Purpose |
|---|---|
| `normalize_uri()` | JS UDF, same rules as `normalize.py` for logged URIs |
| `migration_events` | every file/folder attempt (partitioned by day, clustered by `source_key`, `source_item_guid`) |
| `migration_executions`, `migration_waves` | execution → wave → batch name |
| `sync_state` | watermark plus last run time and row count |
| `uri_mapping_current` | view: the current mapping per source (for reporting and exports) |

## Identities (least privilege)
| Service account | Permissions |
|---|---|
| `<prefix>-app` | `bigquery.jobUser` (project); READER on the export dataset and the index dataset |
| `<prefix>-sync` | `bigquery.jobUser`; READER on the export dataset, WRITER on the index dataset |
| `<prefix>-scheduler` | `run.jobsExecutorWithOverrides` on the sync job |
| `<prefix>-build` | Cloud Build: objectViewer on `run-sources-…` bucket, AR writer, logWriter |
| IAP service agent | `run.invoker` on the service (the only invoker) |

## Runbook
```bash
set -a; source deploy.env; set +a     # load PROJECT_ID, REGION, JOB_NAME, ...
./setup.sh                            # everything (idempotent)
./setup.sh service job                # redeploy app + sync job after code changes
./setup.sh schedule                   # (re)create Cloud Scheduler triggers

# Run a sync now / a full re-scan of the last N hours
gcloud run jobs execute "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" --wait
gcloud run jobs execute "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" \
  --args=-m,tools.sync_job,--overlap-hours,720 --wait

# Full rebuild of the index (safe: MERGE is keyed on event_id)
GCP_PROJECT=$PROJECT_ID BQ_SOURCE_TABLE=$EXPORT_TABLE BQ_DATASET=$INDEX_DATASET \
  python3 -m tools.run_sql sync --overlap-hours 100000

# Recent sync results
gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB_NAME\" AND jsonPayload.event:*" \
  --project="$PROJECT_ID" --freshness=1h --format='value(timestamp,jsonPayload.event,jsonPayload.events_inserted,jsonPayload.error)'

# Index freshness
#   SELECT watermark_usec, last_run_at, last_inserted FROM <INDEX_DATASET>.sync_state
```

### Local development
```bash
python3 -m venv --without-pip .venv && curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -s tests             # unit + API tests
set -a; source deploy.env; set +a; GCP_PROJECT=$PROJECT_ID BQ_SOURCE_TABLE=$EXPORT_TABLE RUN_BQ_TESTS=1 .venv/bin/python -m unittest discover -s tests   # + BigQuery integration (needs gcloud auth)
set -a; source deploy.env; set +a; AUTH_MODE=dev .venv/bin/uvicorn app.main:app --port 8080     # dev mode refuses to start on Cloud Run
```

### Teardown (end of the migration project)
```bash
./uninstall.sh            # list what would be removed
./uninstall.sh --yes      # remove (add --delete-index to drop the index dataset too)
```
The Workspace export dataset is never modified (only the app's read access is removed).
