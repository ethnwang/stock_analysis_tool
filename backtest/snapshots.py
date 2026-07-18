from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
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
                records.append(SnapshotRecord(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Skipping malformed snapshot line %d: %s", line_no, exc)
    return records
