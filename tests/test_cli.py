from __future__ import annotations

import pytest

from main import build_parser


class TestBuildParser:
    def test_analyze_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze"])
        assert args.command == "analyze"
        assert args.universe is None
        assert args.ticker is None
        assert args.top is None
        assert args.verbose is False
        assert args.risk is None
        assert args.no_portfolio is False
        assert args.budget is None

    def test_analyze_with_tickers(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--ticker", "AAPL", "NVDA"])
        assert args.ticker == ["AAPL", "NVDA"]

    def test_analyze_with_all_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "analyze", "--universe", "sp500", "--top", "20",
            "-v", "--risk", "aggressive", "--no-portfolio", "--budget", "500",
        ])
        assert args.universe == "sp500"
        assert args.top == 20
        assert args.verbose is True
        assert args.risk == "aggressive"
        assert args.no_portfolio is True
        assert args.budget == 500.0

    def test_sync_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync"])
        assert args.command == "sync"
        assert args.schwab_only is False
        assert args.plaid_only is False

    def test_sync_schwab_only(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync", "--schwab-only"])
        assert args.schwab_only is True

    def test_link_schwab(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["link", "--institution", "schwab"])
        assert args.command == "link"
        assert args.institution == "schwab"

    def test_import_file(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["import", "positions.csv"])
        assert args.command == "import"
        assert args.file == "positions.csv"

    def test_top_zero(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--top", "0"])
        assert args.top == 0

    def test_analyze_with_account_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--account", "roth"])
        assert args.account == "roth"

    def test_analyze_account_brokerage(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--account", "brokerage"])
        assert args.account == "brokerage"

    def test_analyze_account_hsa(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--account", "hsa"])
        assert args.account == "hsa"

    def test_analyze_account_invalid_choice(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["analyze", "--account", "401k"])

    def test_analyze_account_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze"])
        assert args.account is None

    def test_analyze_universe_etf(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["analyze", "--universe", "etf"])
        assert args.universe == "etf"
