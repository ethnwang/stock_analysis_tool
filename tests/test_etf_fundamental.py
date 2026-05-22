from __future__ import annotations

from analysis.etf_fundamental import score_etf_fundamental


class TestScoreEtfFundamental:
    def test_excellent_etf_scores_high(self) -> None:
        fundamentals = {
            "expense_ratio": 0.0003,
            "total_assets": 400_000_000_000,
            "top10_concentration": 0.30,
            "one_year_return": 0.25,
            "three_year_return": 0.18,
            "five_year_return": 0.14,
            "one_year_return_vs_cat": 0.04,
            "dividend_yield": 0.015,
            "is_etf": 1.0,
        }
        score, reasons = score_etf_fundamental(fundamentals)
        assert score >= 75

    def test_poor_etf_scores_low(self) -> None:
        fundamentals = {
            "expense_ratio": 0.015,
            "total_assets": 50_000_000,
            "top10_concentration": 0.80,
            "one_year_return": -0.05,
            "three_year_return": -0.02,
            "five_year_return": 0.01,
            "one_year_return_vs_cat": -0.05,
            "dividend_yield": 0.0,
            "is_etf": 1.0,
        }
        score, reasons = score_etf_fundamental(fundamentals)
        assert score <= 30

    def test_score_in_valid_range(self) -> None:
        fundamentals = {
            "expense_ratio": 0.005,
            "total_assets": 5_000_000_000,
            "top10_concentration": 0.45,
            "one_year_return": 0.10,
            "three_year_return": 0.08,
            "five_year_return": 0.07,
            "one_year_return_vs_cat": 0.01,
            "dividend_yield": 0.02,
            "is_etf": 1.0,
        }
        score, _ = score_etf_fundamental(fundamentals)
        assert 0.0 <= score <= 100.0

    def test_missing_data_returns_neutral(self) -> None:
        score, reasons = score_etf_fundamental({})
        assert 0.0 <= score <= 100.0
        assert len(reasons) > 0

    def test_returns_reasoning_strings(self) -> None:
        fundamentals = {
            "expense_ratio": 0.0003,
            "total_assets": 100_000_000_000,
            "top10_concentration": 0.30,
            "one_year_return": 0.15,
            "three_year_return": 0.12,
            "five_year_return": 0.10,
            "one_year_return_vs_cat": 0.02,
            "dividend_yield": 0.01,
            "is_etf": 1.0,
        }
        _, reasons = score_etf_fundamental(fundamentals)
        assert len(reasons) == 6
        assert any("Expense" in r for r in reasons)
        assert any("return" in r.lower() for r in reasons)
        assert any("AUM" in r for r in reasons)

    def test_risk_profile_affects_scoring(self) -> None:
        fundamentals = {
            "expense_ratio": 0.0003,
            "total_assets": 100_000_000_000,
            "top10_concentration": 0.30,
            "one_year_return": 0.30,
            "three_year_return": 0.08,
            "five_year_return": 0.05,
            "one_year_return_vs_cat": 0.05,
            "dividend_yield": 0.05,
            "is_etf": 1.0,
        }
        aggressive_score, _ = score_etf_fundamental(fundamentals, "aggressive")
        conservative_score, _ = score_etf_fundamental(fundamentals, "conservative")
        assert aggressive_score != conservative_score

    def test_low_expense_scores_higher_than_high_expense(self) -> None:
        base = {
            "total_assets": 10_000_000_000,
            "top10_concentration": 0.30,
            "one_year_return": 0.10,
            "three_year_return": 0.10,
            "five_year_return": 0.10,
            "one_year_return_vs_cat": 0.0,
            "dividend_yield": 0.01,
            "is_etf": 1.0,
        }
        low_expense = {**base, "expense_ratio": 0.0003}
        high_expense = {**base, "expense_ratio": 0.012}
        low_score, _ = score_etf_fundamental(low_expense)
        high_score, _ = score_etf_fundamental(high_expense)
        assert low_score > high_score

    def test_large_aum_scores_higher_than_small(self) -> None:
        base = {
            "expense_ratio": 0.003,
            "top10_concentration": 0.30,
            "one_year_return": 0.10,
            "three_year_return": 0.10,
            "five_year_return": 0.10,
            "one_year_return_vs_cat": 0.0,
            "dividend_yield": 0.01,
            "is_etf": 1.0,
        }
        large_aum = {**base, "total_assets": 200_000_000_000}
        small_aum = {**base, "total_assets": 50_000_000}
        large_score, _ = score_etf_fundamental(large_aum)
        small_score, _ = score_etf_fundamental(small_aum)
        assert large_score > small_score
