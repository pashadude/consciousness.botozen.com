# Mutual Specification Game MVP Design Spec

## Objective

`mutual-spec-agent` is an ADK Python 2.x prototype for narrowing the gap between a user's latent task and an executable task specification. The agent treats task formation as a staged workflow and game, not a single chat response.

The coordination object is the evolving shared specification. The final answer
is downstream of that specification.

## Game Model

A user query is a lossy signal of a richer latent task. The system therefore
optimizes for convergence among:

- the user's latent intent
- the expressed query
- an executable, verifiable specification

The staged game composition is:

| Stage | Game type | Output |
| --- | --- | --- |
| Elicitation | Cooperative partial-information game. | Candidate intent hypotheses and high-impact ambiguities. |
| Dialogue | Signaling and commitment game under asymmetric information. | Accepted assumptions, constraints, success criteria, and deferrals. |
| Retrieval | Evidence-selection game. | Evidence references, source confidence, and known gaps. |
| Decision planning | Full-information graph game. | Tool plan, route plan, budgets, safety gates, and human decision preconditions. |
| Verification | Checking game. | Pass/fail findings, repair loop, or refusal. |

Routing across small models, frontier models, tools, verifiers, Codex-like
executors, and human review should be based on expected specification gain,
risk reduction, cost, latency, and user cognitive burden.

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

The intended spec-game extension is documented in
`docs/design/mutual_specification_game.md`. It adds explicit fields for
`expressed_query`, `latent_intent_hypotheses`, `commitments`,
`evidence_contract`, `route_plan`, `verification_conditions`, and
`decision_gate`.

The implementation also includes a local formalization layer inspired by
Axolver's task shape. It converts the ledger into deterministic
`problem/question/answer/hypothesis` records and evaluates each hypothesis with
an `is_valid` result plus missing obligations. This is implemented inside
`app/formalization.py`; the repo does not depend on Axolver's training stack.

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

## Trader Decision-State Instruction Layer

Traders are the primary high-value user class because their prompts are
compressed, high-stakes, and ambiguous signals of latent strategies under
incomplete information. The agent should reconstruct and verify the trade,
analysis, alert, or strategy specification. It should not execute and should not
replace the trader's decision. The formalization task should expose unresolved
proof obligations before a trader-facing frame can claim readiness.

Use this mapping:

```text
theta = the real trading task
q     = what the trader said in chat
s     = executable specification of the trade, analysis, alert, or strategy
```

The expressed query `q` can be extremely lossy. "Look at HO/RB arb" may mean:

- is there a physical arbitrage?
- is the counterparty clean?
- is this sanctions/legal suicide?
- where is the basis risk?
- what logistics or inventory constraint breaks the idea?
- can I explain this quickly to a partner?
- am I self-confirming a thesis I already want to trade?

For trader tasks, the executable specification must include:

- instrument universe and contract mapping
- side, hedge legs, position convention, and horizon
- rebalance time and liquidity window
- signal formula and feature transforms
- data-source contract, freshness requirements, and entitlement status
- physical, legal, sanctions, logistics, and counterparty risk flags
- lookahead and leakage controls
- risk target, leverage, margin, and maximum lots
- backtest, live-sim, transaction-cost, and correlation validation
- human decision state: `needs_more_info`, `analysis_ready`, `alert_ready`, or `decision_frame_ready`
- audit requirements for source rows, assumptions, simulations, risk checks, and PnL calculations

Use `/Users/pauldudko/VSProjects/brent_strategy` as the local instruction
pattern for trader-facing evidence and validation discipline:

- IBKR supplies market data and futures history for this agent. Do not place
  orders, manage broker execution, or expose an order workflow.
- Yahoo Finance can provide free reference/proxy data such as OVX. Proxy
  transforms must be labeled with calibration and confidence assumptions.
- ClickHouse and JSONL streams provide reproducible data, simulation, and PnL
  audit surfaces.
- Sparta, Improm, ZenPulsar, Refinitiv/Enel, EIA, and similar feeds must be
  tracked with freshness, units, transform, confidence, and lookahead controls.

The preferred output for trader tasks is proof-carrying:

- interpreted thesis
- missing information
- legal, logistics, market, basis, and counterparty risk ledger
- assumptions and source contract
- required documents or data
- verification checklist
- falsification triggers
- decision frame for the trader

## Region-Aware Multicriteria Domination

The current router selects a model class. The next routing dimension is Google
Cloud region. This should be implemented as a model-plus-region decision over a
multicriteria domination relation, not as a claim that the agent can select an
individual physical data center and not as a single weighted utility function.

The design skeleton is in `docs/design/gcp_compute_electricity_spread.md` with
supporting examples:

- `config/region_power_map.example.yaml`
- `sql/gcp_resource_billing_hourly.example.sql`
- `sql/resource_region_criteria_view.example.sql`

The telemetry layer joins:

- Cloud Asset Inventory resource-change events for supported asset types
- Cloud Monitoring CPU/reservation/accelerator metrics
- Cloud Billing BigQuery export costs and usage for all billable resources
- Cloud Billing Pricing API or pricing export data
- regional wholesale electricity price proxies
- Google regional CFE/carbon context

The output is a criteria vector by region and hour. The router should compare
model-region candidates with a partial order:

```text
candidate = (model, google_region)

criteria(candidate) = {
  policy_allowed,
  model_quality_risk,
  latency_ms,
  all_resource_cost,
  compute_electricity_spread_stress,
  carbon_context_penalty,
  proxy_confidence_penalty
}

A dominates B iff:
  A satisfies hard constraints
  A is no worse than B on every soft criterion within epsilon
  A is strictly better than B on at least one soft criterion
```

The router keeps the nondominated frontier first. A scalar score can be used
only as a secondary tie-breaker after domination leaves multiple candidates.

All billable resources contribute through `all_resource_cost`. Compute,
serverless compute, AI platform, GPU, and TPU usage also contribute through
`compute_electricity_spread_stress` because they have the strongest available
usage-to-kWh proxy. Storage, networking, BigQuery, and managed services stay in
the criteria vector through billing cost, region, carbon context, and confidence
penalties until stronger energy coefficients exist.

Google Cloud does not expose actual data-center electricity prices or exact
workload-level kWh, so the view must carry confidence scores and coefficient
assumptions.

Future route records can add:

- selected Google region
- all-resource regional cost
- nondominated-frontier rank
- compute-electricity spread proxy
- power proxy source
- mapping confidence
- latency/compliance constraints

This domination layer must not bypass verification, policy checks, deployment
controls, or any external settlement/execution rail.

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
