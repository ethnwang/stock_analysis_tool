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
python3 main.py analyze --include-incomplete   # Keep low-data-quality stocks in ranking (marked '!')
python3 main.py analyze --snapshot             # Record scores to snapshots/scores.jsonl for validation
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
| `--include-incomplete` | Keep stocks below the data-completeness threshold in the ranking (marked `!`) |
| `--snapshot` | Append the full scored cross-section to `snapshots/scores.jsonl` |

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

### `backtest` — Validate scores against realized returns

```bash
python3 main.py backtest --universe watchlist              # As-of technical backtest (3y)
python3 main.py backtest --ticker AAPL MSFT NVDA JPM       # Specific tickers
python3 main.py backtest --eval-snapshots                  # Composite vs realized returns
python3 main.py backtest --eval-snapshots --by-component   # Per-signal IC table
```

Reports per-date Spearman rank IC (mean ± std, t-stat, hit-rate) and mean
forward return per score quintile, excess of SPY when benchmark data is
available. `analyze --snapshot` appends the scored cross-section (including
per-signal components) to `snapshots/scores.jsonl`; once snapshots are ≥21
days old, `--eval-snapshots` measures whether the scores actually predicted
returns — the survivorship-free validation path.

**Weight retuning is evidence-gated**: pillar weights and score bands change
only on snapshot-IC evidence, never by feel. The full process and decision
rules live in [`docs/RESEARCH.md`](docs/RESEARCH.md).

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

**Technical scoring** uses seven indicators: 12-1 momentum (25%), MACD (20%), RSI (15%), SMA 50/200 crossover (15%), Bollinger Bands (10%), volume (10%), and ADX (5%). Momentum is the 12-month return excluding the most recent month (the standard skip-month construction, avoiding short-term reversal). Direction-aware — a strong bullish trend with high volume scores higher than a strong bearish trend with high volume.

**Fundamental scoring** evaluates valuation (trailing P/E, with a labeled forward-P/E fallback), growth (EPS, revenue), balance sheet (D/E), income (dividend yield), and quality (ROE, margins, FCF yield). Weights shift by risk profile. P/E, D/E, and gross margin are scored relative to sector medians when enough peers are in the batch. ETFs are automatically detected and scored on expense ratio, blended returns, category performance, concentration, AUM, and yield instead.

**Sentiment scoring** analyzes news headlines with recency weighting (last 24h counts 2x) and low-count shrinkage. The polarity backend is pluggable via `SENTIMENT_BACKEND`: the keyword lexicon (default, zero dependencies) or FinBERT (local model, much higher accuracy). Batch-median recentering removes the lexicon's positive skew. No news = neutral (50).

**Cross-sectional normalization** blends each factor's absolute band score with its percentile in the analyzed universe (50/50 at full S&P 500 breadth). S&P 500 runs cache universe-wide decile breakpoints (`data/cache/factor_stats.json`, 30-day staleness) so watchlist runs blend against the full-universe distribution instead of a 48-name batch.

### Risk Profiles

| Metric | Aggressive | Moderate | Conservative |
|--------|-----------|----------|-------------|
| P/E ratio | 10% | 20% | 25% |
| EPS growth | 25% | 15% | 5% |
| Revenue growth | 25% | 15% | 5% |
| D/E ratio | 5% | 10% | 15% |
| Dividend yield | 5% | 10% | 20% |
| ROE | 10% | 10% | 10% |
| Margins | 10% | 10% | 10% |
| FCF yield | 10% | 10% | 10% |

### Portfolio-Aware Features

When `portfolio.json` is present:

- **Overlap penalty** — up to -5 points for stocks you already hold, tapering linearly from full at composite ≤65 to zero at ≥80 (no rank cliff at the Strong Buy cutoff)
- **Sector diversification** — -3 points for stocks in sectors >30% of portfolio value
- **Position sizing** — Monthly budget split by conviction × inverse-volatility (calmer names get more at equal score; scalar clamped to 0.5–2.0), with no position above 35% of the monthly budget
- **Account placement** — Suggests Roth IRA (high-growth) or Brokerage (dividends, shorter-term) per stock

### Portfolio file (`portfolio.json`)

```json
{
  "schwab_brokerage": {"holdings": [{"ticker": "AAPL", "name": "Apple Inc", "shares": 10, "market_value": 2100.00}]},
  "schwab_roth_ira": {"holdings": []},
  "fidelity": {"401k": {"holdings": []}, "hsa": {"holdings": []}, "roth_401k": {"holdings": []}},
  "roth_ira_maxed": true,
  "emergency_fund": ["SGOV"],
  "monthly_expenses": {"personal_investment": 500.00},
  "last_sync": "2026-05-02T12:00:00+00:00"
}
```

- `roth_ira_maxed` — Roth account analysis becomes reallocation-only (no new position sizing; swaps are the actionable output; account placement routes to Brokerage)
- `emergency_fund` — tickers excluded from swap suggestions and sector allocation, labeled `[emergency fund]` instead of being flagged as weak
- `monthly_expenses.personal_investment` — default monthly budget for position sizing (override with `--budget`)
- `last_sync` — ISO timestamp of the most recent `sync`

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
| `LOOKBACK_DAYS` | `420` | Price history window (~290 trading bars; 12-1 momentum needs 252) |
| `WEIGHT_TECHNICAL` | `0.45` | Technical analysis weight |
| `WEIGHT_FUNDAMENTAL` | `0.45` | Fundamental analysis weight |
| `WEIGHT_SENTIMENT` | `0.10` | Sentiment analysis weight |
| `RISK_PROFILE` | `moderate` | Scoring risk profile |
| `SENTIMENT_BACKEND` | `lexicon` | `lexicon` or `finbert` (falls back to lexicon if unavailable) |
| `MIN_PRICE` | `5.0` | Minimum stock price filter |
| `MIN_MARKET_CAP` | `300000000` | Minimum market cap ($300M) |
| `MIN_DATA_COMPLETENESS` | `0.5` | Below this weighted completeness fraction, a stock is excluded as "Insufficient Data" |
| `SCHWAB_CLIENT_ID` | — | Schwab API client ID |
| `SCHWAB_CLIENT_SECRET` | — | Schwab API client secret |
| `SCHWAB_REFRESH_TOKEN` | — | Set automatically by `link` |
| `PLAID_CLIENT_ID` | — | Plaid API client ID |
| `PLAID_SECRET` | — | Plaid API secret |
| `PLAID_ENV` | `sandbox` | Plaid environment |
| `PLAID_ACCESS_TOKEN_CHASE` | — | Set automatically by `link --institution chase` |
| `PLAID_ACCESS_TOKEN_FIDELITY` | — | Unused — Fidelity is not available via Plaid |
| `YF_SETUP_URL` | — | Override Yahoo cookie-bootstrap URL if the default is blocked |

Weights are auto-normalized if they don't sum to 1.0; if all three are zero they reset to the defaults (0.45/0.45/0.10).

## Data Sources

- **yahooquery** (no API key) — Price history, fundamentals, current quotes, company profile, ETF fund data
- **Finnhub** (free tier, 60 calls/min) — Company news, S&P 500 constituents
- **Yahoo Finance RSS** (no API key) — Fallback news source when Finnhub is unavailable

## Limitations

- Historical backtesting covers the technical pillar only (fundamentals/sentiment aren't available as-of past dates) and is survivorship-biased; snapshot evaluation is the honest composite-validation path
- No options or derivatives data; no real-time streaming — batch analysis only
- Default sentiment is keyword-based; set `SENTIMENT_BACKEND=finbert` for model-based accuracy
- Fidelity accounts require manual CSV import (not supported by Plaid)
- 401(k) accounts are not selectable for `--account` analysis (employer-managed)

## Testing

```bash
python3 -m pytest tests/ -v
```

All tests use hardcoded data fixtures — no API calls required.
