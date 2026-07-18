from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.models import StockData

# Sector medians computed from fewer peers than this are noise, not signal —
# scorers fall back to absolute bands below it.
MIN_SECTOR_PEERS = 5

_METRICS = ("pe_ratio", "debt_to_equity", "eps_growth", "revenue_growth")


@dataclass(frozen=True)
class SectorStats:
    medians: dict[str, dict[str, float]]  # sector -> metric -> median
    counts: dict[str, int]  # sector -> number of peer stocks in batch

    def get(self, sector: str, metric: str) -> float | None:
        if sector == "Unknown" or self.counts.get(sector, 0) < MIN_SECTOR_PEERS:
            return None
        return self.medians.get(sector, {}).get(metric)


def build_sector_stats(stocks: list[StockData]) -> SectorStats:
    by_sector: dict[str, dict[str, list[float]]] = {}
    counts: dict[str, int] = {}

    for stock in stocks:
        if (stock.fundamentals.get("is_etf") or 0.0) > 0:
            continue
        sector = stock.sector
        if not sector or sector == "Unknown":
            continue
        counts[sector] = counts.get(sector, 0) + 1
        buckets = by_sector.setdefault(sector, {m: [] for m in _METRICS})
        for metric in _METRICS:
            value = stock.fundamentals.get(metric)
            if value is None:
                continue
            if metric == "pe_ratio" and value <= 0:
                continue  # negative P/E is "unprofitable", not a valuation level
            buckets[metric].append(value)

    medians: dict[str, dict[str, float]] = {}
    for sector, buckets in by_sector.items():
        medians[sector] = {
            metric: statistics.median(values)
            for metric, values in buckets.items()
            if values
        }

    return SectorStats(medians=medians, counts=counts)
