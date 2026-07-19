from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from analysis.etf_fundamental import score_etf_fundamental
from analysis.fundamental import quality_component_score, score_fundamental_adjusted
from analysis.relative import SectorStats, build_sector_stats
from analysis.sentiment import score_sentiment
from analysis.sentiment_backends import SentimentBackend, get_backend
from analysis.technical import compute_indicators, score_momentum, score_technical
from analysis.xsection import (
    FactorStats,
    build_factor_stats,
    load_factor_stats,
    save_factor_stats,
    sentiment_offset,
)
from data.models import ScoredStock, ScoreResult
from portfolio.loader import suggest_account

if TYPE_CHECKING:
    from config import Config
    from data.models import StockData

logger = logging.getLogger(__name__)

OVERLAP_PENALTY = 5.0
# The overlap penalty tapers linearly: full below the taper start, zero at the
# Strong Buy cutoff — a cliff exactly at 80 would reorder ranks discontinuously.
OVERLAP_TAPER_START = 65.0
OVERLAP_TAPER_END = 80.0
SECTOR_OVERWEIGHT_THRESHOLD = 30.0
SECTOR_PENALTY = 3.0

MIN_PRICE_BARS = 30

# Batches at least this large rebuild and refresh the factor-stats cache;
# smaller batches prefer a fresh cached full-universe run instead. 75 sits
# safely above the 48-name watchlist and below the ~98-name sp500 fallback.
FACTOR_CACHE_MIN_N = 75

INSUFFICIENT_DATA_LABEL = "Insufficient Data"


def _recommendation_label(score: float) -> str:
    if score >= 80:
        return "Strong Buy"
    if score >= 65:
        return "Buy"
    if score >= 45:
        return "Hold"
    return "Avoid"


def _score_technical_safe(
    stock: StockData,
    factor_stats: FactorStats | None = None,
) -> tuple[ScoreResult, dict[str, Any] | None]:
    """Score the technical pillar; also return the raw indicators so callers
    can lift sub-signal values into ScoredStock.components without recomputing."""
    if len(stock.price_history) < MIN_PRICE_BARS:
        return ScoreResult(
            0.0,
            [f"Insufficient price history (<{MIN_PRICE_BARS} bars) — technical not scored"],
            completeness=0.0,
        ), None
    try:
        indicators = compute_indicators(stock.price_history)
        return score_technical(indicators, factor_stats), indicators
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        return ScoreResult(
            0.0, [f"Technical analysis failed ({exc}) — not scored"], completeness=0.0,
        ), None


def _composite_from_pillars(pillars: list[tuple[float, ScoreResult]]) -> float:
    """Blend pillar scores, dropping zero-completeness pillars and
    renormalizing over what remains; neutral 50 when nothing is scoreable."""
    active = [(w, p) for w, p in pillars if p.completeness > 0]
    if not active:
        return 50.0
    active_weight = sum(w for w, _ in active)
    return sum(w * p.score for w, p in active) / active_weight


def score_stock(
    stock: StockData,
    config: Config,
    held_tickers: dict[str, list[dict[str, Any]]] | None = None,
    roth_ira_maxed: bool = False,
    sector_stats: SectorStats | None = None,
    factor_stats: FactorStats | None = None,
    batch_sentiment_offset: float = 0.0,
    sentiment_backend: SentimentBackend | None = None,
    sentiment_result: ScoreResult | None = None,
) -> ScoredStock | None:
    is_etf = (stock.fundamentals.get("is_etf") or 0.0) > 0

    # ETFs are never blended against the stock universe's factor breakpoints
    tech, indicators = _score_technical_safe(
        stock, None if is_etf else factor_stats,
    )

    components: dict[str, float] = {}
    realized_vol: float | None = None
    if indicators is not None:
        mom = indicators.get("mom_12_1")
        if mom is not None:
            components["mom_12_1"] = float(mom)
            components["momentum"] = score_momentum(float(mom))
        vol = indicators.get("realized_vol")
        if vol is not None:
            realized_vol = float(vol)
            components["realized_vol"] = realized_vol

    if not is_etf:
        quality = quality_component_score(stock.fundamentals)
        if quality is not None:
            components["quality"] = quality

    if is_etf:
        fund = score_etf_fundamental(stock.fundamentals, config.risk_profile)
    else:
        fund = score_fundamental_adjusted(
            stock.fundamentals, config.risk_profile,
            sector_stats=sector_stats, sector=stock.sector,
            factor_stats=factor_stats,
        )

    # Callers that already scored sentiment (rank_stocks' recentering pre-pass)
    # pass it in — model backends make recomputation genuinely expensive
    sent_raw = (
        sentiment_result
        if sentiment_result is not None
        else score_sentiment(stock.news, backend=sentiment_backend)
    )
    sent = sent_raw
    if batch_sentiment_offset and sent_raw.completeness > 0:
        recentered = min(max(sent_raw.score + batch_sentiment_offset, 0.0), 100.0)
        sent = ScoreResult(
            recentered,
            [*sent_raw.reasons,
             f"Recentered vs batch median ({batch_sentiment_offset:+.0f} pts)"],
            sent_raw.completeness,
        )
        components["sentiment_raw"] = sent_raw.score

    # Pillars with zero completeness carry no information — drop them and
    # renormalize the composite over what remains.
    pillars = [
        (config.weight_technical, tech),
        (config.weight_fundamental, fund),
        (config.weight_sentiment, sent),
    ]
    composite = _composite_from_pillars(pillars)

    if factor_stats is not None or batch_sentiment_offset:
        # Continuity series: the composite as the pre-cross-sectional formula
        # would have produced it, so the snapshot eval isn't silently broken
        # by the definition change. Cheap arithmetic — indicators are reused.
        tech_pre = score_technical(indicators) if indicators is not None else tech
        fund_pre = fund if is_etf else score_fundamental_adjusted(
            stock.fundamentals, config.risk_profile,
            sector_stats=sector_stats, sector=stock.sector,
        )
        components["composite_pre_xs"] = _composite_from_pillars([
            (config.weight_technical, tech_pre),
            (config.weight_fundamental, fund_pre),
            (config.weight_sentiment, sent_raw),
        ])
    # Deliberately weighted by the ORIGINAL pillar weights, not the renormalized
    # ones the composite uses: completeness measures how much of the intended
    # model actually ran, so a stock scored on a single pillar must not report
    # full completeness and slip past the min_data_completeness gate.
    data_completeness = sum(w * p.completeness for w, p in pillars)

    tech_score, tech_reasons = tech.score, tech.reasons
    fund_score, fund_reasons = fund.score, fund.reasons
    sent_score, sent_reasons = sent.score, sent.reasons

    reasoning = []
    reasoning.append(f"Technical: {tech_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in tech_reasons)
    reasoning.append(f"Fundamental: {fund_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in fund_reasons)
    reasoning.append(f"Sentiment: {sent_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in sent_reasons)

    eps_growth = 0.0 if is_etf else (stock.fundamentals.get("eps_growth") or 0.0)
    dividend_yield = stock.fundamentals.get("dividend_yield") or 0.0

    is_held = False
    held_shares = 0.0
    held_accounts: list[str] = []
    overlap_penalty = 0.0

    if held_tickers and stock.ticker in held_tickers:
        is_held = True
        holdings = held_tickers[stock.ticker]
        held_shares = sum(h["shares"] for h in holdings)
        held_accounts = [h["account"] for h in holdings]

        taper = (OVERLAP_TAPER_END - composite) / (OVERLAP_TAPER_END - OVERLAP_TAPER_START)
        overlap_penalty = round(OVERLAP_PENALTY * min(max(taper, 0.0), 1.0), 2)
        if overlap_penalty > 0:
            composite = max(composite - overlap_penalty, 0.0)
            accts = ", ".join(held_accounts)
            reasoning.append(
                f"Overlap: already hold {held_shares:.1f} shares in {accts} "
                f"(-{overlap_penalty:.1f} pts)"
            )
        else:
            reasoning.append(
                f"Overlap: already hold {held_shares:.1f} shares "
                f"(strong conviction — no penalty)"
            )

    insufficient_data = data_completeness < config.min_data_completeness
    if insufficient_data:
        recommendation = INSUFFICIENT_DATA_LABEL
        reasoning.append(
            f"Data completeness {data_completeness:.0%} below "
            f"{config.min_data_completeness:.0%} threshold — score is unreliable"
        )
    else:
        recommendation = _recommendation_label(composite)

    account, account_reason = suggest_account(
        eps_growth, dividend_yield, recommendation,
        roth_ira_maxed=roth_ira_maxed,
    )

    return ScoredStock(
        ticker=stock.ticker,
        name=stock.name,
        sector=stock.sector,
        current_price=stock.quote.get("price", 0.0),
        technical_score=tech_score,
        fundamental_score=fund_score,
        sentiment_score=sent_score,
        composite_score=round(composite, 1),
        recommendation=recommendation,
        reasoning=reasoning,
        is_held=is_held,
        held_shares=held_shares,
        held_accounts=held_accounts,
        overlap_penalty=overlap_penalty,
        eps_growth=eps_growth,
        dividend_yield=dividend_yield,
        is_etf=is_etf,
        suggested_account=account,
        suggested_account_reason=account_reason,
        data_completeness=round(data_completeness, 2),
        insufficient_data=insufficient_data,
        realized_vol=realized_vol,
        components=components,
    )


def _apply_sector_penalties(
    scored: list[ScoredStock],
    sector_allocation: dict[str, float],
) -> None:
    for stock in scored:
        pct = sector_allocation.get(stock.sector, 0.0)
        if pct > SECTOR_OVERWEIGHT_THRESHOLD:
            stock.sector_penalty = SECTOR_PENALTY
            stock.composite_score = round(
                max(stock.composite_score - SECTOR_PENALTY, 0.0), 1
            )
            stock.recommendation = _recommendation_label(stock.composite_score)
            stock.reasoning.append(
                f"Sector overweight: {stock.sector} is {pct:.0f}% of portfolio "
                f"(-{SECTOR_PENALTY:.0f} pts)"
            )


def _resolve_factor_stats(stocks: list[StockData]) -> FactorStats | None:
    """Batch stats for large universes (refreshing the cache); a fresh cached
    full-universe run for small batches; None when neither has breakpoints."""
    batch = build_factor_stats(stocks)
    if batch.n >= FACTOR_CACHE_MIN_N:
        save_factor_stats(batch)
        return batch
    cached = load_factor_stats()
    if cached is not None and cached.n > batch.n:
        return cached
    return batch if batch.breakpoints else None


def rank_stocks(
    stocks: list[StockData],
    config: Config,
    held_tickers: dict[str, list[dict[str, Any]]] | None = None,
    sector_allocation: dict[str, float] | None = None,
    roth_ira_maxed: bool = False,
    return_all: bool = False,
    include_incomplete: bool = False,
) -> list[ScoredStock]:
    sector_stats = build_sector_stats(stocks)
    factor_stats = _resolve_factor_stats(stocks)
    backend = get_backend(config.sentiment_backend)

    # Recenter sentiment on the batch median: the keyword lexicon skews
    # positive, which would otherwise lift every composite uniformly.
    sentiment_results = [score_sentiment(s.news, backend=backend) for s in stocks]
    offset = sentiment_offset(
        [r.score for r in sentiment_results if r.completeness > 0]
    )

    scored: list[ScoredStock] = []
    for stock, sent_result in zip(stocks, sentiment_results):
        result = score_stock(
            stock, config, held_tickers=held_tickers,
            roth_ira_maxed=roth_ira_maxed,
            sector_stats=sector_stats,
            factor_stats=factor_stats,
            batch_sentiment_offset=offset,
            sentiment_backend=backend,
            sentiment_result=sent_result,
        )
        if result is not None:
            scored.append(result)

    if sector_allocation:
        _apply_sector_penalties(scored, sector_allocation)

    excluded = [s for s in scored if s.insufficient_data]
    if excluded:
        detail = ", ".join(f"{s.ticker} ({s.data_completeness:.0%})" for s in excluded)
        logger.info("Excluded for insufficient data: %s", detail)

    scored.sort(key=lambda s: s.composite_score, reverse=True)
    if return_all:
        return scored
    if not include_incomplete:
        scored = [s for s in scored if not s.insufficient_data]
    return scored[: config.top_n]
