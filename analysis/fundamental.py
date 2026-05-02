from __future__ import annotations


def score_fundamental(fundamentals: dict[str, float]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    pe = fundamentals.get("pe_ratio", 0.0)
    eps_growth = fundamentals.get("eps_growth", 0.0)
    rev_growth = fundamentals.get("revenue_growth", 0.0)
    de_ratio = fundamentals.get("debt_to_equity", 0.0)
    div_yield = fundamentals.get("dividend_yield", 0.0)

    # P/E Ratio (25% weight) — lower is better, but negative means unprofitable
    if pe <= 0:
        pe_score = 20.0
        reasons.append(f"P/E {pe:.1f} — negative earnings")
    elif pe < 15:
        pe_score = 100.0
        reasons.append(f"P/E {pe:.1f} — undervalued")
    elif pe < 20:
        pe_score = 75.0
        reasons.append(f"P/E {pe:.1f} — fairly valued")
    elif pe < 30:
        pe_score = 50.0
        reasons.append(f"P/E {pe:.1f} — moderately valued")
    elif pe < 50:
        pe_score = 25.0
        reasons.append(f"P/E {pe:.1f} — expensive")
    else:
        pe_score = 5.0
        reasons.append(f"P/E {pe:.1f} — very expensive")
    score += pe_score * 0.25

    # EPS Growth (25% weight) — quarterly YoY
    if eps_growth > 0.25:
        eps_score = 100.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — strong")
    elif eps_growth > 0.10:
        eps_score = 75.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — solid")
    elif eps_growth > 0:
        eps_score = 50.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — positive")
    elif eps_growth > -0.10:
        eps_score = 25.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — slight decline")
    else:
        eps_score = 0.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — declining")
    score += eps_score * 0.25

    # Revenue Growth (20% weight)
    if rev_growth > 0.20:
        rev_score = 100.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — strong")
    elif rev_growth > 0.10:
        rev_score = 75.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — solid")
    elif rev_growth > 0:
        rev_score = 50.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — positive")
    elif rev_growth > -0.05:
        rev_score = 25.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — flat/slight decline")
    else:
        rev_score = 0.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — declining")
    score += rev_score * 0.20

    # Debt-to-Equity (15% weight) — lower is healthier
    if de_ratio <= 0:
        de_score = 50.0
        reasons.append(f"D/E {de_ratio:.2f} — no debt data")
    elif de_ratio < 0.3:
        de_score = 100.0
        reasons.append(f"D/E {de_ratio:.2f} — very low debt")
    elif de_ratio < 0.7:
        de_score = 75.0
        reasons.append(f"D/E {de_ratio:.2f} — healthy debt level")
    elif de_ratio < 1.5:
        de_score = 50.0
        reasons.append(f"D/E {de_ratio:.2f} — moderate debt")
    elif de_ratio < 3.0:
        de_score = 25.0
        reasons.append(f"D/E {de_ratio:.2f} — high debt")
    else:
        de_score = 0.0
        reasons.append(f"D/E {de_ratio:.2f} — very high debt")
    score += de_score * 0.15

    # Dividend Yield (15% weight) — bonus for income
    if div_yield > 0.04:
        div_score = 100.0
        reasons.append(f"Dividend yield {div_yield:.1%} — strong income")
    elif div_yield > 0.02:
        div_score = 75.0
        reasons.append(f"Dividend yield {div_yield:.1%} — decent income")
    elif div_yield > 0.01:
        div_score = 50.0
    elif div_yield > 0:
        div_score = 35.0
    else:
        div_score = 25.0
    score += div_score * 0.15

    return min(max(score, 0.0), 100.0), reasons


_RISK_WEIGHTS: dict[str, dict[str, float]] = {
    "aggressive": {
        "pe": 0.10, "eps": 0.35, "rev": 0.30, "de": 0.10, "div": 0.15,
    },
    "moderate": {
        "pe": 0.25, "eps": 0.25, "rev": 0.20, "de": 0.15, "div": 0.15,
    },
    "conservative": {
        "pe": 0.30, "eps": 0.15, "rev": 0.10, "de": 0.20, "div": 0.25,
    },
}


def score_fundamental_adjusted(
    fundamentals: dict[str, float],
    risk_profile: str = "moderate",
) -> tuple[float, list[str]]:
    w = _RISK_WEIGHTS.get(risk_profile, _RISK_WEIGHTS["moderate"])
    aggressive = risk_profile == "aggressive"

    score = 0.0
    reasons: list[str] = []

    pe = fundamentals.get("pe_ratio", 0.0)
    eps_growth = fundamentals.get("eps_growth", 0.0)
    rev_growth = fundamentals.get("revenue_growth", 0.0)
    de_ratio = fundamentals.get("debt_to_equity", 0.0)
    div_yield = fundamentals.get("dividend_yield", 0.0)

    if pe <= 0:
        pe_score = 20.0
        reasons.append(f"P/E {pe:.1f} — negative earnings")
    elif pe < 15:
        pe_score = 100.0
        reasons.append(f"P/E {pe:.1f} — undervalued")
    elif pe < 20:
        pe_score = 75.0
        reasons.append(f"P/E {pe:.1f} — fairly valued")
    elif pe < 30:
        pe_score = 65.0 if aggressive else 50.0
        reasons.append(f"P/E {pe:.1f} — moderately valued")
    elif pe < 50:
        pe_score = 40.0 if aggressive else 25.0
        reasons.append(f"P/E {pe:.1f} — expensive")
    else:
        pe_score = 5.0
        reasons.append(f"P/E {pe:.1f} — very expensive")
    score += pe_score * w["pe"]

    if eps_growth > 0.25:
        eps_score = 100.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — strong")
    elif eps_growth > 0.10:
        eps_score = 75.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — solid")
    elif eps_growth > 0:
        eps_score = 50.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — positive")
    elif eps_growth > -0.10:
        eps_score = 25.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — slight decline")
    else:
        eps_score = 0.0
        reasons.append(f"EPS growth {eps_growth:+.0%} — declining")
    score += eps_score * w["eps"]

    if rev_growth > 0.20:
        rev_score = 100.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — strong")
    elif rev_growth > 0.10:
        rev_score = 75.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — solid")
    elif rev_growth > 0:
        rev_score = 50.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — positive")
    elif rev_growth > -0.05:
        rev_score = 25.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — flat/slight decline")
    else:
        rev_score = 0.0
        reasons.append(f"Revenue growth {rev_growth:+.0%} — declining")
    score += rev_score * w["rev"]

    if de_ratio <= 0:
        de_score = 50.0
        reasons.append(f"D/E {de_ratio:.2f} — no debt data")
    elif de_ratio < 0.3:
        de_score = 100.0
        reasons.append(f"D/E {de_ratio:.2f} — very low debt")
    elif de_ratio < 0.7:
        de_score = 75.0
        reasons.append(f"D/E {de_ratio:.2f} — healthy debt level")
    elif de_ratio < 1.5:
        de_score = 50.0
        reasons.append(f"D/E {de_ratio:.2f} — moderate debt")
    elif de_ratio < 3.0:
        de_score = 25.0
        reasons.append(f"D/E {de_ratio:.2f} — high debt")
    else:
        de_score = 0.0
        reasons.append(f"D/E {de_ratio:.2f} — very high debt")
    score += de_score * w["de"]

    if div_yield > 0.04:
        div_score = 100.0
        reasons.append(f"Dividend yield {div_yield:.1%} — strong income")
    elif div_yield > 0.02:
        div_score = 75.0
        reasons.append(f"Dividend yield {div_yield:.1%} — decent income")
    elif div_yield > 0.01:
        div_score = 50.0
    elif div_yield > 0:
        div_score = 35.0
    else:
        div_score = 25.0
    score += div_score * w["div"]

    return min(max(score, 0.0), 100.0), reasons
