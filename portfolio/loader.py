from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data.models import ScoredStock, SwapSuggestion

PORTFOLIO_PATH = Path(__file__).parent.parent / "portfolio.json"

_STANDARD_TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z]{1,2})?$")

_ACCOUNT_KEYS_WITH_HOLDINGS = [
    "schwab_brokerage",
    "schwab_roth_ira",
]

_FIDELITY_SUB_ACCOUNTS = ["401k", "hsa", "roth_401k"]


def load_portfolio(path: Path | None = None) -> dict[str, Any] | None:
    target = path or PORTFOLIO_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def get_all_holdings(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []

    for account_key in _ACCOUNT_KEYS_WITH_HOLDINGS:
        account = portfolio.get(account_key, {})
        for h in account.get("holdings", []):
            holdings.append({
                "ticker": h.get("ticker", ""),
                "shares": h.get("shares", 0.0),
                "market_value": h.get("market_value", 0.0),
                "account": account_key,
            })

    fidelity = portfolio.get("fidelity", {})
    for sub in _FIDELITY_SUB_ACCOUNTS:
        sub_account = fidelity.get(sub, {})
        for h in sub_account.get("holdings", []):
            holdings.append({
                "ticker": h.get("ticker", ""),
                "shares": h.get("shares", 0.0),
                "market_value": h.get("market_value", 0.0),
                "account": f"fidelity_{sub}",
            })

    return holdings


def get_held_tickers_detailed(
    portfolio: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    all_holdings = get_all_holdings(portfolio)
    result: dict[str, list[dict[str, Any]]] = {}

    for h in all_holdings:
        ticker = h["ticker"]
        if not _STANDARD_TICKER.match(ticker):
            continue
        result.setdefault(ticker, []).append({
            "account": h["account"],
            "shares": h["shares"],
        })

    return result


def get_sector_allocation(
    holdings: list[dict[str, Any]],
    sector_map: dict[str, str],
) -> dict[str, float]:
    sector_values: dict[str, float] = {}
    total = 0.0

    for h in holdings:
        ticker = h["ticker"]
        value = h.get("market_value", 0.0)
        total += value
        sector = sector_map.get(ticker)
        if sector:
            sector_values[sector] = sector_values.get(sector, 0.0) + value

    if total <= 0:
        return {}

    return {
        sector: round(value / total * 100, 1)
        for sector, value in sorted(
            sector_values.items(), key=lambda x: x[1], reverse=True
        )
    }


def get_account_holdings(
    portfolio: dict[str, Any],
    account_key: str,
) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []

    if account_key.startswith("fidelity_"):
        sub = account_key[len("fidelity_"):]
        fidelity = portfolio.get("fidelity", {})
        sub_account = fidelity.get(sub, {})
        for h in sub_account.get("holdings", []):
            holdings.append({
                "ticker": h.get("ticker", ""),
                "name": h.get("name", ""),
                "shares": h.get("shares", 0.0),
                "market_value": h.get("market_value", 0.0),
                "account": account_key,
            })
    else:
        account = portfolio.get(account_key, {})
        for h in account.get("holdings", []):
            holdings.append({
                "ticker": h.get("ticker", ""),
                "name": h.get("name", ""),
                "shares": h.get("shares", 0.0),
                "market_value": h.get("market_value", 0.0),
                "account": account_key,
            })

    return holdings


def get_held_tickers_for_account(
    portfolio: dict[str, Any],
    account_key: str,
) -> dict[str, list[dict[str, Any]]]:
    account_holdings = get_account_holdings(portfolio, account_key)
    result: dict[str, list[dict[str, Any]]] = {}

    for h in account_holdings:
        ticker = h["ticker"]
        if not _STANDARD_TICKER.match(ticker):
            continue
        result.setdefault(ticker, []).append({
            "account": h["account"],
            "shares": h["shares"],
        })

    return result


def is_roth_maxed(portfolio: dict[str, Any]) -> bool:
    return bool(portfolio.get("roth_ira_maxed", False))


def get_emergency_fund_tickers(portfolio: dict[str, Any] | None) -> set[str]:
    if not portfolio:
        return set()
    return set(portfolio.get("emergency_fund", []))


def get_monthly_budget(portfolio: dict[str, Any]) -> float:
    expenses = portfolio.get("monthly_expenses", {})
    return float(expenses.get("personal_investment", 0.0))


def suggest_account(
    eps_growth: float,
    dividend_yield: float,
    recommendation: str,
    roth_ira_maxed: bool = False,
) -> tuple[str, str]:
    if roth_ira_maxed:
        if dividend_yield > 0.02:
            return "Brokerage", "Dividend income gets favorable tax in taxable accounts"
        return "Brokerage", "Roth IRA maxed for the year — using brokerage"
    if dividend_yield > 0.02:
        return "Brokerage", "Dividend income gets favorable tax in taxable accounts"
    if eps_growth > 0.15 and dividend_yield < 0.01:
        return "Roth IRA", "High-growth, low-dividend — tax-free compounding"
    if recommendation == "Hold":
        return "Brokerage", "Shorter-term hold — keep Roth space for growth"
    if recommendation in ("Buy", "Strong Buy") and eps_growth > 0:
        return "Roth IRA", "Growth stock — tax-free gains in Roth"
    return "Brokerage", "Default to brokerage for flexibility"


# Sizing weights use points above the Hold threshold, not raw scores:
# raw-linear compresses a 66 vs 90 into ~1.4x; conviction should matter more.
_SIZING_BASELINE = 45.0

# Inverse-volatility scaling: a high-conviction but high-volatility name gets a
# smaller slice than an equally-scored calm one. The scalar is clamped so vol
# adjusts sizes but never dominates conviction.
_VOL_SCALAR_MIN = 0.5
_VOL_SCALAR_MAX = 2.0

# No single monthly buy exceeds this fraction of the budget; excess
# redistributes to the other buyable names (or stays unallocated).
MAX_POSITION_FRACTION = 0.35


def _sizing_weights(buyable: list[ScoredStock]) -> dict[str, float]:
    vols = [
        s.realized_vol for s in buyable
        if s.realized_vol is not None and s.realized_vol > 0
    ]
    median_vol = statistics.median(vols) if vols else None

    weights: dict[str, float] = {}
    for s in buyable:
        conviction = max(s.composite_score - _SIZING_BASELINE, 1.0)
        vol_scalar = 1.0
        if median_vol is not None and s.realized_vol is not None and s.realized_vol > 0:
            vol_scalar = min(
                max(median_vol / s.realized_vol, _VOL_SCALAR_MIN), _VOL_SCALAR_MAX,
            )
        weights[s.ticker] = conviction * vol_scalar
    return weights


def _capped_fractions(weights: dict[str, float]) -> dict[str, float]:
    """Proportional budget fractions with an iterative per-position cap:
    capped names hold at MAX_POSITION_FRACTION and the excess redistributes
    across the uncapped names. If everything caps, the rest stays unallocated."""
    total = sum(weights.values())
    fractions = {t: w / total for t, w in weights.items()}
    capped: set[str] = set()
    for _ in range(len(fractions)):
        over = {
            t for t, f in fractions.items()
            if t not in capped and f > MAX_POSITION_FRACTION + 1e-9
        }
        if not over:
            break
        capped |= over
        for t in over:
            fractions[t] = MAX_POSITION_FRACTION
        free = [t for t in fractions if t not in capped]
        remaining = 1.0 - MAX_POSITION_FRACTION * len(capped)
        if not free or remaining <= 0:
            break
        free_weight = sum(weights[t] for t in free)
        for t in free:
            fractions[t] = weights[t] / free_weight * remaining
    return fractions


def compute_position_sizes(
    ranked: list[ScoredStock],
    monthly_budget: float,
) -> None:
    buyable = [s for s in ranked if s.recommendation in ("Buy", "Strong Buy")]
    if not buyable or monthly_budget <= 0:
        return

    weights = _sizing_weights(buyable)
    if sum(weights.values()) <= 0:
        return
    fractions = _capped_fractions(weights)

    for stock in buyable:
        stock.suggested_amount = round(
            monthly_budget * fractions[stock.ticker], 2
        )
        if stock.current_price > 0:
            stock.suggested_shares = round(
                stock.suggested_amount / stock.current_price, 2
            )


WEAK_THRESHOLD = 50.0
MIN_SWAP_DELTA = 10.0


def generate_swaps(
    weak_holdings: list[ScoredStock],
    alternatives: list[ScoredStock],
    emergency_fund_tickers: set[str] | None = None,
) -> list[SwapSuggestion]:
    from data.models import SwapSuggestion as _SwapSuggestion

    ef_tickers = emergency_fund_tickers or set()
    swaps: list[_SwapSuggestion] = []
    for weak in weak_holdings:
        if weak.ticker in ef_tickers:
            continue
        if weak.composite_score >= WEAK_THRESHOLD:
            continue

        same_sector = [
            a for a in alternatives
            if a.sector == weak.sector
            and a.composite_score > weak.composite_score + MIN_SWAP_DELTA
        ]
        if same_sector:
            best = same_sector[0]
            delta = best.composite_score - weak.composite_score
            reason = f"Same sector ({weak.sector}), score delta +{delta:.0f} pts"
        else:
            candidates = [
                a for a in alternatives
                if a.composite_score > weak.composite_score + MIN_SWAP_DELTA
            ]
            if not candidates:
                continue
            best = candidates[0]
            delta = best.composite_score - weak.composite_score
            reason = f"Best alternative ({best.sector}), score delta +{delta:.0f} pts"

        swaps.append(_SwapSuggestion(
            sell_ticker=weak.ticker,
            sell_name=weak.name,
            sell_score=weak.composite_score,
            buy_ticker=best.ticker,
            buy_name=best.name,
            buy_score=best.composite_score,
            reason=reason,
        ))

    return swaps
