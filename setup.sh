#!/usr/bin/env bash
# Idempotent provisioning + deploy for LinkFinder (SharePoint/OneDrive -> Drive links).
# All customer-specific values come from a config file (default ./deploy.env),
# normally created by ./install.sh. Safe to re-run.
#
#   ./setup.sh [--config FILE] [step ...]
#   steps: apis iam schema service iap job schedule alerts   (default: all, in order)
set -euo pipefail
cd "$(dirname "$0")"

CONFIG=deploy.env
if [[ "${1:-}" == "--config" ]]; then CONFIG=$2; shift 2; fi
[[ -f "$CONFIG" ]] || { echo "Config $CONFIG not found. Run ./install.sh first." >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONFIG"

need() { [[ -n "${!1:-}" ]] || { echo "$CONFIG: $1 is required" >&2; exit 1; }; }
for v in PROJECT_ID REGION SERVICE_NAME EXPORT_TABLE ALLOWED_DOMAINS; do need "$v"; done

# ---- derived settings (each can be overridden in the config file) ----------------
PROJECT=$PROJECT_ID
SERVICE=$SERVICE_NAME
NAME_PREFIX=${NAME_PREFIX:-link-converter}
JOB=${JOB_NAME:-${NAME_PREFIX}-sync}
DATASET=${INDEX_DATASET:-migration_lookup}
BQ_LOCATION=${BQ_LOCATION:-US}
SYNC_CRON=${SYNC_CRON:-*/5 * * * *}
CATCHUP_CRON=${CATCHUP_CRON:-17 2 * * *}
SCHEDULE_NAME=${SCHEDULE_NAME:-${JOB}-frequent}
CATCHUP_SCHEDULE_NAME=${CATCHUP_SCHEDULE_NAME:-${JOB}-nightly-catchup}
SCHED_REGION=${SCHEDULER_REGION:-${REGION}}
METRIC_NAME=${METRIC_NAME:-${JOB//-/_}_ok}
APP_TITLE=${APP_TITLE:-}
ALERT_EMAIL=${ALERT_EMAIL:-}
read -r -a DOMAINS <<< "${ALLOWED_DOMAINS//,/ }"
IFS=. read -r SRC_PROJECT SRC_DATASET SRC_TABLE <<< "$EXPORT_TABLE"
[[ -n "$SRC_TABLE" ]] || { echo "EXPORT_TABLE must be project.dataset.table" >&2; exit 1; }

G="gcloud --project=${PROJECT} --quiet"
PY=${PY:-python3}
PROJECT_NUMBER=${PROJECT_NUMBER:-$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')}
APP_SA=${NAME_PREFIX}-app@${PROJECT}.iam.gserviceaccount.com
SYNC_SA=${NAME_PREFIX}-sync@${PROJECT}.iam.gserviceaccount.com
SCHED_SA=${NAME_PREFIX}-scheduler@${PROJECT}.iam.gserviceaccount.com
BUILD_SA=${NAME_PREFIX}-build@${PROJECT}.iam.gserviceaccount.com
SRC_BUCKET=run-sources-${PROJECT}-${REGION}
AR_REPO=cloud-run-source-deploy
IAP_AGENT=service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com
AUDIENCE=/projects/${PROJECT_NUMBER}/locations/${REGION}/services/${SERVICE}

# Env vars shared by the web app and the sync job. ';' separates domains because
# ',' separates variables in --set-env-vars. Custom delimiter '^@^' lets APP_TITLE
# contain commas.
COMMON_ENV="GCP_PROJECT=${PROJECT}@BQ_DATASET=${DATASET}@BQ_SOURCE_TABLE=${EXPORT_TABLE}@BQ_LOCATION=${BQ_LOCATION}"
APP_ENV="^@^AUTH_MODE=iap@IAP_AUDIENCE=${AUDIENCE}@ALLOWED_DOMAIN=$(IFS=\;; echo "${DOMAINS[*]}")@${COMMON_ENV}@APP_TITLE=${APP_TITLE}@MAX_BULK=${MAX_BULK:-500}"
# Backfill can scan more than interactive lookups; cap is configurable.
JOB_ENV="^@^${COMMON_ENV}@BQ_MAX_BYTES=${SYNC_MAX_BYTES:-21474836480}"
export GCP_PROJECT=$PROJECT BQ_LOCATION BQ_DATASET=$DATASET BQ_SOURCE_TABLE=$EXPORT_TABLE

step_apis() {
  $G services enable run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com cloudscheduler.googleapis.com iap.googleapis.com \
    bigquery.googleapis.com logging.googleapis.com monitoring.googleapis.com \
    cloudresourcemanager.googleapis.com storage.googleapis.com
}

ensure_sa() {  # name, display
  $G iam service-accounts describe "$1@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    $G iam service-accounts create "$1" --display-name="$2"
}

step_iam() {
  ensure_sa "${NAME_PREFIX}-app" "LinkFinder web app (read-only BigQuery)"
  ensure_sa "${NAME_PREFIX}-sync" "LinkFinder sync job (writes ${DATASET})"
  ensure_sa "${NAME_PREFIX}-scheduler" "LinkFinder scheduler (runs sync job)"
  ensure_sa "${NAME_PREFIX}-build" "LinkFinder Cloud Build"
  # Project-level: only permission to run query jobs (jobs run in PROJECT).
  local sa
  for sa in "$APP_SA" "$SYNC_SA"; do
    $G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$sa" \
      --role=roles/bigquery.jobUser --condition=None >/dev/null
  done
  # Dataset-level data access (least privilege). The export may live in another project.
  # (Index dataset grants happen in step_schema, after the dataset exists.)
  $PY -m tools.grant_dataset "$SRC_PROJECT" "$SRC_DATASET" READER "$APP_SA" "$SYNC_SA"
  # Build identity for `run deploy --source` (default compute SA has no access in new
  # projects). Pre-create the source bucket + image repo so grants can be scoped.
  $G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$BUILD_SA" \
    --role=roles/logging.logWriter --condition=None >/dev/null
  gcloud storage buckets describe "gs://${SRC_BUCKET}" --project="$PROJECT" >/dev/null 2>&1 ||
    gcloud storage buckets create "gs://${SRC_BUCKET}" --project="$PROJECT" \
      --location="$REGION" --uniform-bucket-level-access
  gcloud storage buckets add-iam-policy-binding "gs://${SRC_BUCKET}" --project="$PROJECT" \
    --member="serviceAccount:$BUILD_SA" --role=roles/storage.objectViewer >/dev/null
  $G artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1 ||
    $G artifacts repositories create "$AR_REPO" --location="$REGION" \
      --repository-format=docker --description="Cloud Run source deployments"
  $G artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
    --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer >/dev/null
  echo "iam: ok"
}

step_schema() {
  # Creates the index dataset (same location as the export) + tables; idempotent.
  $PY -m tools.run_sql schema --ds "$DATASET" >/dev/null
  $PY -m tools.grant_dataset "$PROJECT" "$DATASET" READER "$APP_SA"
  $PY -m tools.grant_dataset "$PROJECT" "$DATASET" WRITER "$SYNC_SA"
  echo "schema: ok"
}

step_service() {
  $G run deploy "$SERVICE" --region="$REGION" --source=. \
    --build-service-account="projects/${PROJECT}/serviceAccounts/${BUILD_SA}" \
    --service-account="$APP_SA" --set-env-vars="$APP_ENV" \
    --ingress=all --no-allow-unauthenticated --invoker-iam-check --iap \
    --cpu=1 --memory=512Mi --min-instances=0 --max-instances=3 --concurrency=40 \
    --timeout=60
}

step_iap() {
  # IAP's service agent must be able to invoke the service it fronts.
  $G beta services identity create --service=iap.googleapis.com >/dev/null 2>&1 || true
  $G run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
    --member="serviceAccount:${IAP_AGENT}" --role=roles/run.invoker >/dev/null
  # Every Workspace user in the allowed domains may use the app. IAM matches `domain:`
  # by Workspace customer, so secondary domains of the same tenant are already covered
  # by the primary-domain binding (their bindings are folded away; harmless).
  local d
  for d in "${DOMAINS[@]}"; do
    $G iap web add-iam-policy-binding --resource-type=cloud-run --region="$REGION" \
      --service="$SERVICE" --member="domain:${d}" \
      --role=roles/iap.httpsResourceAccessor >/dev/null
  done
  echo "iap: ok"
}

step_job() {
  local image
  image=$($G run services describe "$SERVICE" --region="$REGION" \
          --format='value(spec.template.spec.containers[0].image)')
  local verb=create
  $G run jobs describe "$JOB" --region="$REGION" >/dev/null 2>&1 && verb=update
  $G run jobs "$verb" "$JOB" --region="$REGION" --image="$image" \
    --service-account="$SYNC_SA" --command=python --args=-m,tools.sync_job \
    --set-env-vars="$JOB_ENV" \
    --tasks=1 --max-retries=1 --task-timeout=600s --cpu=1 --memory=512Mi
  # The nightly trigger passes arg overrides -> needs run.jobs.runWithOverrides,
  # which roles/run.invoker lacks. Scoped to this one job.
  $G run jobs add-iam-policy-binding "$JOB" --region="$REGION" \
    --member="serviceAccount:${SCHED_SA}" --role=roles/run.jobsExecutorWithOverrides >/dev/null
}

ensure_schedule() {  # name, cron, body-json
  local uri="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run"
  # create takes --headers, update takes --update-headers.
  local verb=create hdr=--headers
  if $G scheduler jobs describe "$1" --location="$SCHED_REGION" >/dev/null 2>&1; then
    verb=update; hdr=--update-headers
  fi
  $G scheduler jobs "$verb" http "$1" --location="$SCHED_REGION" --schedule="$2" \
    --time-zone=Etc/UTC --uri="$uri" --http-method=POST \
    --oauth-service-account-email="$SCHED_SA" \
    "$hdr"=Content-Type=application/json --message-body="$3" \
    --attempt-deadline=180s
}

step_schedule() {
  ensure_schedule "$SCHEDULE_NAME" "$SYNC_CRON" '{}'
  ensure_schedule "$CATCHUP_SCHEDULE_NAME" "$CATCHUP_CRON" \
    '{"overrides":{"containerOverrides":[{"args":["-m","tools.sync_job","--overlap-hours","72"]}]}}'
}

step_alerts() {
  if [[ -z "$ALERT_EMAIL" ]]; then echo "alerts: skipped (ALERT_EMAIL not set)"; return; fi
  # Log-based metric counting successful syncs (feeds the staleness alert).
  $G logging metrics describe "$METRIC_NAME" >/dev/null 2>&1 ||
    $G logging metrics create "$METRIC_NAME" \
      --description="Successful ${JOB} runs" \
      --log-filter="resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB}\" AND jsonPayload.event=\"sync_ok\""
  # Email channel (reused if it already exists).
  local channel
  channel=$($G beta monitoring channels list \
            --filter="type=\"email\" AND labels.email_address=\"${ALERT_EMAIL}\"" \
            --format='value(name)' | head -1)
  if [[ -z "$channel" ]]; then
    channel=$($G beta monitoring channels create --type=email \
              --display-name="LinkFinder alerts (${ALERT_EMAIL})" \
              --channel-labels="email_address=${ALERT_EMAIL}" --format='value(name)')
  fi
  local f name tmp
  tmp=$(mktemp)
  for f in monitoring/alert_*.json; do
    sed -e "s#__CHANNEL__#${channel}#g" -e "s#__JOB__#${JOB}#g" \
        -e "s#__METRIC__#${METRIC_NAME}#g" -e "s#__SCHEDULE__#${SCHEDULE_NAME}#g" "$f" > "$tmp"
    name=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['displayName'])" "$tmp")
    if [[ -z $($G alpha monitoring policies list --filter="displayName=\"${name}\"" --format='value(name)') ]]; then
      $G alpha monitoring policies create --policy-from-file="$tmp" --format='value(name)'
    else
      echo "policy exists: ${name}"
    fi
  done
  rm -f "$tmp"
}

step_show() {  # print the resolved settings (no changes)
  cat <<EOF
project=$PROJECT ($PROJECT_NUMBER) region=$REGION service=$SERVICE
export_table=$EXPORT_TABLE index_dataset=$PROJECT.$DATASET location=$BQ_LOCATION
domains=${DOMAINS[*]} job=$JOB schedules=$SCHEDULE_NAME[$SYNC_CRON],$CATCHUP_SCHEDULE_NAME[$CATCHUP_CRON]
service_accounts=$APP_SA $SYNC_SA $SCHED_SA $BUILD_SA
metric=$METRIC_NAME alert_email=${ALERT_EMAIL:-<none>} title=${APP_TITLE:-<default>}
app_env=$APP_ENV
job_env=$JOB_ENV
EOF
}

record() {  # level, name, detail -> results file (install.sh summary) + console
  local color; case "$1" in PASS) color=32;; WARN) color=33;; *) color=31;; esac
  printf '  \033[%sm%s\033[0m  %s%s\n' "$color" "$1" "$2" "${3:+ - $3}"
  [[ -n "${RESULTS_FILE:-}" ]] && printf 'deploy\t%s\t%s\t%s\n' "$1" "$2" "$3" >> "$RESULTS_FILE"
  return 0
}

steps=("$@")
[[ ${#steps[@]} -eq 0 ]] && steps=(apis iam schema service iap job schedule alerts)
for s in "${steps[@]}"; do
  echo "=== step: $s ==="
  if ! declare -F "step_$s" >/dev/null; then
    record FAIL "step $s" "unknown step (valid: apis iam schema service iap job schedule alerts show)"
    exit 2
  fi
  t0=$SECONDS
  # Run each step in a subshell with errexit ON (not inside an `if`, which would
  # disable errexit), so the first failing command fails the step.
  set +e
  ( set -e; "step_$s" )
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    record FAIL "step $s" "exit code $rc after $((SECONDS - t0))s - see the messages above"
    echo "=== stopped at step: $s ===" >&2
    exit "$rc"
  fi
  [[ "$s" == show ]] || record PASS "step $s" "$((SECONDS - t0))s"
done
echo "=== done: ${steps[*]} ==="
