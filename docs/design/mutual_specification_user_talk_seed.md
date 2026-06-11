# Mutual Specification Game User Talk Seed

## Problem To Solve

Agent minimizes cognitive difference between the user or trader understanding of
the task and the query sent to the model interface.

## Solution

The Mutual Specification Game treats the object of human-AI coordination not as
the final answer, but as the evolving shared specification of the task.

A user's query is a lossy signal of a richer latent task. The agent should
therefore not optimize for the best answer to the prompt, but for convergence
between the user's latent intent, the expressed query, and an executable,
verifiable specification.

The system models this as a staged composition of games: elicitation as a
cooperative partial-information game, dialogue as a signaling and commitment
game under asymmetric information, and execution as a full-information graph
game with explicit success, verification, safety, and budget conditions.

The system routes work across a model zoo: small models, frontier models,
tools, verifiers, Codex-like executors, and human review. Routing is based on
expected specification gain, risk reduction, cost, latency, and user cognitive
burden.

Traders are ideal users because their queries are compressed, high-stakes, and
ambiguous signals of latent strategies under incomplete information. The
agent's job is not to answer the query directly, but to reconstruct, test, and
verify the trade specification before execution.

## Key Principles

### Game Theory Backbone

Vasin and Morozov: game theory, especially games of many persons. Use this for
the multi-agent view: user, main agent, router, verifier, Codex, tools, and
human reviewer are interacting players rather than one monolithic assistant.

Bayesian games, Harsanyi types, and signaling games are the mathematical
backbone for the theta to q to s model. The user's real task is a hidden type or
latent state, the query is a signal, and the agent updates beliefs before
choosing whether to ask, assume, propose, execute, or verify.

### Human-AI Collaboration

Peng, Garg, and Kleinberg's no-free-lunch result motivates that human-AI
complementarity cannot be obtained for free.

McIlroy-Young, Sen, Kleinberg, and Anderson motivate the separation between
superhuman ability and human compatibility.

Hamade, McIlroy-Young, Sen, Kleinberg, and Anderson motivate
skill-compatible AI: the output must not only be correct, but also compatible
with the user's ability to continue effectively.

Kleinberg, Mullainathan, and Raghavan motivate the anti-revealed-preference
argument: user behavior, engagement, acceptance, or fluent approval is not
automatically user welfare or true preference.

Kleinberg, Mehrotra, Saberi, and Velegkas motivate bounded-memory state:
Spec Ledger, Assumption Ledger, Claim Graph, Verification State, and User
Endorsement State.

### Alignment Lineage

Leike et al.'s AI Safety Gridworlds, Krakovna et al.'s Specification Gaming,
and scalable reward modeling work motivate the failure mode where an agent
optimizes the wrong specification. The difference here is that intention is not
collapsed into a scalar reward; it is represented as an inspectable executable
specification.

### Formalization And Verification

Lean, Coq, Curry-Howard, propositions-as-types, Voevodsky, Univalent
Foundations, and proof assistants motivate the proof-carrying response pattern:
answer plus specification ledger, assumption ledger, verification trace, and
claim dependency structure.

The north-star reference is temporary: Lattice Deduction Transformers,
https://arxiv.org/pdf/2605.08605.
