# StockBot

Stock analysis CLI that fetches live market data, scores stocks across technical, fundamental, and sentiment dimensions, and outputs ranked buy recommendations. Supports multi-account portfolio tracking via Schwab API and Plaid integrations.

## Setup

Requires Python 3.10+.

```bash
pip3 install yahooquery pandas numpy ta finnhub-python python-dotenv requests plaid-python schwab-py
```

Copy `.env.example` to `.env` and fill in your API keys. A Finnhub API key is recommended for news/sentiment scoring but not required — the bot falls back to Yahoo Finance RSS if Finnhub is unavailable.

## Commands

### `analyze` — Run stock analysis

Score and rank stocks, then output buy recommendations with position sizing.

```bash
python3 main.py analyze                        # Default 48-stock watchlist, top 10
python3 main.py analyze --ticker AAPL NVDA     # Specific tickers
python3 main.py analyze --universe sp500       # Full S&P 500
python3 main.py analyze --universe etf         # ~35 popular ETFs
python3 main.py analyze --ticker VOO QQQ SPY   # Specific ETFs (auto-detected)
python3 main.py analyze --top 20 --verbose     # Top 20 with detailed reasoning
python3 main.py analyze --risk aggressive      # Growth-biased scoring
python3 main.py analyze --budget 500           # Custom monthly budget
python3 main.py analyze --no-portfolio         # Skip portfolio-aware features
```

**Account-specific analysis** — scoped to a single account's holdings with swap suggestions:

```bash
python3 main.py analyze --account roth         # Schwab Roth IRA
python3 main.py analyze --account brokerage    # Schwab Brokerage
python3 main.py analyze --account hsa          # Fidelity HSA
```

| Flag | Description |
|------|-------------|
| `--universe` | `watchlist` (default, 48 stocks), `sp500` (~500 stocks), or `etf` (35 ETFs) |
| `--ticker` | One or more specific tickers (overrides `--universe`) |
| `--top` | Number of top picks to display (default: 10) |
| `--verbose, -v` | Show detailed per-stock analysis breakdown |
| `--risk` | `aggressive`, `moderate` (default), or `conservative` |
| `--budget` | Monthly investment budget override |
| `--no-portfolio` | Skip portfolio loading (no overlap penalty, sizing, or placement) |
| `--account` | Analyze a specific account — shows holdings, swaps, and reallocation |

### `sync` — Pull latest portfolio data

Fetch current positions and balances from linked accounts and write to `portfolio.json`.

```bash
python3 main.py sync                           # Sync all linked accounts
python3 main.py sync --schwab-only             # Only sync Schwab
python3 main.py sync --plaid-only              # Only sync Plaid
```

### `link` — Link a new brokerage account

Start an OAuth flow to connect a brokerage account.

```bash
python3 main.py link --institution schwab      # Schwab OAuth flow
python3 main.py link --institution chase       # Plaid Link for Chase
```

### `import` — Import positions from CSV

Import holdings from a Fidelity portfolio positions CSV export.

```bash
python3 main.py import path/to/Portfolio_Positions.csv
```

## How It Works

### Data Flow

```
Config → Portfolio (optional) → Universe → Fetch → Analyze → Score → Rank → Size → Report
```

1. **Universe resolution** — Determines which tickers to analyze (watchlist, S&P 500, ETFs, or custom tickers)
2. **Data fetching** — Pulls price history, fundamentals, and news via yahooquery, Finnhub, and Yahoo RSS (concurrent, rate-limited)
3. **Analysis** — Runs technical, fundamental, and sentiment scoring on each stock
4. **Scoring** — Computes a composite score with portfolio-aware adjustments
5. **Ranking** — Sorts by composite score, assigns recommendations (Strong Buy / Buy / Hold / Avoid)
6. **Position sizing** — Splits a monthly budget proportionally across Buy/Strong Buy picks
7. **Reporting** — Outputs a ranked table with scores, recommendations, and suggested allocations

### Scoring

Each stock gets a composite score (0–100):

```
Composite = (tech_weight × technical) + (fund_weight × fundamental) + (sent_weight × sentiment)
```

Default weights: 45% technical, 45% fundamental, 10% sentiment. Configurable via `.env`.

| Score | Recommendation |
|-------|----------------|
| 80+   | Strong Buy |
| 65–79 | Buy |
| 45–64 | Hold |
| < 45  | Avoid |

**Technical scoring** uses six indicators: MACD, RSI, SMA 50/200 crossover, volume, ADX, and Bollinger Bands. Direction-aware — a strong bullish trend with high volume scores higher than a strong bearish trend with high volume.

**Fundamental scoring** evaluates P/E ratio, EPS growth, revenue growth, debt-to-equity, and dividend yield. Weights shift by risk profile (aggressive favors growth metrics, conservative favors dividends and low debt). ETFs are automatically detected and scored on expense ratio, blended returns, category performance, concentration, AUM, and yield instead.

**Sentiment scoring** analyzes news headlines using keyword/phrase matching with recency weighting (last 24h counts 2x). No news = neutral (50).

### Risk Profiles

| Metric | Aggressive | Moderate | Conservative |
|--------|-----------|----------|-------------|
| P/E ratio | 10% | 25% | 30% |
| EPS growth | 35% | 25% | 15% |
| Revenue growth | 30% | 20% | 10% |
| D/E ratio | 10% | 15% | 20% |
| Dividend yield | 15% | 15% | 25% |

### Portfolio-Aware Features

When `portfolio.json` is present:

- **Overlap penalty** — -5 points for stocks you already hold (waived for Strong Buy conviction >80)
- **Sector diversification** — -3 points for stocks in sectors >30% of portfolio value
- **Position sizing** — Monthly budget split proportionally by composite score across recommended picks
- **Account placement** — Suggests Roth IRA (high-growth) or Brokerage (dividends, shorter-term) per stock

### Account Analysis & Swap Suggestions

`--account` runs a scoped analysis for one account:

- Scores all held positions alongside the full analysis universe
- Generates **swap suggestions** for weak positions (score < 50) when a better alternative exists
- Prefers same-sector replacements with at least a 10-point improvement
- When Roth IRA contributions are maxed, shows reallocation-only mode

## Configuration

All settings via `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `FINNHUB_API_KEY` | — | Finnhub API key for news/sentiment |
| `UNIVERSE` | `watchlist` | Default stock universe |
| `TOP_N` | `10` | Number of top picks |
| `LOOKBACK_DAYS` | `365` | Price history window |
| `WEIGHT_TECHNICAL` | `0.45` | Technical analysis weight |
| `WEIGHT_FUNDAMENTAL` | `0.45` | Fundamental analysis weight |
| `WEIGHT_SENTIMENT` | `0.10` | Sentiment analysis weight |
| `RISK_PROFILE` | `moderate` | Scoring risk profile |
| `MIN_PRICE` | `5.0` | Minimum stock price filter |
| `MIN_MARKET_CAP` | `300000000` | Minimum market cap ($300M) |
| `SCHWAB_CLIENT_ID` | — | Schwab API client ID |
| `SCHWAB_CLIENT_SECRET` | — | Schwab API client secret |
| `SCHWAB_REFRESH_TOKEN` | — | Set automatically by `link` |
| `PLAID_CLIENT_ID` | — | Plaid API client ID |
| `PLAID_SECRET` | — | Plaid API secret |
| `PLAID_ENV` | `sandbox` | Plaid environment |

Weights are auto-normalized if they don't sum to 1.0.

## Data Sources

- **yahooquery** (no API key) — Price history, fundamentals, current quotes, company profile, ETF fund data
- **Finnhub** (free tier, 60 calls/min) — Company news, S&P 500 constituents
- **Yahoo Finance RSS** (no API key) — Fallback news source when Finnhub is unavailable

## Testing

```bash
python3 -m pytest tests/ -v
```

All tests use hardcoded data fixtures — no API calls required.
