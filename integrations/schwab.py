from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from schwab import SchwabAuth, SchwabClient

CALLBACK_URL = "https://127.0.0.1:5000/callback"


def _parse_positions(positions_dict: dict[str, Any]) -> list[dict[str, Any]]:
    holdings = []
    for symbol, pos in positions_dict.items():
        if hasattr(pos, "instrument"):
            asset_type = getattr(pos.instrument, "asset_type", "") or getattr(pos.instrument, "assetType", "")
            if str(asset_type).upper() == "CASH_EQUIVALENT":
                continue
        shares = getattr(pos, "long_quantity", 0) or getattr(pos, "settled_long_quantity", 0)
        avg_cost = getattr(pos, "average_price", 0.0)
        market_value = getattr(pos, "market_value", 0.0)
        holdings.append({
            "ticker": symbol,
            "shares": shares,
            "avg_cost": avg_cost,
            "market_value": market_value,
        })
    return holdings


def create_client(client_id: str, client_secret: str, refresh_token: str) -> SchwabClient:
    auth = SchwabAuth(client_id=client_id, client_secret=client_secret, redirect_uri=CALLBACK_URL)
    auth.refresh_token = refresh_token
    auth.refresh_access_token()
    return SchwabClient(client_id=client_id, client_secret=client_secret, redirect_uri=CALLBACK_URL, auth=auth)


def get_authorization_url(client_id: str, client_secret: str) -> str:
    auth = SchwabAuth(client_id=client_id, client_secret=client_secret, redirect_uri=CALLBACK_URL)
    return auth.get_authorization_url()


def exchange_code(client_id: str, client_secret: str, code: str) -> str:
    auth = SchwabAuth(client_id=client_id, client_secret=client_secret, redirect_uri=CALLBACK_URL)
    tokens = auth.exchange_code_for_tokens(authorization_code=code)
    return tokens.get("refresh_token", "")


def sync(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    if not (client_id and client_secret and refresh_token):
        return {}

    try:
        client = create_client(client_id, client_secret, refresh_token)
    except Exception as exc:
        logger.error("Schwab auth failed: %s", exc)
        return {}

    result: dict[str, Any] = {}

    try:
        accounts = client.get_accounts(include_positions=True)
        if not accounts:
            logger.warning("No Schwab accounts found.")
            return {}

        for account in accounts:
            sec = account.securities_account
            if sec is None:
                continue

            account_id = getattr(sec, "account_number", "") or getattr(sec, "accountNumber", "")
            acc_type = getattr(sec, "type", "unknown")

            current_bal = getattr(sec, "current_balances", None)
            initial_bal = getattr(sec, "initial_balances", None)

            cash = 0.0
            total_account_value = 0.0

            if current_bal:
                cash = (
                    getattr(current_bal, "cash_balance", 0.0)
                    or getattr(current_bal, "total_cash", 0.0)
                    or 0.0
                )
                total_account_value = (
                    getattr(current_bal, "liquidation_value", 0.0)
                    or getattr(current_bal, "equity", 0.0)
                    or 0.0
                )

            if not total_account_value and initial_bal:
                total_account_value = (
                    getattr(initial_bal, "liquidation_value", 0.0)
                    or getattr(initial_bal, "account_value", 0.0)
                    or 0.0
                )

            positions_dict = account.positions
            holdings = _parse_positions(positions_dict)
            holdings_value = sum(h["market_value"] for h in holdings)
            total_value = total_account_value if total_account_value else (holdings_value + cash)

            if not cash and total_value > holdings_value:
                cash = round(total_value - holdings_value, 2)

            if total_value == 0 and not holdings:
                logger.info("Account %s: type=%s — empty, skipping", account_id, acc_type)
                continue

            if acc_type == "CASH":
                label = "schwab_roth_ira"
            else:
                label = "schwab_brokerage"

            logger.info("Account %s: type=%s, positions=%d, value=$%.2f", account_id, acc_type, len(positions_dict), total_value)

            result[label] = {
                "account_id": account_id,
                "account_type": acc_type,
                "cash": round(cash, 2),
                "total_value": round(total_value, 2),
                "holdings": holdings,
            }

        logger.info("Schwab: synced %d account(s)", len(result))
    except Exception as exc:
        logger.error("Schwab sync error: %s", exc)

    return result
