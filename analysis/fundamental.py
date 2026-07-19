from __future__ import annotations

from typing import TYPE_CHECKING

from analysis.xsection import FactorStats, blend_with_percentile
from data.models import ScoreResult

if TYPE_CHECKING:
    from analysis.relative import SectorStats

# Below this fraction of available metric weight, the score would rest on too
# little data — return neutral instead of pretending confidence.
_MIN_AVAILABLE_WEIGHT = 0.4

_RISK_WEIGHTS: dict[str, dict[str, float]] = {
    "aggressive": {
        "pe": 0.10, "eps": 0.25, "rev": 0.25, "de": 0.05, "div": 0.05,
        "roe": 0.10, "margin": 0.10, "fcf": 0.10,
    },
    "moderate": {
        "pe": 0.20, "eps": 0.15, "rev": 0.15, "de": 0.10, "div": 0.10,
        "roe": 0.10, "margin": 0.10, "fcf": 0.10,
    },
    "conservative": {
        "pe": 0.25, "eps": 0.05, "rev": 0.05, "de": 0.15, "div": 0.20,
        "roe": 0.10, "margin": 0.10, "fcf": 0.10,
    },
}


def _score_pe_relative(pe: float, sector_median: float, reasons: list[str]) -> float:
    ratio = pe / sector_median
    if ratio <= 0.6:
        label, score = "well below sector norm", 100.0
    elif ratio <= 0.85:
        label, score = "below sector norm", 80.0
    elif ratio <= 1.15:
        label, score = "near sector norm", 55.0
    elif ratio <= 1.5:
        label, score = "above sector norm", 30.0
    else:
        label, score = "far above sector norm", 10.0
    reasons.append(f"P/E {pe:.1f} vs sector median {sector_median:.1f} — {label}")
    return score


def _score_de_relative(de: float, sector_median: float, reasons: list[str]) -> float:
    ratio = de / sector_median if sector_median > 0 else float("inf")
    if ratio <= 0.5:
        label, score = "well below sector norm", 100.0
    elif ratio <= 0.8:
        label, score = "below sector norm", 80.0
    elif ratio <= 1.25:
        label, score = "near sector norm", 55.0
    elif ratio <= 2.0:
        label, score = "above sector norm", 30.0
    else:
        label, score = "far above sector norm", 10.0
    reasons.append(f"D/E {de:.2f} vs sector median {sector_median:.2f} — {label}")
    return score


# Growth stays on absolute bands (a sector where everyone shrinks shouldn't
# score shrinkage well), but gets a clamped nudge vs the sector median.
_GROWTH_SECTOR_ADJUSTMENT = 10.0


def _sector_growth_adjustment(
    value: float, sector_median: float | None, label: str, reasons: list[str],
) -> float:
    if sector_median is None:
        return 0.0
    if value > sector_median:
        reasons.append(f"{label} above sector median ({sector_median:+.0%})")
        return _GROWTH_SECTOR_ADJUSTMENT
    if value < sector_median:
        reasons.append(f"{label} below sector median ({sector_median:+.0%})")
        return -_GROWTH_SECTOR_ADJUSTMENT
    return 0.0


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
    # Single-quarter YoY figure (Yahoo earningsQuarterlyGrowth) — noisy by nature
    if eps_growth > 0.25:
        reasons.append(f"EPS growth (qtr YoY) {eps_growth:+.0%} — strong")
        return 100.0
    if eps_growth > 0.10:
        reasons.append(f"EPS growth (qtr YoY) {eps_growth:+.0%} — solid")
        return 75.0
    if eps_growth > 0:
        reasons.append(f"EPS growth (qtr YoY) {eps_growth:+.0%} — positive")
        return 50.0
    if eps_growth > -0.10:
        reasons.append(f"EPS growth (qtr YoY) {eps_growth:+.0%} — slight decline")
        return 25.0
    reasons.append(f"EPS growth (qtr YoY) {eps_growth:+.0%} — declining")
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


def _score_roe(roe: float, reasons: list[str] | None = None) -> float:
    if roe > 0.25:
        score, label = 90.0, "excellent capital efficiency"
    elif roe > 0.15:
        score, label = 75.0, "strong"
    elif roe > 0.08:
        score, label = 55.0, "adequate"
    elif roe > 0:
        score, label = 35.0, "weak"
    else:
        score, label = 15.0, "destroying shareholder value"
    if reasons is not None:
        reasons.append(f"ROE {roe:.0%} — {label}")
    return score


def _score_margin_relative(
    gross_margin: float, sector_median: float, reasons: list[str] | None = None,
) -> float:
    # Margins are hugely sector-dependent (software ~80%, retail ~25%), so the
    # signal is the spread vs sector peers, not the level.
    diff = gross_margin - sector_median
    if diff >= 0.15:
        score, label = 90.0, "far above sector norm"
    elif diff >= 0.05:
        score, label = 70.0, "above sector norm"
    elif diff >= -0.05:
        score, label = 50.0, "near sector norm"
    elif diff >= -0.15:
        score, label = 30.0, "below sector norm"
    else:
        score, label = 15.0, "far below sector norm"
    if reasons is not None:
        reasons.append(
            f"Gross margin {gross_margin:.0%} vs sector median {sector_median:.0%} — {label}"
        )
    return score


def _score_profit_margin(profit_margin: float, reasons: list[str] | None = None) -> float:
    if profit_margin > 0.20:
        score, label = 90.0, "highly profitable"
    elif profit_margin > 0.10:
        score, label = 70.0, "solid profitability"
    elif profit_margin > 0.03:
        score, label = 55.0, "modest profitability"
    elif profit_margin > 0:
        score, label = 40.0, "thin margins"
    else:
        score, label = 15.0, "unprofitable"
    if reasons is not None:
        reasons.append(f"Profit margin {profit_margin:.0%} — {label}")
    return score


def _score_fcf_yield(fcf_yield: float, reasons: list[str] | None = None) -> float:
    if fcf_yield > 0.06:
        score, label = 90.0, "strong cash generation for the price"
    elif fcf_yield > 0.03:
        score, label = 75.0, "healthy cash generation"
    elif fcf_yield > 0.01:
        score, label = 55.0, "modest cash generation"
    elif fcf_yield >= 0:
        score, label = 40.0, "minimal free cash flow"
    else:
        score, label = 20.0, "burning cash"
    if reasons is not None:
        reasons.append(f"FCF yield {fcf_yield:.1%} — {label}")
    return score


def quality_component_score(fundamentals: dict[str, float | None]) -> float | None:
    """Equal-weighted mean of the available quality sub-scores (ROE, profit
    margin, FCF yield) — tracked in snapshot components so quality's standalone
    predictive power is measurable."""
    scores: list[float] = []
    roe = fundamentals.get("roe")
    if roe is not None:
        scores.append(_score_roe(roe))
    profit_margin = fundamentals.get("profit_margin")
    if profit_margin is not None:
        scores.append(_score_profit_margin(profit_margin))
    fcf_yield = fundamentals.get("fcf_yield")
    if fcf_yield is not None:
        scores.append(_score_fcf_yield(fcf_yield))
    if not scores:
        return None
    return sum(scores) / len(scores)


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
    factor_stats: FactorStats | None = None,
) -> ScoreResult:
    w = _RISK_WEIGHTS.get(risk_profile, _RISK_WEIGHTS["moderate"])
    aggressive = risk_profile == "aggressive"

    reasons: list[str] = []
    weighted_sum = 0.0
    available_weight = 0.0

    pe = fundamentals.get("pe_ratio")
    forward_pe = fundamentals.get("forward_pe")
    eps_growth = fundamentals.get("eps_growth")
    rev_growth = fundamentals.get("revenue_growth")
    de_ratio = fundamentals.get("debt_to_equity")
    # dividend_yield: absence means "pays no dividend" — always scored
    div_yield = fundamentals.get("dividend_yield") or 0.0
    roe = fundamentals.get("roe")
    gross_margin = fundamentals.get("gross_margin")
    profit_margin = fundamentals.get("profit_margin")
    fcf_yield = fundamentals.get("fcf_yield")

    pe_median = sector_stats.get(sector, "pe_ratio") if sector_stats else None
    de_median = sector_stats.get(sector, "debt_to_equity") if sector_stats else None
    eps_median = sector_stats.get(sector, "eps_growth") if sector_stats else None
    rev_median = sector_stats.get(sector, "revenue_growth") if sector_stats else None
    margin_median = sector_stats.get(sector, "gross_margin") if sector_stats else None

    if pe is not None:
        if pe > 0 and pe_median is not None:
            pe_score = _score_pe_relative(pe, pe_median, reasons)
        else:
            pe_score = _score_pe(pe, aggressive, reasons)
        if pe > 0:
            pe_score = blend_with_percentile(pe_score, "pe_ratio", pe, factor_stats)
        weighted_sum += pe_score * w["pe"]
        available_weight += w["pe"]
    elif forward_pe is not None:
        # Absolute bands only: sector medians are trailing-basis, and mixing
        # bases within one comparison would corrupt the relative signal.
        reasons.append(f"Trailing P/E unavailable — using forward P/E {forward_pe:.1f}")
        weighted_sum += _score_pe(forward_pe, aggressive, reasons) * w["pe"]
        available_weight += w["pe"]
    else:
        reasons.append("P/E unavailable — not scored")

    if eps_growth is not None:
        eps_score = _score_eps_growth(eps_growth, reasons)
        eps_score += _sector_growth_adjustment(eps_growth, eps_median, "EPS growth", reasons)
        weighted_sum += min(max(eps_score, 0.0), 100.0) * w["eps"]
        available_weight += w["eps"]
    else:
        reasons.append("EPS growth unavailable — not scored")

    if rev_growth is not None:
        rev_score = _score_rev_growth(rev_growth, reasons)
        rev_score += _sector_growth_adjustment(rev_growth, rev_median, "Revenue growth", reasons)
        weighted_sum += min(max(rev_score, 0.0), 100.0) * w["rev"]
        available_weight += w["rev"]
    else:
        reasons.append("Revenue growth unavailable — not scored")

    if de_ratio is not None:
        if de_ratio > 0 and de_median is not None:
            de_score = _score_de_relative(de_ratio, de_median, reasons)
        else:
            de_score = _score_de(de_ratio, reasons)
        weighted_sum += de_score * w["de"]
        available_weight += w["de"]
    else:
        reasons.append("D/E unavailable — not scored")

    weighted_sum += _score_dividend(div_yield, reasons) * w["div"]
    available_weight += w["div"]

    if roe is not None:
        roe_score = blend_with_percentile(
            _score_roe(roe, reasons), "roe", roe, factor_stats,
        )
        weighted_sum += roe_score * w["roe"]
        available_weight += w["roe"]
    else:
        reasons.append("ROE unavailable — not scored")

    if gross_margin is not None and margin_median is not None:
        margin_score = _score_margin_relative(gross_margin, margin_median, reasons)
    elif profit_margin is not None:
        margin_score = _score_profit_margin(profit_margin, reasons)
    else:
        # Absolute gross-margin bands would be sector noise (software ~80%,
        # retail ~25%) — without a sector median or a profit margin, skip.
        margin_score = None
        reasons.append("Margins unavailable — not scored")
    if margin_score is not None:
        if gross_margin is not None:
            margin_score = blend_with_percentile(
                margin_score, "gross_margin", gross_margin, factor_stats,
            )
        weighted_sum += margin_score * w["margin"]
        available_weight += w["margin"]

    if fcf_yield is not None:
        fcf_score = blend_with_percentile(
            _score_fcf_yield(fcf_yield, reasons), "fcf_yield", fcf_yield, factor_stats,
        )
        weighted_sum += fcf_score * w["fcf"]
        available_weight += w["fcf"]
    else:
        reasons.append("FCF yield unavailable — not scored")

    if available_weight < _MIN_AVAILABLE_WEIGHT:
        reasons.append("Too few fundamentals available — score is neutral")
        return ScoreResult(50.0, reasons, completeness=available_weight)

    score = weighted_sum / available_weight
    return ScoreResult(min(max(score, 0.0), 100.0), reasons, completeness=available_weight)


def score_fundamental(fundamentals: dict[str, float | None]) -> ScoreResult:
    return score_fundamental_adjusted(fundamentals, "moderate")
