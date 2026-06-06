# Gemini Platform Green-Box Map

This file maps the green boxes in
`docs/agent-building insights/gemini_agent_platform_required_marked.png` to the
repo implementation.

## Required Green Boxes

| Marked item | Repo implementation |
| --- | --- |
| 3P agent frameworks | ADK 2.x graph workflow in `app/workflow.py`; MCP toolsets for external tools. |
| 3P and open models | Environment-driven model routes in `app/router.py`; cheap, strong, and verifier models are configurable. |
| Grounding | Uploaded artifact evidence, Gemini multimodal embeddings, and optional MCP research. |
| MCP | `MCP_RESEARCH_URL` and `MCP_RESEARCH_COMMAND` wire ADK `McpToolset` into retrieval. |
| APIs and connectors | MCP connectors, Google ADC/Vertex AI, GCS artifacts, BigQuery analytics, and optional Model Armor REST calls. |
| Agent Gateway | Agent Runtime wrapper in `app/agent_runtime_app.py`; optional Cloud Run deployment in `deploy/deploy_runtime.py`. |
| Agent Identity | Google account or service account through Application Default Credentials; runtime identity comes from the deployed Google Cloud service. |
| Model Armor | Optional prompt screening in `app/model_armor.py`, enabled with `MODEL_ARMOR_TEMPLATE` or `MODEL_ARMOR_TEMPLATE_ID`. |
| Agent Policy | MIME allow-list, artifact byte limits, model routing policy, safety refusal policy, and optional Model Armor template policy. |
| Agent Evaluation | Deterministic pytest checks plus ADK eval set under `tests/eval/`. |
| Agent Observability | BigQuery Agent Analytics hook, Cloud Logging feedback, Agent Runtime telemetry, route history, trajectory, and verifier findings. |

## Google Account Setup

Use this path when spending the Google Cloud grant through your own account:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_GENAI_USE_VERTEXAI=true
```

Enable the Google Cloud services used by the green boxes:

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  modelarmor.googleapis.com
```

If `gcloud` has trouble enabling Model Armor from your local shell, set the
regional endpoint override first:

```bash
gcloud config set api_endpoint_overrides/modelarmor \
  "https://modelarmor.us-central1.rep.googleapis.com/"
```

## Multimodal Retrieval

The Colab notebook points at a Gemini Embedding 2 workflow:

- embed text, images, audio, video, and PDFs into one vector space
- rank cross-modal matches with cosine similarity
- use lower dimensions to reduce cost, latency, and storage
- optionally add FAISS or another vector store once there are many artifacts

This repo implements the same pattern in `app/multimodal.py`. During
`retrieve_evidence`, uploaded artifacts are loaded from ADK artifacts, embedded
with Gemini, ranked against the user request, and written back as evidence IDs
in the spec ledger.

Recommended grant-aware defaults:

```bash
export MULTIMODAL_RETRIEVAL_ENABLED=true
export MULTIMODAL_EMBEDDING_MODEL=gemini-embedding-2
export MULTIMODAL_OUTPUT_DIMENSIONALITY=768
export MULTIMODAL_MAX_ARTIFACT_BYTES=4000000
export MULTIMODAL_MAX_RESULTS=5
```

Use `GEMINI_API_KEY` only if you want Gemini API key auth instead of the
Vertex AI / Google account path.

## Model Armor

Create a Model Armor template in the same region as the agent and enable it:

```bash
export MODEL_ARMOR_TEMPLATE_ID=default-agent-policy
export MODEL_ARMOR_LOCATION=us-central1
```

or pass the full resource:

```bash
export MODEL_ARMOR_TEMPLATE=projects/YOUR_PROJECT_ID/locations/us-central1/templates/default-agent-policy
```

The verifier calls Model Armor for user prompt screening before falling back to
the local MVP safety policy. `MODEL_ARMOR_FAIL_CLOSED=false` keeps local
development from blocking if auth or network is unavailable; set it to `true`
for stricter deployed behavior.

## Next Production Step

The current multimodal index is per-turn and in-memory. For larger corpora,
persist vectors in Matching Engine, BigQuery vector search, AlloyDB, Vertex AI
Vector Search, or a managed vector database, then replace the per-turn ranking
with top-k retrieval from that store.
