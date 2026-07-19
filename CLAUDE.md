# StockBot

Stock analysis CLI that fetches live market data, scores stocks across technical, fundamental, and sentiment dimensions, and outputs ranked buy recommendations. Supports multi-account portfolio tracking via Schwab API and Plaid integrations.

## Quick Start

```bash
# Analysis
python3 main.py analyze                        # Default 48-stock watchlist, top 10
python3 main.py analyze --ticker AAPL NVDA     # Specific tickers
python3 main.py analyze --universe sp500       # Full S&P 500 (via Finnhub or 100-stock fallback)
python3 main.py analyze --top 20 --verbose     # Top 20 with detailed reasoning
python3 main.py analyze --risk aggressive      # Growth-biased scoring
python3 main.py analyze --budget 500           # Custom monthly budget
python3 main.py analyze --no-portfolio         # Skip portfolio-aware features
python3 main.py analyze --universe etf         # ETF universe (~35 popular ETFs)
python3 main.py analyze --ticker VOO QQQ SPY   # Specific ETFs (auto-detected)
python3 main.py analyze --include-incomplete   # Keep low-data-quality stocks in ranking
python3 main.py analyze --snapshot             # Record scores for later validation

# Validation
python3 main.py backtest --universe watchlist  # As-of technical backtest (3y history)
python3 main.py backtest --eval-snapshots      # Validate past snapshots vs realized returns

# Account-specific analysis
python3 main.py analyze --account roth         # Schwab Roth IRA — holdings, swaps, reallocation
python3 main.py analyze --account brokerage    # Schwab Brokerage
python3 main.py analyze --account hsa          # Fidelity HSA

# Portfolio sync
python3 main.py sync                           # Pull latest from all linked accounts
python3 main.py sync --schwab-only             # Only sync Schwab
python3 main.py sync --plaid-only              # Only sync Plaid

# Linking accounts
python3 main.py link --institution schwab      # OAuth flow for Schwab API
python3 main.py link --institution chase       # Plaid Link for Chase

# Importing positions
python3 main.py import path/to/Fidelity.csv   # Import Fidelity CSV export
```

Requires Python 3.10+. Install deps: `pip3 install yahooquery pandas numpy ta finnhub-python python-dotenv requests plaid-python schwab-py`

## Architecture

```
main.py (CLI — analyze, sync, link, import subcommands)
  → config.py (loads .env, builds Config dataclass)
  → portfolio/loader.py (portfolio.json — holdings, budget, sector allocation, swaps, account placement)
  → data/universe.py (resolves ticker list — 48-stock watchlist, S&P 500, or 35-ETF watchlist)
  → data/fetcher.py (yahooquery for price/fundamentals, Finnhub + Yahoo RSS for news, rate limiters)
  → data/models.py (StockData, ScoredStock, SwapSuggestion)
  → analysis/technical.py (12-1 momentum, RSI, MACD, Bollinger Bands, SMA crossover, ADX, volume, realized vol — direction-aware)
  → analysis/fundamental.py (P/E, EPS growth, revenue growth, D/E, dividend yield, ROE, margins, FCF yield — risk-adjusted)
  → analysis/etf_fundamental.py (expense ratio, returns, concentration, AUM, yield — risk-adjusted)
  → analysis/sentiment.py (news scoring aggregation — dedup, recency weighting, shrinkage)
  → analysis/sentiment_backends.py (pluggable polarity backends: lexicon default, FinBERT optional)
  → analysis/xsection.py (cross-sectional factor percentiles, factor-stats cache, sentiment recentering)
  → scoring/engine.py (composite scoring, overlap penalty taper, sector diversification, account placement)
  → reporting/console.py (full report + account-specific report with swap suggestions)
  → integrations/schwab.py (Schwab API — OAuth, account positions)
  → integrations/plaid_sync.py (Plaid API — Chase/Fidelity account balances and holdings)
  → integrations/sync_all.py (orchestrates Schwab + Plaid sync into portfolio.json)
  → integrations/fidelity_csv.py (imports Fidelity CSV position exports)
  → integrations/link_server.py (local HTTP server for Plaid Link OAuth callback)
```

Data flows: Config → Portfolio (optional) → Universe → Fetch → Analyze → Score → Rank → Size → Report

## Data Sources

**yahooquery** (no API key needed): Price history (OHLCV), fundamentals (P/E, EPS, revenue, debt-to-equity, dividend yield), current quotes, company profile (sector, name). Uses `finance.yahoo.com` for cookie bootstrapping instead of the often-blocked `fc.yahoo.com`. Set `YF_SETUP_URL` env var to override the bootstrap URL if needed.

**Finnhub** (free tier, 60 calls/min): Company news (last 7 days). Register at https://finnhub.io for a free API key. Set `FINNHUB_API_KEY` in `.env`. The key is validated via `config.has_finnhub` — placeholder values like `"your_key_here"` are treated as unconfigured. If Finnhub is unavailable or returns no articles, news falls back to **Yahoo Finance RSS** (`feeds.finance.yahoo.com/rss/2.0/headline`). Note: Finnhub's index-constituents endpoint is premium-tier, so S&P 500 membership resolves Finnhub → **Wikipedia** (List of S&P 500 companies table, ~503 names, BRK.B→BRK-B symbol mapping) → 90-day cache → hardcoded ~100-name mega-cap fallback (`data/universe.py`).

**Yahoo Finance RSS** (no API key needed): Fallback news source. Fetches headlines via RSS feed with a `User-Agent` header. Used automatically when Finnhub is not configured or returns empty results.

### Rate Limiting

Two independent rate limiters in `data/fetcher.py`:
- **Finnhub**: 55 calls per 60 seconds (buffer under the 60/min free-tier limit)
- **Yahoo RSS**: 120 calls per 60 seconds

Both use a thread-safe token-bucket implementation (`collections.deque` of timestamps with `threading.Lock`). The Finnhub client is created once and reused across `ThreadPoolExecutor` workers (thread-safe via `requests.Session`).

### Security

- API keys are loaded from `.env` via `python-dotenv`, never hardcoded
- `urllib3` and `finnhub` loggers are set to WARNING level to prevent API keys from leaking in verbose/debug URL logs

## Integrations

### Schwab API

OAuth-based integration for Schwab brokerage and Roth IRA accounts. Requires `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, and `SCHWAB_REFRESH_TOKEN` in `.env`.

- **Linking**: `python3 main.py link --institution schwab` — opens OAuth authorization URL, exchanges redirect code for a refresh token, saves to `.env`
- **Syncing**: `python3 main.py sync` — fetches account positions (ticker, shares, market value), writes to `portfolio.json` under `schwab_brokerage` and `schwab_roth_ira` keys

### Plaid API

Used for Chase bank account data. Requires `PLAID_CLIENT_ID` and `PLAID_SECRET` in `.env`.

- **Linking**: `python3 main.py link --institution chase` — starts a local HTTP server for the Plaid Link flow, exchanges public token for an access token, saves to `.env` as `PLAID_ACCESS_TOKEN_CHASE`
- **Syncing**: `python3 main.py sync` — fetches balances and holdings from linked Plaid institutions

Note: Fidelity is not supported by Plaid's institution coverage. Use the CSV import instead.

### Fidelity CSV Import

`python3 main.py import path/to/Portfolio_Positions.csv` — parses a Fidelity portfolio positions CSV export and writes holdings to `portfolio.json` under `fidelity` with sub-accounts (`401k`, `hsa`, `roth_401k`).

## Portfolio Structure (`portfolio.json`)

```json
{
  "schwab_brokerage": {
    "holdings": [
      {"ticker": "AAPL", "name": "Apple Inc", "shares": 10, "market_value": 2100.00}
    ]
  },
  "schwab_roth_ira": {
    "holdings": [...]
  },
  "fidelity": {
    "401k": {"holdings": [...]},
    "hsa": {"holdings": [...]},
    "roth_401k": {"holdings": [...]}
  },
  "roth_ira_maxed": true,
  "emergency_fund": ["SGOV"],
  "monthly_expenses": {
    "personal_investment": 500.00
  },
  "last_sync": "2026-05-02T12:00:00+00:00"
}
```

- `roth_ira_maxed` — when `true`, Roth IRA account analysis shows reallocation-only mode (no new position sizing, swap suggestions instead)
- `emergency_fund` — list of tickers that are emergency fund positions. These are excluded from swap suggestions, sector allocation calculations, and labeled `[emergency fund]` in account reports instead of being flagged as weak
- `monthly_expenses.personal_investment` — default monthly budget for position sizing (overridable with `--budget`)
- `last_sync` — ISO 8601 timestamp of the most recent sync

## Configuration

All settings via `.env` file (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `FINNHUB_API_KEY` | (none) | Finnhub API key for news/sentiment |
| `UNIVERSE` | `watchlist` | `watchlist` (48 stocks), `sp500` (~500 stocks), or `etf` (35 ETFs) |
| `TOP_N` | `10` | Number of top picks to display |
| `LOOKBACK_DAYS` | `420` | Price history window (~290 trading bars — 12-1 momentum needs 252) |
| `WEIGHT_TECHNICAL` | `0.45` | Technical analysis weight (live `.env` currently runs 0.40) |
| `WEIGHT_FUNDAMENTAL` | `0.45` | Fundamental analysis weight (live `.env` currently runs 0.40) |
| `WEIGHT_SENTIMENT` | `0.10` | Sentiment analysis weight (live `.env` currently runs 0.20) |
| `RISK_PROFILE` | `moderate` | `aggressive`, `moderate`, or `conservative` |
| `SENTIMENT_BACKEND` | `lexicon` | `lexicon` or `finbert`; unavailable backends fall back to lexicon with a warning |
| `MIN_PRICE` | `5.0` | Minimum stock price filter |
| `MIN_MARKET_CAP` | `300000000` | Minimum market cap ($300M) |
| `MIN_DATA_COMPLETENESS` | `0.5` | Below this weighted data-completeness fraction, a stock is excluded as "Insufficient Data" |
| `SCHWAB_CLIENT_ID` | (none) | Schwab API client ID |
| `SCHWAB_CLIENT_SECRET` | (none) | Schwab API client secret |
| `SCHWAB_REFRESH_TOKEN` | (none) | Schwab OAuth refresh token (set by `link`) |
| `PLAID_CLIENT_ID` | (none) | Plaid API client ID |
| `PLAID_SECRET` | (none) | Plaid API secret |
| `PLAID_ENV` | `sandbox` | Plaid environment (`sandbox`, `development`, `production`) |
| `PLAID_ACCESS_TOKEN_CHASE` | (none) | Plaid access token for Chase (set by `link`) |
| `PLAID_ACCESS_TOKEN_FIDELITY` | (none) | Plaid access token for Fidelity (not available via Plaid) |

Weights are auto-normalized if they don't sum to 1.0. If all weights are zero, they reset to defaults (0.45, 0.45, 0.10).

**Weight retuning is evidence-gated**: pillar/sub-weights only change when `backtest --eval-snapshots --by-component` shows it (≥3–5 evaluable snapshot dates). Decision rules live in README.md under the backtest command.

## Scoring System

Composite score = (tech_weight × technical) + (fund_weight × fundamental) + (sent_weight × sentiment)

Each sub-score is 0–100. Recommendations: Strong Buy (80+), Buy (65–79), Hold (45–64), Avoid (<45), plus "Insufficient Data" when completeness is too low.

### Data Quality & Completeness

Missing data is represented as `None` end-to-end (never `0.0`): the fetcher's `_EMPTY_FUNDAMENTALS`/`_EMPTY_ETF_FUNDAMENTALS` default to `None`, and every scorer skips absent metrics, renormalizing the remaining weights instead of scoring fabricated zeros. Each scorer returns a `ScoreResult(score, reasons, completeness)` where `completeness` is the weight-fraction of inputs actually present; below 0.4 available weight, a scorer returns neutral 50.

**The one deliberate exception**: `dividend_yield` defaults to `0.0` when absent, because Yahoo omits the field for non-payers — absence *is* the signal.

At the engine level, pillars (technical/fundamental/sentiment) with zero completeness are dropped and the composite is renormalized over the remainder. Per-stock `data_completeness` = weighted sum of pillar completeness, shown as the `Data` column in reports. Stocks below `MIN_DATA_COMPLETENESS` (default 0.5) are labeled "Insufficient Data" and excluded from rankings unless `--include-incomplete` is passed (marked `!`). A stock with unknown market cap passes the size filter but logs an INFO line.

### Sector-Relative Valuation

`rank_stocks` builds per-sector medians (P/E, D/E, EPS growth, revenue growth, gross margin, ROE) from the fetched batch (`analysis/relative.py`). With ≥5 sector peers, P/E and D/E are banded on ratio-to-sector-median (≤0.6 → 100 … >1.5 → 10) with reasons citing both values; gross margin is banded on the spread vs the median; growth keeps absolute bands plus a clamped ±10 adjustment vs the median. Below 5 peers, for Unknown sectors, ETFs, or negative P/E, the absolute bands apply. Medians are **batch-relative**: meaningful for `--universe sp500`, mostly falls back on the 48-name watchlist.

### Cross-Sectional Normalization (`analysis/xsection.py`)

Absolute bands ignore market regime and compress scores toward the middle, so band scores for momentum, P/E, ROE, gross margin, and FCF yield are **blended with the stock's percentile** in the analyzed universe. Decile breakpoints per factor are built from the batch; the percentile leg's share ramps from 0 at n≤20 to 50% at n≥100 stocks. S&P 500 runs save the breakpoints to `data/cache/factor_stats.json` (30-day staleness), and smaller runs (watchlist, `--ticker`) blend against that cached full-universe distribution instead of their own thin batch. P/E percentiles are inverted (cheap = good) and negative P/Es are excluded. ETFs are never blended against stock breakpoints.

Sentiment is **recentered on the batch median** (mapped to 50) to remove the lexicon's positive skew; the raw score is preserved in `components["sentiment_raw"]`. During the transition, `components["composite_pre_xs"]` records the composite under the pre-normalization formula so the snapshot evaluation series stays comparable.

### Risk Profiles

The `RISK_PROFILE` setting adjusts fundamental scoring weights:

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

Aggressive mode also relaxes P/E penalties for growth stocks (P/E 20-50 penalized less).

**Quality metrics**: ROE and FCF yield use absolute bands; margins use the gross-margin spread vs sector median when ≥5 peers, else absolute profit-margin bands, else skipped (absolute gross-margin bands would be sector noise). The equal-weighted quality mean is tracked in `components["quality"]`.

**P/E basis**: `pe_ratio` is trailing-only; forward P/E is stored separately (`forward_pe`) and used as an explicitly-labeled fallback on absolute bands when trailing is missing — mixing bases would corrupt sector medians.

### Technical Scoring

Seven indicators with direction-aware scoring:

| Indicator | Weight | Direction-Aware |
|-----------|--------|-----------------|
| 12-1 Momentum | 25% | Monotonic — return from 12 months ago to 1 month ago (skip-month avoids short-term reversal); needs 252 bars |
| MACD | 20% | Yes — bullish crossover with rising momentum scores higher than simple above-signal |
| RSI | 15% | No — pure momentum oscillator |
| SMA 50/200 | 15% | No — golden/death cross detection |
| Bollinger Bands | 10% | No — position within bands |
| Volume | 10% | Yes — high volume confirms current trend direction (bullish or bearish) |
| ADX | 5% | Yes — strong trend scored bullish/bearish based on MACD + SMA signals |

ADX and volume check `_is_bullish_trend()` (MACD above signal + SMA 50 above 200) to determine whether a strong trend or high volume is bullish or bearish. A strong bearish trend with high volume scores low; a strong bullish trend with high volume scores high.

`compute_indicators` also produces `realized_vol` (annualized std of the last 63 daily returns) — not scored, but used for inverse-vol position sizing and tracked in `components`.

### Sentiment Scoring

The aggregation pipeline (dedup, recency weighting, shrinkage) is backend-agnostic; per-text polarity comes from a pluggable backend (`analysis/sentiment_backends.py`, selected by `SENTIMENT_BACKEND`):

- **lexicon** (default, zero deps) — keyword/phrase matching described below
- **finbert** (optional extra) — ProsusAI/finbert via transformers, much higher accuracy. Install: `pip3 install torch --index-url https://download.pytorch.org/whl/cpu && pip3 install transformers`. Any load failure falls back to lexicon with a warning (cron never hard-fails). Confident classifications are scaled to phrase-match units (2 pts).

In batch runs (`rank_stocks`), sentiment scores are recentered on the batch median (see Cross-Sectional Normalization).

The lexicon backend uses two-tier matching:

1. **Phrases** (2 points each): "beat estimates", "raised guidance", "fda approval", "analyst downgrade", "earnings miss", etc. Matched first; constituent words are excluded from keyword matching to avoid double-counting.
2. **Keywords** (1 point each): "bullish", "outperform", "surge", "bearish", "lawsuit", "plunge", etc.

**Recency weighting**: Articles from the last 24 hours count 2x, 1–3 days count 1x, older than 3 days count 0.5x.

**Robustness** (all in `analysis/sentiment.py`):
- **Shrinkage**: score = (pos + k)/(pos + neg + 2k) × 100 with k=3 — one lone positive signal lands ~57, not 100; it takes ~10 net signals to clear 75. Zero signals = exactly 50.
- **Negation**: a keyword preceded within 2 tokens by "not"/"fails"/"never"/etc. flips polarity ("not strong" counts negative).
- **Neutral tokens**: bare "revenue", "debt", "launch", "contract" carry no sentiment; directional phrases ("revenue beat", "rising debt", "wins contract") do.
- **Dedup**: headlines with token-set Jaccard ≥ 0.8 are the same syndicated story, counted once.

Sentiment completeness = unique articles / 5 (capped at 1.0); no news = completeness 0 (pillar dropped from composite).

### ETF Fundamental Scoring

When a ticker is detected as an ETF (via yahooquery `quoteType`), the fundamental scorer switches to ETF-specific metrics. ETFs in custom ticker lists (`--ticker VOO AAPL`) are auto-detected — no flag needed.

Six metrics with risk-adjusted weights:

| Metric | Weight (moderate) | What it measures |
|--------|-------------------|------------------|
| Expense ratio | 20% | Fund cost (lower is better: 0.03% = ultra-low, >1% = very expensive) |
| Blended returns | 25% | Weighted 1yr/3yr/5yr returns (blend shifts by risk profile) |
| vs Category | 15% | Outperformance vs category peers |
| Top-10 concentration | 15% | Holdings diversification (lower = more diversified) |
| AUM (total assets) | 10% | Fund size and liquidity |
| Distribution yield | 15% | Dividend/income yield |

Data sources: yahooquery `fund_profile` (expense ratio, category), `fund_performance` (returns), `fund_holding_info` (top holdings), `key_stats`/`summary_detail` (AUM, yield), `price` (current price).

### Portfolio-Aware Adjustments

When `portfolio.json` exists, the scoring engine applies:

- **Overlap penalty**: up to -5 points for stocks already held, tapering linearly from full at composite ≤65 to zero at ≥80 (no rank cliff at the Strong Buy cutoff)
- **Sector diversification**: -3 points for stocks in sectors >30% of portfolio value
- **Position sizing**: Monthly budget split by conviction (`max(composite − 45, 1)`) × inverse-vol scalar (`clamp(median_vol / stock_vol, 0.5, 2.0)`; missing vol = neutral 1.0), with a per-position cap of 35% of the monthly budget (`MAX_POSITION_FRACTION`); capped excess redistributes to uncapped names, or stays unallocated if everything caps
- **Account placement**: Suggests Roth IRA (high-growth, low-dividend, tax-free compounding) or Brokerage (dividends, shorter-term holds, flexibility) per stock. When `roth_ira_maxed` is true, all suggestions route to Brokerage.

## Account-Specific Analysis (`--account`)

The `--account roth|brokerage|hsa` flag runs a scoped analysis for a single account:

1. **Loads account holdings** from `portfolio.json` (handles flat Schwab structure and nested Fidelity sub-accounts)
2. **Unions held tickers** with the analysis universe so existing positions are always scored
3. **Updates market values** with live prices from yahooquery (falls back to stale `portfolio.json` values for tickers not in the fetch results, e.g., index funds)
4. **Computes account-scoped sector allocation** (not portfolio-wide)
5. **Scores all stocks** with `return_all=True` (no top-N truncation)
6. **Splits results** into current holdings and alternatives
7. **Generates swap suggestions** for weak positions (score < 50) when a better alternative exists (same-sector preferred, minimum 10-point improvement required)

### Swap Suggestions

For each held stock scoring below 50:
- Prefer a same-sector replacement with score delta > 10 points
- Fall back to the best cross-sector alternative if no same-sector candidate
- Skip if no candidate improves the score by at least 10 points

### Roth IRA Maxed Mode

When `roth_ira_maxed` is `true` in `portfolio.json` and analyzing the Roth account:
- Position sizing is suppressed (no new contributions)
- Report shows "CONTRIBUTIONS MAXED (reallocation only)" status
- Swap suggestions are the primary actionable output
- Account placement routes all suggestions to Brokerage

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
3. Adjust existing weights in `_WEIGHTS` dict to sum to 1.0 (there's a test for this)
4. Add a test case in `tests/test_technical.py`
5. To track the signal's standalone predictive power, record it in `ScoredStock.components` in `scoring/engine.py:score_stock` — it flows into snapshots automatically and appears in `backtest --eval-snapshots --by-component`
6. To make it cross-sectionally normalized, add it to `FACTOR_DIRECTIONS` in `analysis/xsection.py` and blend via `blend_with_percentile`

### Adding a New Data Source

1. Add the fetch function in `data/fetcher.py`
2. If it needs an API key, add to `Config` in `config.py` and `.env.example`
3. If it has rate limits, use the existing `RateLimiter` class (thread-safe, supports concurrent fetch workers)
4. Store the data in `StockData` (add field to `data/models.py` if needed)

### Adding a New Integration

1. Create a new module in `integrations/`
2. Add credentials to `Config` in `config.py` and `.env.example`
3. Wire the sync into `integrations/sync_all.py`
4. If it requires OAuth/linking, add a case in `main.py:_run_link_*`

### Adjusting Scoring

Weights are in `.env` (WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL, WEIGHT_SENTIMENT). Sub-indicator weights are hardcoded in each analysis module — edit the `_WEIGHTS` dict in `analysis/technical.py` or the `0.XX` multipliers in the score functions.

Recommendation thresholds are in `scoring/engine.py:_recommendation_label()`.

Portfolio penalties (overlap, sector overweight) are constants in `scoring/engine.py`.

Swap thresholds are in `portfolio/loader.py` (`WEAK_THRESHOLD = 50.0`, `MIN_SWAP_DELTA = 10.0`).

## Known Limitations

- Default sentiment is keyword/phrase-based — set `SENTIMENT_BACKEND=finbert` (after installing the extra) for model-based accuracy; recentering fixes the lexicon's skew but not its blindness to nuance
- Backtesting covers the technical pillar only and is survivorship-biased (universes are today's constituents; delisted names are absent) — snapshot evaluation is the survivorship-free path, but it needs history to accumulate
- Backtest t-stats are upper bounds when forward windows overlap (step < horizon) — per-date ICs are autocorrelated
- Sector-relative valuation is batch-relative — small universes (watchlist) mostly fall back to absolute bands (cross-sectional percentiles use the cached sp500 stats instead)
- Pillar weights and band cutoffs remain hand-set until snapshot IC evidence accumulates (see the retuning policy in README.md)
- No options or derivatives data
- yahooquery uses unofficial Yahoo Finance endpoints — can break if Yahoo changes their API
- Yahoo RSS feed (`feeds.finance.yahoo.com`) is an unofficial endpoint and may also break
- No real-time streaming — batch analysis only
- Finnhub free tier rate limit (60/min) is shared across all processes — back-to-back runs can hit 429 errors, which fall back to Yahoo RSS
- Fidelity is not available via Plaid — must use CSV import
- 401(k) accounts are not selectable for `--account` analysis (employer-managed, limited reallocation options)

## Automation

`scripts/auto_routine.py` runs daily via user crontab (12:15, `flock`-guarded) and executes whatever is due per `logs/routine_state.json` — a catch-up design, since WSL cron only fires while the machine is on:

| Task | Cadence | Command |
|------|---------|---------|
| Schwab sync | every 6 days | `sync --schwab-only` (also keeps the OAuth refresh token alive — it expires after ~7 days of inactivity) |
| Score snapshot | every 6 days | `analyze --universe sp500 --no-portfolio --snapshot` (raw model scores; ~500-name cross-section for tight IC error bars; also refreshes the factor-stats cache; ~15-25 min through the Finnhub limiter, 30-min timeout) |
| Snapshot evaluation | every 28 days | `backtest --eval-snapshots --by-component` (per-signal IC table) |

Results are appended to the Obsidian vault note `Notes/Projects/StockBot/StockBot Automation Log.md` (✅/❌ status lines; full report for evaluations). Failures don't update state, so they retry the next day. **Plaid is never called** (limited API quota); Fidelity remains manual CSV import.

Three redundant triggers share the same flock lock + state file (extra fires are no-ops):

1. WSL cron daily at 12:15 (only fires while WSL is running)
2. WSL cron `@reboot` (+90s) — fires whenever WSL starts
3. **Windows Task Scheduler task "StockBot Routine"** — daily 12:30 + at logon, with missed-run catch-up (`StartWhenAvailable`). Boots WSL headlessly via `wsl.exe`, so the routine runs even if WSL is never opened. Re-register with `scripts/windows_task_setup.ps1` (run from PowerShell). This is the primary trigger on a machine where WSL is rarely opened.

Disable: remove the two `stockbot` lines from `crontab -e` and delete the "StockBot Routine" task in Windows Task Scheduler (`Unregister-ScheduledTask -TaskName 'StockBot Routine'`).

## Backtesting & Snapshots

```bash
python3 main.py backtest --ticker AAPL MSFT NVDA JPM --years 3   # as-of technical backtest
python3 main.py backtest --universe watchlist                    # backtest the watchlist
python3 main.py analyze --snapshot                               # append scores to snapshots/scores.jsonl
python3 main.py backtest --eval-snapshots                        # validate accumulated snapshots vs realized returns
python3 main.py backtest --eval-snapshots --by-component         # per-signal IC table (momentum, quality, ...)
```

The `backtest` subcommand (`backtest/engine.py`) scores each ticker as-of historical dates using only bars up to that date (no lookahead — the input frame is sliced), keyed by **calendar date** so tickers with different history lengths still align cross-sectionally. It reports per-horizon (21/63-bar) rank-IC statistics — mean ± std, t-stat, hit-rate, N dates — plus mean forward return per score quintile, **averaged per date then across dates**. Forward returns are **excess of SPY** over the same window when SPY data is available (within a date this shifts by a constant, so the IC is unchanged — it makes the quintile returns honest in trending markets). It fetches its own 3-year history (`_WARMUP_BARS = 252` for momentum + forward window don't fit the default lookback). **Technical pillar only** — fundamentals and sentiment aren't available as-of past dates — and survivorship-biased by construction (see caveats it prints).

`analyze --snapshot` appends the full scored cross-section to `snapshots/scores.jsonl` (gitignored, one JSON object per line). Each record carries the pillar scores, `benchmark_price` (SPY close on snapshot day, for excess-return evaluation), and a `components` dict (momentum, quality, raw sentiment, realized vol, `composite_pre_xs`, …) — the extension seam: new factors recorded there accrue evaluation history with no schema changes, and `load_snapshots` tolerates schema evolution in both directions. After snapshots are ≥21 days old, `backtest --eval-snapshots` compares snapshot scores against realized (excess) returns — the honest way to validate the composite; `--by-component` prints the per-signal IC table that drives weight decisions. Snapshot the whole universe (not a 2-ticker list) so cross-sectional statistics have enough names; tickers that have since lost price data are counted in the caveats rather than silently dropped.

## Testing

```bash
python3 -m pytest tests/ -v
```

~285 tests covering technical analysis (incl. momentum and realized vol), fundamental scoring (stock, ETF, quality metrics), sector-relative valuation, cross-sectional normalization (percentiles, blending, cache), sentiment analysis (shrinkage/negation/dedup/backends), data completeness and exclusion, backtest statistics (IC t-stats, excess returns, per-date quintiles) and snapshot schema compatibility, portfolio management, vol-aware position sizing, account placement, swap suggestions, CLI argument parsing, data fetching, and universe resolution. Tests use hardcoded data fixtures — no API calls (an autouse fixture isolates the factor-stats cache). All analysis and scoring logic is testable offline.
