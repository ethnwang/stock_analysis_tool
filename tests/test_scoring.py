from __future__ import annotations

import pytest

from data.models import ScoredStock
from scoring.engine import rank_stocks, score_stock
from tests.conftest import default_config, make_stock


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
