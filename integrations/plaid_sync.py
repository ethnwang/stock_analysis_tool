from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import plaid
import requests
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

logger = logging.getLogger(__name__)

CALL_LOG_PATH = Path(__file__).parent.parent / ".plaid_call_count"
FREE_TIER_LIMIT = 200


def _log_call(n: int = 1) -> int:
    count = 0
    if CALL_LOG_PATH.exists():
        try:
            count = int(CALL_LOG_PATH.read_text().strip())
        except ValueError:
            count = 0
    count += n
    CALL_LOG_PATH.write_text(str(count))
    logger.info("Plaid API calls: %d/%d", count, FREE_TIER_LIMIT)
    if count > FREE_TIER_LIMIT * 0.8:
        logger.warning("Approaching Plaid free tier limit!")
    return count


def _get_client(client_id: str, secret: str, env: str = "sandbox") -> plaid_api.PlaidApi:
    env_map = {
        "sandbox": plaid.Environment.Sandbox,
        "production": plaid.Environment.Production,
    }
    configuration = plaid.Configuration(
        host=env_map.get(env, plaid.Environment.Sandbox),
        api_key={"clientId": client_id, "secret": secret},
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def create_link_token(client_id: str, secret: str, env: str = "sandbox") -> str:
    client = _get_client(client_id, secret, env)

    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="stockbot-user"),
        client_name="StockBot",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
    )

    response = client.link_token_create(request)
    _log_call()
    return response["link_token"]


def exchange_public_token(client_id: str, secret: str, public_token: str, env: str = "sandbox") -> str:
    client = _get_client(client_id, secret, env)

    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = client.item_public_token_exchange(request)
    _log_call()
    return response["access_token"]


def get_balances(client_id: str, secret: str, access_token: str, env: str = "sandbox") -> list[dict[str, Any]]:
    client = _get_client(client_id, secret, env)

    request = AccountsBalanceGetRequest(access_token=access_token)
    response = client.accounts_balance_get(request)
    _log_call()

    accounts = []
    for acc in response.get("accounts", []):
        accounts.append({
            "account_id": acc.get("account_id", ""),
            "name": acc.get("name", ""),
            "official_name": acc.get("official_name", ""),
            "type": acc.get("type", ""),
            "subtype": acc.get("subtype", ""),
            "balance_current": acc.get("balances", {}).get("current", 0.0),
            "balance_available": acc.get("balances", {}).get("available", 0.0),
        })

    return accounts


def _plaid_host(env: str) -> str:
    hosts = {
        "sandbox": "https://sandbox.plaid.com",
        "production": "https://production.plaid.com",
    }
    return hosts.get(env, "https://sandbox.plaid.com")


def get_investment_holdings(client_id: str, secret: str, access_token: str, env: str = "sandbox") -> dict[str, Any]:
    resp = requests.post(
        f"{_plaid_host(env)}/investments/holdings/get",
        json={"client_id": client_id, "secret": secret, "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _log_call()

    securities = {}
    for sec in data.get("securities", []):
        securities[sec["security_id"]] = {
            "ticker": sec.get("ticker_symbol", ""),
            "name": sec.get("name", ""),
            "type": sec.get("type", ""),
        }

    holdings = []
    for h in data.get("holdings", []):
        sec = securities.get(h.get("security_id", ""), {})
        if not sec.get("ticker") or sec.get("type") == "cash":
            continue
        holdings.append({
            "account_id": h.get("account_id", ""),
            "ticker": sec["ticker"],
            "name": sec.get("name", ""),
            "shares": h.get("quantity", 0),
            "cost_basis": h.get("cost_basis", 0.0),
            "market_value": h.get("institution_value", 0.0),
        })

    accounts = []
    for acc in data.get("accounts", []):
        accounts.append({
            "account_id": acc.get("account_id", ""),
            "name": acc.get("name", ""),
            "type": acc.get("type", ""),
            "subtype": acc.get("subtype", ""),
            "balance": acc.get("balances", {}).get("current", 0.0),
        })

    return {"accounts": accounts, "holdings": holdings}


def _classify_chase_accounts(accounts: list[dict]) -> dict[str, Any]:
    result: dict[str, float] = {}
    for acc in accounts:
        subtype = str(acc.get("subtype", "")).lower()
        if "checking" in subtype:
            result["checking"] = round(acc.get("balance_current", 0.0), 2)
        elif "saving" in subtype:
            result["savings"] = round(acc.get("balance_current", 0.0), 2)
        else:
            result[acc.get("name", "other")] = round(acc.get("balance_current", 0.0), 2)
    return result


def _classify_fidelity_accounts(balances: list[dict], investment_data: dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    all_holdings = investment_data.get("holdings", [])

    for acc in investment_data.get("accounts", []):
        acc_type = str(acc.get("type", "")).lower()
        if acc_type != "investment":
            continue

        acc_id = acc.get("account_id", "")
        name = (acc.get("name") or "").lower()
        subtype = str(acc.get("subtype", "")).lower()

        if "401" in name or "401" in subtype:
            key = "401k"
        elif "hsa" in name or "hsa" in subtype:
            key = "hsa"
        else:
            key = acc.get("name", "other")

        account_holdings = [
            {k: v for k, v in h.items() if k != "account_id"}
            for h in all_holdings if h.get("account_id") == acc_id
        ]

        result[key] = {
            "balance": round(acc.get("balance", 0.0) or 0.0, 2),
            "holdings": account_holdings,
        }

    return result


def sync_chase(client_id: str, secret: str, access_token: str, env: str = "sandbox") -> dict[str, Any]:
    if not access_token:
        return {}
    try:
        accounts = get_balances(client_id, secret, access_token, env)
        result = _classify_chase_accounts(accounts)
        logger.info("Chase: synced %d account(s)", len(result))
        return result
    except (ConnectionError, TimeoutError, requests.RequestException) as exc:
        logger.error("Chase sync error: %s", exc)
        return {}


def sync_fidelity(client_id: str, secret: str, access_token: str, env: str = "sandbox") -> dict[str, Any]:
    if not access_token:
        return {}
    try:
        balances = get_balances(client_id, secret, access_token, env)
        investments = get_investment_holdings(client_id, secret, access_token, env)
        result = _classify_fidelity_accounts(balances, investments)
        logger.info("Fidelity: synced %d account(s)", len(result))
        return result
    except (ConnectionError, TimeoutError, requests.RequestException) as exc:
        logger.error("Fidelity sync error: %s", exc)
        return {}


def sync(client_id: str, secret: str, env: str, access_token_chase: str, access_token_fidelity: str) -> dict[str, Any]:
    if not (client_id and secret):
        return {}

    result: dict[str, Any] = {}

    chase_data = sync_chase(client_id, secret, access_token_chase, env)
    if chase_data:
        result["chase"] = chase_data

    fidelity_data = sync_fidelity(client_id, secret, access_token_fidelity, env)
    if fidelity_data:
        result["fidelity"] = fidelity_data

    return result
