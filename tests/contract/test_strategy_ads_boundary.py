import ast
import unittest

from quanthus.cli import _parser
from quanthus.policy import find_project_root


class StrategyADSBoundaryContractTests(unittest.TestCase):
    def test_strategy_replay_commands_require_ads_capabilities(self) -> None:
        parser = _parser()
        subparsers = next(action for action in parser._actions if getattr(action, "choices", None))
        for command in (
            "audit-historical-replay-frames",
            "run-baseline-strategy-suite",
            "run-governed-portfolio-replay",
        ):
            command_parser = subparsers.choices[command]
            destinations = {action.dest for action in command_parser._actions}
            self.assertIn("ads_bundle", destinations)
            self.assertIn("ads_role", destinations)
            self.assertNotIn("snapshot_dir", destinations)

    def test_strategy_consumers_do_not_load_warehouse_or_legacy_snapshots(self) -> None:
        root = find_project_root()
        consumers = (
            root / "src" / "quanthus" / "backtest" / "baseline_research.py",
            root / "src" / "quanthus" / "backtest" / "portfolio_replay.py",
            root / "src" / "quanthus" / "backtest" / "strategy_replay.py",
            root / "src" / "quanthus" / "governed_strategy.py",
        )
        forbidden_calls = {
            "load_historical_frame_reader",
            "load_historical_replay_bundle",
            "verify_ods_release",
            "select_market_daily_ods_release",
        }
        forbidden_import_prefix = "quanthus.pipelines"
        forbidden_runtime_paths = {
            "daily_market_CURRENT.json",
            "daily_market_summary_CURRENT.json",
            "release-set_CURRENT.json",
            "snapshot.json",
        }

        for path in consumers:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_modules = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            }
            imported_modules.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            self.assertFalse(
                any(name.startswith(forbidden_import_prefix) for name in imported_modules),
                path,
            )
            called_names = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse(called_names & forbidden_calls, path)
            string_literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            self.assertFalse(string_literals & forbidden_runtime_paths, path)


if __name__ == "__main__":
    unittest.main()
