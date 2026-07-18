from __future__ import annotations

from typing import TYPE_CHECKING

from data.models import ScoreResult

if TYPE_CHECKING:
    from analysis.relative import SectorStats

# Below this fraction of available metric weight, the score would rest on too
# little data — return neutral instead of pretending confidence.
_MIN_AVAILABLE_WEIGHT = 0.4

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


def _score_pe(pe: float, aggressive: bool, reasons: list[str]) -> float:
    if pe <= 0:
        reasons.append(f"P/E {pe:.1f} — negative earnings")
        return 20.0
    if pe < 15:
        reasons.append(f"P/E {pe:.1f} — undervalued")
        return 100.0
    if pe < 20:
        reasons.append(f"P/E {pe:.1f} — fairly valued")
        return 75.0
    if pe < 30:
        reasons.append(f"P/E {pe:.1f} — moderately valued")
        return 65.0 if aggressive else 50.0
    if pe < 50:
        reasons.append(f"P/E {pe:.1f} — expensive")
        return 40.0 if aggressive else 25.0
    reasons.append(f"P/E {pe:.1f} — very expensive")
    return 5.0


def _score_eps_growth(eps_growth: float, reasons: list[str]) -> float:
    if eps_growth > 0.25:
        reasons.append(f"EPS growth {eps_growth:+.0%} — strong")
        return 100.0
    if eps_growth > 0.10:
        reasons.append(f"EPS growth {eps_growth:+.0%} — solid")
        return 75.0
    if eps_growth > 0:
        reasons.append(f"EPS growth {eps_growth:+.0%} — positive")
        return 50.0
    if eps_growth > -0.10:
        reasons.append(f"EPS growth {eps_growth:+.0%} — slight decline")
        return 25.0
    reasons.append(f"EPS growth {eps_growth:+.0%} — declining")
    return 0.0


def _score_rev_growth(rev_growth: float, reasons: list[str]) -> float:
    if rev_growth > 0.20:
        reasons.append(f"Revenue growth {rev_growth:+.0%} — strong")
        return 100.0
    if rev_growth > 0.10:
        reasons.append(f"Revenue growth {rev_growth:+.0%} — solid")
        return 75.0
    if rev_growth > 0:
        reasons.append(f"Revenue growth {rev_growth:+.0%} — positive")
        return 50.0
    if rev_growth > -0.05:
        reasons.append(f"Revenue growth {rev_growth:+.0%} — flat/slight decline")
        return 25.0
    reasons.append(f"Revenue growth {rev_growth:+.0%} — declining")
    return 0.0


def _score_de(de_ratio: float, reasons: list[str]) -> float:
    if de_ratio < 0:
        reasons.append(f"D/E {de_ratio:.2f} — negative equity")
        return 25.0
    if de_ratio < 0.3:
        reasons.append(f"D/E {de_ratio:.2f} — very low debt")
        return 100.0
    if de_ratio < 0.7:
        reasons.append(f"D/E {de_ratio:.2f} — healthy debt level")
        return 75.0
    if de_ratio < 1.5:
        reasons.append(f"D/E {de_ratio:.2f} — moderate debt")
        return 50.0
    if de_ratio < 3.0:
        reasons.append(f"D/E {de_ratio:.2f} — high debt")
        return 25.0
    reasons.append(f"D/E {de_ratio:.2f} — very high debt")
    return 0.0


def _score_dividend(div_yield: float, reasons: list[str]) -> float:
    if div_yield > 0.04:
        reasons.append(f"Dividend yield {div_yield:.1%} — strong income")
        return 100.0
    if div_yield > 0.02:
        reasons.append(f"Dividend yield {div_yield:.1%} — decent income")
        return 75.0
    if div_yield > 0.01:
        return 50.0
    if div_yield > 0:
        return 35.0
    return 25.0


def score_fundamental_adjusted(
    fundamentals: dict[str, float | None],
    risk_profile: str = "moderate",
    sector_stats: SectorStats | None = None,
    sector: str = "Unknown",
) -> ScoreResult:
    w = _RISK_WEIGHTS.get(risk_profile, _RISK_WEIGHTS["moderate"])
    aggressive = risk_profile == "aggressive"

    reasons: list[str] = []
    weighted_sum = 0.0
    available_weight = 0.0

    pe = fundamentals.get("pe_ratio")
    eps_growth = fundamentals.get("eps_growth")
    rev_growth = fundamentals.get("revenue_growth")
    de_ratio = fundamentals.get("debt_to_equity")
    # dividend_yield: absence means "pays no dividend" — always scored
    div_yield = fundamentals.get("dividend_yield") or 0.0

    if pe is not None:
        weighted_sum += _score_pe(pe, aggressive, reasons) * w["pe"]
        available_weight += w["pe"]
    else:
        reasons.append("P/E unavailable — not scored")

    if eps_growth is not None:
        weighted_sum += _score_eps_growth(eps_growth, reasons) * w["eps"]
        available_weight += w["eps"]
    else:
        reasons.append("EPS growth unavailable — not scored")

    if rev_growth is not None:
        weighted_sum += _score_rev_growth(rev_growth, reasons) * w["rev"]
        available_weight += w["rev"]
    else:
        reasons.append("Revenue growth unavailable — not scored")

    if de_ratio is not None:
        weighted_sum += _score_de(de_ratio, reasons) * w["de"]
        available_weight += w["de"]
    else:
        reasons.append("D/E unavailable — not scored")

    weighted_sum += _score_dividend(div_yield, reasons) * w["div"]
    available_weight += w["div"]

    if available_weight < _MIN_AVAILABLE_WEIGHT:
        reasons.append("Too few fundamentals available — score is neutral")
        return ScoreResult(50.0, reasons, completeness=available_weight)

    score = weighted_sum / available_weight
    return ScoreResult(min(max(score, 0.0), 100.0), reasons, completeness=available_weight)


def score_fundamental(fundamentals: dict[str, float | None]) -> ScoreResult:
    return score_fundamental_adjusted(fundamentals, "moderate")
