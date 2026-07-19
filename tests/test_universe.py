from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import data.universe as universe_mod
from data.universe import (
    DEFAULT_ETF_WATCHLIST,
    _SP500_FALLBACK,
    _fetch_sp500_wikipedia,  # bound before the autouse no-network patch
    get_sp500_tickers,
    get_universe,
)
from tests.conftest import default_config


class _FakeAPIError(Exception):
    pass


def _mock_finnhub_module(mock_client: MagicMock) -> MagicMock:
    mock_module = MagicMock()
    mock_module.Client.return_value = mock_client
    mock_module.FinnhubAPIException = _FakeAPIError
    mock_module.FinnhubRequestException = _FakeAPIError
    return mock_module


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(universe_mod, "_SP500_CACHE_PATH", tmp_path / "sp500.json")


@pytest.fixture(autouse=True)
def _no_network_wikipedia(monkeypatch):
    """Fallback tests must not hit Wikipedia; specific tests override this."""
    monkeypatch.setattr(universe_mod, "_fetch_sp500_wikipedia", lambda: None)


class TestGetSp500Tickers:
    def test_returns_finnhub_results_when_available(self) -> None:
        fake_tickers = [f"TICK{i}" for i in range(500)]
        mock_client = MagicMock()
        mock_client.indices_const.return_value = {"constituents": fake_tickers}

        with patch.dict("sys.modules", {"finnhub": _mock_finnhub_module(mock_client)}):
            result = get_sp500_tickers("real_key")

        assert len(result) == 500
        assert result == sorted(fake_tickers)

    def test_falls_back_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.indices_const.side_effect = _FakeAPIError("API error")

        with patch.dict("sys.modules", {"finnhub": _mock_finnhub_module(mock_client)}):
            result = get_sp500_tickers("real_key")

        assert result == list(_SP500_FALLBACK)

    def test_falls_back_when_no_api_key(self) -> None:
        result = get_sp500_tickers("")
        assert result == list(_SP500_FALLBACK)

    def test_falls_back_when_too_few_results(self) -> None:
        mock_client = MagicMock()
        mock_client.indices_const.return_value = {"constituents": ["AAPL", "MSFT"]}

        with patch.dict("sys.modules", {"finnhub": _mock_finnhub_module(mock_client)}):
            result = get_sp500_tickers("real_key")

        assert result == list(_SP500_FALLBACK)

    def test_successful_fetch_writes_cache(self) -> None:
        fake_tickers = [f"TICK{i}" for i in range(500)]
        mock_client = MagicMock()
        mock_client.indices_const.return_value = {"constituents": fake_tickers}

        with patch.dict("sys.modules", {"finnhub": _mock_finnhub_module(mock_client)}):
            get_sp500_tickers("real_key")

        payload = json.loads(universe_mod._SP500_CACHE_PATH.read_text())
        assert len(payload["tickers"]) == 500
        assert payload["fetched"] == date.today().isoformat()

    def test_api_error_uses_cache_before_hardcoded_fallback(self) -> None:
        cached = sorted(f"CACHED{i}" for i in range(500))
        universe_mod._SP500_CACHE_PATH.write_text(
            json.dumps({"fetched": date.today().isoformat(), "tickers": cached})
        )
        mock_client = MagicMock()
        mock_client.indices_const.side_effect = _FakeAPIError("API error")

        with patch.dict("sys.modules", {"finnhub": _mock_finnhub_module(mock_client)}):
            result = get_sp500_tickers("real_key")

        assert result == cached

    def test_stale_cache_still_used_with_warning(self, caplog) -> None:
        old = (date.today() - timedelta(days=200)).isoformat()
        cached = sorted(f"CACHED{i}" for i in range(500))
        universe_mod._SP500_CACHE_PATH.write_text(
            json.dumps({"fetched": old, "tickers": cached})
        )
        result = get_sp500_tickers("")
        assert result == cached

    def test_wikipedia_used_before_cache_and_writes_it(self, monkeypatch) -> None:
        wiki = sorted(f"WIKI{i}" for i in range(500))
        monkeypatch.setattr(universe_mod, "_fetch_sp500_wikipedia", lambda: wiki)

        result = get_sp500_tickers("")  # no Finnhub key
        assert result == wiki
        payload = json.loads(universe_mod._SP500_CACHE_PATH.read_text())
        assert payload["tickers"] == wiki


class TestWikipediaFetch:
    @staticmethod
    def _fake_response(html: str):
        class _Resp:
            text = html

            @staticmethod
            def raise_for_status() -> None:
                return None

        return _Resp()

    def test_parses_symbol_table_and_maps_dots(self, monkeypatch) -> None:
        rows = "".join(
            f"<tr><td>SYM{i}</td><td>Some Co</td></tr>" for i in range(450)
        )
        html = (
            "<table><thead><tr><th>Symbol</th><th>Security</th></tr></thead>"
            f"<tbody><tr><td>BRK.B</td><td>Berkshire</td></tr>{rows}</tbody></table>"
        )
        monkeypatch.setattr(
            "requests.get", lambda url, timeout, headers: self._fake_response(html),
        )
        tickers = _fetch_sp500_wikipedia()
        assert tickers is not None
        assert len(tickers) == 451
        assert "BRK-B" in tickers  # Yahoo-style symbol, not Wikipedia's BRK.B

    def test_small_table_rejected(self, monkeypatch) -> None:
        html = (
            "<table><thead><tr><th>Symbol</th></tr></thead>"
            "<tbody><tr><td>AAPL</td></tr></tbody></table>"
        )
        monkeypatch.setattr(
            "requests.get", lambda url, timeout, headers: self._fake_response(html),
        )
        assert _fetch_sp500_wikipedia() is None

    def test_network_error_returns_none(self, monkeypatch) -> None:
        import requests

        def _boom(url, timeout, headers):
            raise requests.ConnectionError("offline")

        monkeypatch.setattr("requests.get", _boom)
        assert _fetch_sp500_wikipedia() is None


class TestGetUniverseEtf:
    def test_etf_universe_returns_etf_list(self) -> None:
        config = default_config(universe="etf")
        tickers = get_universe(config)
        assert "VOO" in tickers
        assert "SPY" in tickers
        assert len(tickers) == len(DEFAULT_ETF_WATCHLIST)

    def test_etf_universe_all_uppercase(self) -> None:
        config = default_config(universe="etf")
        tickers = get_universe(config)
        assert all(t == t.upper() for t in tickers)
