from __future__ import annotations

from typing import TYPE_CHECKING, Any

from analysis.fundamental import score_fundamental_adjusted
from analysis.sentiment import score_sentiment
from analysis.technical import compute_indicators, score_technical
from data.models import ScoredStock
from portfolio.loader import suggest_account

if TYPE_CHECKING:
    from config import Config
    from data.models import StockData

OVERLAP_PENALTY = 5.0
SECTOR_OVERWEIGHT_THRESHOLD = 30.0
SECTOR_PENALTY = 3.0


def _recommendation_label(score: float) -> str:
    if score >= 80:
        return "Strong Buy"
    if score >= 65:
        return "Buy"
    if score >= 45:
        return "Hold"
    return "Avoid"


def score_stock(
    stock: StockData,
    config: Config,
    held_tickers: dict[str, list[dict[str, Any]]] | None = None,
    roth_ira_maxed: bool = False,
) -> ScoredStock | None:
    try:
        indicators = compute_indicators(stock.price_history)
        tech_score, tech_reasons = score_technical(indicators)
    except Exception:
        tech_score = 50.0
        tech_reasons = ["Insufficient price data for technical analysis"]

    fund_score, fund_reasons = score_fundamental_adjusted(
        stock.fundamentals, config.risk_profile
    )
    sent_score, sent_reasons = score_sentiment(stock.news)

    composite = (
        config.weight_technical * tech_score
        + config.weight_fundamental * fund_score
        + config.weight_sentiment * sent_score
    )

    reasoning = []
    reasoning.append(f"Technical: {tech_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in tech_reasons)
    reasoning.append(f"Fundamental: {fund_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in fund_reasons)
    reasoning.append(f"Sentiment: {sent_score:.0f}/100")
    reasoning.extend(f"  {r}" for r in sent_reasons)

    eps_growth = stock.fundamentals.get("eps_growth", 0.0)
    dividend_yield = stock.fundamentals.get("dividend_yield", 0.0)

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
        suggested_account=account,
        suggested_account_reason=account_reason,
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

    scored.sort(key=lambda s: s.composite_score, reverse=True)
    if return_all:
        return scored
    return scored[: config.top_n]
