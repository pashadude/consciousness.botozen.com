#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-zenpulsar}}"
BUCKET="${SPANNER_RAG_GCS_BUCKET:-zenpulsar-spanner-rag-ingest}"
INGEST_DIR="${SPANNER_RAG_LOCAL_INGEST_DIR:-data/spanner_rag_ingest}"
LOCATION="${SPANNER_RAG_GCS_LOCATION:-US}"

if [[ ! -f "${INGEST_DIR}/documents.jsonl" ]]; then
  echo "Missing ${INGEST_DIR}/documents.jsonl. Run scripts/prepare_spanner_rag_json.py first." >&2
  exit 1
fi

if [[ ! -f "${INGEST_DIR}/chunks.jsonl" ]]; then
  echo "Missing ${INGEST_DIR}/chunks.jsonl. Run scripts/prepare_spanner_rag_json.py first." >&2
  exit 1
fi

if [[ ! -f "${INGEST_DIR}/manifest.json" ]]; then
  echo "Missing ${INGEST_DIR}/manifest.json. Run scripts/prepare_spanner_rag_json.py first." >&2
  exit 1
fi

gcloud storage buckets describe "gs://${BUCKET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud storage buckets create "gs://${BUCKET}" \
  --project="${PROJECT_ID}" \
  --location="${LOCATION}"

gcloud storage cp "${INGEST_DIR}/documents.jsonl" "gs://${BUCKET}/documents.jsonl"
gcloud storage cp "${INGEST_DIR}/chunks.jsonl" "gs://${BUCKET}/chunks.jsonl"
gcloud storage cp "${INGEST_DIR}/manifest.json" "gs://${BUCKET}/manifest.json"

if [[ -f "${INGEST_DIR}/user_talks_documents.jsonl" ]]; then
  gcloud storage cp "${INGEST_DIR}/user_talks_documents.jsonl" "gs://${BUCKET}/user_talks_documents.jsonl"
fi

if [[ -f "${INGEST_DIR}/user_talks_chunks.jsonl" ]]; then
  gcloud storage cp "${INGEST_DIR}/user_talks_chunks.jsonl" "gs://${BUCKET}/user_talks_chunks.jsonl"
fi

gcloud storage ls -l "gs://${BUCKET}"
