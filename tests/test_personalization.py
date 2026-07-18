from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from analysis.fundamental import score_fundamental, score_fundamental_adjusted
from data.models import ScoredStock
from portfolio.loader import (
    compute_position_sizes,
    generate_swaps,
    get_account_holdings,
    get_all_holdings,
    get_held_tickers_detailed,
    get_held_tickers_for_account,
    get_monthly_budget,
    get_sector_allocation,
    is_roth_maxed,
    load_portfolio,
    suggest_account,
)
from scoring.engine import rank_stocks, score_stock
from tests.conftest import default_config, make_stock


SAMPLE_PORTFOLIO = {
    "schwab_brokerage": {
        "account_id": "123",
        "holdings": [
            {"ticker": "NVDA", "shares": 120.0, "market_value": 24000.0},
            {"ticker": "AAPL", "shares": 10.0, "market_value": 1900.0},
        ],
    },
    "schwab_roth_ira": {
        "account_id": "456",
        "holdings": [
            {"ticker": "NVDA", "shares": 20.0, "market_value": 4000.0},
            {"ticker": "CRSP", "shares": 45.0, "market_value": 2300.0},
        ],
    },
    "fidelity": {
        "hsa": {
            "holdings": [
                {"ticker": "MSFT", "shares": 3.0, "market_value": 1300.0},
                {"ticker": "84679P611", "shares": 10.0, "market_value": 2000.0},
            ],
        },
        "401k": {
            "holdings": [
                {"ticker": "STATE ST S&P 500 IDX", "shares": 100.0, "market_value": 3800.0},
            ],
        },
        "roth_401k": {"holdings": []},
    },
    "monthly_income": {"take_home_pay": 3855.77},
    "monthly_expenses": {"personal_investment": 1090.50, "rent_and_utilities": 2400},
}


class TestPortfolioLoader:
    def test_load_missing_file_returns_none(self) -> None:
        result = load_portfolio(Path("/nonexistent/portfolio.json"))
        assert result is None

    def test_load_valid_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(SAMPLE_PORTFOLIO, f)
            f.flush()
            result = load_portfolio(Path(f.name))
        assert result is not None
        assert "schwab_brokerage" in result

    def test_get_all_holdings_flattens_accounts(self) -> None:
        holdings = get_all_holdings(SAMPLE_PORTFOLIO)
        tickers = [h["ticker"] for h in holdings]
        assert "NVDA" in tickers
        assert "AAPL" in tickers
        assert "CRSP" in tickers
        assert "MSFT" in tickers
        assert "84679P611" in tickers
        assert "STATE ST S&P 500 IDX" in tickers

    def test_get_all_holdings_includes_account_source(self) -> None:
        holdings = get_all_holdings(SAMPLE_PORTFOLIO)
        msft = [h for h in holdings if h["ticker"] == "MSFT"][0]
        assert msft["account"] == "fidelity_hsa"

    def test_get_held_tickers_excludes_cusips(self) -> None:
        detailed = get_held_tickers_detailed(SAMPLE_PORTFOLIO)
        assert "NVDA" in detailed
        assert "AAPL" in detailed
        assert "84679P611" not in detailed
        assert "STATE ST S&P 500 IDX" not in detailed

    def test_get_held_tickers_aggregates_across_accounts(self) -> None:
        detailed = get_held_tickers_detailed(SAMPLE_PORTFOLIO)
        nvda_holdings = detailed["NVDA"]
        assert len(nvda_holdings) == 2
        total_shares = sum(h["shares"] for h in nvda_holdings)
        assert total_shares == 140.0

    def test_get_monthly_budget(self) -> None:
        budget = get_monthly_budget(SAMPLE_PORTFOLIO)
        assert budget == 1090.50

    def test_get_monthly_budget_missing_data(self) -> None:
        budget = get_monthly_budget({})
        assert budget == 0.0

    def test_get_sector_allocation(self) -> None:
        holdings = [
            {"ticker": "NVDA", "market_value": 7000},
            {"ticker": "AAPL", "market_value": 3000},
            {"ticker": "JPM", "market_value": 2000},
        ]
        sector_map = {"NVDA": "Technology", "AAPL": "Technology", "JPM": "Financials"}
        allocation = get_sector_allocation(holdings, sector_map)
        assert allocation["Technology"] == pytest.approx(83.3, abs=0.1)
        assert allocation["Financials"] == pytest.approx(16.7, abs=0.1)

    def test_get_sector_allocation_empty(self) -> None:
        assert get_sector_allocation([], {}) == {}


class TestRiskAdjustedScoring:
    def test_aggressive_favors_growth_stocks(self) -> None:
        growth_fundamentals = {
            "pe_ratio": 45.0,
            "eps_growth": 0.40,
            "revenue_growth": 0.30,
            "debt_to_equity": 0.5,
            "dividend_yield": 0.0,
        }
        aggressive = score_fundamental_adjusted(growth_fundamentals, "aggressive")
        conservative = score_fundamental_adjusted(growth_fundamentals, "conservative")
        assert aggressive.score > conservative.score

    def test_conservative_favors_value_stocks(self) -> None:
        value_fundamentals = {
            "pe_ratio": 12.0,
            "eps_growth": 0.05,
            "revenue_growth": 0.03,
            "debt_to_equity": 0.3,
            "dividend_yield": 0.04,
        }
        conservative = score_fundamental_adjusted(value_fundamentals, "conservative")
        aggressive = score_fundamental_adjusted(value_fundamentals, "aggressive")
        assert conservative.score > aggressive.score

    def test_moderate_matches_default_scoring(self) -> None:
        fundamentals = {
            "pe_ratio": 20.0,
            "eps_growth": 0.10,
            "revenue_growth": 0.10,
            "debt_to_equity": 0.5,
            "dividend_yield": 0.02,
        }
        assert score_fundamental_adjusted(fundamentals, "moderate").score == score_fundamental(fundamentals).score

    def test_default_risk_profile_is_moderate(self) -> None:
        config = default_config()
        assert config.risk_profile == "moderate"

    def test_risk_profile_applied_in_scoring(self) -> None:
        stock = make_stock(pe=40.0, eps_growth=0.35, rev_growth=0.25, div_yield=0.0)
        config_agg = default_config(risk_profile="aggressive")
        config_con = default_config(risk_profile="conservative")
        result_agg = score_stock(stock, config_agg)
        result_con = score_stock(stock, config_con)
        assert result_agg.composite_score > result_con.composite_score


class TestPortfolioOverlap:
    def test_held_stock_gets_penalty(self) -> None:
        stock = make_stock("NVDA")
        config = default_config()
        held = {"NVDA": [{"account": "brokerage", "shares": 120.0}]}
        result = score_stock(stock, config, held_tickers=held)
        assert result.is_held is True
        assert result.held_shares == 120.0
        assert result.overlap_penalty > 0

    def test_strong_conviction_no_penalty(self) -> None:
        stock = make_stock(
            "BEST", pe=10.0, eps_growth=0.40, rev_growth=0.30,
            de_ratio=0.2, div_yield=0.03,
        )
        config = default_config()
        result_without = score_stock(stock, config)

        if result_without.composite_score <= 80:
            pytest.skip("Random price data produced composite <= 80; can't test conviction bypass")

        held = {"BEST": [{"account": "brokerage", "shares": 5.0}]}
        result_with = score_stock(stock, config, held_tickers=held)
        assert result_with.is_held is True
        assert result_with.overlap_penalty == 0

    def test_no_portfolio_no_penalty(self) -> None:
        stock = make_stock("NVDA")
        config = default_config()
        result = score_stock(stock, config, held_tickers=None)
        assert result.is_held is False
        assert result.overlap_penalty == 0

    def test_held_accounts_populated(self) -> None:
        stock = make_stock("NVDA")
        config = default_config()
        held = {
            "NVDA": [
                {"account": "schwab_brokerage", "shares": 120.0},
                {"account": "schwab_roth_ira", "shares": 20.0},
            ]
        }
        result = score_stock(stock, config, held_tickers=held)
        assert "schwab_brokerage" in result.held_accounts
        assert "schwab_roth_ira" in result.held_accounts
        assert result.held_shares == 140.0


class TestSectorDiversification:
    def test_overweight_sector_penalized(self) -> None:
        stocks = [make_stock("A", sector="Technology")]
        config = default_config(top_n=5)
        sector_alloc = {"Technology": 55.0}
        ranked = rank_stocks(stocks, config, sector_allocation=sector_alloc)
        assert ranked[0].sector_penalty > 0
        assert any("overweight" in r.lower() for r in ranked[0].reasoning)

    def test_underweight_sector_no_penalty(self) -> None:
        stocks = [make_stock("A", sector="Healthcare")]
        config = default_config(top_n=5)
        sector_alloc = {"Healthcare": 10.0}
        ranked = rank_stocks(stocks, config, sector_allocation=sector_alloc)
        assert ranked[0].sector_penalty == 0

    def test_no_sector_data_no_penalty(self) -> None:
        stocks = [make_stock("A")]
        config = default_config(top_n=5)
        ranked = rank_stocks(stocks, config, sector_allocation=None)
        assert ranked[0].sector_penalty == 0


class TestPositionSizing:
    def test_budget_split_proportional_to_score(self) -> None:
        s1 = ScoredStock(
            ticker="A", name="A", sector="Tech", current_price=100.0,
            technical_score=70, fundamental_score=80, sentiment_score=60,
            composite_score=75.0, recommendation="Buy",
        )
        s2 = ScoredStock(
            ticker="B", name="B", sector="Tech", current_price=50.0,
            technical_score=70, fundamental_score=80, sentiment_score=60,
            composite_score=65.0, recommendation="Buy",
        )
        compute_position_sizes([s1, s2], 1000.0)
        assert s1.suggested_amount > s2.suggested_amount
        assert abs(s1.suggested_amount + s2.suggested_amount - 1000.0) < 0.05

    def test_only_buy_recommendations_get_allocation(self) -> None:
        buy = ScoredStock(
            ticker="A", name="A", sector="Tech", current_price=100.0,
            technical_score=70, fundamental_score=80, sentiment_score=60,
            composite_score=70.0, recommendation="Buy",
        )
        hold = ScoredStock(
            ticker="B", name="B", sector="Tech", current_price=50.0,
            technical_score=50, fundamental_score=50, sentiment_score=50,
            composite_score=50.0, recommendation="Hold",
        )
        compute_position_sizes([buy, hold], 1000.0)
        assert buy.suggested_amount == 1000.0
        assert hold.suggested_amount == 0.0

    def test_zero_budget_no_allocation(self) -> None:
        s = ScoredStock(
            ticker="A", name="A", sector="Tech", current_price=100.0,
            technical_score=70, fundamental_score=80, sentiment_score=60,
            composite_score=70.0, recommendation="Buy",
        )
        compute_position_sizes([s], 0.0)
        assert s.suggested_amount == 0.0

    def test_suggested_shares_computed(self) -> None:
        s = ScoredStock(
            ticker="A", name="A", sector="Tech", current_price=100.0,
            technical_score=70, fundamental_score=80, sentiment_score=60,
            composite_score=70.0, recommendation="Buy",
        )
        compute_position_sizes([s], 500.0)
        assert s.suggested_shares == pytest.approx(5.0, abs=0.01)


class TestAccountPlacement:
    def test_high_growth_low_div_goes_roth(self) -> None:
        account, _ = suggest_account(eps_growth=0.30, dividend_yield=0.0, recommendation="Buy")
        assert account == "Roth IRA"

    def test_high_dividend_goes_brokerage(self) -> None:
        account, _ = suggest_account(eps_growth=0.05, dividend_yield=0.04, recommendation="Buy")
        assert account == "Brokerage"

    def test_hold_goes_brokerage(self) -> None:
        account, _ = suggest_account(eps_growth=0.10, dividend_yield=0.0, recommendation="Hold")
        assert account == "Brokerage"

    def test_default_growth_buy_goes_roth(self) -> None:
        account, _ = suggest_account(eps_growth=0.08, dividend_yield=0.005, recommendation="Strong Buy")
        assert account == "Roth IRA"

    def test_no_growth_goes_brokerage(self) -> None:
        account, _ = suggest_account(eps_growth=-0.05, dividend_yield=0.0, recommendation="Buy")
        assert account == "Brokerage"

    def test_roth_maxed_redirects_to_brokerage(self) -> None:
        account, reason = suggest_account(
            eps_growth=0.30, dividend_yield=0.0,
            recommendation="Buy", roth_ira_maxed=True,
        )
        assert account == "Brokerage"
        assert "maxed" in reason.lower()

    def test_roth_maxed_high_div_still_brokerage(self) -> None:
        account, reason = suggest_account(
            eps_growth=0.05, dividend_yield=0.04,
            recommendation="Buy", roth_ira_maxed=True,
        )
        assert account == "Brokerage"
        assert "dividend" in reason.lower()

    def test_roth_not_maxed_still_suggests_roth(self) -> None:
        account, _ = suggest_account(
            eps_growth=0.30, dividend_yield=0.0,
            recommendation="Buy", roth_ira_maxed=False,
        )
        assert account == "Roth IRA"


class TestAccountHoldings:
    def test_get_account_holdings_schwab_roth(self) -> None:
        holdings = get_account_holdings(SAMPLE_PORTFOLIO, "schwab_roth_ira")
        tickers = [h["ticker"] for h in holdings]
        assert "NVDA" in tickers
        assert "CRSP" in tickers
        assert len(holdings) == 2

    def test_get_account_holdings_schwab_brokerage(self) -> None:
        holdings = get_account_holdings(SAMPLE_PORTFOLIO, "schwab_brokerage")
        tickers = [h["ticker"] for h in holdings]
        assert "NVDA" in tickers
        assert "AAPL" in tickers
        assert "CRSP" not in tickers

    def test_get_account_holdings_fidelity_hsa(self) -> None:
        holdings = get_account_holdings(SAMPLE_PORTFOLIO, "fidelity_hsa")
        tickers = [h["ticker"] for h in holdings]
        assert "MSFT" in tickers
        assert len(holdings) == 2

    def test_get_account_holdings_includes_account_key(self) -> None:
        holdings = get_account_holdings(SAMPLE_PORTFOLIO, "fidelity_hsa")
        assert all(h["account"] == "fidelity_hsa" for h in holdings)

    def test_get_account_holdings_empty(self) -> None:
        holdings = get_account_holdings(SAMPLE_PORTFOLIO, "nonexistent_account")
        assert holdings == []

    def test_get_held_tickers_for_account_scoped(self) -> None:
        held = get_held_tickers_for_account(SAMPLE_PORTFOLIO, "schwab_roth_ira")
        assert "NVDA" in held
        assert "CRSP" in held
        assert "AAPL" not in held

    def test_get_held_tickers_for_account_excludes_cusips(self) -> None:
        held = get_held_tickers_for_account(SAMPLE_PORTFOLIO, "fidelity_hsa")
        assert "MSFT" in held
        assert "84679P611" not in held

    def test_is_roth_maxed_default_false(self) -> None:
        assert is_roth_maxed(SAMPLE_PORTFOLIO) is False

    def test_is_roth_maxed_when_set(self) -> None:
        p = {**SAMPLE_PORTFOLIO, "roth_ira_maxed": True}
        assert is_roth_maxed(p) is True


class TestSwapSuggestions:
    def test_same_sector_swap_preferred(self) -> None:
        weak = ScoredStock(
            ticker="BAD", name="Bad Tech", sector="Technology",
            current_price=50.0, technical_score=30, fundamental_score=30,
            sentiment_score=30, composite_score=30.0, recommendation="Avoid",
        )
        alt_same = ScoredStock(
            ticker="GOOD", name="Good Tech", sector="Technology",
            current_price=100.0, technical_score=70, fundamental_score=80,
            sentiment_score=60, composite_score=72.0, recommendation="Buy",
        )
        alt_other = ScoredStock(
            ticker="BEST", name="Best Health", sector="Healthcare",
            current_price=200.0, technical_score=80, fundamental_score=85,
            sentiment_score=70, composite_score=80.0, recommendation="Strong Buy",
        )
        swaps = generate_swaps([weak], [alt_other, alt_same])
        assert len(swaps) == 1
        assert swaps[0].buy_ticker == "GOOD"
        assert "Same sector" in swaps[0].reason

    def test_falls_back_to_best_overall(self) -> None:
        weak = ScoredStock(
            ticker="BAD", name="Bad Energy", sector="Energy",
            current_price=50.0, technical_score=30, fundamental_score=30,
            sentiment_score=30, composite_score=30.0, recommendation="Avoid",
        )
        alt = ScoredStock(
            ticker="GOOD", name="Good Tech", sector="Technology",
            current_price=100.0, technical_score=70, fundamental_score=80,
            sentiment_score=60, composite_score=72.0, recommendation="Buy",
        )
        swaps = generate_swaps([weak], [alt])
        assert len(swaps) == 1
        assert swaps[0].buy_ticker == "GOOD"
        assert "Best alternative" in swaps[0].reason

    def test_no_swap_when_delta_too_small(self) -> None:
        weak = ScoredStock(
            ticker="OK", name="Ok Stock", sector="Technology",
            current_price=50.0, technical_score=40, fundamental_score=45,
            sentiment_score=40, composite_score=45.0, recommendation="Hold",
        )
        alt = ScoredStock(
            ticker="SLIGHT", name="Slightly Better", sector="Technology",
            current_price=100.0, technical_score=50, fundamental_score=50,
            sentiment_score=50, composite_score=52.0, recommendation="Hold",
        )
        swaps = generate_swaps([weak], [alt])
        assert len(swaps) == 0

    def test_no_swap_for_stocks_above_threshold(self) -> None:
        strong = ScoredStock(
            ticker="GOOD", name="Good Stock", sector="Technology",
            current_price=100.0, technical_score=60, fundamental_score=60,
            sentiment_score=60, composite_score=60.0, recommendation="Hold",
        )
        alt = ScoredStock(
            ticker="GREAT", name="Great Stock", sector="Technology",
            current_price=200.0, technical_score=80, fundamental_score=80,
            sentiment_score=70, composite_score=78.0, recommendation="Buy",
        )
        swaps = generate_swaps([strong], [alt])
        assert len(swaps) == 0
