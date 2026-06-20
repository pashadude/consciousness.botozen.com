# Operator Human Review Workflow

Human review is a separate gate from the user prompt. The trader can clarify or
endorse the shared specification in the prompt/alignment loop, but the operator
gate is controlled by the review buttons in the console.

## State Machine

```text
queued
  -> Start Review -> in_review
  -> Run Evidence Search -> in_review
  -> Approve Gate -> approved, only if hard obligations are clear
  -> Request Changes -> changes_requested
  -> Reject Frame -> rejected
```

The agent refuses approval when hard obligations remain open. This is intentional:
operator approval does not override missing evidence, verifier findings, artifact
inspection, or formalization gaps.

## Buttons

| Button | What it does | What it does not do |
| --- | --- | --- |
| Start Review | Marks the review packet as owned by the operator. | Does not clear evidence or approval conditions. |
| Run Evidence Search | Runs the configured trader source layer against the current evidence plan, attaches cited evidence to the ledger, and reruns formalization/verification state. | Does not guarantee evidence was found or sufficient. |
| Approve Gate | Approves the human-review gate only if hard obligations are already clear. | Does not execute a trade, broker a transaction, or waive missing evidence. |
| Request Changes | Sends the packet back to user/agent/tools with an operator note. | Does not discard the ledger. |
| Reject Frame | Rejects the current decision frame. | Does not prevent the user from submitting a corrected query. |

## Evidence Search

`Run Evidence Search` uses `TRADER_RAG_PROVIDER` unless
`CONSOLE_REVIEW_RAG_PROVIDER` is set. The review route is allowed to be slower
than the first console response:

```bash
CONSOLE_REVIEW_RAG_PROVIDER=spanner_rag,mcp
CONSOLE_REVIEW_RAG_MAX_QUERIES=6
CONSOLE_REVIEW_RAG_MAX_RESULTS=8
CONSOLE_REVIEW_RAG_TIMEOUT_SECONDS=10
CONSOLE_REVIEW_SPANNER_RAG_SEARCH_MODE=hybrid
```

For a high-stakes physical commodity offer, source agents should try to support:

- price benchmark and market context near the offer date
- product specification, grade, quantity tolerance, and inspection standard
- counterparty identity, title chain, documents, and payment terms
- port/loading terms, laycan, demurrage, freight, and insurance
- sanctions, compliance, route, bankability, and political risk
- resale path, buyer demand, hedge/proxy availability, and netback economics

If no relevant result passes the RAG relevance filter, the source layer returns
`empty` and the gate remains blocked. Empty is better than irrelevant evidence.

## Operator Rule

Typing "agree", "approved", or "looks good" into the user prompt does not approve
human review. Use the operator review buttons and leave a note with evidence,
waiver rationale, or reason for rejection.
