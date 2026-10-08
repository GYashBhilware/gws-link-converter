#!/usr/bin/env bash
# Remove everything install.sh created for one deployment. Read-only unless --yes.
# Never touches the Workspace export dataset's data (only removes the app's read access).
#
#   ./uninstall.sh [--config deploy.env]                 # list what would be deleted
#   ./uninstall.sh [--config deploy.env] --yes           # delete (index dataset kept)
#   ./uninstall.sh [--config deploy.env] --yes --delete-index   # also drop the index dataset
set -euo pipefail
cd "$(dirname "$0")"

CONFIG=deploy.env; APPLY=0; DROP_INDEX=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG=$2; shift 2 ;;
    --yes) APPLY=1; shift ;;
    --delete-index) DROP_INDEX=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$CONFIG" ]] || { echo "Config $CONFIG not found." >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONFIG"
PROJECT=$PROJECT_ID
NAME_PREFIX=${NAME_PREFIX:-link-converter}
JOB=${JOB_NAME:-${NAME_PREFIX}-sync}
DATASET=${INDEX_DATASET:-migration_lookup}
SCHEDULE_NAME=${SCHEDULE_NAME:-${JOB}-frequent}
CATCHUP_SCHEDULE_NAME=${CATCHUP_SCHEDULE_NAME:-${JOB}-nightly-catchup}
SCHED_REGION=${SCHEDULER_REGION:-${REGION}}
METRIC_NAME=${METRIC_NAME:-${JOB//-/_}_ok}
IFS=. read -r SRC_PROJECT SRC_DATASET _ <<< "$EXPORT_TABLE"
G="gcloud --project=${PROJECT} --quiet"
PY=python3; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
export GCP_PROJECT=$PROJECT BQ_SOURCE_TABLE=$EXPORT_TABLE BQ_LOCATION=${BQ_LOCATION:-US}
SAS=()
for r in app sync scheduler build; do SAS+=("${NAME_PREFIX}-${r}@${PROJECT}.iam.gserviceaccount.com"); done

run() {  # print, and execute only with --yes; keep going on "not found"
  echo "  - $*"
  [[ $APPLY == 1 ]] || return 0
  "$@" >/dev/null 2>&1 || echo "    (skipped: not found or already removed)"
}

echo "Deployment: project=$PROJECT region=$REGION service=$SERVICE_NAME job=$JOB"
[[ $APPLY == 1 ]] && echo "DELETING:" || echo "Would delete (dry run; add --yes to apply):"
run $G scheduler jobs delete "$SCHEDULE_NAME" --location="$SCHED_REGION"
run $G scheduler jobs delete "$CATCHUP_SCHEDULE_NAME" --location="$SCHED_REGION"
run $G run jobs delete "$JOB" --region="$REGION"
run $G run services delete "$SERVICE_NAME" --region="$REGION"
while IFS=$'\t' read -r pname pdisplay; do
  [[ "$pdisplay" == "${JOB}: "* ]] && run $G alpha monitoring policies delete "$pname"
done < <($G alpha monitoring policies list --format='value(name,displayName)' 2>/dev/null)
run $G logging metrics delete "$METRIC_NAME"
for sa in "${SAS[@]}"; do
  for role in roles/bigquery.jobUser roles/logging.logWriter; do
    run $G projects remove-iam-policy-binding "$PROJECT" --member="serviceAccount:$sa" \
      --role="$role" --condition=None
  done
done
run $PY -m tools.grant_dataset --remove "$SRC_PROJECT" "$SRC_DATASET" "${SAS[@]}"
if [[ $DROP_INDEX == 1 ]]; then
  run $PY -m tools.run_sql_drop "$PROJECT" "$DATASET"
else
  echo "  (kept) index dataset $PROJECT.$DATASET - add --delete-index to drop it"
  run $PY -m tools.grant_dataset --remove "$PROJECT" "$DATASET" "${SAS[@]}"
fi
for sa in "${SAS[@]}"; do run $G iam service-accounts delete "$sa"; done
echo "  (kept) Workspace export ${EXPORT_TABLE}, email notification channel, and the shared"
echo "         Cloud Run source bucket / Artifact Registry repo (used by other deployments)."
[[ $APPLY == 1 ]] && echo "Uninstall complete." || echo "Dry run only - nothing was changed."
