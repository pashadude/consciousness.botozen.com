# Mutual Specification Game MVP Design Spec

## Objective

`mutual-spec-agent` is an ADK Python 2.x prototype for narrowing the gap between a user's latent task and an executable task specification. The agent treats task formation as a staged workflow, not a single chat response.

## ADK Basis

The project was scaffolded with `uvx google-agents-cli create mutual-spec-agent --adk --prototype --deployment-target agent_runtime --bq-analytics` and then adapted to ADK Python 2.x graph workflow APIs. ADK 2.0 makes graph workflows the controlling execution engine, while Events carry node output and state updates. The implementation follows the current docs rather than older screenshot-era naming.

Relevant docs:

- https://adk.dev/2.0/
- https://adk.dev/graphs/
- https://adk.dev/graphs/data-handling/
- https://adk.dev/graphs/human-input/
- https://adk.dev/tools-custom/mcp-tools/
- https://adk.dev/artifacts/
- https://adk.dev/sessions/state/
- https://adk.dev/evaluate/

## Workflow Graph

Required stage order:

1. ingest
2. hypothesize spec
3. ask for clarification when ambiguity is high-impact
4. retrieve evidence
5. draft output
6. verify
7. finalize or loop

The ADK graph is built in `app/workflow.py` with `Workflow` edges and `@node` function nodes. The graph routes to `RequestInput` when the ledger is missing any material field: goal, audience, or output format. It routes to an MCP research agent when MCP is configured and otherwise continues with local artifact/session evidence.

## State and Artifacts

The shared ledger is stored under `session.state["spec_ledger"]` as plain JSON from Pydantic models. It contains only serializable strings, numbers, booleans, lists, and dicts. It records:

- accepted goal, audience, output format, constraints, assumptions, and success criteria
- high-impact ambiguities and clarification questions
- artifact metadata references, not file bytes
- evidence references and evidence IDs used in the draft
- optional Gemini multimodal artifact matches, stored as evidence metadata
- model routing history
- workflow trajectory
- verifier findings

Uploaded files, PDFs, and images are stored with `Context.save_artifact` when inline binary parts are present. The ledger stores only filename, MIME type, artifact ID, and version.

## Multimodal Retrieval

`app/multimodal.py` implements an optional Gemini embedding retrieval layer for
uploaded text, image, audio, video, and PDF artifacts. It follows the Colab
pattern from "Gemini Embedding 2 - Complete Guide":

- use Gemini Embedding 2 to map multiple modalities into one vector space
- embed the user request and each eligible artifact part
- rank artifacts with cosine similarity
- write ranked matches back as `multimodal:*` evidence IDs

The layer is enabled only when Google credentials are configured through either
Gemini API key auth or the preferred grant-backed Vertex AI / Application
Default Credentials path:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_GENAI_USE_VERTEXAI=true`
- `MULTIMODAL_RETRIEVAL_ENABLED=true`

Policy controls:

- `MULTIMODAL_ALLOWED_MIME_TYPES`
- `MULTIMODAL_MAX_ARTIFACT_BYTES`
- `MULTIMODAL_MAX_RESULTS`
- `MULTIMODAL_OUTPUT_DIMENSIONALITY`

The default dimensionality is 768 to control cost, latency, and storage while
keeping the model configurable.

## MCP Research

MCP external retrieval is configured through environment variables:

- `MCP_RESEARCH_URL` for Streamable HTTP MCP
- `MCP_RESEARCH_COMMAND` for stdio MCP, for example `npx -y @modelcontextprotocol/server-fetch`
- `MCP_RESEARCH_CWD` optionally sets the stdio server working directory

When MCP is present, the graph routes through `mcp_research_agent`, a cheap-model agent with `McpToolset`. When MCP is absent, tests and local development still run deterministically using artifact/session evidence.

## Model Routing

Routing is Python-side in `app/router.py`.

- Cheap model: `MUTUAL_SPEC_CHEAP_MODEL`, default `gemini-flash-latest`
- Strong model: `MUTUAL_SPEC_STRONG_MODEL`, default `gemini-pro-latest`
- Verifier model: `MUTUAL_SPEC_VERIFIER_MODEL`, default `gemini-pro-latest`

The workflow records a `RouteRecord` for each stage. Low-risk extraction, classification, and retrieval summarization use the cheap route. Failed verification or revision loops escalate drafting to the strong route. Verification always records the verifier route.

## Region-Aware Routing Telemetry

The current router selects a model class. The next routing dimension is Google
Cloud region. This should be implemented as a model-plus-region decision, not as
a claim that the agent can select an individual physical data center.

The design skeleton is in `docs/design/gcp_compute_electricity_spread.md` with
supporting examples:

- `config/region_power_map.example.yaml`
- `sql/gcp_compute_billing_hourly.example.sql`
- `sql/compute_electricity_spread_view.example.sql`

The telemetry layer joins:

- Cloud Asset Inventory resource-change events
- Cloud Monitoring CPU/reservation/accelerator metrics
- Cloud Billing BigQuery export costs and usage
- Cloud Billing Pricing API or pricing export data
- regional wholesale electricity price proxies
- Google regional CFE/carbon context

The output is a compute-electricity spread proxy by region and hour. It is a
routing/scoring signal only. Google Cloud does not expose actual data-center
electricity prices or exact workload-level kWh, so the view must carry
confidence scores and coefficient assumptions.

Future route records can add:

- selected Google region
- regional spread proxy
- power proxy source
- mapping confidence
- latency/compliance constraints

This signal must not bypass verification, policy checks, deployment controls, or
any external settlement/execution rail.

## Verification

`app/verifiers.py` checks:

- missing material spec fields
- selected evidence not cited in the draft
- uncited external claims
- required trajectory stages
- unsafe requests involving phishing, malware, credential theft, or evasion

When `MODEL_ARMOR_TEMPLATE` or `MODEL_ARMOR_TEMPLATE_ID` is configured,
`app/model_armor.py` screens the latest user prompt with Google Cloud Model
Armor before the local MVP safety policy is applied.

Failed high-severity verification loops once through the graph and escalates synthesis. After one repair attempt, the agent finalizes with verifier findings instead of looping indefinitely.

## Observability

The ADK `App` can attach BigQuery Agent Analytics when:

- `BQ_ANALYTICS_ENABLED=true`
- `GOOGLE_CLOUD_PROJECT` is set
- optional `BQ_ANALYTICS_DATASET_ID`, `BQ_ANALYTICS_GCS_BUCKET`, and `BQ_ANALYTICS_CONNECTION_ID` are set

The Agent Runtime wrapper sets `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` and supports GCS-backed artifacts via `ARTIFACTS_GCS_BUCKET` or `LOGS_BUCKET_NAME`.

## Evaluation

Acceptance coverage is split across:

- `tests/test_acceptance.py` for deterministic pytest checks
- `tests/eval/evalsets/msg_mvp.evalset.json` for ADK/Agents CLI golden cases
- `tests/eval/eval_config.json` for required acceptance metric names and thresholds

The tests cover clarification behavior, tool/artifact trajectory, final response quality, hallucination control, safety, and routing escalation.
