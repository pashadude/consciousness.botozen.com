# Mutual Specification Game

## Core Claim

The object of human-AI coordination is not the final answer. It is the evolving
shared specification of the task.

A user query is a lossy signal of a richer latent task. The agent should
therefore optimize for convergence between:

- the user's latent intent
- the expressed query
- an executable, verifiable specification

The final answer is downstream of that shared specification.

## Staged Game Composition

| Stage | Game type | Agent objective | Output |
| --- | --- | --- | --- |
| Elicitation | Cooperative partial-information game | Recover hidden task variables with minimal user burden. | Candidate intent hypotheses and ambiguity records. |
| Dialogue | Signaling and commitment game under asymmetric information | Convert vague signals into explicit commitments. | Accepted assumptions, constraints, success criteria, and deferrals. |
| Retrieval | Evidence-selection game | Choose sources that reduce specification uncertainty. | Evidence refs, source confidence, and known gaps. |
| Decision planning | Full-information graph game | Build a decision frame with budget, safety, and verification gates. | Tool plan, route plan, human decision preconditions. |
| Verification | Adversarial/checking game | Test whether the spec and answer satisfy the commitments. | Pass/fail findings, repair loop, or refusal. |

The agent should only move forward when the next stage has enough specification
to be meaningful. High-impact ambiguity should trigger clarification or a
bounded assumption, not silent action.

## Specification Ledger Contract

The shared state should track these fields explicitly:

| Field | Meaning |
| --- | --- |
| `expressed_query` | The user's literal request. |
| `latent_intent_hypotheses` | Plausible interpretations of what the user is trying to achieve. |
| `accepted_spec` | The current executable task specification. |
| `ambiguities` | Material unknowns, ranked by impact. |
| `commitments` | User-accepted assumptions, constraints, budgets, and success criteria. |
| `evidence_contract` | Sources required, allowed, missing, and disallowed. |
| `route_plan` | Model/tool/human-review routing decisions and rationale. |
| `verification_conditions` | What must be true before finalization or decision framing. |
| `decision_gate` | Whether the task needs more information, is analysis-ready, alert-ready, or decision-frame-ready. |

## Formalization Task Contract

The formalization layer borrows the useful shape from Axolver without importing
its training stack. Axolver tasks are organized around generated triples:

```text
problem, question, answer
```

and deterministic evaluation of a hypothesis with an `is_valid` result. For this
agent, the triple becomes:

```text
problem  = the current expressed query plus detected domain variables
question = the proof-obligation query the agent must answer
answer   = the required fields, constraints, and obligations for that task type
hyp      = the current spec ledger state
```

The local implementation lives in `app/formalization.py`. It provides a small
registry of formalization tasks and writes compact records into
`SpecLedger.formalization_records`.

Current tasks:

| Task | Domain | Purpose |
| --- | --- | --- |
| `general_spec_completion` | General | Check whether goal, audience, and output format are resolved before drafting. |
| `trader_decision_frame` | Trader | Check whether a compressed trading prompt has the obligations needed for a proof-carrying decision frame. |

Each record stores:

```text
task_name
domain
problem
question
answer
hypothesis
tokens
is_valid    # 1 valid, 0 incorrect/incomplete, -1 decoding/problem error
metrics
missing_obligations
class_id
```

This makes the specification game executable: the agent can say not only "the
spec seems incomplete," but exactly which formal obligation is missing. For
trader prompts, missing obligations keep the decision gate at `needs_more_info`.
If the ledger claims `decision_frame_ready` while the latest formalization is
invalid, the verifier treats that as a high-severity spec gap.

## Routing Across A Model Zoo

Routing is another game over scarce attention and compute. The router should
choose among small models, frontier models, tools, verifiers, Codex-like
executors, and human review according to:

- expected specification gain
- expected risk reduction
- cost
- latency
- user cognitive burden
- safety and policy constraints

This is a multicriteria decision, not a fixed weighted sum. Keep hard
constraints first, then use nondominated-frontier logic where tradeoffs matter.

## Trader Strategy Use Case

Traders are ideal users because their queries are compressed, high-stakes, and
ambiguous signals of latent strategies under incomplete information.

Use this mapping:

```text
theta = the real trading task
q     = what the trader said in chat or to the agent
s     = executable specification of the trade, analysis, alert, or strategy
```

The expressed query `q` is often extremely lossy:

```text
look at HO/RB arb
is there cheating oil?
Brent/WTI bounce?
can we route through Turkey?
give me risk on this spread
```

The hidden task `theta` may be one or more of:

- find whether a physical arbitrage exists
- verify a counterparty
- avoid sanctions or legal exposure
- estimate basis risk
- distinguish a trade idea from legal suicide
- explain a thesis quickly to a partner
- check whether the trader is self-confirming a desired trade

A request like "check the Brent spread idea" is not an order or an executable
task. It is a signal pointing at an underspecified strategy or analysis.

Before a trader can make a decision, the agent must reconstruct and verify:

- instrument universe and contract mapping
- side, hedge legs, and position convention
- horizon, rebalance time, and liquidity window
- signal formula and feature transforms
- data-source contract and freshness requirements
- physical, logistical, legal, sanctions, and counterparty constraints
- inventory, basis, venue, and capital constraints
- lookahead and leakage controls
- risk target, leverage, margin, and maximum lots
- backtest, live-sim, and transaction-cost validation
- assumptions, falsification triggers, and kill-switch conditions
- audit trail for data rows, simulations, source references, assumptions, and PnL calculations

The agent should not answer a trader's compressed prompt directly as a trade.
It should first converge the trade specification and surface what remains
unknown. The trader makes the decision.

## Trader Preference And Cognitive Debt

Traders have strong latent tasks, but their expressed preferences can be
misleading. A trader may say "I only want to check" while already being
emotionally committed to a position. A request for neutral analysis may really
be a request for confirmation.

Observed behavior is not the same as true welfare or true preference:

```text
clicks / engagement != value
confidence != correctness
short-horizon PnL != reasoning quality
```

A trader can be right by accident and reinforce a bad model. A trader can also
be wrong because of a tail event while the reasoning was sound. The agent should
therefore preserve the reasoning state, not just the answer.

A good trader output is proof-carrying:

- interpreted trade thesis
- assumption ledger
- source and freshness ledger
- legal, logistics, market, basis, and counterparty risk ledger
- required documents or data
- verification checklist
- falsification triggers
- position-sizing logic or risk frame
- decision frame the trader can explain to a partner

This avoids cognitive debt. The agent should not give an opaque "yes, attractive
spread" conclusion that removes ownership from the trader.

## Skill-Compatible Output

A powerful model can overproduce analysis the trader cannot carry forward:
complex macro structure, option Greeks, shipping routes, sanctions language,
refinery economics, or legal caveats. That can be worse than a simpler output if
the trader cannot verify or explain it.

Trader-facing output should be compatible with the user's next action:

- explainable to a partner
- checkable against named sources
- convertible into a trader-owned order, no-order, alert, or research decision
- explicit about fact, assumption, bet, and unknown
- clear about what would falsify the thesis
- short enough to preserve ownership, with detail available on demand

The agent is not an oracle. It is a compatible co-pilot that makes the trader's
decision state inspectable.

## Local Brent Strategy Pattern

The local repo at `/Users/pauldudko/VSProjects/brent_strategy` is the reference
pattern for trader-facing evidence and validation discipline. Use it as an
instruction source, not as code to import into this agent.

Observed source patterns:

| Source pattern | Local examples | Instruction for this agent |
| --- | --- | --- |
| IBKR market data and history | `pull_ibkr_energy_history.py`, `pull_ibkr_calendar_history.py` | Treat IBKR as market-data and futures-history evidence only. Do not place orders, manage broker execution, or present an order workflow. |
| Yahoo Finance reference data | `implied_volatility/src/build_iv_from_ovx.py` | Treat Yahoo as a free reference/proxy source. Label proxy transforms, cache raw data, and record calibration assumptions. |
| ClickHouse and JSONL audit | `clickhouse/`, `live_trading/data/*.jsonl`, `api/data.py` | Preserve append-only data, simulation, source, and PnL state so metrics are reproducible from stored rows. |
| Commodity data platforms | `Brent_features/`, `Enel/`, `improm_signal/data/DATA_SOURCES.md` | Record source entitlement, freshness, units, transforms, and whether the source is load-bearing. |
| Strategy validation | `wti_brent_spread_validate.py`, `spread_universe_backtest.py` | Require out-of-sample, bootstrap, transaction-cost, correlation, and small-sample checks before a decision frame is marked ready. |

## Evidence Source Contract

Every source used for trader tasks should be represented with:

```text
source_id
source_type
instrument_or_market
timestamp
freshness_window
entitlement_or_license_status
raw_reference
fields_used
transform_applied
lookahead_guard
confidence
known_limitations
```

For example, Yahoo OVX can be useful as a Brent implied-volatility proxy, but it
is not Brent options truth. It must carry the basis calibration and confidence
limits. IBKR can provide futures history and market data, but this agent should
not use IBKR for live order placement.

## Decision Gate

A decision frame must remain blocked until:

1. The executable trade spec is accepted or an assumption is explicitly marked.
2. Required source freshness is satisfied.
3. Instrument and contract mappings are resolved.
4. Risk sizing, lot caps, margin, and liquidity window are explicit.
5. Verification passes.
6. The output clearly separates fact, assumption, bet, and unknown.

Allowed decision states:

```text
needs_more_info
analysis_ready
alert_ready
decision_frame_ready
```

The default is `needs_more_info` for under-specified tasks. Even when the
decision frame is ready, the trader remains responsible for the final action.

## Demo Output Shape

For a commodity desk demo, the agent should turn a compressed prompt into:

1. Interpreted deal or analysis thesis.
2. Missing information.
3. Legal, logistics, sanctions, market, and counterparty risk flags.
4. Assumption ledger.
5. Required documents and data.
6. Verification checklist.
7. Falsification triggers and kill-switch conditions.
8. Go/no-go decision frame for the trader.

This is formal trade-spec elicitation and verification, not advice to buy or
sell.
