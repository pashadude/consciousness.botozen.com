#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-zenpulsar}}"
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
AR_LOCATION="${AR_LOCATION:-${REGION}}"
AR_REPOSITORY="${AR_REPOSITORY:-mutual-spec}"
POOL_ID="${POOL_ID:-github}"
PROVIDER_ID="${PROVIDER_ID:-consciousness-botozen}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-pashadude/consciousness.botozen.com}"
TELEMETRY_RUNTIME_SA="${TELEMETRY_RUNTIME_SA:-telemetry-collector@${PROJECT_ID}.iam.gserviceaccount.com}"
TELEMETRY_SCHEDULER_SA="${TELEMETRY_SCHEDULER_SA:-telemetry-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}"
CONSOLE_RUNTIME_SA="${CONSOLE_RUNTIME_SA:-console-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
GITHUB_DEPLOYER_SA="${GITHUB_DEPLOYER_SA:-github-deployer@${PROJECT_ID}.iam.gserviceaccount.com}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")"

gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  aiplatform.googleapis.com \
  spanner.googleapis.com \
  cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com \
  pubsub.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  --project "${PROJECT_ID}"

gcloud artifacts repositories describe "${AR_REPOSITORY}" \
  --location "${AR_LOCATION}" \
  --project "${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud artifacts repositories create "${AR_REPOSITORY}" \
  --repository-format=docker \
  --location "${AR_LOCATION}" \
  --description="mutual-spec-agent containers" \
  --project "${PROJECT_ID}"

for sa in "${TELEMETRY_RUNTIME_SA}" "${TELEMETRY_SCHEDULER_SA}" "${CONSOLE_RUNTIME_SA}" "${GITHUB_DEPLOYER_SA}"; do
  name="${sa%@*}"
  gcloud iam service-accounts describe "${sa}" \
    --project "${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${name}" \
    --project "${PROJECT_ID}" \
    --display-name="${name}"
done

for role in \
  roles/bigquery.user \
  roles/bigquery.dataEditor \
  roles/bigquery.jobUser \
  roles/pubsub.subscriber \
  roles/monitoring.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${TELEMETRY_RUNTIME_SA}" \
    --role="${role}" \
    --condition=None >/dev/null
done

for role in \
  roles/bigquery.dataViewer \
  roles/bigquery.jobUser \
  roles/spanner.databaseUser \
  roles/aiplatform.user \
  roles/storage.objectViewer \
  roles/storage.objectCreator \
  roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${CONSOLE_RUNTIME_SA}" \
    --role="${role}" \
    --condition=None >/dev/null
done

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${TELEMETRY_SCHEDULER_SA}" \
  --role="roles/run.invoker" \
  --condition=None >/dev/null

for role in \
  roles/artifactregistry.writer \
  roles/run.developer \
  roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${GITHUB_DEPLOYER_SA}" \
    --role="${role}" \
    --condition=None >/dev/null
done

for sa in "${TELEMETRY_RUNTIME_SA}" "${TELEMETRY_SCHEDULER_SA}" "${CONSOLE_RUNTIME_SA}"; do
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --project "${PROJECT_ID}" \
    --member="serviceAccount:${GITHUB_DEPLOYER_SA}" \
    --role="roles/iam.serviceAccountUser" >/dev/null
done

gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --location=global \
  --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --location=global \
  --project="${PROJECT_ID}" \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" \
  --issuer-uri="https://token.actions.githubusercontent.com/" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_REPOSITORY}' && assertion.ref=='refs/heads/main'" \
  --project="${PROJECT_ID}"

gcloud iam service-accounts add-iam-policy-binding "${GITHUB_DEPLOYER_SA}" \
  --project="${PROJECT_ID}" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPOSITORY}" \
  --role="roles/iam.workloadIdentityUser" >/dev/null

cat <<EOF
Cloud telemetry CI/CD setup complete.

Add these GitHub repository variables:
GCP_PROJECT_ID=${PROJECT_ID}
GCP_REGION=${REGION}
GCP_ARTIFACT_REGION=${AR_LOCATION}
GCP_ARTIFACT_REPOSITORY=${AR_REPOSITORY}
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}
GCP_DEPLOYER_SERVICE_ACCOUNT=${GITHUB_DEPLOYER_SA}
TELEMETRY_RUNTIME_SERVICE_ACCOUNT=${TELEMETRY_RUNTIME_SA}
TELEMETRY_SCHEDULER_SERVICE_ACCOUNT=${TELEMETRY_SCHEDULER_SA}
CONSOLE_RUNTIME_SERVICE_ACCOUNT=${CONSOLE_RUNTIME_SA}
TRADER_SOURCE_LAYER_CONFIG=config/trader_source_layer.yaml
TRADER_RAG_PROVIDER=spanner_rag
GOOGLE_AGENT_SEARCH_ENABLED=true
SPANNER_RAG_INSTANCE_ID=commodity-rag
SPANNER_RAG_DATABASE_ID=trader_rag
SPANNER_RAG_DOCUMENTS_TABLE=RagDocuments
SPANNER_RAG_CHUNKS_TABLE=RagChunks
SPANNER_RAG_EMBEDDING_MODEL=RagEmbeddingModel
SPANNER_RAG_GCS_BUCKET=zenpulsar-spanner-rag-ingest
USER_TALKS_RAG_LOG_ENABLED=true
USER_TALKS_GCS_BUCKET=zenpulsar-spanner-rag-ingest
USER_TALKS_GCS_PREFIX=user_talks
EOF
