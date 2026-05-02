# StockBot

Stock analysis CLI that fetches live market data, scores stocks across technical, fundamental, and sentiment dimensions, and outputs ranked buy recommendations.

## Quick Start

```bash
python main.py analyze                        # Default 50-stock watchlist, top 10
python main.py analyze --ticker AAPL NVDA     # Specific tickers
python main.py analyze --universe sp500       # Full S&P 500
python main.py analyze --top 20 --verbose     # Top 20 with detailed reasoning
python main.py analyze --risk aggressive      # Growth-biased scoring
python main.py analyze --budget 500           # Custom monthly budget
python main.py analyze --no-portfolio         # Skip portfolio-aware features
```

Requires Python 3.10+. Install deps: `pip install yahooquery pandas numpy ta finnhub-python python-dotenv requests`

## Architecture

```
main.py (CLI)
  → config.py (loads .env, builds Config dataclass)
  → portfolio/loader.py (loads portfolio.json — holdings, budget, sector allocation)
  → data/universe.py (resolves ticker list — watchlist or S&P 500 via Finnhub)
  → data/fetcher.py (yahooquery for price/fundamentals, Finnhub for news, with rate limiter)
  → analysis/technical.py (RSI, MACD, Bollinger Bands, SMA crossover, ADX, volume)
  → analysis/fundamental.py (P/E, EPS growth, revenue growth, D/E, dividend yield)
  → analysis/sentiment.py (keyword-based news headline scoring)
  → scoring/engine.py (risk-adjusted scoring, overlap penalty, sector diversification)
  → reporting/console.py (recommendations, sector allocation, position sizing, account placement)
```

Data flows: Config → Portfolio (optional) → Universe → Fetch → Analyze → Score → Rank → Size → Report

## Data Sources

**yahooquery** (no API key needed): Price history (OHLCV), fundamentals (P/E, EPS, revenue, debt-to-equity, dividend yield), current quotes, company profile (sector, name). Uses `finance.yahoo.com` for cookie bootstrapping instead of the often-blocked `fc.yahoo.com`. Set `YF_SETUP_URL` env var to override the bootstrap URL if needed.

**Finnhub** (free tier, 60 calls/min): Company news (last 7 days), S&P 500 index constituents. Register at https://finnhub.io for a free API key. Set `FINNHUB_API_KEY` in `.env`. If not configured, news falls back to **yahooquery** (Yahoo Finance headlines, no API key needed). Sentiment analysis works with either source.

Rate limiter in `data/fetcher.py` uses a token-bucket (`collections.deque` of timestamps, 55 calls/60s with buffer) to stay under Finnhub's limit.

## Configuration

All settings via `.env` file (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `FINNHUB_API_KEY` | (none) | Finnhub API key for news/sentiment |
| `UNIVERSE` | `watchlist` | `watchlist` (50 stocks) or `sp500` (~500 stocks) |
| `TOP_N` | `10` | Number of top picks to display |
| `LOOKBACK_DAYS` | `365` | Price history window |
| `WEIGHT_TECHNICAL` | `0.45` | Technical analysis weight |
| `WEIGHT_FUNDAMENTAL` | `0.45` | Fundamental analysis weight |
| `WEIGHT_SENTIMENT` | `0.10` | Sentiment analysis weight (keyword-based, lower default) |
| `RISK_PROFILE` | `moderate` | `aggressive`, `moderate`, or `conservative` |
| `MIN_PRICE` | `5.0` | Minimum stock price filter |
| `MIN_MARKET_CAP` | `300000000` | Minimum market cap ($300M) |

## Scoring System

Composite score = (tech_weight × technical) + (fund_weight × fundamental) + (sent_weight × sentiment)

Each sub-score is 0–100. Recommendations: Strong Buy (80+), Buy (65–79), Hold (45–64), Avoid (<45).

### Risk Profiles

The `RISK_PROFILE` setting adjusts fundamental scoring weights:

| Metric | Aggressive | Moderate | Conservative |
|--------|-----------|----------|-------------|
| P/E ratio | 10% | 25% | 30% |
| EPS growth | 35% | 25% | 15% |
| Revenue growth | 30% | 20% | 10% |
| D/E ratio | 10% | 15% | 20% |
| Dividend yield | 15% | 15% | 25% |

Aggressive mode also relaxes P/E penalties for growth stocks (P/E 20-50 penalized less).

### Portfolio-Aware Adjustments

When `portfolio.json` exists, the scoring engine applies:

- **Overlap penalty**: -5 points for stocks already held (waived for Strong Buy conviction scores >80)
- **Sector diversification**: -3 points for stocks in sectors >30% of portfolio value
- **Position sizing**: Monthly investment budget split proportionally across Buy/Strong Buy picks
- **Account placement**: Suggests Roth IRA (high-growth, tax-free compounding) or Brokerage (dividends, flexibility) per stock

## Trading Domain Knowledge

### Technical Indicators

**RSI (Relative Strength Index)** — Momentum oscillator (0–100) measuring speed of price changes over 14 periods. Below 30 = oversold (price dropped too fast, likely to bounce — bullish). Above 70 = overbought (price rose too fast, likely to pull back — bearish). Most reliable when confirmed by other indicators.

**MACD (Moving Average Convergence Divergence)** — Trend-following momentum indicator. Calculated as 12-period EMA minus 26-period EMA, with a 9-period EMA signal line. When MACD crosses above its signal line = bullish. When histogram (MACD minus signal) is rising = strengthening momentum. The MACD+RSI combination historically shows 73–84% win rates when both signals agree on direction.

**Bollinger Bands** — Volatility bands at 2 standard deviations above/below a 20-period SMA. Price touching the lower band suggests oversold conditions (potential buy). Price touching the upper band suggests overbought. Band width indicates volatility — narrow bands often precede big moves.

**SMA 50/200 Crossover** — When the 50-day Simple Moving Average crosses above the 200-day SMA, it's called a "golden cross" (bullish long-term signal). The reverse is a "death cross" (bearish). These are lagging indicators — they confirm trends rather than predict them.

**ADX (Average Directional Index)** — Measures trend strength regardless of direction. Above 25 = strong trend (follow it). Below 20 = weak/no trend (range-bound, mean-reversion strategies work better). Does not indicate direction, only strength.

**Volume Analysis** — Volume confirms price moves. Rising price on rising volume = strong move. Rising price on declining volume = weak move likely to reverse. Unusually high volume often precedes significant price action.

### Fundamental Metrics

**P/E Ratio (Price-to-Earnings)** — Stock price divided by earnings per share. Lower = cheaper relative to earnings. Below 15 is traditionally "value" territory, above 30 is expensive. Negative P/E means the company is losing money. Must be compared within the same sector — tech stocks typically have higher P/E than utilities.

**EPS Growth (Earnings Per Share Growth)** — Quarter-over-quarter or year-over-year change in earnings per share. Positive and accelerating growth is the strongest signal. Negative growth means profitability is declining.

**Revenue Growth** — Top-line growth rate. Even if earnings are flat, strong revenue growth suggests the business is scaling. Declining revenue is a red flag regardless of earnings manipulation.

**Debt-to-Equity Ratio** — Total debt divided by shareholder equity. Below 0.5 = conservatively financed. Above 2.0 = heavily leveraged (higher risk in downturns). Banks and REITs naturally have high D/E — compare within sector.

**Dividend Yield** — Annual dividend divided by stock price. Above 3% = strong income stock. Very high yields (>8%) can signal a "dividend trap" where the price has crashed and the dividend may be cut.

### Key Terminology

- **OHLCV**: Open, High, Low, Close, Volume — the five standard data points per trading period
- **Market Cap**: Total value of outstanding shares (price × shares). Large cap > $10B, mid cap $2B–$10B, small cap < $2B
- **Golden Cross / Death Cross**: SMA 50 crossing above/below SMA 200
- **Overbought / Oversold**: Price has moved too far too fast in one direction, likely to revert
- **Support / Resistance**: Price levels where buying (support) or selling (resistance) pressure historically concentrates
- **Bull / Bear Market**: Sustained upward (bull) or downward (bear) trend, typically defined as 20%+ move
- **Sector Rotation**: Capital flowing between market sectors based on economic cycle stage

## Development Guide

### Adding a New Technical Indicator

1. Add the computation in `analysis/technical.py:compute_indicators()` using the `ta` library
2. Add scoring logic in `score_technical()` with appropriate weight
3. Adjust existing weights to sum to 1.0
4. Add a test case in `tests/test_technical.py`

### Adding a New Data Source

1. Add the fetch function in `data/fetcher.py`
2. If it needs an API key, add to `Config` in `config.py` and `.env.example`
3. If it has rate limits, use the existing `RateLimiter` class
4. Store the data in `StockData` (add field to `data/models.py` if needed)

### Adjusting Scoring

Weights are in `.env` (WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL, WEIGHT_SENTIMENT). Sub-indicator weights are hardcoded in each analysis module — edit the `0.XX` multipliers in the score functions.

Recommendation thresholds are in `scoring/engine.py:_recommendation_label()`.

## Known Limitations

- Sentiment is keyword/phrase-based, not ML — works for obvious signals, misses nuance and sarcasm. Recency-weighted (recent headlines count more). Fallback from Finnhub to Yahoo Finance headlines if no API key configured
- No backtesting engine — scores reflect current snapshot only
- No options or derivatives data
- yahooquery uses unofficial Yahoo Finance endpoints — can break if Yahoo changes their API
- No real-time streaming — batch analysis only

## Testing

```bash
python -m pytest tests/ -v
```

Tests use hardcoded data fixtures — no API calls. All analysis and scoring logic is testable offline.
