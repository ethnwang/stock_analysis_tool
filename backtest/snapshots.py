from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_PATH = Path(__file__).parent.parent / "snapshots" / "scores.jsonl"


@dataclass(frozen=True)
class SnapshotRecord:
    date: str  # ISO date the scores were computed
    ticker: str
    composite: float
    technical: float
    fundamental: float
    sentiment: float
    completeness: float
    price: float
    universe: str
    risk_profile: str
    # Benchmark close on the snapshot date; 0.0 = not recorded (pre-upgrade rows)
    benchmark_price: float = 0.0
    # Extension seam: sub-signal scores (momentum, quality, …) land here so new
    # factors accrue evaluation history without schema changes
    components: dict[str, float] = field(default_factory=dict)


_KNOWN_FIELDS = {f.name for f in fields(SnapshotRecord)}


def append_snapshots(records: list[SnapshotRecord], path: Path = DEFAULT_SNAPSHOT_PATH) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(asdict(record)) + "\n")
    logger.info("Appended %d score snapshots to %s", len(records), path)


def load_snapshots(path: Path = DEFAULT_SNAPSHOT_PATH) -> list[SnapshotRecord]:
    if not path.exists():
        return []
    records: list[SnapshotRecord] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Ignore keys from newer schema versions instead of rejecting the row
                known = {k: v for k, v in data.items() if k in _KNOWN_FIELDS}
                records.append(SnapshotRecord(**known))
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                logger.warning("Skipping malformed snapshot line %d: %s", line_no, exc)
    return records
