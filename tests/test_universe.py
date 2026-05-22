from __future__ import annotations

from unittest.mock import MagicMock, patch

from data.universe import DEFAULT_ETF_WATCHLIST, _SP500_FALLBACK, get_sp500_tickers, get_universe
from tests.conftest import default_config


def _mock_finnhub_module(mock_client: MagicMock) -> MagicMock:
    mock_module = MagicMock()
    mock_module.Client.return_value = mock_client
    return mock_module


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
        mock_client.indices_const.side_effect = Exception("API error")

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
