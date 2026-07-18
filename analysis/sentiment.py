from __future__ import annotations

from datetime import datetime, timezone

from data.models import ScoreResult

# "revenue", "launch", "contract", "debt" are deliberately absent from the
# keyword sets: bare mentions carry no sentiment (any earnings article says
# "revenue"). Directional versions live in the phrase sets instead.
_POSITIVE_KEYWORDS = {
    "beat", "beats", "exceeded", "upgrade", "upgrades", "upgraded",
    "growth", "record", "strong", "bullish", "outperform", "outperforms",
    "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains",
    "profit", "profitable", "positive", "optimistic", "breakout",
    "buy", "overweight", "raise", "raises", "raised", "innovation",
    "approved", "buyback", "repurchase", "expanding", "acquisition",
    "partnership", "momentum", "accelerating", "rebound", "recovery",
    "award",
}

_NEGATIVE_KEYWORDS = {
    "miss", "misses", "missed", "downgrade", "downgrades", "downgraded",
    "decline", "declines", "declining", "weak", "bearish", "underperform",
    "underperforms", "lawsuit", "recall", "crash", "crashes", "plunge",
    "plunges", "drop", "drops", "loss", "losses", "sell", "underweight",
    "cut", "cuts", "warning", "warns", "layoff", "layoffs", "bankruptcy",
    "fraud", "investigation", "probe", "fine", "penalty",
    "rejected", "default", "delisted", "shortage", "subpoena",
    "restructuring", "impairment", "writedown", "disappointing",
    "slowdown", "suspension", "violation",
}

_POSITIVE_PHRASES = {
    "beat estimates", "exceeded expectations", "raised guidance",
    "fda approval", "dividend increase", "all-time high",
    "strong earnings", "record revenue", "stock buyback",
    "share repurchase", "price target raised", "analyst upgrade",
    "wins contract", "contract win", "revenue beat",
}

_NEGATIVE_PHRASES = {
    "missed expectations", "lowered guidance", "fda rejection",
    "margin pressure", "supply chain", "price target cut",
    "analyst downgrade", "earnings miss", "revenue miss",
    "sec investigation", "class action", "debt default",
    "rising debt", "debt downgrade",
}

_NEGATION_TOKENS = {
    "not", "no", "never", "fails", "failed", "fail", "without",
    "isn't", "wasn't", "doesn't", "won't", "lacks", "misses",
}

# Laplace smoothing constant: shrinks the pos/(pos+neg) ratio toward neutral
# when signal counts are low, so one keyword in one article can't score 100.
_SHRINKAGE_K = 3.0

# Headlines whose token sets overlap at least this much are the same
# syndicated story and should only count once.
_DEDUP_JACCARD = 0.8


def _is_negated(tokens: list[str], index: int) -> bool:
    window_start = max(0, index - 2)
    return any(t in _NEGATION_TOKENS for t in tokens[window_start:index])


def _analyze_text(text: str) -> tuple[int, int]:
    lower = text.lower()

    pos = 0
    neg = 0
    matched_words: set[str] = set()

    for phrase in _POSITIVE_PHRASES:
        if phrase in lower:
            pos += 2
            matched_words.update(phrase.split())

    for phrase in _NEGATIVE_PHRASES:
        if phrase in lower:
            neg += 2
            matched_words.update(phrase.split())

    tokens = lower.split()
    seen: set[str] = set()
    for i, token in enumerate(tokens):
        if token in matched_words or token in seen:
            continue
        if token in _POSITIVE_KEYWORDS:
            seen.add(token)
            if _is_negated(tokens, i):
                neg += 1  # "not strong", "fails to beat" — flipped polarity
            else:
                pos += 1
        elif token in _NEGATIVE_KEYWORDS:
            seen.add(token)
            if _is_negated(tokens, i):
                pos += 1  # "no losses", "never missed"
            else:
                neg += 1

    return pos, neg


def _normalize_headline(headline: str) -> set[str]:
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in headline.lower())
    return set(cleaned.split())


def _dedup_articles(news: list[dict[str, str]]) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    kept_tokens: list[set[str]] = []
    for article in news:
        tokens = _normalize_headline(article.get("headline", ""))
        if not tokens:
            kept.append(article)
            kept_tokens.append(tokens)
            continue
        is_duplicate = False
        for existing in kept_tokens:
            if not existing:
                continue
            jaccard = len(tokens & existing) / len(tokens | existing)
            if jaccard >= _DEDUP_JACCARD:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(article)
            kept_tokens.append(tokens)
    return kept


def _recency_weight(article_datetime: str) -> float:
    if not article_datetime:
        return 1.0

    try:
        ts = int(article_datetime)
        article_time = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        try:
            article_time = datetime.fromisoformat(article_datetime)
            if article_time.tzinfo is None:
                article_time = article_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 1.0

    now = datetime.now(timezone.utc)
    age_days = (now - article_time).total_seconds() / 86400

    if age_days <= 1:
        return 2.0
    if age_days <= 3:
        return 1.0
    return 0.5


def score_sentiment(news: list[dict[str, str]]) -> ScoreResult:
    if not news:
        return ScoreResult(
            50.0, ["No news data available — sentiment neutral"], completeness=0.0,
        )

    news = _dedup_articles(news)

    total_pos = 0.0
    total_neg = 0.0
    notable: list[str] = []

    for article in news:
        headline = article.get("headline", "")
        summary = article.get("summary", "")
        text = f"{headline} {summary}"

        pos, neg = _analyze_text(text)
        weight = _recency_weight(article.get("datetime", ""))

        total_pos += pos * weight
        total_neg += neg * weight

        if pos > neg and pos >= 2:
            notable.append(f"  + {headline[:80]}")
        elif neg > pos and neg >= 2:
            notable.append(f"  - {headline[:80]}")

    completeness = min(1.0, len(news) / 5)

    total = total_pos + total_neg
    if total == 0:
        return ScoreResult(
            50.0, [f"Analyzed {len(news)} articles — no strong signals"],
            completeness=completeness,
        )

    # Laplace-smoothed ratio: shrinks toward 50 when signal counts are low
    score = (total_pos + _SHRINKAGE_K) / (total + 2 * _SHRINKAGE_K) * 100.0

    reasons = [
        f"News sentiment: {total_pos:.0f} positive vs {total_neg:.0f} negative "
        f"signals across {len(news)} unique articles (recency-weighted, "
        f"low-count shrinkage applied)"
    ]
    reasons.extend(notable[:5])

    return ScoreResult(min(max(score, 0.0), 100.0), reasons, completeness=completeness)
