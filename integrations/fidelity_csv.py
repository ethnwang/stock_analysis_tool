from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _clean_dollar(val: str) -> float:
    if not val:
        return 0.0
    return float(val.replace("$", "").replace(",", "").replace("+", ""))


def _classify_account(name: str) -> str:
    lower = name.lower()
    if "roth" in lower:
        return "roth_401k"
    if "401k" in lower or "401 k" in lower:
        return "401k"
    if "hsa" in lower or "health savings" in lower:
        return "hsa"
    return name


def parse_positions(csv_path: str | Path) -> dict[str, Any]:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        logger.error("%s not found", csv_path)
        return {}

    accounts: dict[str, dict[str, Any]] = {}

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            account_name = (row.get("Account Name") or "").strip()
            if not account_name:
                break

            key = _classify_account(account_name)
            if key not in accounts:
                accounts[key] = {"balance": 0.0, "holdings": []}

            symbol = (row.get("Symbol") or "").strip().rstrip("*")
            description = (row.get("Description") or "").strip()
            quantity = (row.get("Quantity") or "").strip()
            current_value = (row.get("Current Value") or "").strip()
            cost_basis = (row.get("Cost Basis Total") or "").strip()
            avg_cost = (row.get("Average Cost Basis") or "").strip()
            asset_type = (row.get("Type") or "").strip()

            value = _clean_dollar(current_value)
            accounts[key]["balance"] += value

            if asset_type == "Cash" and "MONEY MARKET" in description.upper():
                continue

            if not quantity:
                continue

            holding = {
                "ticker": symbol or description,
                "name": description,
                "shares": float(quantity),
                "market_value": value,
            }

            if cost_basis:
                holding["cost_basis"] = _clean_dollar(cost_basis)
            if avg_cost:
                holding["avg_cost"] = _clean_dollar(avg_cost)

            accounts[key]["holdings"].append(holding)

    for acc in accounts.values():
        acc["balance"] = round(acc["balance"], 2)

    return accounts


def import_to_portfolio(csv_path: str | Path, portfolio_path: str | Path) -> dict[str, Any]:
    import json

    portfolio_path = Path(portfolio_path)
    portfolio: dict[str, Any] = {}
    if portfolio_path.exists():
        try:
            portfolio = json.loads(portfolio_path.read_text())
        except (json.JSONDecodeError, OSError):
            portfolio = {}

    accounts = parse_positions(csv_path)
    if not accounts:
        return portfolio

    fidelity = portfolio.get("fidelity", {})
    fidelity.update(accounts)
    portfolio["fidelity"] = fidelity

    portfolio_path.write_text(json.dumps(portfolio, indent=2) + "\n")

    count = len(accounts)
    holdings = sum(len(a["holdings"]) for a in accounts.values())
    logger.info("Fidelity: imported %d account(s), %d position(s)", count, holdings)

    return portfolio
