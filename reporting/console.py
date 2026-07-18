from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import Config
    from data.models import ScoredStock, SwapSuggestion


_REC_COLORS = {
    "Strong Buy": "\033[1;32m",
    "Buy": "\033[32m",
    "Hold": "\033[33m",
    "Avoid": "\033[31m",
}
_RESET = "\033[0m"


def print_report(
    ranked: list[ScoredStock],
    config: Config,
    portfolio_context: dict[str, Any] | None = None,
) -> None:
    if not ranked:
        print("\nNo stocks to display. Check your filters or ticker list.")
        return

    ctx = portfolio_context or {}
    has_portfolio = ctx.get("has_portfolio", False)
    has_held = any(s.is_held for s in ranked)

    all_etf = all(s.is_etf for s in ranked)
    asset_label = "ETF" if all_etf else "Stock"

    print("\n" + "=" * 90)
    print(f"  STOCKBOT ANALYSIS — Top {len(ranked)} {asset_label} Picks")
    if config.risk_profile != "moderate":
        print(f"  Risk Profile: {config.risk_profile.upper()}")
    print("=" * 90)

    header = (
        f"{'#':>3}  {'Ticker':<8} {'Name':<25} {'Price':>9} "
        f"{'Score':>6} {'Rec':<12} {'Tech':>5} {'Fund':>5} {'Sent':>5} {'Data':>5}"
    )
    print(f"\n{header}")
    print("-" * 96)

    for i, stock in enumerate(ranked, 1):
        color = _REC_COLORS.get(stock.recommendation, "")
        rec_str = f"{color}{stock.recommendation}{_RESET}"

        name = stock.name[:24] if len(stock.name) > 24 else stock.name
        held_marker = "*" if stock.is_held else ""
        incomplete_marker = "!" if stock.insufficient_data else ""
        ticker_display = f"{stock.ticker}{held_marker}{incomplete_marker}"

        print(
            f"{i:>3}  {ticker_display:<8} {name:<25} "
            f"${stock.current_price:>8.2f} "
            f"{stock.composite_score:>5.1f} "
            f" {rec_str:<21} "
            f"{stock.technical_score:>4.0f} "
            f"{stock.fundamental_score:>5.0f} "
            f"{stock.sentiment_score:>5.0f} "
            f"{stock.data_completeness:>4.0%}"
        )

    print("-" * 96)
    print(
        f"  Weights: Technical {config.weight_technical:.0%} | "
        f"Fundamental {config.weight_fundamental:.0%} | "
        f"Sentiment {config.weight_sentiment:.0%}"
    )
    if has_held:
        print("  * = already held in portfolio")
    if any(s.insufficient_data for s in ranked):
        print("  ! = insufficient data — score unreliable")

    if has_portfolio:
        _print_sector_allocation(ctx.get("sector_allocation", {}))
        _print_position_sizing(ranked, ctx.get("monthly_budget", 0.0))

    if config.verbose:
        _print_detailed_analysis(ranked)

    print(f"\n{'=' * 90}")
    print("  Disclaimer: This is not financial advice. Past performance does")
    print("  not guarantee future results. Always do your own research.")
    print(f"{'=' * 90}\n")


def _print_sector_allocation(sector_allocation: dict[str, float]) -> None:
    if not sector_allocation:
        return

    print(f"\n{'=' * 90}")
    print("  PORTFOLIO SECTOR ALLOCATION")
    print("=" * 90)

    max_pct = max(sector_allocation.values()) if sector_allocation else 1
    bar_scale = 30 / max_pct if max_pct > 0 else 1

    for sector, pct in sector_allocation.items():
        bar_len = int(pct * bar_scale)
        bar = "█" * bar_len
        warning = " ⚠ overweight" if pct > 30 else ""
        print(f"  {sector:<22} {pct:>5.1f}%  {bar}{warning}")


def _print_position_sizing(
    ranked: list[ScoredStock],
    monthly_budget: float,
) -> None:
    buyable = [s for s in ranked if s.suggested_amount > 0]
    if not buyable or monthly_budget <= 0:
        return

    print(f"\n{'=' * 90}")
    print(f"  POSITION SIZING (Monthly budget: ${monthly_budget:,.2f})")
    print("=" * 90)

    for stock in buyable:
        held_note = " *" if stock.is_held else ""
        account_note = ""
        if stock.suggested_account:
            account_note = f"  → {stock.suggested_account}"

        shares_str = f"~{stock.suggested_shares:.1f} shares"
        print(
            f"  {stock.ticker:<7} ${stock.suggested_amount:>8.2f}  "
            f"({shares_str}){account_note}{held_note}"
        )

    if any(s.suggested_account_reason for s in buyable):
        print()
        for stock in buyable:
            if stock.suggested_account_reason:
                print(
                    f"  {stock.ticker:<7} {stock.suggested_account}: "
                    f"{stock.suggested_account_reason}"
                )


def _print_detailed_analysis(ranked: list[ScoredStock]) -> None:
    print("\n" + "=" * 90)
    print("  DETAILED ANALYSIS")
    print("=" * 90)
    for i, stock in enumerate(ranked, 1):
        color = _REC_COLORS.get(stock.recommendation, "")
        print(f"\n{'─' * 70}")
        print(f"  #{i} {stock.ticker} — {stock.name} ({stock.sector})")
        print(
            f"  Price: ${stock.current_price:.2f}  |  "
            f"Composite: {stock.composite_score:.1f}  |  "
            f"Recommendation: {color}{stock.recommendation}{_RESET}"
        )
        print()
        for line in stock.reasoning:
            print(f"    {line}")


def _print_holdings_table(
    holdings: list[ScoredStock],
    unscored: list[dict[str, Any]],
    emergency_fund_tickers: set[str] | None = None,
) -> None:
    header = (
        f"{'#':>3}  {'Ticker':<8} {'Name':<25} {'Price':>9} "
        f"{'Score':>6} {'Rec':<12} {'Tech':>5} {'Fund':>5} {'Sent':>5}"
    )
    print(f"\n{header}")
    print("-" * 90)

    ef_tickers = emergency_fund_tickers or set()
    weak_threshold = 50.0
    for i, stock in enumerate(holdings, 1):
        color = _REC_COLORS.get(stock.recommendation, "")
        rec_str = f"{color}{stock.recommendation}{_RESET}"
        name = stock.name[:24] if len(stock.name) > 24 else stock.name

        if stock.ticker in ef_tickers:
            flag = "  [emergency fund]"
        elif stock.composite_score < weak_threshold:
            flag = "  << weak"
        else:
            flag = ""

        print(
            f"{i:>3}  {stock.ticker:<8} {name:<25} "
            f"${stock.current_price:>8.2f} "
            f"{stock.composite_score:>5.1f} "
            f" {rec_str:<21} "
            f"{stock.technical_score:>4.0f} "
            f"{stock.fundamental_score:>5.0f} "
            f"{stock.sentiment_score:>5.0f}{flag}"
        )

    if unscored:
        print()
        for h in unscored:
            name = h.get("name", h["ticker"])[:30]
            print(f"       {h['ticker']:<8} {name:<25}  (index fund/ETF — not scored)")


def _print_swap_suggestions(swaps: list[SwapSuggestion]) -> None:
    if not swaps:
        return

    print(f"\n{'=' * 90}")
    print("  SWAP SUGGESTIONS")
    print("=" * 90)

    for swap in swaps:
        sell_color = _REC_COLORS.get("Avoid", "")
        buy_color = _REC_COLORS.get("Buy", "")
        print(
            f"\n  {sell_color}SELL{_RESET}  {swap.sell_ticker} "
            f"(score {swap.sell_score:.0f})  →  "
            f"{buy_color}BUY{_RESET}  {swap.buy_ticker} "
            f"(score {swap.buy_score:.0f})"
        )
        print(f"        {swap.reason}")


def print_account_report(
    account_label: str,
    current_holdings: list[ScoredStock],
    unscored_holdings: list[dict[str, Any]],
    alternatives: list[ScoredStock],
    swaps: list[SwapSuggestion],
    sector_allocation: dict[str, float],
    config: Config,
    is_maxed: bool = False,
    monthly_budget: float = 0.0,
    emergency_fund_tickers: set[str] | None = None,
) -> None:
    status = "CONTRIBUTIONS MAXED (reallocation only)" if is_maxed else "Open for new contributions"

    print("\n" + "=" * 90)
    print(f"  STOCKBOT ACCOUNT ANALYSIS — {account_label}")
    print(f"  Status: {status}")
    print("=" * 90)

    n_positions = len(current_holdings) + len(unscored_holdings)
    print(f"\n  CURRENT HOLDINGS ({n_positions} positions)")
    _print_holdings_table(current_holdings, unscored_holdings, emergency_fund_tickers)

    if sector_allocation:
        print(f"\n{'=' * 90}")
        print("  SECTOR ALLOCATION (This Account)")
        print("=" * 90)
        max_pct = max(sector_allocation.values()) if sector_allocation else 1
        bar_scale = 30 / max_pct if max_pct > 0 else 1
        for sector, pct in sector_allocation.items():
            bar_len = int(pct * bar_scale)
            bar = "█" * bar_len
            warning = " ⚠ overweight" if pct > 30 else ""
            print(f"  {sector:<22} {pct:>5.1f}%  {bar}{warning}")

    _print_swap_suggestions(swaps)

    top_n = config.top_n
    top_alts = [a for a in alternatives if not a.is_held][:top_n]
    if top_alts:
        print(f"\n{'=' * 90}")
        print(f"  TOP PICKS (not currently held)")
        print("=" * 90)

        header = (
            f"{'#':>3}  {'Ticker':<8} {'Name':<25} {'Price':>9} "
            f"{'Score':>6} {'Rec':<12} {'Tech':>5} {'Fund':>5} {'Sent':>5}"
        )
        print(f"\n{header}")
        print("-" * 90)

        for i, stock in enumerate(top_alts, 1):
            color = _REC_COLORS.get(stock.recommendation, "")
            rec_str = f"{color}{stock.recommendation}{_RESET}"
            name = stock.name[:24] if len(stock.name) > 24 else stock.name
            print(
                f"{i:>3}  {stock.ticker:<8} {name:<25} "
                f"${stock.current_price:>8.2f} "
                f"{stock.composite_score:>5.1f} "
                f" {rec_str:<21} "
                f"{stock.technical_score:>4.0f} "
                f"{stock.fundamental_score:>5.0f} "
                f"{stock.sentiment_score:>5.0f}"
            )

    if is_maxed:
        print(f"\n{'=' * 90}")
        print("  Roth IRA contributions are maxed for the year.")
        print("  Consider the swap suggestions above to optimize existing holdings.")
        print("=" * 90)
    elif monthly_budget > 0:
        buyable_alts = [a for a in top_alts if a.suggested_amount > 0]
        if buyable_alts:
            print(f"\n{'=' * 90}")
            print(f"  POSITION SIZING (Monthly budget: ${monthly_budget:,.2f})")
            print("=" * 90)
            for stock in buyable_alts:
                shares_str = f"~{stock.suggested_shares:.1f} shares"
                print(
                    f"  {stock.ticker:<7} ${stock.suggested_amount:>8.2f}  ({shares_str})"
                )

    if config.verbose and current_holdings:
        _print_detailed_analysis(current_holdings)

    print(f"\n{'=' * 90}")
    print("  Disclaimer: This is not financial advice. Past performance does")
    print("  not guarantee future results. Always do your own research.")
    print(f"{'=' * 90}\n")


def print_backtest_report(result: Any) -> None:
    print("\n" + "=" * 90)
    print("  BACKTEST REPORT")
    print("=" * 90)

    if result.n_observations == 0:
        print("\n  No observations produced.")
        for caveat in result.caveats:
            print(f"  ! {caveat}")
        print()
        return

    print(f"\n  Tickers: {len(result.tickers)}  |  "
          f"Observations: {result.n_observations}  |  "
          f"Cross-sectional dates: {result.n_dates}")

    print(f"\n  {'Horizon':<12} {'Mean Spearman (score vs fwd return)':>38}")
    print("  " + "-" * 52)
    for horizon, corr in result.spearman_by_horizon.items():
        label = f"{horizon} bars" if horizon else "since snapshot"
        corr_str = f"{corr:+.3f}" if corr == corr else "n/a"
        print(f"  {label:<12} {corr_str:>38}")

    for horizon, means in result.bucket_means.items():
        label = f"{horizon}-bar" if horizon else "since-snapshot"
        print(f"\n  Mean forward return by score quintile ({label}):")
        print(f"  {'Quintile':<12} {'(low score)':<14}{'':<14}{'':<14}{'':<14}{'(high score)'}")
        cells = []
        for m in means:
            cells.append(f"{m:+.2%}" if m == m else "n/a")
        print("  " + "  ".join(f"{c:>10}" for c in cells))

    if result.caveats:
        print()
        for caveat in result.caveats:
            print(f"  ! {caveat}")
    print()
