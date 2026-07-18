from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import auto_routine
from auto_routine import (
    RoutineTask,
    append_to_note,
    is_due,
    load_state,
    save_state,
    strip_ansi,
)


class TestIsDue:
    def test_never_run_is_due(self) -> None:
        assert is_due(None, 6)
        assert is_due("", 6)

    def test_recent_run_not_due(self) -> None:
        now = datetime.now(timezone.utc)
        five_days_ago = (now - timedelta(days=5)).isoformat()
        assert not is_due(five_days_ago, 6, now)

    def test_exactly_cadence_days_is_due(self) -> None:
        now = datetime.now(timezone.utc)
        six_days_ago = (now - timedelta(days=6)).isoformat()
        assert is_due(six_days_ago, 6, now)

    def test_corrupt_timestamp_is_due(self) -> None:
        assert is_due("not-a-date", 6)

    def test_naive_timestamp_handled(self) -> None:
        now = datetime.now(timezone.utc)
        naive = (now - timedelta(days=10)).replace(tzinfo=None).isoformat()
        assert is_due(naive, 6, now)


class TestState:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        save_state({"last_sync": "2026-07-18T12:00:00+00:00"}, path)
        assert load_state(path) == {"last_sync": "2026-07-18T12:00:00+00:00"}

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_state(tmp_path / "nope.json") == {}

    def test_corrupt_file_is_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("{not json")
        assert load_state(path) == {}

    def test_non_dict_payload_is_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps([1, 2, 3]))
        assert load_state(path) == {}


class TestStripAnsi:
    def test_removes_color_codes(self) -> None:
        assert strip_ansi("\033[32mBuy\033[0m") == "Buy"

    def test_plain_text_unchanged(self) -> None:
        assert strip_ansi("plain text") == "plain text"


class TestAppendToNote:
    def test_creates_note_with_header(self, tmp_path: Path) -> None:
        note = tmp_path / "log.md"
        append_to_note("\n- entry one\n", note)
        content = note.read_text()
        assert content.startswith("---")
        assert "# StockBot Automation Log" in content
        assert "- entry one" in content

    def test_appends_without_duplicating_header(self, tmp_path: Path) -> None:
        note = tmp_path / "log.md"
        append_to_note("\n- entry one\n", note)
        append_to_note("\n- entry two\n", note)
        content = note.read_text()
        assert content.count("# StockBot Automation Log") == 1
        assert "- entry two" in content


class TestOrchestration:
    def _patch_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        note = tmp_path / "log.md"
        monkeypatch.setattr(auto_routine, "STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr(auto_routine, "VAULT_NOTE_PATH", note)
        # re-bind defaults captured at call sites
        monkeypatch.setattr(
            auto_routine, "load_state", lambda: load_state(tmp_path / "state.json")
        )
        monkeypatch.setattr(
            auto_routine, "save_state",
            lambda s: save_state(s, tmp_path / "state.json"),
        )
        monkeypatch.setattr(
            auto_routine, "append_to_note",
            lambda entry: append_to_note(entry, note),
        )
        return note

    def test_due_tasks_run_and_state_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        note = self._patch_env(tmp_path, monkeypatch)
        calls: list[list[str]] = []

        def fake_run(task: RoutineTask) -> tuple[bool, str]:
            calls.append(task.command)
            return True, "Schwab: synced 2 account(s)"

        monkeypatch.setattr(auto_routine, "run_task", fake_run)
        exit_code = auto_routine.main()

        assert exit_code == 0
        assert len(calls) == len(auto_routine.TASKS)
        state = load_state(tmp_path / "state.json")
        assert set(state) == {"last_sync", "last_snapshot", "last_eval"}
        assert "✅" in note.read_text()

    def test_failure_leaves_state_unset_and_logs_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        note = self._patch_env(tmp_path, monkeypatch)

        def fake_run(task: RoutineTask) -> tuple[bool, str]:
            if task.state_key == "last_sync":
                return False, "exit code 1\n```\nSchwab auth failed\n```"
            return True, "ok"

        monkeypatch.setattr(auto_routine, "run_task", fake_run)
        exit_code = auto_routine.main()

        assert exit_code == 1
        state = load_state(tmp_path / "state.json")
        assert "last_sync" not in state  # retries tomorrow
        assert "last_snapshot" in state
        assert "❌ Schwab sync failed" in note.read_text()

    def test_not_due_tasks_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        save_state(
            {"last_sync": now, "last_snapshot": now, "last_eval": now},
            tmp_path / "state.json",
        )
        self._patch_env(tmp_path, monkeypatch)
        calls: list[str] = []
        monkeypatch.setattr(
            auto_routine, "run_task",
            lambda task: calls.append(task.name) or (True, "ok"),
        )
        exit_code = auto_routine.main()

        assert exit_code == 0
        assert calls == []

    def test_no_plaid_anywhere(self) -> None:
        for task in auto_routine.TASKS:
            assert "--plaid-only" not in task.command
            joined = " ".join(task.command)
            assert "plaid" not in joined.lower()
        sync_tasks = [t for t in auto_routine.TASKS if "sync" in t.command]
        assert all("--schwab-only" in t.command for t in sync_tasks)
