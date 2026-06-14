"""Small deterministic calculations for user-supplied market marks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.spec_state import SpecLedger, add_external_evidence

GALLONS_PER_BARREL = 42
NYMEX_PRODUCT_CONTRACT_GALLONS = 42_000
NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"


@dataclass(frozen=True)
class MarketMarks:
    leg_a: str
    leg_b: str
    price_a: float
    price_b: float
    unit: str
    multiplier: float | None = None
    timeframe: str | None = None
    brent_usd_per_bbl: float | None = None
    wti_usd_per_bbl: float | None = None


@dataclass(frozen=True)
class MarketMathFrame:
    marks: MarketMarks
    spread: float
    spread_bbl_equivalent: float | None = None
    contract_value_usd: float | None = None
    leg_a_brent_crack: float | None = None
    leg_b_brent_crack: float | None = None
    leg_a_wti_crack: float | None = None
    leg_b_wti_crack: float | None = None

    @property
    def risk_label(self) -> str:
        absolute = abs(self.spread)
        if self.marks.unit == "USD/gal":
            if absolute >= 0.30:
                return "elevated"
            if absolute >= 0.15:
                return "medium"
            return "low-to-medium"
        return "requires calibration"


def build_market_math_frame(text: str) -> MarketMathFrame | None:
    marks = extract_market_marks(text)
    if marks is None:
        return None
    spread = marks.price_a - marks.price_b
    spread_bbl = spread * GALLONS_PER_BARREL if marks.unit == "USD/gal" else None
    return MarketMathFrame(
        marks=marks,
        spread=spread,
        spread_bbl_equivalent=spread_bbl,
        contract_value_usd=spread * marks.multiplier if marks.multiplier else None,
        leg_a_brent_crack=crack(marks.price_a, marks.brent_usd_per_bbl, marks.unit),
        leg_b_brent_crack=crack(marks.price_b, marks.brent_usd_per_bbl, marks.unit),
        leg_a_wti_crack=crack(marks.price_a, marks.wti_usd_per_bbl, marks.unit),
        leg_b_wti_crack=crack(marks.price_b, marks.wti_usd_per_bbl, marks.unit),
    )


def extract_market_marks(text: str) -> MarketMarks | None:
    lower = text.lower()
    if any(token in lower for token in ("ho/rb", "heating oil", "rbob")):
        ho = extract_product_mark(text, ("heating oil", "ho"))
        rb = extract_product_mark(text, ("rbob gasoline", "rbob", "rb gasoline"))
        if ho is not None and rb is not None:
            return MarketMarks(
                leg_a="HO",
                leg_b="RB",
                price_a=ho,
                price_b=rb,
                unit="USD/gal",
                multiplier=NYMEX_PRODUCT_CONTRACT_GALLONS,
                timeframe=extract_timeframe(text),
                brent_usd_per_bbl=extract_crude_mark(text, ("brent crude", "brent")),
                wti_usd_per_bbl=extract_crude_mark(text, ("west texas intermediate", "wti")),
            )
    return None


def attach_user_market_marks(ledger: SpecLedger, frame: MarketMathFrame | None) -> None:
    if frame is None:
        return
    marks = frame.marks
    summary = (
        "User-supplied market marks: "
        f"{marks.leg_a}={format_number(marks.price_a)} {marks.unit}, "
        f"{marks.leg_b}={format_number(marks.price_b)} {marks.unit}"
    )
    if marks.brent_usd_per_bbl is not None:
        summary += f", Brent={format_number(marks.brent_usd_per_bbl)} USD/bbl"
    if marks.wti_usd_per_bbl is not None:
        summary += f", WTI={format_number(marks.wti_usd_per_bbl)} USD/bbl"
    if marks.timeframe:
        summary += f", timeframe={marks.timeframe}"
    add_external_evidence(
        ledger,
        source_type="user",
        title="User-supplied market marks",
        uri=f"user://market-marks/{marks.leg_a.lower()}-{marks.leg_b.lower()}",
        summary=summary,
        source_name="user_input",
        query=ledger.expressed_query or ledger.user_request,
        confidence="medium",
        used=True,
    )


def extract_product_mark(text: str, aliases: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for alias in aliases:
        match = re.search(
            rf"\$?\s*({NUMBER})\s*(?:/|per\s+)?gal(?:lon)?s?\s+(?:for|on)\s+{re.escape(alias)}\b",
            lower,
            re.IGNORECASE,
        )
        if match:
            return to_float(match.group(1))
    paired = re.search(
        rf"\b(?:heating oil|ho)\b.*?\b(?:rbob|rbob gasoline|rb gasoline)\b.*?"
        rf"\$?\s*({NUMBER}).*?\$?\s*({NUMBER})",
        lower,
        re.IGNORECASE,
    )
    if paired:
        return to_float(paired.group(1) if aliases[-1] == "ho" else paired.group(2))
    for alias in aliases:
        match = re.search(
            rf"\b{re.escape(alias)}\b[^$0-9]{{0,80}}\$?\s*({NUMBER})\s*(?:/|per\s+)?gal(?:lon)?s?",
            lower,
            re.IGNORECASE,
        )
        if match:
            return to_float(match.group(1))
    return None


def extract_crude_mark(text: str, aliases: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for alias in aliases:
        match = re.search(
            rf"\b{re.escape(alias)}\b[^$0-9]{{0,90}}\$?\s*({NUMBER})\s*(?:/|per\s+)?b(?:arrel|bl|bls)?",
            lower,
            re.IGNORECASE,
        )
        if match:
            return to_float(match.group(1))
    return None


def extract_timeframe(text: str) -> str | None:
    lower = text.lower()
    if re.search(r"\btoday\b", lower):
        return "today"
    match = re.search(r"\b(next\s+(?:day|week|month)|intraday|this\s+week|this\s+month)\b", lower)
    return match.group(1) if match else None


def crack(product_price: float, crude_usd_per_bbl: float | None, unit: str) -> float | None:
    if crude_usd_per_bbl is None or unit != "USD/gal":
        return None
    return product_price * GALLONS_PER_BARREL - crude_usd_per_bbl


def format_money(value: float | None, precision: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.{precision}f}"


def format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None
