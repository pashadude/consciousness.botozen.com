"""Axolver-inspired formalization tasks for Mutual Specification Game ledgers.

The shape mirrors the useful part of Axolver for this project:
problem + optional question + expected answer, plus a deterministic evaluator
that returns ``is_valid`` as 1, 0, or -1.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from app.spec_state import FormalizationRecord, SpecLedger, looks_like_trader_query

MAX_FORMALIZATION_RECORDS = 20


@dataclass(frozen=True)
class FormalizationExample:
    problem: dict[str, Any]
    question: str | None
    answer: dict[str, Any]


class FormalizationTask(ABC):
    name: str
    domain: str

    @abstractmethod
    def generate(self, ledger: SpecLedger) -> FormalizationExample:
        """Return the formal problem, query, and expected answer contract."""

    @abstractmethod
    def hypothesis(self, ledger: SpecLedger) -> dict[str, Any]:
        """Return the ledger-derived hypothesis to evaluate."""

    @abstractmethod
    def evaluate(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
        hypothesis: Mapping[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Return evaluation metrics with an ``is_valid`` key."""

    def encode_class_id(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
    ) -> int:
        return len(answer)

    def tokenize(self, problem: Mapping[str, Any], question: str | None) -> list[str]:
        tokens = ["TASK", self.name, "DOMAIN", self.domain]
        tokens.extend(tokenize_mapping(problem))
        if question:
            tokens.extend(["QUESTION", *tokenize_text(question)])
        return tokens


class GeneralSpecCompletionTask(FormalizationTask):
    name = "general_spec_completion"
    domain = "general"

    required_fields = ("goal", "audience", "output_format")

    def generate(self, ledger: SpecLedger) -> FormalizationExample:
        problem = {
            "expressed_query": ledger.expressed_query or ledger.user_request,
            "known_fields": {
                "goal": bool(ledger.goal),
                "audience": bool(ledger.audience),
                "output_format": bool(ledger.output_format),
            },
            "ambiguities": [item.field for item in ledger.ambiguities],
        }
        question = "Which material specification fields must be resolved before drafting?"
        answer = {
            "required_fields": list(self.required_fields),
            "rule": "clarify every missing material field before finalization",
        }
        return FormalizationExample(problem=problem, question=question, answer=answer)

    def hypothesis(self, ledger: SpecLedger) -> dict[str, Any]:
        return {
            "goal": ledger.goal,
            "audience": ledger.audience,
            "output_format": ledger.output_format,
        }

    def evaluate(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
        hypothesis: Mapping[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        if not problem.get("expressed_query"):
            return {"is_valid": -1, "missing_obligations": ["expressed_query"]}
        required = tuple(answer.get("required_fields", self.required_fields))
        missing = [field for field in required if not hypothesis.get(field)]
        satisfied = len(required) - len(missing)
        metrics.update(
            {
                "required_count": len(required),
                "satisfied_count": satisfied,
                "coverage": satisfied / len(required) if required else 1.0,
                "missing_obligations": missing,
            }
        )
        return {
            **metrics,
            "is_valid": 1 if not missing else 0,
            "missing_obligations": missing,
        }

    def encode_class_id(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
    ) -> int:
        known = problem.get("known_fields", {})
        missing_count = sum(1 for field in self.required_fields if not known.get(field))
        return 100 + missing_count


class TraderDecisionFrameTask(FormalizationTask):
    name = "trader_decision_frame"
    domain = "trader"

    obligations: ClassVar[dict[str, str]] = {
        "interpreted_thesis": "State the inferred trade, analysis, alert, or strategy thesis.",
        "instrument_mapping": "Resolve the commodity, spread, contract, or leg names.",
        "horizon_or_timeframe": "State horizon, date range, rebalance, or review cadence.",
        "data_source_contract": "Name allowed and required evidence sources with freshness/fidelity limits.",
        "risk_frame": "Separate market, basis, liquidity, sizing, and falsification risk.",
        "legal_logistics_counterparty_flags": "Surface legal, logistics, sanctions, route, and counterparty flags when relevant.",
        "falsification_triggers": "State what would invalidate the thesis or force a stop.",
        "decision_gate": "Keep the output in an explicit decision state.",
    }

    def generate(self, ledger: SpecLedger) -> FormalizationExample:
        query = ledger.expressed_query or ledger.user_request
        problem = {
            "expressed_query": query,
            "detected_instruments": extract_instruments(query),
            "detected_actions": extract_actions(query),
            "decision_gate": ledger.decision_gate,
        }
        question = "Which proof obligations must be satisfied before a trader-facing decision frame is ready?"
        answer = {
            "obligations": self.obligations,
            "hard_constraints": [
                "no broker order placement",
                "not a buy/sell recommendation",
                "trader retains decision ownership",
            ],
            "valid_decision_states": [
                "needs_more_info",
                "analysis_ready",
                "alert_ready",
                "decision_frame_ready",
            ],
        }
        return FormalizationExample(problem=problem, question=question, answer=answer)

    def hypothesis(self, ledger: SpecLedger) -> dict[str, Any]:
        query = ledger.expressed_query or ledger.user_request
        return {
            "goal": ledger.goal,
            "audience": ledger.audience,
            "output_format": ledger.output_format,
            "latent_intent_hypotheses": ledger.latent_intent_hypotheses,
            "evidence_contract": ledger.evidence_contract,
            "verification_conditions": ledger.verification_conditions,
            "constraints": ledger.constraints,
            "success_criteria": ledger.success_criteria,
            "assumptions": ledger.assumptions,
            "decision_gate": ledger.decision_gate,
            "detected_instruments": extract_instruments(query),
        }

    def evaluate(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
        hypothesis: Mapping[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        if not problem.get("expressed_query"):
            return {"is_valid": -1, "missing_obligations": ["expressed_query"]}

        checks = {
            "interpreted_thesis": bool(
                hypothesis.get("goal") or hypothesis.get("latent_intent_hypotheses")
            ),
            "instrument_mapping": bool(
                problem.get("detected_instruments") or hypothesis.get("detected_instruments")
            ),
            "horizon_or_timeframe": contains_any(
                hypothesis,
                ("horizon", "timeframe", "intraday", "next", "day", "week", "month", "m1", "q1"),
            ),
            "data_source_contract": contains_any(
                hypothesis,
                ("ibkr", "yahoo", "source", "freshness", "entitlement", "confidence"),
            ),
            "risk_frame": contains_any(
                hypothesis,
                ("risk", "basis", "liquidity", "sizing", "margin", "falsification"),
            ),
            "legal_logistics_counterparty_flags": contains_any(
                hypothesis,
                ("legal", "logistics", "sanctions", "route", "counterparty"),
            ),
            "falsification_triggers": contains_any(
                hypothesis,
                ("falsification", "invalidate", "kill-switch", "stop"),
            ),
            "decision_gate": hypothesis.get("decision_gate")
            in answer.get("valid_decision_states", ()),
        }
        missing = [name for name, passed in checks.items() if not passed]
        required_count = len(checks)
        satisfied_count = required_count - len(missing)
        metrics.update(
            {
                "required_count": required_count,
                "satisfied_count": satisfied_count,
                "coverage": satisfied_count / required_count,
                "missing_obligations": missing,
            }
        )
        return {
            **metrics,
            "is_valid": 1 if not missing else 0,
            "missing_obligations": missing,
        }

    def encode_class_id(
        self,
        problem: Mapping[str, Any],
        question: str | None,
        answer: Mapping[str, Any],
    ) -> int:
        instruments = problem.get("detected_instruments") or []
        actions = problem.get("detected_actions") or []
        return 200 + min(len(instruments), 9) * 10 + min(len(actions), 9)


FORMALIZATION_REGISTRY: dict[str, FormalizationTask] = {
    GeneralSpecCompletionTask.name: GeneralSpecCompletionTask(),
    TraderDecisionFrameTask.name: TraderDecisionFrameTask(),
}


def formalize_ledger(ledger: SpecLedger) -> FormalizationRecord:
    task = select_formalization_task(ledger)
    example = task.generate(ledger)
    hypothesis = task.hypothesis(ledger)
    metrics = task.evaluate(
        example.problem,
        example.question,
        example.answer,
        hypothesis,
        {},
    )
    record = FormalizationRecord(
        task_name=task.name,
        domain=task.domain,  # type: ignore[arg-type]
        problem=example.problem,
        question=example.question,
        answer=example.answer,
        hypothesis=hypothesis,
        tokens=task.tokenize(example.problem, example.question),
        is_valid=metrics.get("is_valid", -1),
        metrics={key: value for key, value in metrics.items() if key != "is_valid"},
        missing_obligations=list(metrics.get("missing_obligations", [])),
        class_id=task.encode_class_id(example.problem, example.question, example.answer),
    )
    ledger.formalization_records.append(record)
    ledger.formalization_records = ledger.formalization_records[-MAX_FORMALIZATION_RECORDS:]
    return record


def select_formalization_task(ledger: SpecLedger) -> FormalizationTask:
    text = ledger.expressed_query or ledger.user_request
    if looks_like_trader_query(text):
        return FORMALIZATION_REGISTRY[TraderDecisionFrameTask.name]
    return FORMALIZATION_REGISTRY[GeneralSpecCompletionTask.name]


def extract_instruments(text: str) -> list[str]:
    lower = text.lower()
    instruments: list[str] = []
    patterns = {
        "HO/RB": (r"\bho/rb\b", r"\bheating oil\b.*\brbob\b"),
        "RBOB": (r"\brbob\b", r"\brb\b"),
        "ULSD": (r"\bulsd\b", r"\bho\b"),
        "Brent": (r"\bbrent\b",),
        "WTI": (r"\bwti\b",),
        "crack spread": (r"\bcrack\b",),
        "basis spread": (r"\bbasis\b", r"\bspread\b"),
    }
    for label, regexes in patterns.items():
        if any(re.search(pattern, lower) for pattern in regexes):
            instruments.append(label)
    return dedupe(instruments)


def extract_actions(text: str) -> list[str]:
    lower = text.lower()
    actions: list[str] = []
    action_terms = {
        "risk": ("risk", "var", "exposure"),
        "arbitrage": ("arb", "arbitrage"),
        "route_check": ("route", "logistics", "turkey"),
        "counterparty_check": ("counterparty", "sanction", "legal"),
        "spread_analysis": ("spread", "basis", "bounce"),
    }
    for label, terms in action_terms.items():
        if any(term in lower for term in terms):
            actions.append(label)
    return dedupe(actions)


def contains_any(value: Any, needles: tuple[str, ...]) -> bool:
    text = stringify(value)
    return any(needle in text for needle in needles)


def stringify(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {stringify(item)}" for key, item in value.items()).lower()
    if isinstance(value, list | tuple | set):
        return " ".join(stringify(item) for item in value).lower()
    return str(value or "").lower()


def tokenize_mapping(mapping: Mapping[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key, value in sorted(mapping.items()):
        tokens.extend([f"FIELD:{key}", *tokenize_text(stringify(value))])
    return tokens


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_./-]+", text.lower())


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
