from app.market_math import build_market_math_frame


def test_market_math_extracts_ho_rb_supplied_marks() -> None:
    frame = build_market_math_frame(
        "today Heating Oil (HO) and RBOB Gasoline (RB) sits at roughly "
        "$3.40 per gallon for Heating Oil and $3.05 per gallon for RBOB Gasoline, "
        "while Brent Crude is $87.33 per barrel and WTI is $84.88 per barrel"
    )

    assert frame is not None
    assert frame.marks.leg_a == "HO"
    assert frame.marks.leg_b == "RB"
    assert frame.marks.timeframe == "today"
    assert round(frame.spread, 4) == 0.35
    assert round(frame.spread_bbl_equivalent or 0, 2) == 14.70
    assert round(frame.contract_value_usd or 0, 0) == 14700
    assert round(frame.leg_a_brent_crack or 0, 2) == 55.47
    assert round(frame.leg_b_wti_crack or 0, 2) == 43.22
