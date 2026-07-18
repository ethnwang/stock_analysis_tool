from __future__ import annotations

from data.models import ScoreResult

# Below this fraction of available metric weight, the score would rest on too
# little data — return neutral instead of pretending confidence.
_MIN_AVAILABLE_WEIGHT = 0.4

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
    one_yr: float | None, three_yr: float | None, five_yr: float | None,
    blend: tuple[float, float, float],
) -> tuple[float, str] | None:
    # Blend over whichever horizons are present, renormalizing their weights.
    parts = [(r, b) for r, b in zip((one_yr, three_yr, five_yr), blend) if r is not None]
    if not parts:
        return None
    blend_total = sum(b for _, b in parts)
    blended = sum(r * b for r, b in parts) / blend_total
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
    fundamentals: dict[str, float | None],
    risk_profile: str = "moderate",
) -> ScoreResult:
    weights = _ETF_RISK_WEIGHTS.get(risk_profile, _ETF_RISK_WEIGHTS["moderate"])
    blend = _RETURNS_BLEND.get(risk_profile, _RETURNS_BLEND["moderate"])
    reasons: list[str] = []
    weighted_sum = 0.0
    available_weight = 0.0

    expense = fundamentals.get("expense_ratio")
    if expense is not None:
        score, reason = _score_expense_ratio(expense)
        weighted_sum += score * weights["expense"]
        available_weight += weights["expense"]
        reasons.append(reason)
    else:
        reasons.append("Expense ratio unavailable — not scored")

    returns_result = _score_blended_returns(
        fundamentals.get("one_year_return"),
        fundamentals.get("three_year_return"),
        fundamentals.get("five_year_return"),
        blend,
    )
    if returns_result is not None:
        score, reason = returns_result
        weighted_sum += score * weights["returns"]
        available_weight += weights["returns"]
        reasons.append(reason)
    else:
        reasons.append("Return history unavailable — not scored")

    vs_cat = fundamentals.get("one_year_return_vs_cat")
    if vs_cat is not None:
        score, reason = _score_vs_category(vs_cat)
        weighted_sum += score * weights["vs_category"]
        available_weight += weights["vs_category"]
        reasons.append(reason)
    else:
        reasons.append("Category comparison unavailable — not scored")

    top10 = fundamentals.get("top10_concentration")
    if top10 is not None:
        score, reason = _score_concentration(top10)
        weighted_sum += score * weights["concentration"]
        available_weight += weights["concentration"]
        reasons.append(reason)
    else:
        reasons.append("Holdings data unavailable — not scored")

    total_assets = fundamentals.get("total_assets")
    if total_assets is not None:
        score, reason = _score_aum(total_assets)
        weighted_sum += score * weights["aum"]
        available_weight += weights["aum"]
        reasons.append(reason)
    else:
        reasons.append("AUM unavailable — not scored")

    # dividend_yield: absence means "no distributions" — always scored
    score, reason = _score_yield(fundamentals.get("dividend_yield") or 0.0)
    weighted_sum += score * weights["yield"]
    available_weight += weights["yield"]
    reasons.append(reason)

    if available_weight < _MIN_AVAILABLE_WEIGHT:
        reasons.append("Too few ETF metrics available — score is neutral")
        return ScoreResult(50.0, reasons, completeness=available_weight)

    total = weighted_sum / available_weight
    return ScoreResult(min(max(total, 0.0), 100.0), reasons, completeness=available_weight)
