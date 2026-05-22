from __future__ import annotations


_ETF_RISK_WEIGHTS: dict[str, dict[str, float]] = {
    "aggressive": {
        "expense": 0.10, "returns": 0.35, "vs_category": 0.20,
        "concentration": 0.10, "aum": 0.10, "yield": 0.15,
    },
    "moderate": {
        "expense": 0.20, "returns": 0.25, "vs_category": 0.15,
        "concentration": 0.15, "aum": 0.10, "yield": 0.15,
    },
    "conservative": {
        "expense": 0.25, "returns": 0.15, "vs_category": 0.10,
        "concentration": 0.15, "aum": 0.10, "yield": 0.25,
    },
}

_RETURNS_BLEND: dict[str, tuple[float, float, float]] = {
    "aggressive": (0.60, 0.25, 0.15),
    "moderate": (0.40, 0.30, 0.30),
    "conservative": (0.20, 0.30, 0.50),
}


def _score_expense_ratio(ratio: float) -> tuple[float, str]:
    pct = ratio * 100
    if ratio <= 0.0005:
        return 100.0, f"Expense ratio {pct:.2f}% — ultra-low cost"
    if ratio <= 0.001:
        return 90.0, f"Expense ratio {pct:.2f}% — very low cost"
    if ratio <= 0.002:
        return 75.0, f"Expense ratio {pct:.2f}% — low cost"
    if ratio <= 0.005:
        return 50.0, f"Expense ratio {pct:.2f}% — moderate cost"
    if ratio <= 0.01:
        return 25.0, f"Expense ratio {pct:.2f}% — expensive"
    return 5.0, f"Expense ratio {pct:.2f}% — very expensive"


def _score_blended_returns(
    one_yr: float, three_yr: float, five_yr: float,
    blend: tuple[float, float, float],
) -> tuple[float, str]:
    blended = blend[0] * one_yr + blend[1] * three_yr + blend[2] * five_yr
    pct = blended * 100

    if blended > 0.20:
        score = 100.0
    elif blended > 0.12:
        score = 80.0
    elif blended > 0.07:
        score = 60.0
    elif blended > 0.0:
        score = 40.0
    else:
        score = 10.0

    return score, f"Blended return {pct:+.1f}%"


def _score_vs_category(delta: float) -> tuple[float, str]:
    pct = delta * 100
    if delta > 0.05:
        return 100.0, f"vs category {pct:+.1f}% — strong outperformance"
    if delta > 0.02:
        return 80.0, f"vs category {pct:+.1f}% — outperforming"
    if delta > 0.0:
        return 60.0, f"vs category {pct:+.1f}% — slightly above"
    if delta > -0.02:
        return 40.0, f"vs category {pct:+.1f}% — slightly below"
    return 15.0, f"vs category {pct:+.1f}% — underperforming"


def _score_concentration(top10: float) -> tuple[float, str]:
    pct = top10 * 100
    if top10 < 0.20:
        return 90.0, f"Top-10 holdings {pct:.0f}% — well diversified"
    if top10 < 0.35:
        return 70.0, f"Top-10 holdings {pct:.0f}% — moderately diversified"
    if top10 < 0.50:
        return 50.0, f"Top-10 holdings {pct:.0f}% — moderate concentration"
    if top10 < 0.70:
        return 35.0, f"Top-10 holdings {pct:.0f}% — concentrated"
    return 20.0, f"Top-10 holdings {pct:.0f}% — highly concentrated"


def _score_aum(total_assets: float) -> tuple[float, str]:
    if total_assets > 100_000_000_000:
        return 100.0, f"AUM ${total_assets / 1e9:.0f}B — very large fund"
    if total_assets > 10_000_000_000:
        return 85.0, f"AUM ${total_assets / 1e9:.0f}B — large fund"
    if total_assets > 1_000_000_000:
        return 70.0, f"AUM ${total_assets / 1e9:.1f}B — mid-size fund"
    if total_assets > 100_000_000:
        return 50.0, f"AUM ${total_assets / 1e6:.0f}M — small fund"
    return 25.0, f"AUM ${total_assets / 1e6:.0f}M — very small fund"


def _score_yield(dividend_yield: float) -> tuple[float, str]:
    pct = dividend_yield * 100
    if dividend_yield > 0.04:
        return 100.0, f"Yield {pct:.1f}% — strong income"
    if dividend_yield > 0.02:
        return 75.0, f"Yield {pct:.1f}% — decent income"
    if dividend_yield > 0.01:
        return 50.0, f"Yield {pct:.1f}% — modest"
    if dividend_yield > 0.0:
        return 35.0, f"Yield {pct:.2f}% — very low"
    return 25.0, "No distribution yield"


def score_etf_fundamental(
    fundamentals: dict[str, float],
    risk_profile: str = "moderate",
) -> tuple[float, list[str]]:
    weights = _ETF_RISK_WEIGHTS.get(risk_profile, _ETF_RISK_WEIGHTS["moderate"])
    blend = _RETURNS_BLEND.get(risk_profile, _RETURNS_BLEND["moderate"])
    reasons: list[str] = []

    expense_score, expense_reason = _score_expense_ratio(
        fundamentals.get("expense_ratio", 0.0),
    )
    reasons.append(expense_reason)

    returns_score, returns_reason = _score_blended_returns(
        fundamentals.get("one_year_return", 0.0),
        fundamentals.get("three_year_return", 0.0),
        fundamentals.get("five_year_return", 0.0),
        blend,
    )
    reasons.append(returns_reason)

    vs_cat_score, vs_cat_reason = _score_vs_category(
        fundamentals.get("one_year_return_vs_cat", 0.0),
    )
    reasons.append(vs_cat_reason)

    conc_score, conc_reason = _score_concentration(
        fundamentals.get("top10_concentration", 0.0),
    )
    reasons.append(conc_reason)

    aum_score, aum_reason = _score_aum(
        fundamentals.get("total_assets", 0.0),
    )
    reasons.append(aum_reason)

    yield_score, yield_reason = _score_yield(
        fundamentals.get("dividend_yield", 0.0),
    )
    reasons.append(yield_reason)

    total = (
        weights["expense"] * expense_score
        + weights["returns"] * returns_score
        + weights["vs_category"] * vs_cat_score
        + weights["concentration"] * conc_score
        + weights["aum"] * aum_score
        + weights["yield"] * yield_score
    )

    return min(max(total, 0.0), 100.0), reasons
