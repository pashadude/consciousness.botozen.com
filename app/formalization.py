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

from app.spec_state import (
    FormalizationRecord,
    SpecLedger,
    looks_like_trader_query,
    update_mutual_spec_game_state,
)

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


class MutualSpecificationGameTask(FormalizationTask):
    name = "mutual_specification_game"
    domain = "mutual_spec"

    obligations: ClassVar[dict[str, str]] = {
        "players": "Represent user, main agent, router, verifier, tools, and human reviewer as interacting players.",
        "staged_games": "Represent elicitation, dialogue/commitment, retrieval, execution graph, and verification as explicit games.",
        "latent_type_beliefs": "Maintain Harsanyi-style beliefs over hidden task types theta from query signal q.",
        "commitments": "Track accepted/proposed commitments rather than treating fluent output as agreement.",
        "claim_graph": "Transform response content into claims with support, dependencies, and verifier state.",
        "proof_obligations": "Condense unsupported claims, evidence gaps, formal gaps, and review gaps into verifier work items.",
        "equilibrium_diagnostics": "Expose multicriteria action dominance instead of silently choosing the next move.",
        "convergence_metric": "Score convergence between latent intent, expressed query, and executable specification.",
        "human_review_gate": "Build a reviewer packet for high-stakes or blocked specs before decision-ready output.",
        "skill_compatible_handoff": "Track what the user can verify and what handoff format preserves user agency.",
        "model_zoo_routing": "Route across models, tools, verifiers, async jobs, and human review by risk and spec gain.",
        "trader_gate": "Keep trader decisions blocked until evidence, risk, and proof obligations are satisfied.",
    }

    def generate(self, ledger: SpecLedger) -> FormalizationExample:
        problem = {
            "expressed_query": ledger.expressed_query or ledger.user_request,
            "players": [item.player_id for item in ledger.game_players],
            "game_states": [item.stage_id for item in ledger.game_states],
            "beliefs": [item.type_id for item in ledger.latent_type_beliefs],
            "claim_count": len(ledger.claim_graph),
            "human_review_required": ledger.human_review.required,
            "decision_gate": ledger.decision_gate,
        }
        question = "Which mechanics must exist for the Mutual Specification Game to be executable?"
        answer = {
            "obligations": self.obligations,
            "non_goals": [
                "do not reduce user intent to a scalar reward",
                "do not clear trader execution gates from model fluency alone",
                "do not treat Search Console/user approval as task welfare",
            ],
        }
        return FormalizationExample(problem=problem, question=question, answer=answer)

    def hypothesis(self, ledger: SpecLedger) -> dict[str, Any]:
        return {
            "players": [item.model_dump(mode="json") for item in ledger.game_players],
            "game_states": [item.model_dump(mode="json") for item in ledger.game_states],
            "latent_type_beliefs": [
                item.model_dump(mode="json") for item in ledger.latent_type_beliefs
            ],
            "commitments": [item.model_dump(mode="json") for item in ledger.commitments],
            "claim_graph": [item.model_dump(mode="json") for item in ledger.claim_graph],
            "proof_obligations": [
                item.model_dump(mode="json") for item in ledger.proof_obligations
            ],
            "equilibrium_diagnostics": ledger.equilibrium_diagnostics.model_dump(mode="json"),
            "human_review": ledger.human_review.model_dump(mode="json"),
            "skill_compatibility": ledger.skill_compatibility.model_dump(mode="json"),
            "spec_convergence": ledger.spec_convergence.model_dump(mode="json"),
            "route_history": [item.model_dump(mode="json") for item in ledger.route_history],
            "async_jobs": [item.model_dump(mode="json") for item in ledger.async_jobs],
            "verification_conditions": ledger.verification_conditions,
            "decision_gate": ledger.decision_gate,
            "success_criteria": ledger.success_criteria,
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
        player_ids = {item.get("player_id") for item in hypothesis.get("players", [])}
        stage_ids = {item.get("stage_id") for item in hypothesis.get("game_states", [])}
        belief_ids = {item.get("type_id") for item in hypothesis.get("latent_type_beliefs", [])}
        claim_graph = hypothesis.get("claim_graph", [])
        route_history = hypothesis.get("route_history", [])
        verification_conditions = stringify(hypothesis.get("verification_conditions", []))
        success_criteria = stringify(hypothesis.get("success_criteria", []))
        checks = {
            "players": {
                "user",
                "main_agent",
                "router",
                "verifier",
                "tool_layer",
                "human_reviewer",
            }.issubset(player_ids),
            "staged_games": {
                "elicitation",
                "dialogue_commitment",
                "retrieval",
                "execution_graph",
                "verification",
            }.issubset(stage_ids),
            "latent_type_beliefs": bool(belief_ids),
            "commitments": bool(hypothesis.get("commitments")),
            "claim_graph": bool(claim_graph),
            "proof_obligations": bool(hypothesis.get("proof_obligations") is not None),
            "equilibrium_diagnostics": bool(
                hypothesis.get("equilibrium_diagnostics", {}).get("recommended_action")
            ),
            "convergence_metric": bool(hypothesis.get("spec_convergence", {}).get("overall") is not None),
            "human_review_gate": bool(
                hypothesis.get("human_review", {}).get("assigned_player") == "human_reviewer"
            ),
            "skill_compatible_handoff": bool(
                hypothesis.get("skill_compatibility", {}).get("handoff_format")
            ),
            "model_zoo_routing": bool(route_history),
            "trader_gate": "trader" in success_criteria or "trader" in verification_conditions,
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
        return 300 + len(problem.get("players") or [])


FORMALIZATION_REGISTRY: dict[str, FormalizationTask] = {
    GeneralSpecCompletionTask.name: GeneralSpecCompletionTask(),
    TraderDecisionFrameTask.name: TraderDecisionFrameTask(),
    MutualSpecificationGameTask.name: MutualSpecificationGameTask(),
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
    update_mutual_spec_game_state(ledger)
    return record


def select_formalization_task(ledger: SpecLedger) -> FormalizationTask:
    text = ledger.expressed_query or ledger.user_request
    if is_mutual_specification_game_request(text):
        return FORMALIZATION_REGISTRY[MutualSpecificationGameTask.name]
    if looks_like_trader_query(text):
        return FORMALIZATION_REGISTRY[TraderDecisionFrameTask.name]
    return FORMALIZATION_REGISTRY[GeneralSpecCompletionTask.name]


def is_mutual_specification_game_request(text: str) -> bool:
    lower = text.lower()
    return "mutual specification" in lower or "specification game" in lower


def extract_instruments(text: str) -> list[str]:
    lower = text.lower()
    instruments: list[str] = []
    patterns = {
        "HO/RB": (r"\bho/rb\b", r"\bheating oil\b.*\brbob\b"),
        "RBOB": (r"\brbob\b", r"\brb\b"),
        "ULSD": (r"\bulsd\b", r"\bho\b"),
        "Brent": (r"\bbrent\b",),
        "WTI": (r"\bwti\b",),
        "sulfur": (r"\bsulfur\b", r"\bsulphur\b"),
        "FOB physical cargo": (r"\bfob\b", r"\bcargo\b", r"\btonne?s?\b", r"\bmt\b"),
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
        "physical_offer": ("offer", "fob", "cfr", "cif", "cargo", "ton", "tonne", "mt"),
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
