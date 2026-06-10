"""Multicriteria domination for model-region routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RouteCandidate:
    model: str
    region: str
    policy_allowed: bool
    model_quality_loss: float
    latency_loss: float
    all_resource_cost_loss: float
    compute_electricity_spread_loss: float
    carbon_context_loss: float
    policy_penalty: float = 0.0
    proxy_confidence_penalty: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.model}@{self.region}"


@dataclass(frozen=True)
class DominationEpsilons:
    model_quality_loss: float = 0.05
    latency_loss: float = 250.0
    all_resource_cost_loss: float = 0.01
    compute_electricity_spread_loss: float = 0.01
    carbon_context_loss: float = 0.01
    policy_penalty: float = 0.0
    proxy_confidence_penalty: float = 0.01


SOFT_CRITERIA = (
    "model_quality_loss",
    "latency_loss",
    "all_resource_cost_loss",
    "compute_electricity_spread_loss",
    "carbon_context_loss",
    "policy_penalty",
    "proxy_confidence_penalty",
)


def route_loss(candidate: RouteCandidate) -> float:
    """Scalar diagnostic route loss.

    The routing rule should use nondomination first. This sum is useful for
    unresolved ties, dashboards, and regression tests.
    """

    return (
        candidate.model_quality_loss
        + candidate.latency_loss
        + candidate.all_resource_cost_loss
        + candidate.compute_electricity_spread_loss
        + candidate.carbon_context_loss
        + candidate.policy_penalty
        + candidate.proxy_confidence_penalty
    )


def dominates(
    challenger: RouteCandidate,
    incumbent: RouteCandidate,
    *,
    epsilons: DominationEpsilons | None = None,
) -> bool:
    epsilons = epsilons or DominationEpsilons()
    if not challenger.policy_allowed:
        return False
    if challenger.policy_allowed and not incumbent.policy_allowed:
        return True

    no_worse = True
    strictly_better = False
    for criterion in SOFT_CRITERIA:
        challenger_value = float(getattr(challenger, criterion))
        incumbent_value = float(getattr(incumbent, criterion))
        epsilon = float(getattr(epsilons, criterion))
        if challenger_value > incumbent_value + epsilon:
            no_worse = False
            break
        if challenger_value + epsilon < incumbent_value:
            strictly_better = True
    return no_worse and strictly_better


def nondominated_candidates(
    candidates: list[RouteCandidate],
    *,
    epsilons: DominationEpsilons | None = None,
) -> list[RouteCandidate]:
    epsilons = epsilons or DominationEpsilons()
    allowed = [candidate for candidate in candidates if candidate.policy_allowed]
    if not allowed:
        return []
    frontier: list[RouteCandidate] = []
    for candidate in allowed:
        if any(
            dominates(other, candidate, epsilons=epsilons)
            for other in allowed
            if other is not candidate
        ):
            continue
        frontier.append(candidate)
    return sorted(frontier, key=lambda item: (route_loss(item), item.key))


def candidate_from_criteria_row(
    row: dict[str, Any],
    *,
    model: str,
    model_quality_loss: float = 0.0,
    latency_loss: float = 0.0,
    policy_allowed: bool = True,
    policy_penalty: float = 0.0,
) -> RouteCandidate:
    """Build a route candidate from `resource_region_criteria_by_hour` output."""

    return RouteCandidate(
        model=model,
        region=str(row.get("google_region") or row.get("region") or "unknown"),
        policy_allowed=policy_allowed,
        model_quality_loss=model_quality_loss,
        latency_loss=latency_loss,
        all_resource_cost_loss=float(row.get("all_resource_cost_criterion") or 0.0),
        compute_electricity_spread_loss=float(
            row.get("compute_electricity_spread_stress") or 0.0
        ),
        carbon_context_loss=float(row.get("carbon_context_penalty") or 0.0),
        policy_penalty=policy_penalty,
        proxy_confidence_penalty=float(
            row.get("confidence_penalty_criterion") or 0.0
        ),
    )

