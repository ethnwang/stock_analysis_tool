"""Daily catch-up runner for StockBot's recurring chores.

Invoked daily by cron; runs only the tasks that are due per the state file,
so missed days (machine off) are caught up on the next run instead of
silently skipped. Weekly Schwab sync also keeps the OAuth refresh token
alive — it expires after ~7 days of inactivity.

Plaid is deliberately never called here (limited API quota); Fidelity is
manual CSV import only.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TRADING_DIR = Path(__file__).parent.parent
STATE_PATH = TRADING_DIR / "logs" / "routine_state.json"
VAULT_NOTE_PATH = Path(
    "/home/ethnwang/claude-glass/Notes/Projects/StockBot/StockBot Automation Log.md"
)

SYNC_CADENCE_DAYS = 6
SNAPSHOT_CADENCE_DAYS = 6
EVAL_CADENCE_DAYS = 28

SYNC_TIMEOUT_S = 300
ANALYZE_TIMEOUT_S = 900
EVAL_TIMEOUT_S = 900

_FAILURE_TAIL_LINES = 10

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_NOTE_HEADER = """---
created: {today}
tags: [stockbot, automation]
---

# StockBot Automation Log

Written by `scripts/auto_routine.py` (daily cron, 12:15). Weekly Schwab sync
+ score snapshots, monthly snapshot evaluation. Plaid is never called.
"""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutineTask:
    name: str
    state_key: str
    cadence_days: int
    command: list[str]
    timeout_s: int


TASKS: list[RoutineTask] = [
    RoutineTask(
        name="Schwab sync",
        state_key="last_sync",
        cadence_days=SYNC_CADENCE_DAYS,
        command=[sys.executable, "main.py", "sync", "--schwab-only"],
        timeout_s=SYNC_TIMEOUT_S,
    ),
    RoutineTask(
        name="Score snapshot",
        state_key="last_snapshot",
        cadence_days=SNAPSHOT_CADENCE_DAYS,
        # --no-portfolio: snapshots should record the raw scoring model,
        # not overlap/sector-penalized values
        command=[
            sys.executable, "main.py", "analyze",
            "--universe", "watchlist", "--no-portfolio", "--snapshot",
        ],
        timeout_s=ANALYZE_TIMEOUT_S,
    ),
    RoutineTask(
        name="Snapshot evaluation",
        state_key="last_eval",
        cadence_days=EVAL_CADENCE_DAYS,
        command=[sys.executable, "main.py", "backtest", "--eval-snapshots"],
        timeout_s=EVAL_TIMEOUT_S,
    ),
]

# Tasks whose full (ANSI-stripped) output goes into the vault note, not just a status line
_FULL_REPORT_TASKS = {"last_eval"}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def load_state(path: Path = STATE_PATH) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Corrupt state file %s (%s) — treating as never-run", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def save_state(state: dict[str, str], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_due(last_run_iso: str | None, cadence_days: int, now: datetime | None = None) -> bool:
    if not last_run_iso:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_iso)
    except ValueError:
        return True
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - last_run).days >= cadence_days


def append_to_note(entry: str, path: Path = VAULT_NOTE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            today = datetime.now(timezone.utc).date().isoformat()
            path.write_text(_NOTE_HEADER.format(today=today), encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as exc:
        logger.error("Could not write vault note %s: %s", path, exc)


def run_task(task: RoutineTask) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            task.command,
            cwd=TRADING_DIR,
            capture_output=True,
            text=True,
            timeout=task.timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {task.timeout_s}s"
    except OSError as exc:
        return False, f"failed to launch: {exc}"

    output = strip_ansi(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-_FAILURE_TAIL_LINES:])
        return False, f"exit code {proc.returncode}\n```\n{tail}\n```"
    return True, output


def _format_entry(task: RoutineTask, ok: bool, detail: str, now: datetime) -> str:
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    if not ok:
        return f"\n## {stamp} — ❌ {task.name} failed\n\n{detail}\n\nWill retry tomorrow.\n"
    if task.state_key in _FULL_REPORT_TASKS:
        return f"\n## {stamp} — ✅ {task.name}\n\n```\n{detail.strip()}\n```\n"
    # concise status line for routine successes
    summary = _summarize_output(detail)
    return f"\n- {stamp} — ✅ {task.name}{summary}\n"


def _summarize_output(output: str) -> str:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Schwab: synced") or line.startswith("Appended"):
            return f" — {line}"
    return ""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state()
    now = datetime.now(timezone.utc)
    ran_anything = False
    had_failure = False

    for task in TASKS:
        if not is_due(state.get(task.state_key), task.cadence_days, now):
            logger.info("%s not due — skipping", task.name)
            continue
        ran_anything = True
        logger.info("Running %s: %s", task.name, " ".join(task.command[1:]))
        ok, detail = run_task(task)
        append_to_note(_format_entry(task, ok, detail, now))
        if ok:
            state[task.state_key] = now.isoformat()
            save_state(state)
            logger.info("%s succeeded", task.name)
        else:
            had_failure = True
            logger.error("%s failed: %s", task.name, detail.splitlines()[0] if detail else "?")

    if not ran_anything:
        logger.info("Nothing due today.")
    return 1 if had_failure else 0


if __name__ == "__main__":
    sys.exit(main())
