# StockBot Research Methodology

Process rules for ANY change to pillar weights, sub-weights, score bands, recommendation cutoffs, factors, or the composite formula. Definitions live in the code and README; this file is about how to decide, not what things mean.

## The prime rule

**No scoring change without cross-sectional IC evidence.** Never tune on gut feel, on a single run's output, or because one pick "looked wrong." Evidence means `backtest --eval-snapshots --by-component` over at least 3–5 evaluable snapshot dates (snapshots become evaluable 21 days after they're taken).

## Change workflow (follow in order)

1. **Hypothesis first.** Before touching code, write down: the signal, the expected direction, and WHY it should predict cross-sectional returns. A change must be justified ex ante — not discovered by scanning the component IC table for the biggest number.
2. **Implement behind components.** Record the raw signal in `ScoredStock.components` (`scoring/engine.py:score_stock`) with **zero weight in the composite**. `components` flows into snapshots with no schema changes, so the signal accrues evaluation history before it can affect a single recommendation.
3. **Accumulate.** The canonical snapshot run is `analyze --universe sp500 --no-portfolio --snapshot` — full breadth (~500 names) for tight IC error bars, `--no-portfolio` so snapshots record the raw model, not penalized values. The automation already does this every 6 days.
4. **Measure.** After snapshots are ≥21 days old, run `backtest --eval-snapshots --by-component` and read the signal's row: IC mean ± std, t-stat, hit-rate, number of dates.
5. **Decide.** Apply the decision rules below. Prefer effects that hold at both horizons the as-of backtest reports (21 and 63 bars) and across multiple snapshot dates — one good month is noise.
6. **Apply.** Change the weight. Whenever the composite formula changes, record a continuity component (pattern: `components["composite_pre_xs"]`) so the evaluation series isn't silently broken by the definition change.
7. **Re-check the labels.** After any scoring change, sanity-check the 80/65/45 recommendation cutoffs against a fresh snapshot cross-section — a formula change can shift the whole distribution.

## Decision rules

- Technical IC t-stat ≤ 0 across horizons → cut `WEIGHT_TECHNICAL` to ~0.25, redistribute to fundamental.
- Sentiment IC ≈ 0 → cut `WEIGHT_SENTIMENT` to ~0.10.
- Momentum component IC ≥ 0.05 while other technical components stay ≤ 0 → raise momentum's share inside `analysis/technical.py:_WEIGHTS`, or promote it to its own pillar.
- A candidate signal in `components` earns composite weight only with IC ≥ ~0.03 sustained across ≥3 evaluation dates and a defensible ex-ante story.

## Overfitting guards

- **Count every variant you evaluate.** Multiple comparisons inflate the best-looking IC — if you test 10 ideas, the best one looks good by luck alone. Cap experiments at ~3 per evaluation cycle and note the count when deciding.
- **No tweak-and-rerun loops** against the same snapshot history. Once you've conditioned a change on a dataset, that dataset stops being out-of-sample; the next decision needs snapshot history accumulated AFTER the change.
- **Treat overlapping-window t-stats as upper bounds.** The as-of backtest steps every 5 bars with 21/63-bar horizons, so per-date ICs are autocorrelated; the reported t-stat overstates significance (the report says so in its caveats).
- **Walk-forward mindset.** Snapshots ARE the out-of-sample set; the historical as-of backtest is in-sample exploration only. Nothing graduates to the composite on backtest evidence alone.
- **Prefer fewer, well-motivated factors** over many marginal ones — every added parameter is another chance to fit noise.

## Known biases in this repo's evaluation

- **Survivorship**: backtest universes are TODAY'S constituents — delisted names are absent, so historical results are optimistic. Snapshot evaluation is the survivorship-free path (and it counts tickers that lose price data in its caveats rather than silently dropping them — keep it that way).
- **Lookahead**: `backtest/engine.py` slices the input frame as-of each date; there is a regression test for it. Fundamentals and sentiment are not available as-of past dates, so the backtest validates the technical pillar only — it cannot bless the composite.
- **Batch-relative statistics**: sector medians and factor percentiles depend on whichever batch was analyzed. Only sp500 runs are dense; watchlist runs lean on the cached sp500 factor stats. Never compare scores across runs with different universes as if they were on one scale.
- **Excess-of-SPY returns** shift every name at a date by the same constant, so the IC is unchanged — the point is honest quintile returns that don't ride a trending market.

## Data caveats (free-tier sources)

- yahooquery and Yahoo RSS are unofficial endpoints; silent breakage is a data-quality event, not a market signal. Investigate before trusting any anomalous cross-section.
- Finnhub free tier: 60 calls/min shared across processes; the constituents endpoint is premium, so universe membership comes from Wikipedia → cache → a ~100-name fallback and can drift between runs. Check the run log's universe size before comparing cross-sections.
- P/E is trailing-only in `pe_ratio`; `forward_pe` is separate. Never mix bases in any cross-sectional statistic.
- `dividend_yield` absent = `0.0` by design (non-payers); every other missing metric is `None`, skipped and renormalized. A cross-section with unusually low completeness is a fetch problem, not a signal.

## Snapshot hygiene

- Snapshot the WHOLE universe, never a thin ticker list — cross-sectional statistics need breadth (`_MIN_TICKERS_PER_DATE = 8` is a floor, not a target).
- `snapshots/scores.jsonl` is append-only. Never rewrite, dedupe, or "fix" history; a bad run's rows are still evidence about the code that produced them.
- Schema evolves only by adding optional fields or `components` keys; `load_snapshots` must keep tolerating unknown keys (forward) and missing new fields (backward).
- Extra manual snapshots are harmless (more dates = more evidence); missing ones just slow the evidence down.
