#!/usr/bin/env bash
# Interactive installer for the SharePoint/OneDrive -> Google Drive link finder.
# Run in Google Cloud Shell (or any shell with gcloud + python3) as a project Owner.
#
#   ./install.sh                     # ask questions, write deploy.env, preflight, deploy
#   ./install.sh --config FILE       # non-interactive: use an existing config file
#   ./install.sh --dry-run           # ask questions + preflight only (no changes)
#   ./install.sh preflight [--config FILE]   # checks only
#   ./install.sh verify [--config FILE]      # health check of an existing install
#
# Every run writes logs/install-<time>.log (full output), a PASS/WARN/FAIL summary
# (logs/install-<time>-summary.txt + .json) and, when possible, one Cloud Logging
# entry (log name: link-finder-install) in the project.
set -euo pipefail
cd "$(dirname "$0")"

CONFIG=deploy.env
INTERACTIVE=1
DEPLOY=1
ASSUME_YES=0
MODE=install
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG=$2; INTERACTIVE=0; shift 2 ;;
    --out) CONFIG=$2; shift 2 ;;
    --dry-run) DEPLOY=0; MODE=dry-run; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    preflight) INTERACTIVE=0; DEPLOY=0; MODE=preflight; shift ;;
    verify) INTERACTIVE=0; DEPLOY=0; MODE=verify; shift ;;
    -h|--help) sed -n 2,14p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ask() {  # var, prompt, default  -> sets var (keeps existing config value as default)
  local __v=$1 __p=$2 __d=${!1:-$3} __a
  read -r -e -p "$__p [${__d}]: " __a || true
  printf -v "$__v" '%s' "${__a:-$__d}"
}
confirm() {  # prompt -> 0 if yes
  [[ $ASSUME_YES == 1 ]] && return 0
  local a; read -r -p "$1 [y/N]: " a || true; [[ $a =~ ^[Yy] ]]
}
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python

# ---- logging: per-run log + PASS/WARN/FAIL results + summary on exit ------------
TS=$(date -u +%Y%m%d-%H%M%S)
mkdir -p logs
LOG="logs/install-$TS.log"
SUMMARY="logs/install-$TS-summary.txt"
export RESULTS_FILE="logs/install-$TS.results.tsv"
: > "$RESULTS_FILE"
: > "$LOG"
rec() {  # stage, level, check, detail
  local color; case "$2" in PASS) color=32;; WARN) color=33;; INFO) color=36;; *) color=31;; esac
  printf '  \033[%sm%s\033[0m  %s%s\n' "$color" "$2" "$3" "${4:+ - $4}"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}" >> "$RESULTS_FILE"
}
finish() {
  local code=$?
  set +e
  python3 -m tools.install_summary "$RESULTS_FILE" "$SUMMARY" "$code" "$MODE" "$LOG"
  # Best effort: one structured entry in Cloud Logging so others can see the outcome.
  if [[ -n "${PROJECT_ID:-}" && -f "${SUMMARY%.txt}.json" ]]; then
    local sev=INFO; [[ $code -ne 0 ]] && sev=ERROR
    grep -q $'\tFAIL\t' "$RESULTS_FILE" && sev=ERROR
    timeout 30 gcloud logging write link-finder-install "$(cat "${SUMMARY%.txt}.json")" \
      --payload-type=json --severity="$sev" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 &&
      echo "Also logged to Cloud Logging (log name: link-finder-install) in $PROJECT_ID."
  fi
  sleep 0.3  # let the log tee flush
  exit $code
}
trap finish EXIT
start_log() {  # from here on, everything printed is also written to $LOG
  exec > >(tee -a "$LOG") 2>&1
}

# ---- prerequisites ------------------------------------------------------------
if command -v gcloud >/dev/null; then rec prereq PASS "gcloud CLI installed"
else rec prereq FAIL "gcloud CLI installed" "use Google Cloud Shell"; exit 1; fi
if command -v python3 >/dev/null; then rec prereq PASS "python3 installed"
else rec prereq FAIL "python3 installed"; exit 1; fi
if [[ -n "$(gcloud auth print-access-token 2>/dev/null)" ]]; then
  rec prereq PASS "signed in to gcloud" "$(gcloud config get-value account 2>/dev/null)"
else
  rec prereq FAIL "signed in to gcloud" "run: gcloud auth login"; exit 1
fi

if [[ $INTERACTIVE == 1 ]]; then
  # Re-running: previous answers become the defaults.
  # shellcheck disable=SC1090
  [[ -f "$CONFIG" ]] && source "$CONFIG"
  bold "SharePoint/OneDrive -> Google Drive link finder: installer"
  echo "Signed in as: $(gcloud config get-value account 2>/dev/null)"
  echo "Press Enter to accept the [default]."
  echo

  bold "1/5  Google Cloud project (hosts the app, sync job and index dataset)"
  while true; do
    ask PROJECT_ID "Project ID (lowercase, e.g. my-project-123456)" "$(gcloud config get-value project 2>/dev/null)"
    # Project IDs are lowercase; accept pasted values with capitals/spaces.
    PROJECT_ID=$(tr '[:upper:]' '[:lower:]' <<< "$PROJECT_ID" | tr -d '[:space:]')
    if [[ ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
      echo "  '$PROJECT_ID' is not a valid project ID (6-30 chars: lowercase letters, digits, '-')."
    elif PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' 2>/dev/null) &&
         [[ -n "$PROJECT_NUMBER" ]]; then
      break
    else
      echo "  Cannot access project '$PROJECT_ID' (wrong ID, or you lack access)."
    fi
    echo "  Projects you can access:"
    gcloud projects list --format='table[no-heading](projectId,name)' --limit=20 2>/dev/null | sed 's/^/    /'
    PROJECT_ID=""
  done
  echo "  project number: $PROJECT_NUMBER"

  bold "2/5  Cloud Run"
  ask REGION "Cloud Run region (e.g. us-central1, europe-west1, asia-south1)" "us-central1"
  ask SERVICE_NAME "Cloud Run service name" "gws-link-converter"
  ask NAME_PREFIX "Name prefix for service accounts (5-20 chars)" "link-converter"
  ask JOB_NAME "Sync job name" "${NAME_PREFIX}-sync"

  bold "3/5  Workspace audit log export in BigQuery"
  echo "  (Admin console > Reporting > Data integrations > BigQuery Export)"
  EXPORT_PROJECT=$(cut -d. -f1 <<< "${EXPORT_TABLE:-$PROJECT_ID}")
  ask EXPORT_PROJECT "Project that holds the Workspace export" "$PROJECT_ID"
  echo "  looking for datasets with an 'activity' table in $EXPORT_PROJECT ..."
  FOUND=$(GCP_PROJECT=$PROJECT_ID BQ_SOURCE_TABLE=x.y.z $PY -m tools.preflight discover-datasets "$EXPORT_PROJECT" 2>/dev/null || echo "[]")
  $PY - "$FOUND" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])
if not d: print("  (none found automatically - type the dataset ID)")
for x in d: print(f"  - {x['dataset']}  location={x['location']}  rows={x['rows']}")
EOF
  DEFAULT_DS=$($PY -c 'import json,sys;d=json.loads(sys.argv[1]);print(d[0]["dataset"] if d else "")' "$FOUND")
  CUR_DS=$(cut -d. -f2 <<< "${EXPORT_TABLE:-}")
  EXPORT_DATASET=${CUR_DS:-$DEFAULT_DS}
  ask EXPORT_DATASET "Export dataset ID" "$EXPORT_DATASET"
  CUR_T=$(cut -d. -f3 <<< "${EXPORT_TABLE:-}")
  EXPORT_TABLE_NAME=${CUR_T:-activity}
  ask EXPORT_TABLE_NAME "Export table name" "activity"
  EXPORT_TABLE="${EXPORT_PROJECT}.${EXPORT_DATASET}.${EXPORT_TABLE_NAME}"
  INFO=$(GCP_PROJECT=$PROJECT_ID BQ_SOURCE_TABLE=$EXPORT_TABLE $PY -m tools.preflight table-info "$EXPORT_TABLE")
  BQ_LOCATION=$($PY -c 'import json,sys;print(json.loads(sys.argv[1]).get("location",""))' "$INFO")
  if [[ -z "$BQ_LOCATION" ]]; then
    echo "  WARNING: cannot read $EXPORT_TABLE ($INFO)"; ask BQ_LOCATION "BigQuery location" "US"
  else
    echo "  export location: $BQ_LOCATION (index dataset will be created there too)"
  fi
  ask INDEX_DATASET "Index dataset to create in $PROJECT_ID" "migration_lookup"

  bold "4/5  Who may sign in"
  ORG_DOMAIN=$(GCP_PROJECT=$PROJECT_ID BQ_SOURCE_TABLE=x.y.z $PY -m tools.preflight org-domain "$PROJECT_ID" 2>/dev/null || true)
  [[ -n "$ORG_DOMAIN" ]] && echo "  organization primary domain: $ORG_DOMAIN"
  ask ALLOWED_DOMAINS "Workspace domains allowed to use the app (comma-separated)" "$ORG_DOMAIN"

  bold "5/5  Sync schedule, alerts and branding"
  ask SYNC_CRON "Sync schedule (cron, UTC)" "*/5 * * * *"
  ask CATCHUP_CRON "Nightly catch-up schedule (cron, UTC)" "17 2 * * *"
  ask ALERT_EMAIL "Alert email (blank = no alerts)" "$(gcloud config get-value account 2>/dev/null)"
  ask APP_TITLE "App title shown to users (blank = default)" ""

  q() { local v=${!1}; printf "%s='%s'\n" "$1" "${v//\'/\'\\\'\'}"; }  # KEY='value'
  umask 077
  {
    echo "# Link finder deployment config - written by install.sh on $(date -u +%FT%TZ)"
    echo "# Re-run ./install.sh to change, or edit and run ./install.sh --config $CONFIG"
    for k in PROJECT_ID PROJECT_NUMBER REGION SERVICE_NAME NAME_PREFIX JOB_NAME \
             EXPORT_TABLE BQ_LOCATION INDEX_DATASET ALLOWED_DOMAINS SYNC_CRON CATCHUP_CRON \
             ALERT_EMAIL APP_TITLE; do
      q "$k"
    done
    # Optional overrides carried over from an existing config (advanced).
    for k in SCHEDULE_NAME CATCHUP_SCHEDULE_NAME SCHEDULER_REGION METRIC_NAME; do
      [[ -n "${!k:-}" ]] && q "$k"
    done
  } > "$CONFIG"
  echo
  bold "Saved $CONFIG"
  rec config PASS "answers saved" "$CONFIG"
fi

if [[ -f "$CONFIG" ]]; then
  [[ $INTERACTIVE == 1 ]] || rec config PASS "config file found" "$CONFIG"
else
  rec config FAIL "config file found" "$CONFIG missing - run ./install.sh"; exit 1
fi
# shellcheck disable=SC1090
source "$CONFIG"
start_log
echo "=== Link Finder installer: mode=$MODE, $(date -u +%FT%TZ), config=$CONFIG ==="
sed 's/^/  /' "$CONFIG"

if [[ $MODE == verify ]]; then
  bold "Health check"
  RESULTS_STAGE=verify GCP_PROJECT=x BQ_SOURCE_TABLE=x.y.z $PY -m tools.verify --config "$CONFIG"
  exit $?
fi
echo
bold "Settings"
./setup.sh --config "$CONFIG" show | sed 's/^/  /' | grep -v '_env='
echo
bold "Preflight checks"
RESULTS_STAGE=preflight GCP_PROJECT=x BQ_SOURCE_TABLE=x.y.z $PY -m tools.preflight check --config "$CONFIG" ||
  { echo "Fix the FAIL items (edit $CONFIG or re-run ./install.sh) and try again." >&2; exit 1; }

if [[ $DEPLOY != 1 ]]; then
  rec result INFO "nothing was changed" "$MODE"
  exit 0
fi
echo
if ! confirm "Deploy now? This creates/updates the resources listed above"; then
  rec deploy WARN "deployment" "not confirmed - nothing was changed"
  exit 0
fi
PY=$PY ./setup.sh --config "$CONFIG"
echo
bold "Running the first sync (backfill) ..."
JOB=${JOB_NAME:-${NAME_PREFIX:-link-converter}-sync}
if gcloud run jobs execute "$JOB" --project="$PROJECT_ID" --region="$REGION" --wait --quiet; then
  rec sync PASS "first sync (backfill)" "$JOB"
else
  rec sync FAIL "first sync (backfill)" "check: gcloud run jobs executions list --job=$JOB --region=$REGION"
fi
echo
bold "Health check"
RESULTS_STAGE=verify GCP_PROJECT=x BQ_SOURCE_TABLE=x.y.z $PY -m tools.verify --config "$CONFIG" || true
URL=$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" \
      --format='value(status.url)' --quiet)
rec result INFO "app URL" "$URL"
echo
bold "Done. App URL: $URL"
echo "Users of ${ALLOWED_DOMAINS} sign in with their Google account. IAP access can take a few minutes to apply."
grep -q $'\tFAIL\t' "$RESULTS_FILE" && exit 1
exit 0
