# StockBot — Agent Guide

Stock analysis CLI: fetches market data, scores stocks (technical/fundamental/sentiment), outputs ranked buy recommendations with portfolio-aware sizing. Human docs, all commands/flags, and scoring tables: `README.md`. Before changing ANY weight, band, factor, or threshold: read `docs/RESEARCH.md` and follow its checklist.

## Commands

```bash
python3 -m pytest tests/ -q                                        # ~290 tests, all offline — run after every change
python3 main.py analyze --universe sp500 --no-portfolio --snapshot # canonical snapshot run (feeds validation)
python3 main.py backtest --eval-snapshots --by-component           # per-signal IC table — the weight-change instrument
python3 main.py backtest --universe watchlist                      # as-of technical backtest (fetches own 3y history)
python3 main.py analyze --account roth|brokerage|hsa               # account-scoped analysis with swap suggestions
```

Full flag reference in README.md. Python 3.10+.

## Architecture

```
main.py (CLI: analyze, sync, link, import, backtest)
  → config.py (.env → frozen Config; weights auto-normalized)
  → portfolio/loader.py (portfolio.json, position sizing, swaps, account placement)
  → data/universe.py (watchlist 48 / sp500 via Finnhub→Wikipedia→cache / 35 ETFs)
  → data/fetcher.py (yahooquery prices+fundamentals, Finnhub+RSS news, rate limiters)
  → data/models.py (StockData, ScoredStock, ScoreResult, SwapSuggestion)
  → analysis/technical.py, fundamental.py, etf_fundamental.py (per-pillar scorers)
  → analysis/sentiment.py + sentiment_backends.py (aggregation + lexicon/FinBERT polarity)
  → analysis/relative.py (batch sector medians), xsection.py (percentile blending, factor-stats cache)
  → scoring/engine.py (composite, penalties, rank_stocks)
  → backtest/engine.py + snapshots.py (IC stats, snapshot eval)
  → reporting/console.py | integrations/ (Schwab OAuth, Plaid, Fidelity CSV)
```

Data flow: Config → Portfolio → Universe → Fetch → Analyze → Score → Rank → Size → Report.

## Invariants — do not break

- Missing data = `None` end-to-end, never `0.0`. Scorers skip absent metrics and renormalize remaining weights. ONE exception: `dividend_yield` defaults to `0.0` — Yahoo omits it for non-payers, absence IS the signal.
- `data_completeness` is weighted by the ORIGINAL pillar weights while the composite renormalizes over active pillars — deliberate, so a one-pillar stock can't pass the `MIN_DATA_COMPLETENESS` gate (`scoring/engine.py:score_stock`).
- `pe_ratio` is trailing-only; `forward_pe` is a separate field used as an explicitly-labeled absolute-band fallback. Never mix P/E bases inside sector medians or percentiles.
- `snapshots/scores.jsonl` is append-only; never rewrite history. Schema must stay tolerant BOTH directions (old code reads new rows, new code reads old rows). Extend only via `ScoredStock.components` — new signals recorded there accrue evaluation history with zero schema changes.
- When the composite formula changes, record a continuity component (pattern: `components["composite_pre_xs"]`) so the snapshot evaluation series stays comparable.
- Backtest semantics (`backtest/engine.py`): no lookahead (input frame sliced as-of), cross-sections keyed by calendar date, forward returns excess-of-SPY when available, technical pillar only, survivorship-biased (universes are today's constituents). Keep every one of those properties.
- Weight/band/threshold changes are EVIDENCE-GATED. Follow `docs/RESEARCH.md`; never tune by feel or from a single run's output.
- Live `.env` overrides documented defaults (weights currently differ from the 0.45/0.45/0.10 defaults). Read `.env` before asserting what the system runs — never quote defaults as live behavior.
- Tests are offline-only: hardcoded fixtures, no network, deterministic RNG seeds; an autouse fixture isolates the factor-stats cache. Keep new tests that way.

## Gotchas & environment

- NEVER call Plaid automatically — limited API quota. Plaid is manual-only; Fidelity isn't in Plaid at all (CSV import: `main.py import <file>`).
- The Schwab refresh token expires after ~7 idle days; the 6-day auto-sync keeps it alive. Don't disable the sync without a replacement.
- yahooquery and Yahoo RSS use unofficial endpoints — silent breakage is expected occasionally. `YF_SETUP_URL` overrides the cookie-bootstrap URL.
- Finnhub free tier is 60 calls/min shared across ALL processes — concurrent runs 429 and fall back to RSS. The index-constituents endpoint is premium: sp500 resolves Finnhub → Wikipedia scrape → 90-day cache → hardcoded ~100-name fallback (`data/universe.py:get_sp500_tickers`).
- Sector medians and factor percentiles are batch-relative. Small batches blend against the cached sp500 factor stats (`data/cache/factor_stats.json`, 30-day staleness, refreshed by sp500 runs). ETFs never blend against stock breakpoints.
- Sentiment backend comes from `SENTIMENT_BACKEND`; any FinBERT load failure falls back to the lexicon with a warning — automation must never hard-fail on it.
- Noisy third-party loggers (urllib3, finnhub, httpx, huggingface_hub, …) are pinned to WARNING in `main.py` so API keys and hub chatter don't leak into `-v` output. Add new HTTP libraries to that list.
- 401(k) accounts are not selectable via `--account` (employer-managed).

## Automation

`scripts/auto_routine.py` runs daily (flock-guarded, catch-up design driven by `logs/routine_state.json` — failures don't update state, so they retry next day):

| Task | Cadence |
|------|---------|
| `sync --schwab-only` | 6 days (keeps OAuth token alive) |
| `analyze --universe sp500 --no-portfolio --snapshot` | 6 days (~20 min; 30-min timeout) |
| `backtest --eval-snapshots --by-component` | 28 days |

Three redundant triggers: WSL cron 12:15 + `@reboot`, and Windows Task Scheduler task "StockBot Routine" (primary — boots WSL headlessly; re-register with `scripts/windows_task_setup.ps1`). Results append to the vault note `Notes/Projects/StockBot/StockBot Automation Log.md`. Disable: remove the two `stockbot` crontab lines and `Unregister-ScheduledTask -TaskName 'StockBot Routine'`.

## Development recipes

**New technical indicator**: compute in `analysis/technical.py:compute_indicators()` → score in `score_technical()` → rebalance `_WEIGHTS` to 1.0 (tested) → record the signal in `ScoredStock.components` (`scoring/engine.py:score_stock`) so its standalone IC accrues → optionally add to `FACTOR_DIRECTIONS` in `analysis/xsection.py` for percentile blending.

**New fundamental factor**: field in `data/fetcher.py:_extract_ticker_info` + `_EMPTY_FUNDAMENTALS` → scorer + weight in every `_RISK_WEIGHTS` profile (`analysis/fundamental.py`, each must sum to 1.0) → sector-relative? add to `_METRICS` in `analysis/relative.py`.

**New data source**: fetch fn in `data/fetcher.py`; credentials via `Config` + `.env.example`; reuse `RateLimiter` (thread-safe); new fields go on `StockData` (`data/models.py`).

**New integration**: module in `integrations/` → credentials in `Config` → wire into `integrations/sync_all.py` → OAuth flows get a case in `main.py:_run_link_*`.

**Knob locations**: pillar weights in `.env`; sub-weights in each analysis module's `_WEIGHTS`/`_RISK_WEIGHTS`; recommendation cutoffs in `scoring/engine.py:_recommendation_label`; overlap/sector penalties + taper constants in `scoring/engine.py`; sizing cap + vol clamps in `portfolio/loader.py`; swap thresholds `WEAK_THRESHOLD`/`MIN_SWAP_DELTA` in `portfolio/loader.py`.

## Testing

`python3 -m pytest tests/ -q` — all offline, fixtures in `tests/conftest.py` (`make_stock`/`make_etf`/`default_config`). Every new scorer/threshold needs a test; exact-value assertions must account for weight renormalization when metrics are absent.

## Research methodology

Any change to weights, bands, factors, thresholds, or the composite formula must follow the workflow in `docs/RESEARCH.md` (hypothesis → components → snapshot IC → decision rules). No exceptions, including "obvious" improvements.
