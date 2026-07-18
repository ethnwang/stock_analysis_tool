from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from analysis.etf_fundamental import score_etf_fundamental
from analysis.fundamental import score_fundamental_adjusted
from analysis.sentiment import score_sentiment
from analysis.technical import compute_indicators, score_technical
from data.models import ScoredStock, ScoreResult
from portfolio.loader import suggest_account

if TYPE_CHECKING:
    from config import Config
    from data.models import StockData

logger = logging.getLogger(__name__)

OVERLAP_PENALTY = 5.0
SECTOR_OVERWEIGHT_THRESHOLD = 30.0
SECTOR_PENALTY = 3.0

MIN_PRICE_BARS = 30

INSUFFICIENT_DATA_LABEL = "Insufficient Data"


def _recommendation_label(score: float) -> str:
    if score >= 80:
        return "Strong Buy"
    if score >= 65:
        return "Buy"
    if score >= 45:
        return "Hold"
    return "Avoid"


def _score_technical_safe(stock: StockData) -> ScoreResult:
    if len(stock.price_history) < MIN_PRICE_BARS:
        return ScoreResult(
            0.0,
            [f"Insufficient price history (<{MIN_PRICE_BARS} bars) — technical not scored"],
            completeness=0.0,
        )
    try:
        indicators = compute_indicators(stock.price_history)
        return score_technical(indicators)
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        return ScoreResult(
            0.0, [f"Technical analysis failed ({exc}) — not scored"], completeness=0.0,
        )


def score_stock(
    stock: StockData,
    config: Config,
    held_tickers: dict[str, list[dict[str, Any]]] | None = None,
    roth_ira_maxed: bool = False,
) -> ScoredStock | None:
    tech = _score_technical_safe(stock)

    is_etf = (stock.fundamentals.get("is_etf") or 0.0) > 0

    if is_etf:
        fund = score_etf_fundamental(stock.fundamentals, config.risk_profile)
    else:
        fund = score_fundamental_adjusted(stock.fundamentals, config.risk_profile)
    sent = score_sentiment(stock.news)

    # Pillars with zero completeness carry no information — drop them and
    # renormalize the composite over what remains.
    pillars = [
        (config.weight_technical, tech),
        (config.weight_fundamental, fund),
        (config.weight_sentiment, sent),
    ]
    active = [(w, p) for w, p in pillars if p.completeness > 0]
    if active:
        active_weight = sum(w for w, _ in active)
        composite = sum(w * p.score for w, p in active) / active_weight
    else:
        composite = 50.0
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

        if composite <= 80:
            overlap_penalty = OVERLAP_PENALTY
            composite = max(composite - overlap_penalty, 0.0)
            accts = ", ".join(held_accounts)
            reasoning.append(
                f"Overlap: already hold {held_shares:.1f} shares in {accts} "
                f"(-{overlap_penalty:.0f} pts)"
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


def rank_stocks(
    stocks: list[StockData],
    config: Config,
    held_tickers: dict[str, list[dict[str, Any]]] | None = None,
    sector_allocation: dict[str, float] | None = None,
    roth_ira_maxed: bool = False,
    return_all: bool = False,
    include_incomplete: bool = False,
) -> list[ScoredStock]:
    scored: list[ScoredStock] = []
    for stock in stocks:
        result = score_stock(
            stock, config, held_tickers=held_tickers,
            roth_ira_maxed=roth_ira_maxed,
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
