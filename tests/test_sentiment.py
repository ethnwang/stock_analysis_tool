from __future__ import annotations

import time

from analysis.sentiment import _analyze_text, _recency_weight, score_sentiment


class TestAnalyzeText:
    def test_single_positive_keyword(self) -> None:
        pos, neg = _analyze_text("Stock rally continues")
        assert pos >= 1
        assert neg == 0

    def test_single_negative_keyword(self) -> None:
        pos, neg = _analyze_text("Company announces layoffs")
        assert neg >= 1

    def test_positive_phrase_match(self) -> None:
        pos, neg = _analyze_text("AAPL beat estimates in Q4")
        assert pos >= 2

    def test_negative_phrase_match(self) -> None:
        pos, neg = _analyze_text("Company lowered guidance for next quarter")
        assert neg >= 2

    def test_mixed_signals(self) -> None:
        pos, neg = _analyze_text("Strong growth despite lawsuit concerns")
        assert pos > 0
        assert neg > 0

    def test_no_signals(self) -> None:
        pos, neg = _analyze_text("Company held annual meeting today")
        assert pos == 0
        assert neg == 0

    def test_phrase_scores_higher_than_word(self) -> None:
        _, neg_phrase = _analyze_text("Company missed expectations")
        _, neg_word = _analyze_text("Company missed target")
        assert neg_phrase > neg_word


class TestRecencyWeight:
    def test_recent_article_weighted_higher(self) -> None:
        recent_ts = str(int(time.time()) - 3600)
        weight = _recency_weight(recent_ts)
        assert weight == 2.0

    def test_old_article_weighted_lower(self) -> None:
        old_ts = str(int(time.time()) - 6 * 86400)
        weight = _recency_weight(old_ts)
        assert weight == 0.5

    def test_mid_age_article_normal_weight(self) -> None:
        mid_ts = str(int(time.time()) - 2 * 86400)
        weight = _recency_weight(mid_ts)
        assert weight == 1.0

    def test_empty_datetime_returns_default(self) -> None:
        assert _recency_weight("") == 1.0

    def test_invalid_datetime_returns_default(self) -> None:
        assert _recency_weight("not-a-date") == 1.0


class TestScoreSentiment:
    def test_no_news_returns_neutral(self) -> None:
        result = score_sentiment([])
        assert result.score == 50.0
        assert result.completeness == 0.0
        assert any("neutral" in r.lower() for r in result.reasons)

    def test_positive_news_scores_above_50(self) -> None:
        news = [
            {"headline": "Company beats estimates with record revenue and strong growth", "summary": ""},
            {"headline": "Analyst upgrade to buy with raised guidance", "summary": ""},
        ]
        result = score_sentiment(news)
        assert result.score > 50.0

    def test_negative_news_scores_below_50(self) -> None:
        news = [
            {"headline": "Stock crashes after fraud investigation and layoffs", "summary": ""},
            {"headline": "Earnings miss leads to downgrade and sell warning", "summary": ""},
        ]
        result = score_sentiment(news)
        assert result.score < 50.0

    def test_neutral_news_returns_50(self) -> None:
        news = [
            {"headline": "Company held quarterly meeting today", "summary": ""},
            {"headline": "New office location announced", "summary": ""},
        ]
        result = score_sentiment(news)
        assert result.score == 50.0

    def test_score_in_range(self) -> None:
        news = [
            {"headline": "beat growth surge rally profit buy", "summary": ""},
        ]
        result = score_sentiment(news)
        assert 0 <= result.score <= 100

    def test_notable_headlines_in_reasons(self) -> None:
        news = [
            {"headline": "Company beats estimates with record profit margins", "summary": ""},
        ]
        result = score_sentiment(news)
        assert any("+" in r for r in result.reasons)

    def test_recency_affects_score(self) -> None:
        recent_ts = str(int(time.time()) - 3600)
        old_ts = str(int(time.time()) - 6 * 86400)

        pos = {"headline": "Strong growth and rally continues", "summary": ""}
        neg = {"headline": "Stock crashes after fraud investigation", "summary": ""}

        pos_recent = [{**pos, "datetime": recent_ts}, {**neg, "datetime": old_ts}]
        neg_recent = [{**pos, "datetime": old_ts}, {**neg, "datetime": recent_ts}]

        assert score_sentiment(pos_recent).score > score_sentiment(neg_recent).score


class TestShrinkage:
    def test_single_positive_signal_shrinks_toward_neutral(self) -> None:
        news = [{"headline": "Outlook bullish", "summary": ""}]
        result = score_sentiment(news)
        # one lone signal must not max out the score anymore
        assert 50.0 < result.score < 65.0

    def test_many_signals_can_still_score_high(self) -> None:
        news = [
            {"headline": "beats estimates strong growth surge rally profit momentum", "summary": ""}
            for _ in range(4)
        ]
        result = score_sentiment(news)
        assert result.score > 75.0

    def test_zero_signals_still_exactly_neutral(self) -> None:
        news = [{"headline": "Company held quarterly meeting today", "summary": ""}]
        result = score_sentiment(news)
        assert result.score == 50.0

    def test_completeness_scales_with_article_count(self) -> None:
        one = score_sentiment([{"headline": "bullish outlook", "summary": ""}])
        five = score_sentiment(
            [{"headline": f"bullish outlook day {i}", "summary": ""} for i in range(5)]
        )
        assert one.completeness < five.completeness
        assert five.completeness == 1.0


class TestNoisyKeywordsRemoved:
    def test_bare_revenue_is_neutral(self) -> None:
        news = [{"headline": "Company reports quarterly revenue", "summary": ""}]
        assert score_sentiment(news).score == 50.0

    def test_bare_debt_is_neutral(self) -> None:
        news = [{"headline": "Company issues new debt", "summary": ""}]
        assert score_sentiment(news).score == 50.0

    def test_revenue_beat_phrase_is_positive(self) -> None:
        news = [{"headline": "Q3 revenue beat sends shares higher", "summary": ""}]
        assert score_sentiment(news).score > 50.0

    def test_rising_debt_phrase_is_negative(self) -> None:
        news = [{"headline": "Concerns over rising debt weigh on outlook", "summary": ""}]
        assert score_sentiment(news).score < 50.0


class TestNegation:
    def test_negated_positive_counts_negative(self) -> None:
        news = [{"headline": "Quarter was not strong for the company", "summary": ""}]
        assert score_sentiment(news).score < 50.0

    def test_fails_to_beat_counts_negative(self) -> None:
        news = [{"headline": "Company fails to beat expectations", "summary": ""}]
        assert score_sentiment(news).score < 50.0

    def test_unnegated_positive_still_positive(self) -> None:
        news = [{"headline": "Quarter was strong for the company", "summary": ""}]
        assert score_sentiment(news).score > 50.0


class TestDedup:
    def test_duplicate_headlines_counted_once(self) -> None:
        article = {"headline": "Company beats estimates with strong growth", "summary": ""}
        single = score_sentiment([article])
        duplicated = score_sentiment([article, dict(article), dict(article)])
        assert duplicated.score == single.score

    def test_distinct_headlines_kept(self) -> None:
        news = [
            {"headline": "Company beats estimates with strong growth", "summary": ""},
            {"headline": "Regulators open fraud investigation into supplier", "summary": ""},
        ]
        result = score_sentiment(news)
        assert "2 unique articles" in result.reasons[0]
