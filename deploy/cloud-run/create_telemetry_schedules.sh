#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-zenpulsar}}"
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
SCHEDULER_REGION="${SCHEDULER_REGION:-${REGION}}"
JOB_PREFIX="${JOB_PREFIX:-mutual-spec-telemetry}"
TELEMETRY_SCHEDULER_SA="${TELEMETRY_SCHEDULER_SA:-${TELEMETRY_SCHEDULER_SERVICE_ACCOUNT:-telemetry-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}}"
TIME_ZONE="${TIME_ZONE:-Etc/UTC}"

job_run_uri() {
  local cloud_run_job="$1"
  echo "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${cloud_run_job}:run"
}

upsert_schedule() {
  local scheduler_job="$1"
  local schedule="$2"
  local cloud_run_job="$3"
  local description="$4"
  local uri
  uri="$(job_run_uri "${cloud_run_job}")"

  if gcloud scheduler jobs describe "${scheduler_job}" \
    --location="${SCHEDULER_REGION}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "${scheduler_job}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT_ID}" \
      --schedule="${schedule}" \
      --time-zone="${TIME_ZONE}" \
      --uri="${uri}" \
      --http-method=POST \
      --oauth-service-account-email="${TELEMETRY_SCHEDULER_SA}" \
      --description="${description}" \
      --quiet
  else
    gcloud scheduler jobs create http "${scheduler_job}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT_ID}" \
      --schedule="${schedule}" \
      --time-zone="${TIME_ZONE}" \
      --uri="${uri}" \
      --http-method=POST \
      --oauth-service-account-email="${TELEMETRY_SCHEDULER_SA}" \
      --description="${description}" \
      --quiet
  fi
}

upsert_schedule \
  "${JOB_PREFIX}-pull-assets-every-5m" \
  "*/5 * * * *" \
  "${JOB_PREFIX}-pull-assets" \
  "Pull Cloud Asset Inventory Pub/Sub messages into BigQuery."

upsert_schedule \
  "${JOB_PREFIX}-poll-monitoring-every-15m" \
  "*/15 * * * *" \
  "${JOB_PREFIX}-poll-monitoring" \
  "Poll Cloud Monitoring compute metrics into BigQuery."

upsert_schedule \
  "${JOB_PREFIX}-seed-power-prices-hourly" \
  "5 * * * *" \
  "${JOB_PREFIX}-seed-power-prices" \
  "Refresh static regional power proxy rows."

upsert_schedule \
  "${JOB_PREFIX}-install-views-daily" \
  "15 0 * * *" \
  "${JOB_PREFIX}-install-views" \
  "Refresh BigQuery resource-region routing views."

echo "Cloud Scheduler jobs are configured in ${SCHEDULER_REGION}."
