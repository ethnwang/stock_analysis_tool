from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import load_config

if TYPE_CHECKING:
    from config import Config
from data.fetcher import fetch_all
from data.universe import get_universe
from integrations.sync_all import sync_portfolio
from portfolio.loader import (
    _STANDARD_TICKER,
    compute_position_sizes,
    generate_swaps,
    get_account_cash,
    get_account_holdings,
    get_all_holdings,
    get_emergency_fund_tickers,
    get_held_tickers_detailed,
    get_held_tickers_for_account,
    get_monthly_budget,
    get_sector_allocation,
    is_roth_maxed,
    load_portfolio,
)
from reporting.console import print_account_report, print_report
from scoring.engine import rank_stocks

logger = logging.getLogger(__name__)

ACCOUNT_MAP = {
    "roth": "schwab_roth_ira",
    "brokerage": "schwab_brokerage",
    "hsa": "fidelity_hsa",
}
ACCOUNT_LABELS = {
    "schwab_roth_ira": "Schwab Roth IRA",
    "schwab_brokerage": "Schwab Brokerage",
    "fidelity_hsa": "Fidelity HSA",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockbot",
        description="Analyze stocks and find the best buying opportunities",
    )
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="Run stock analysis")
    analyze.add_argument(
        "--universe",
        choices=["sp500", "watchlist", "etf"],
        default=None,
        help="Stock universe to analyze (default: watchlist)",
    )
    analyze.add_argument(
        "--ticker",
        nargs="+",
        default=None,
        help="Specific tickers to analyze (overrides --universe)",
    )
    analyze.add_argument(
        "--top",
        type=int,
        default=None,
        help="Number of top picks to show (default: 10)",
    )
    analyze.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed per-stock analysis",
    )
    analyze.add_argument(
        "--risk",
        choices=["aggressive", "moderate", "conservative"],
        default=None,
        help="Risk profile for scoring (default: moderate)",
    )
    analyze.add_argument(
        "--no-portfolio",
        action="store_true",
        help="Skip portfolio loading (no overlap/sizing/placement)",
    )
    analyze.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Monthly investment budget override (default: from portfolio.json)",
    )
    analyze.add_argument(
        "--account",
        choices=["roth", "brokerage", "hsa"],
        default=None,
        help="Analyze a specific account (shows holdings, swaps, reallocation)",
    )
    analyze.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Keep stocks with insufficient data in the ranking (marked '!')",
    )
    analyze.add_argument(
        "--snapshot",
        action="store_true",
        help="Append all scores to snapshots/scores.jsonl for later validation",
    )

    sync_cmd = sub.add_parser("sync", help="Pull latest balances/holdings from linked accounts")
    sync_cmd.add_argument("--schwab-only", action="store_true", help="Only sync Schwab, skip Plaid")
    sync_cmd.add_argument("--plaid-only", action="store_true", help="Only sync Plaid, skip Schwab")

    link = sub.add_parser("link", help="Link a new institution (Schwab or Plaid)")
    link.add_argument(
        "--institution",
        required=True,
        choices=["chase", "schwab"],
        help="Institution to link",
    )

    import_cmd = sub.add_parser("import", help="Import positions from a Fidelity CSV export")
    import_cmd.add_argument(
        "file",
        help="Path to Fidelity Portfolio_Positions CSV file",
    )

    backtest_cmd = sub.add_parser(
        "backtest",
        help="Validate technical scores against historical forward returns",
    )
    backtest_cmd.add_argument(
        "--ticker",
        nargs="+",
        default=None,
        help="Tickers to backtest (default: watchlist universe)",
    )
    backtest_cmd.add_argument(
        "--universe",
        choices=["sp500", "watchlist", "etf"],
        default=None,
        help="Universe to backtest when --ticker is not given",
    )
    backtest_cmd.add_argument(
        "--years",
        type=int,
        choices=[2, 3],
        default=3,
        help="Years of price history (default: 3)",
    )
    backtest_cmd.add_argument(
        "--step",
        type=int,
        default=5,
        help="Bars between as-of scoring dates (default: 5)",
    )
    backtest_cmd.add_argument(
        "--eval-snapshots",
        action="store_true",
        help="Evaluate accumulated analyze --snapshot history instead",
    )
    backtest_cmd.add_argument(
        "--by-component",
        action="store_true",
        help="With --eval-snapshots: IC table for every recorded signal "
             "(pillars, momentum, quality, ...)",
    )
    backtest_cmd.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )

    return parser


def _run_sync(config: Config, schwab_only: bool = False, plaid_only: bool = False) -> None:
    print("\nStockBot — Portfolio Sync", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    portfolio = sync_portfolio(
        schwab_client_id=config.schwab_client_id if not plaid_only else "",
        schwab_client_secret=config.schwab_client_secret if not plaid_only else "",
        schwab_refresh_token=config.schwab_refresh_token if not plaid_only else "",
        plaid_client_id=config.plaid_client_id if not schwab_only else "",
        plaid_secret=config.plaid_secret if not schwab_only else "",
        plaid_env=config.plaid_env,
        plaid_access_token_chase=config.plaid_access_token_chase if not schwab_only else "",
        plaid_access_token_fidelity=config.plaid_access_token_fidelity if not schwab_only else "",
    )

    if portfolio.get("last_sync"):
        logger.info("Last sync: %s", portfolio["last_sync"])

    sync_errors = portfolio.get("_sync_errors")
    if sync_errors:
        for provider, message in sync_errors.items():
            print(f"Error: {provider} sync failed: {message}", file=sys.stderr)
        sys.exit(1)


def _run_link_schwab(config: Config) -> None:
    if not config.schwab_client_id or not config.schwab_client_secret:
        print("Error: SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)

    from integrations.schwab import exchange_code, get_authorization_url

    url = get_authorization_url(config.schwab_client_id, config.schwab_client_secret)

    print("\nStockBot — Link Schwab Account", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)
    print(f"\n1. Open this URL in your browser:\n", file=sys.stderr)
    print(f"   {url}\n", file=sys.stderr)
    print(f"2. Log in and approve access.", file=sys.stderr)
    print(f"3. You'll be redirected to a URL that looks like:", file=sys.stderr)
    print(f"   https://127.0.0.1:5000/callback?code=XXXX&session=YYYY", file=sys.stderr)
    print(f"4. Copy the FULL redirect URL and paste it below.\n", file=sys.stderr)

    try:
        redirect_url = input("Paste redirect URL: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        sys.exit(1)

    import urllib.parse
    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        print("Error: Could not extract authorization code from URL.", file=sys.stderr)
        sys.exit(1)

    logger.info("Exchanging code for tokens...")
    refresh_token = exchange_code(config.schwab_client_id, config.schwab_client_secret, code)

    if not refresh_token:
        print("Error: No refresh token received.", file=sys.stderr)
        sys.exit(1)

    from integrations.link_server import _update_env
    _update_env("SCHWAB_REFRESH_TOKEN", refresh_token)

    print(f"\nSuccess! Schwab linked.", file=sys.stderr)
    print(f"Refresh token saved to .env as SCHWAB_REFRESH_TOKEN", file=sys.stderr)


def _run_link_plaid(config: Config, institution: str) -> None:
    if not config.has_plaid:
        print("Error: PLAID_CLIENT_ID and PLAID_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)

    from integrations.link_server import run_link_server
    from integrations.plaid_sync import create_link_token

    logger.info("Creating Plaid Link token for %s...", institution)
    link_token = create_link_token(config.plaid_client_id, config.plaid_secret, config.plaid_env)

    access_token = run_link_server(
        link_token=link_token,
        institution=institution,
        client_id=config.plaid_client_id,
        secret=config.plaid_secret,
        plaid_env=config.plaid_env,
    )

    if access_token:
        print(f"\nSuccess! {institution.title()} linked.", file=sys.stderr)
        print(f"Access token saved to .env as PLAID_ACCESS_TOKEN_{institution.upper()}", file=sys.stderr)
    else:
        print(f"\nLinking was not completed.", file=sys.stderr)
        sys.exit(1)


def _run_account_analysis(args: argparse.Namespace, config: Config, portfolio: dict) -> None:
    account_key = ACCOUNT_MAP[args.account]
    account_label = ACCOUNT_LABELS[account_key]

    account_holdings = get_account_holdings(portfolio, account_key)
    if not account_holdings:
        print(f"\nNo holdings found in {account_label}.", file=sys.stderr)
        sys.exit(1)

    roth_maxed = is_roth_maxed(portfolio)
    is_maxed = (account_key == "schwab_roth_ira" and roth_maxed)

    tickers = get_universe(config)
    tickers_set = set(tickers)
    held_in_account = [
        h["ticker"] for h in account_holdings
        if _STANDARD_TICKER.match(h["ticker"])
    ]
    for t in held_in_account:
        if t not in tickers_set:
            tickers.append(t)
            tickers_set.add(t)

    count = len(tickers)
    print(f"\nStockBot v0.1.0 — {account_label} Analysis", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    if count > 100:
        est_minutes = count * 1.2 / 60
        logger.info("Analyzing %d stocks (estimated %.0f min)...", count, est_minutes)
    else:
        logger.info("Analyzing %d stocks...", count)

    start = time.time()
    stocks = fetch_all(tickers, config)

    if not stocks:
        print("\nNo stocks passed filters.", file=sys.stderr)
        sys.exit(1)

    live_prices = {s.ticker: s.quote.get("price", 0.0) for s in stocks}
    for h in account_holdings:
        live = live_prices.get(h["ticker"])
        if live and live > 0:
            h["market_value"] = h["shares"] * live
        else:
            logger.debug("No live price for %s — using portfolio.json value", h["ticker"])

    sector_map = {s.ticker: s.sector for s in stocks if s.sector}
    ef_tickers = get_emergency_fund_tickers(portfolio)
    non_ef_holdings = [h for h in account_holdings if h["ticker"] not in ef_tickers]
    acct_sector_allocation = get_sector_allocation(non_ef_holdings, sector_map)

    held_tickers = get_held_tickers_for_account(portfolio, account_key)

    logger.info("Scoring and ranking...")
    ranked = rank_stocks(
        stocks, config,
        held_tickers=held_tickers,
        sector_allocation=acct_sector_allocation,
        roth_ira_maxed=roth_maxed,
        return_all=True,
    )

    scored_tickers = {s.ticker for s in ranked}
    current_holdings = [s for s in ranked if s.is_held]
    alternatives = [s for s in ranked if not s.is_held]

    unscored = [
        h for h in account_holdings
        if h["ticker"] not in scored_tickers
        and h["ticker"]
    ]

    swaps = generate_swaps(current_holdings, alternatives, emergency_fund_tickers=ef_tickers)

    if is_maxed:
        # Contributions are capped, but idle cash already in the account isn't
        # a new contribution — it should still get sized into a buy.
        monthly_budget = args.budget or get_account_cash(portfolio, account_key)
    else:
        monthly_budget = args.budget or get_monthly_budget(portfolio)

    if monthly_budget > 0:
        buyable = [a for a in alternatives if a.recommendation in ("Buy", "Strong Buy")]
        compute_position_sizes(buyable, monthly_budget)

    elapsed = time.time() - start
    logger.info("Done in %.1fs", elapsed)

    print_account_report(
        account_label=account_label,
        current_holdings=current_holdings,
        unscored_holdings=unscored,
        alternatives=alternatives,
        swaps=swaps,
        sector_allocation=acct_sector_allocation,
        config=config,
        is_maxed=is_maxed,
        monthly_budget=monthly_budget,
        emergency_fund_tickers=ef_tickers,
    )


def _run_backtest(args: argparse.Namespace) -> None:
    from backtest.engine import (
        evaluate_snapshot_components,
        evaluate_snapshots,
        run_technical_backtest,
    )
    from reporting.console import print_backtest_report, print_component_eval_report

    print("\nStockBot — Backtest", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    if args.eval_snapshots and args.by_component:
        print_component_eval_report(evaluate_snapshot_components())
        return
    if args.eval_snapshots:
        result = evaluate_snapshots()
    else:
        if args.ticker:
            tickers = [t.upper() for t in args.ticker]
        else:
            config = load_config(universe=args.universe)
            tickers = get_universe(config)
        result = run_technical_backtest(
            tickers, days=args.years * 365, step=args.step,
        )
    print_backtest_report(result)


def _fetch_benchmark_close() -> float:
    """Latest SPY close for excess-return snapshot evaluation; 0.0 on failure."""
    from data.fetcher import fetch_price_history

    try:
        # fetch_price_history drops tickers with <20 bars, so the window must
        # cover comfortably more than 20 trading days
        spy = fetch_price_history(["SPY"], days=45).get("SPY")
        if spy is not None and len(spy) > 0:
            return float(spy["Close"].iloc[-1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Benchmark price fetch failed: %s", exc)
    return 0.0


def _snapshot_scores(ranked: list, config: Config) -> None:
    from backtest.snapshots import SnapshotRecord, append_snapshots

    today = datetime.now(timezone.utc).date().isoformat()
    benchmark_price = _fetch_benchmark_close()
    if benchmark_price <= 0:
        logger.warning("No benchmark price — snapshots will evaluate on raw returns")
    records = [
        SnapshotRecord(
            date=today,
            ticker=s.ticker,
            composite=s.composite_score,
            technical=s.technical_score,
            fundamental=s.fundamental_score,
            sentiment=s.sentiment_score,
            completeness=s.data_completeness,
            price=s.current_price,
            universe=config.universe,
            risk_profile=config.risk_profile,
            benchmark_price=benchmark_price,
            components=dict(s.components),
        )
        for s in ranked
    ]
    append_snapshots(records)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="  %(message)s",
        stream=sys.stderr,
    )
    # Third-party HTTP/model libraries dump request URLs (which can contain
    # API keys) and hub chatter at DEBUG — keep them at WARNING even under -v
    for noisy in ("urllib3", "finnhub", "httpx", "httpcore",
                  "huggingface_hub", "transformers", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.command == "sync":
        config = load_config()
        _run_sync(config, schwab_only=args.schwab_only, plaid_only=args.plaid_only)
        return

    if args.command == "link":
        config = load_config()
        if args.institution == "schwab":
            _run_link_schwab(config)
        else:
            _run_link_plaid(config, args.institution)
        return

    if args.command == "import":
        from integrations.fidelity_csv import import_to_portfolio
        print("\nStockBot — Fidelity CSV Import", file=sys.stderr)
        print(f"{'─' * 40}", file=sys.stderr)
        import_to_portfolio(args.file, "portfolio.json")
        return

    if args.command == "backtest":
        _run_backtest(args)
        return

    config = load_config(
        universe=args.universe,
        top_n=args.top,
        verbose=args.verbose,
        tickers=args.ticker,
        risk_profile=args.risk,
    )

    if args.account and args.no_portfolio:
        parser.error("--account requires portfolio data; cannot use with --no-portfolio")

    portfolio = None
    held_tickers = None
    sector_allocation = None
    monthly_budget = 0.0
    portfolio_context = None

    if not args.no_portfolio:
        portfolio = load_portfolio()

    if args.account:
        if not portfolio:
            print("\nError: --account requires portfolio.json", file=sys.stderr)
            sys.exit(1)
        _run_account_analysis(args, config, portfolio)
        return

    roth_maxed = is_roth_maxed(portfolio) if portfolio else False

    if portfolio:
        held_tickers = get_held_tickers_detailed(portfolio)
        monthly_budget = args.budget or get_monthly_budget(portfolio)
    elif args.budget:
        monthly_budget = args.budget

    tickers = get_universe(config)
    count = len(tickers)

    print(f"\nStockBot v0.1.0", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    if config.risk_profile != "moderate":
        logger.info("Risk profile: %s", config.risk_profile)

    if count > 100:
        est_minutes = count * 1.2 / 60
        logger.info("Analyzing %d stocks (estimated %.0f min)...", count, est_minutes)
    else:
        logger.info("Analyzing %d stocks...", count)

    start = time.time()
    stocks = fetch_all(tickers, config)

    if not stocks:
        print("\nNo stocks passed filters. Try different tickers or lower MIN_PRICE/MIN_MARKET_CAP.", file=sys.stderr)
        sys.exit(1)

    if portfolio:
        all_holdings = get_all_holdings(portfolio)
        live_prices = {s.ticker: s.quote.get("price", 0.0) for s in stocks}
        for h in all_holdings:
            live = live_prices.get(h["ticker"])
            if live and live > 0:
                h["market_value"] = h["shares"] * live
        sector_map = {s.ticker: s.sector for s in stocks if s.sector}
        sector_allocation = get_sector_allocation(all_holdings, sector_map)

    logger.info("Scoring and ranking...")
    ranked = rank_stocks(
        stocks, config,
        held_tickers=held_tickers,
        sector_allocation=sector_allocation,
        roth_ira_maxed=roth_maxed,
        include_incomplete=args.include_incomplete,
    )

    if monthly_budget > 0:
        compute_position_sizes(ranked, monthly_budget)

    elapsed = time.time() - start
    logger.info("Done in %.1fs", elapsed)

    if portfolio or monthly_budget > 0:
        portfolio_context = {
            "has_portfolio": portfolio is not None,
            "sector_allocation": sector_allocation or {},
            "monthly_budget": monthly_budget,
        }

    if args.snapshot:
        # snapshot the full scored universe, not just top-N — rank correlation
        # against forward returns needs the whole cross-section
        all_scored = rank_stocks(
            stocks, config,
            held_tickers=held_tickers,
            sector_allocation=sector_allocation,
            roth_ira_maxed=roth_maxed,
            return_all=True,
        )
        _snapshot_scores(all_scored, config)

    print_report(ranked, config, portfolio_context=portfolio_context)


if __name__ == "__main__":
    main()
