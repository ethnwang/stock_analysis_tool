from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from integrations import schwab, sync_all
from integrations.schwab import SchwabAuthError


class TestSchwabSyncAuthError:
    def test_raises_on_client_creation_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_create_client(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("400 Client Error: Bad Request")

        monkeypatch.setattr(schwab, "create_client", fake_create_client)

        with pytest.raises(SchwabAuthError, match="400 Client Error"):
            schwab.sync("id", "secret", "refresh-token")

    def test_missing_credentials_returns_empty_without_raising(self) -> None:
        assert schwab.sync("", "", "") == {}


class TestSyncPortfolioErrorPropagation:
    def _patch_portfolio_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sync_all, "PORTFOLIO_PATH", tmp_path / "portfolio.json")

    def test_schwab_auth_error_surfaces_without_crashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_portfolio_path(tmp_path, monkeypatch)

        def fake_sync(*args: Any, **kwargs: Any) -> Any:
            raise SchwabAuthError("400 Client Error: Bad Request")

        monkeypatch.setattr(sync_all.schwab, "sync", fake_sync)

        portfolio = sync_all.sync_portfolio(
            schwab_client_id="id",
            schwab_client_secret="secret",
            schwab_refresh_token="refresh-token",
        )

        assert portfolio["_sync_errors"] == {"schwab": "400 Client Error: Bad Request"}
        assert "last_sync" not in portfolio

    def test_plaid_still_attempted_when_schwab_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_portfolio_path(tmp_path, monkeypatch)
        plaid_calls: list[Any] = []

        def fake_schwab_sync(*args: Any, **kwargs: Any) -> Any:
            raise SchwabAuthError("boom")

        def fake_plaid_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
            plaid_calls.append(args)
            return {"chase_checking": {"cash": 100.0}}

        monkeypatch.setattr(sync_all.schwab, "sync", fake_schwab_sync)
        monkeypatch.setattr(sync_all.plaid_sync, "sync", fake_plaid_sync)

        portfolio = sync_all.sync_portfolio(
            schwab_client_id="id",
            schwab_client_secret="secret",
            schwab_refresh_token="refresh-token",
            plaid_client_id="pid",
            plaid_secret="psecret",
            plaid_access_token_chase="tok",
        )

        assert len(plaid_calls) == 1
        assert portfolio["_sync_errors"] == {"schwab": "boom"}
        assert "chase_checking" in portfolio
        assert "last_sync" in portfolio  # Plaid succeeded, so overall sync is recorded

    def test_happy_path_has_no_errors_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_portfolio_path(tmp_path, monkeypatch)

        def fake_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"schwab_brokerage": {"cash": 10.0}}

        monkeypatch.setattr(sync_all.schwab, "sync", fake_sync)

        portfolio = sync_all.sync_portfolio(
            schwab_client_id="id",
            schwab_client_secret="secret",
            schwab_refresh_token="refresh-token",
        )

        assert "_sync_errors" not in portfolio
        assert "last_sync" in portfolio

    def test_errors_not_persisted_to_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_portfolio_path(tmp_path, monkeypatch)

        def fake_sync(*args: Any, **kwargs: Any) -> Any:
            raise SchwabAuthError("boom")

        monkeypatch.setattr(sync_all.schwab, "sync", fake_sync)

        sync_all.sync_portfolio(
            schwab_client_id="id",
            schwab_client_secret="secret",
            schwab_refresh_token="refresh-token",
        )

        assert not (tmp_path / "portfolio.json").exists()
