from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations import plaid_sync, schwab

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = Path(__file__).parent.parent / "portfolio.json"


def _read_portfolio() -> dict[str, Any]:
    if PORTFOLIO_PATH.exists():
        try:
            return json.loads(PORTFOLIO_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_portfolio(data: dict[str, Any]) -> None:
    PORTFOLIO_PATH.write_text(json.dumps(data, indent=2) + "\n")


def sync_portfolio(
    *,
    schwab_client_id: str = "",
    schwab_client_secret: str = "",
    schwab_refresh_token: str = "",
    plaid_client_id: str = "",
    plaid_secret: str = "",
    plaid_env: str = "sandbox",
    plaid_access_token_chase: str = "",
    plaid_access_token_fidelity: str = "",
) -> dict[str, Any]:
    portfolio = _read_portfolio()
    synced_any = False

    if schwab_client_id and schwab_client_secret and schwab_refresh_token:
        logger.info("Syncing Schwab accounts...")
        schwab_data = schwab.sync(schwab_client_id, schwab_client_secret, schwab_refresh_token)
        if schwab_data:
            for key in [k for k in portfolio if k.startswith("schwab_")]:
                del portfolio[key]
            portfolio.update(schwab_data)
            synced_any = True
    else:
        logger.info("Schwab: skipped (no credentials)")

    if plaid_client_id and plaid_secret:
        has_plaid_tokens = plaid_access_token_chase or plaid_access_token_fidelity
        if has_plaid_tokens:
            logger.info("Syncing Plaid accounts...")
            plaid_data = plaid_sync.sync(
                plaid_client_id,
                plaid_secret,
                plaid_env,
                plaid_access_token_chase,
                plaid_access_token_fidelity,
            )
            if plaid_data:
                portfolio.update(plaid_data)
                synced_any = True
        else:
            logger.info("Plaid: skipped (no access tokens — run 'link' first)")
    else:
        logger.info("Plaid: skipped (no credentials)")

    if synced_any:
        portfolio["last_sync"] = datetime.now(timezone.utc).isoformat()
        _write_portfolio(portfolio)
        logger.info("Portfolio written to %s", PORTFOLIO_PATH)
    else:
        logger.warning("No accounts synced. Add credentials to .env and link institutions first.")

    return portfolio
