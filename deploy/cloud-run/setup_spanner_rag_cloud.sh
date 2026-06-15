#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-zenpulsar}}"
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
SPANNER_CONFIG="${SPANNER_CONFIG:-regional-${REGION}}"
SPANNER_RAG_INSTANCE_ID="${SPANNER_RAG_INSTANCE_ID:-commodity-rag}"
SPANNER_RAG_DATABASE_ID="${SPANNER_RAG_DATABASE_ID:-trader_rag}"
SPANNER_RAG_PROCESSING_UNITS="${SPANNER_RAG_PROCESSING_UNITS:-100}"
SPANNER_RAG_EDITION="${SPANNER_RAG_EDITION:-ENTERPRISE}"

gcloud services enable \
  spanner.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}"

gcloud spanner instances describe "${SPANNER_RAG_INSTANCE_ID}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud spanner instances create "${SPANNER_RAG_INSTANCE_ID}" \
  --project="${PROJECT_ID}" \
  --config="${SPANNER_CONFIG}" \
  --description="Commodity RAG corpus" \
  --processing-units="${SPANNER_RAG_PROCESSING_UNITS}" \
  --edition="${SPANNER_RAG_EDITION}"

gcloud spanner databases describe "${SPANNER_RAG_DATABASE_ID}" \
  --project="${PROJECT_ID}" \
  --instance="${SPANNER_RAG_INSTANCE_ID}" >/dev/null 2>&1 || \
gcloud spanner databases create "${SPANNER_RAG_DATABASE_ID}" \
  --project="${PROJECT_ID}" \
  --instance="${SPANNER_RAG_INSTANCE_ID}"

cat <<EOF
Spanner RAG cloud target is ready.

Project:  ${PROJECT_ID}
Region:   ${REGION}
Instance: ${SPANNER_RAG_INSTANCE_ID}
Database: ${SPANNER_RAG_DATABASE_ID}
Edition:  ${SPANNER_RAG_EDITION}

Next:
1. Apply sql/spanner_rag_schema.example.sql after replacing PROJECT_ID, LOCATION, and MODEL_ID.
2. Load gs://\${SPANNER_RAG_GCS_BUCKET:-zenpulsar-spanner-rag-ingest}/documents.jsonl into RagDocuments.
3. Load gs://\${SPANNER_RAG_GCS_BUCKET:-zenpulsar-spanner-rag-ingest}/chunks.jsonl into RagChunks.
4. Generate embeddings and build RagChunksEmbeddingIndex.

Note: Spanner full-text search and hybrid indexed retrieval require Enterprise
or Enterprise Plus edition. Set SPANNER_RAG_EDITION=STANDARD only if you want
corpus storage with the application's lexical fallback and no search/vector
indexes.
EOF
