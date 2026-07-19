from __future__ import annotations

import pytest

from data.models import ScoredStock
from scoring.engine import rank_stocks, score_stock
from tests.conftest import default_config, make_etf, make_stock


class TestScoreStock:
    def test_returns_scored_stock(self) -> None:
        stock = make_stock()
        config = default_config()
        result = score_stock(stock, config)

        assert isinstance(result, ScoredStock)
        assert result.ticker == "TEST"
        assert 0 <= result.composite_score <= 100

    def test_recommendation_labels(self) -> None:
        stock = make_stock()
        config = default_config()
        result = score_stock(stock, config)

        assert result.recommendation in {"Strong Buy", "Buy", "Hold", "Avoid"}

    def test_reasoning_populated(self) -> None:
        stock = make_stock()
        config = default_config()
        result = score_stock(stock, config)

        assert len(result.reasoning) > 0
        assert any("Technical" in r for r in result.reasoning)
        assert any("Fundamental" in r for r in result.reasoning)
        assert any("Sentiment" in r for r in result.reasoning)


class TestRankStocks:
    def test_ranked_by_score_descending(self) -> None:
        stocks = [
            make_stock("LOW", pe=60.0, eps_growth=-0.20, rev_growth=-0.10, de_ratio=4.0),
            make_stock("HIGH", pe=10.0, eps_growth=0.30, rev_growth=0.25, de_ratio=0.2, div_yield=0.04),
            make_stock("MID", pe=25.0, eps_growth=0.05, rev_growth=0.05),
        ]
        config = default_config(top_n=3)
        ranked = rank_stocks(stocks, config)

        assert len(ranked) == 3
        scores = [s.composite_score for s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_respects_top_n(self) -> None:
        stocks = [make_stock(f"S{i}") for i in range(5)]
        config = default_config(top_n=2)
        ranked = rank_stocks(stocks, config)

        assert len(ranked) == 2

    def test_empty_input(self) -> None:
        config = default_config()
        ranked = rank_stocks([], config)
        assert ranked == []

    def test_weights_affect_scoring(self) -> None:
        stock = make_stock(pe=10.0, eps_growth=0.30)

        config_tech_heavy = default_config(weight_technical=0.80, weight_fundamental=0.10, weight_sentiment=0.10)
        config_fund_heavy = default_config(weight_technical=0.10, weight_fundamental=0.80, weight_sentiment=0.10)

        result_tech = score_stock(stock, config_tech_heavy)
        result_fund = score_stock(stock, config_fund_heavy)

        assert result_tech.composite_score != result_fund.composite_score


class TestScoreEtfDispatch:
    def test_etf_uses_etf_scorer(self) -> None:
        etf = make_etf()
        config = default_config()
        result = score_stock(etf, config)

        assert result.is_etf is True
        assert any("Expense" in r or "expense" in r for r in result.reasoning)

    def test_stock_uses_stock_scorer(self) -> None:
        stock = make_stock()
        config = default_config()
        result = score_stock(stock, config)

        assert result.is_etf is False
        assert any("P/E" in r for r in result.reasoning)

    def test_etf_eps_growth_is_zero(self) -> None:
        etf = make_etf()
        config = default_config()
        result = score_stock(etf, config)

        assert result.eps_growth == 0.0

    def test_mixed_ranking(self) -> None:
        stocks = [
            make_stock("AAPL", pe=15.0, eps_growth=0.20),
            make_etf("VOO"),
        ]
        config = default_config(top_n=10)
        ranked = rank_stocks(stocks, config)

        assert len(ranked) == 2
        tickers = {s.ticker for s in ranked}
        assert "AAPL" in tickers
        assert "VOO" in tickers


class TestComponents:
    def test_momentum_component_recorded(self) -> None:
        result = score_stock(make_stock(), default_config())
        assert "momentum" in result.components
        assert "mom_12_1" in result.components
        assert 0 <= result.components["momentum"] <= 100

    def test_short_history_omits_momentum_component(self) -> None:
        stock = make_stock()
        stock.price_history = stock.price_history.iloc[:100]  # < 252 bars
        result = score_stock(stock, default_config())
        assert "momentum" not in result.components


class TestDataCompleteness:
    def test_full_data_has_high_completeness(self) -> None:
        stock = make_stock()
        config = default_config()
        result = score_stock(stock, config)

        assert result.data_completeness >= 0.85
        assert not result.insufficient_data

    def test_empty_fundamentals_lowers_completeness(self) -> None:
        stock = make_stock()
        stock.fundamentals = {
            "pe_ratio": None, "eps_growth": None, "revenue_growth": None,
            "debt_to_equity": None, "dividend_yield": 0.0,
        }
        config = default_config()
        result = score_stock(stock, config)

        assert result.data_completeness < score_stock(make_stock(), config).data_completeness

    def test_insufficient_data_flagged_and_labeled(self) -> None:
        stock = make_stock()
        stock.fundamentals = {
            "pe_ratio": None, "eps_growth": None, "revenue_growth": None,
            "debt_to_equity": None, "dividend_yield": 0.0,
        }
        stock.price_history = stock.price_history.iloc[:10]  # kills technical too
        config = default_config()
        result = score_stock(stock, config)

        assert result.insufficient_data
        assert result.recommendation == "Insufficient Data"

    def test_insufficient_stocks_excluded_from_ranking(self) -> None:
        good = make_stock("GOOD")
        bad = make_stock("BAD")
        bad.fundamentals = {
            "pe_ratio": None, "eps_growth": None, "revenue_growth": None,
            "debt_to_equity": None, "dividend_yield": 0.0,
        }
        bad.price_history = bad.price_history.iloc[:10]
        config = default_config(top_n=5)

        ranked = rank_stocks([good, bad], config)
        assert [s.ticker for s in ranked] == ["GOOD"]

        included = rank_stocks([good, bad], config, include_incomplete=True)
        assert {s.ticker for s in included} == {"GOOD", "BAD"}

    def test_return_all_keeps_insufficient_stocks(self) -> None:
        bad = make_stock("BAD")
        bad.fundamentals = {
            "pe_ratio": None, "eps_growth": None, "revenue_growth": None,
            "debt_to_equity": None, "dividend_yield": 0.0,
        }
        bad.price_history = bad.price_history.iloc[:10]
        config = default_config()

        ranked = rank_stocks([bad], config, return_all=True)
        assert len(ranked) == 1
        assert ranked[0].insufficient_data

    def test_completeness_keeps_original_weights_when_pillars_drop(self) -> None:
        # The composite renormalizes over active pillars, but completeness
        # deliberately keeps the ORIGINAL weights: a stock scored on the
        # fundamental pillar alone must report only that pillar's weight as
        # completeness, so it can't slip past the min_data_completeness gate.
        stock = make_stock()
        stock.price_history = stock.price_history.iloc[:10]  # technical dropped
        config = default_config()  # no news — sentiment dropped too
        result = score_stock(stock, config)

        assert result.data_completeness == pytest.approx(config.weight_fundamental)

    def test_technical_pillar_dropped_renormalizes_composite(self) -> None:
        stock = make_stock()
        stock.price_history = stock.price_history.iloc[:10]
        config = default_config()
        result = score_stock(stock, config)

        # with no price history AND no news, both those pillars are dropped —
        # composite must equal the fundamental score alone, not include
        # fake neutral-50 pillars
        assert result.composite_score == pytest.approx(result.fundamental_score, abs=0.1)
