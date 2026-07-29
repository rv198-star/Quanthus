import json
import re
import unittest
from pathlib import Path

from quanthus.gates import load_gate_report, load_p2_gate_report
from quanthus.policy import find_project_root
from scripts.sync_github_planning import CHAPTERS, work_items

WORK_ITEM_ID_PATTERN = r"(?:META|P[1-7])-[A-Z0-9-]+-[0-9]{3}|[CS][0-9]+"
WORK_ITEM_ROW = re.compile(rf"^\|\s*({WORK_ITEM_ID_PATTERN})\s*\|")
WORK_ITEM_TOKEN = re.compile(rf"(?<![A-Z0-9-])({WORK_ITEM_ID_PATTERN})(?![A-Z0-9-])")


def _table_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = WORK_ITEM_ROW.match(line)
        if match:
            ids.append(match.group(1))
    return ids


def _cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _header_fields(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if _cells(line) != ["Field", "Value"]:
            continue
        fields: dict[str, str] = {}
        for row in lines[index + 2 :]:
            cells = _cells(row)
            if cells is None or len(cells) != 2:
                break
            fields[cells[0]] = cells[1]
        return fields
    raise AssertionError(f"document has no Field/Value header: {path}")


def _indexed_dependencies(path: Path) -> set[str]:
    dependencies: set[str] = set()
    header: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _cells(line)
        if cells is None:
            header = None
            continue
        if cells and cells[0] == "ID":
            header = cells
            continue
        if header is None or "Depends on" not in header or len(cells) != len(header):
            continue
        dependency_cell = cells[header.index("Depends on")]
        dependencies.update(WORK_ITEM_TOKEN.findall(dependency_cell))
    return dependencies


class DocumentTraceabilityTests(unittest.TestCase):
    def test_lld_work_items_are_unique_and_indexed(self) -> None:
        root = find_project_root()
        index_ids = _table_ids(root / "docs" / "work-items" / "index.md")
        self.assertEqual(len(index_ids), len(set(index_ids)), "work-item index has duplicate ids")

        lld_ids: list[str] = []
        for path in sorted((root / "docs" / "design" / "lld").rglob("*.md")):
            lld_ids.extend(_table_ids(path))

        missing = sorted(set(lld_ids).difference(index_ids))
        self.assertEqual(missing, [], f"LLD work items missing from index: {missing}")

    def test_indexed_work_item_dependencies_exist(self) -> None:
        root = find_project_root()
        index_path = root / "docs" / "work-items" / "index.md"
        index_ids = set(_table_ids(index_path))

        missing = sorted(_indexed_dependencies(index_path).difference(index_ids))

        self.assertEqual(missing, [], f"work-item dependencies missing from index: {missing}")

    def test_progressive_navigation_nodes_exist(self) -> None:
        root = find_project_root()
        required = (
            "docs/AGENTS.md",
            "docs/README.md",
            "docs/architecture/README.md",
            "docs/design/execution-notification-profiles.md",
            "docs/design/hld.md",
            "docs/design/lld/README.md",
            "docs/design/lld/01-data-foundation.md",
            "docs/design/lld/02-backtest-engine.md",
            "docs/design/lld/10-monitoring-attribution.md",
            "docs/design/source-precedence.md",
            "docs/work-items/index.md",
            "config/p2-gates.v1.json",
        )

        missing = [path for path in required if not (root / path).is_file()]

        self.assertEqual(missing, [], f"required documentation navigation nodes missing: {missing}")

    def test_current_design_delivery_fields_match_release_gate(self) -> None:
        root = find_project_root()
        gate = load_gate_report()
        p2_gate = load_p2_gate_report()
        gate_raw = json.loads(gate.path.read_text(encoding="utf-8"))
        docs_map = _header_fields(root / "docs" / "README.md")
        prd = _header_fields(root / "docs" / "product" / "prd.md")
        hld = _header_fields(root / "docs" / "design" / "hld.md")
        p1 = _header_fields(root / "docs" / "design" / "lld" / "01-data-foundation.md")
        p2 = _header_fields(root / "docs" / "design" / "lld" / "02-backtest-engine.md")
        p3_labels = _header_fields(root / "docs" / "design" / "lld" / "03-label-feature-system.md")
        p3_experts = _header_fields(root / "docs" / "design" / "lld" / "04-experts-aggregator.md")
        p3_positions = _header_fields(root / "docs" / "design" / "lld" / "05-position-execution.md")

        self.assertEqual(prd["Design status"], "approved")
        self.assertEqual(hld["Design status"], "approved")
        expected_phase = (
            "P3 implementation open" if p2_gate.p3_can_start else "P2 implementation open"
        )
        self.assertEqual(docs_map["Current phase"], expected_phase)
        self.assertEqual(prd["Current phase"], expected_phase)
        self.assertEqual(hld["Current phase"], expected_phase)
        if not gate.p1_acceptance_passed:
            expected_p1 = "blocked"
        elif gate_raw.get("conditional_acceptance") is not None:
            expected_p1 = "accepted-conditional"
        else:
            expected_p1 = "accepted"
        self.assertEqual(
            p1["Delivery gate"],
            expected_p1,
        )
        self.assertEqual(p2["Delivery gate"], "accepted-conditional")
        expected_p3 = "open" if p2_gate.p3_can_start else "blocked"
        for fields in (p3_labels, p3_experts, p3_positions):
            self.assertEqual(fields["Delivery gate"], expected_p3)
        for fields in (docs_map, prd, hld, p1, p2, p3_labels, p3_experts, p3_positions):
            gate_path = fields.get("Governing gate") or fields.get("Gate source")
            if gate_path is not None:
                self.assertTrue((root / gate_path.strip("`")).is_file(), gate_path)

    def test_stock_stage_draft_cannot_claim_delivery_authority(self) -> None:
        root = find_project_root()
        fields = _header_fields(root / "docs" / "design" / "stock-stage-state-machine.md")

        self.assertEqual(fields["Design status"], "draft")
        self.assertEqual(fields["Delivery gate"], "blocked")
        self.assertIn("Non-governing", fields["Authority"])

    def test_p3_feature_registry_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "03-label-feature-system.md"
        evidence_path = root / "docs" / "acceptance" / "p3-feature-registry-slice-20260728.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("`P3-FEAT-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/feature_registry.py`", lld)
        self.assertIn("`P3-FEAT-001`", evidence)
        self.assertIn("does not publish a production registry", evidence)
        self.assertTrue((root / "src" / "quanthus" / "feature_registry.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_feature_registry.py").is_file())

    def test_p3_strategy_replay_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        evidence_path = root / "docs" / "acceptance" / "p3-strategy-replay-slice-20260728.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertEqual(fields["Delivery gate"], "accepted")
        self.assertEqual(fields["Governing work"], "`P3-REPLAY-001`")
        self.assertIn("never receives T+1 open/close prices", lld)
        self.assertIn("accepts a replay substrate, not a strategy", evidence)
        self.assertIn("multi-year acceptance remains `P3-ACCEPT-001`", evidence)
        self.assertTrue((root / "src" / "quanthus" / "backtest" / "strategy_replay.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_strategy_replay.py").is_file())

    def test_p3_strategy_replay_integration_is_bound_to_the_p2_event_stream(self) -> None:
        root = find_project_root()
        lld = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        ).read_text(encoding="utf-8")
        evidence = (
            root / "docs" / "acceptance" / "p3-strategy-replay-integration-slice-20260728.md"
        ).read_text(encoding="utf-8")
        audit_source = (root / "src" / "quanthus" / "backtest" / "audit.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("`AuditEventStore.persist_strategy_replay`", lld)
        self.assertIn("This accepts replay plumbing, not a strategy", evidence)
        self.assertIn("def persist_strategy_replay", audit_source)
        self.assertIn("event_stream_checksum", audit_source)

    def test_p1_corporate_action_corpus_is_fail_closed_and_traceable(self) -> None:
        root = find_project_root()
        data_lld = (root / "docs" / "design" / "lld" / "01-data-foundation.md").read_text(
            encoding="utf-8"
        )
        replay_lld = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        ).read_text(encoding="utf-8")
        evidence = (
            root / "docs" / "acceptance" / "p1-corporate-action-normalization-audit-20260728.md"
        ).read_text(encoding="utf-8")
        policy = json.loads(
            (root / "config" / "p1-corporate-action-normalization.v2.json").read_text(
                encoding="utf-8"
            )
        )
        source_path = root / "src" / "quanthus" / "corporate_action_corpus.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertEqual(policy["share_quantity_unit"], "per_ten_shares")
        self.assertEqual(policy["cash_unit_candidates"], ["per_share", "per_ten_shares"])
        self.assertEqual(policy["maximum_factor_ratio_error"], "0.005")
        self.assertEqual(
            policy["authority_precedence"],
            ["eastmoney_implementation_plan", "factor_ratio"],
        )
        self.assertEqual(
            policy["eastmoney_implementation_plan"]["cash_per_share_tolerance"],
            "0.00001",
        )
        self.assertIn("`P1-DATA-004`", data_lld)
        self.assertIn("Continuous corpus gate", replay_lld)
        self.assertIn("35,157", evidence)
        self.assertIn("272", evidence)
        self.assertIn("p1-historical-replay-20260729-v1", evidence)
        self.assertIn("ready for frame construction", evidence)
        self.assertIn("quanthus audit-corporate-actions", evidence)
        self.assertIn("`execution_authorized=false`", evidence)
        for forbidden_import in (
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
            "FillRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "src" / "quanthus" / "corporate_action_normalization.py").is_file())
        self.assertTrue((root / "src" / "quanthus" / "corporate_action_revision.py").is_file())
        self.assertTrue((root / "src" / "quanthus" / "historical_replay_snapshot.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_corporate_action_corpus.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_corporate_action_revision.py").is_file())

    def test_historical_replay_bundle_gate_precedes_frame_construction(self) -> None:
        root = find_project_root()
        lld = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        ).read_text(encoding="utf-8")
        evidence = (
            root / "docs" / "acceptance" / "p3-historical-replay-bundle-gate-slice-20260728.md"
        ).read_text(encoding="utf-8")
        source_path = root / "src" / "quanthus" / "backtest" / "historical_bundle.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertIn("`src/quanthus/backtest/historical_bundle.py`", lld)
        self.assertIn("real immutable replay bundle passed", evidence)
        self.assertIn("corporate_actions_normalized", evidence)
        self.assertIn("trading_calendar", evidence)
        self.assertIn("p1-historical-replay-20260729-v1", evidence)
        self.assertIn("ready_for_frame_construction=true", evidence)
        self.assertIn("`execution_authorized=false`", evidence)
        for required_dataset in (
            "corporate_action_exceptions",
            "corporate_actions_normalized",
            "daily_ohlcv_none",
            "daily_universe",
            "price_limit_execution_daily",
            "security_code_validity",
            "trading_calendar",
        ):
            self.assertIn(f'"{required_dataset}"', source)
        for forbidden_import in (
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
            "FillRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_historical_replay_bundle.py").is_file())

    def test_continuous_historical_frame_reader_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        )
        evidence_path = (
            root / "docs" / "acceptance" / "p3-historical-frame-reader-slice-20260729.md"
        )
        checklist_path = root / "docs" / "design" / "p3-strategy-semantics-checklist.md"
        source_path = root / "src" / "quanthus" / "backtest" / "historical_frames.py"
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        checklist = checklist_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")
        checklist_fields = _header_fields(checklist_path)

        self.assertIn("continuous PIT frame reader", lld)
        self.assertIn("`StrategyDecisionView`", lld)
        self.assertIn("3,400", evidence)
        self.assertIn("12,573,234", evidence)
        self.assertIn("`execution_authorized=false`", evidence)
        self.assertIn("does not complete `P3-ACCEPT-001`", evidence)
        self.assertEqual(checklist_fields["Design status"], "draft")
        self.assertEqual(checklist_fields["Delivery gate"], "blocked")
        self.assertIn("Non-governing", checklist_fields["Authority"])
        self.assertIn("twelve-state stock-stage model remains optional", checklist)
        self.assertIn("class HistoricalReplayFrameReader", source)
        self.assertIn("class PITBundleMarketView", source)
        self.assertTrue(source_path.is_file())
        self.assertTrue(
            (root / "tests" / "unit" / "test_historical_frame_reader.py").is_file()
        )

    def test_p3_baseline_suite_is_research_only_and_traceable(self) -> None:
        root = find_project_root()
        feature_lld_path = root / "docs" / "design" / "lld" / "03-label-feature-system.md"
        replay_lld_path = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        )
        v1_evidence_path = (
            root / "docs" / "acceptance" / "p3-baseline-strategy-suite-20260729.md"
        )
        v2_evidence_path = (
            root / "docs" / "acceptance" / "p3-baseline-strategy-suite-v2-20260729.md"
        )
        diagnostic_evidence_path = (
            root / "docs" / "acceptance" / "p3-backtest-sanity-diagnostics-20260729.md"
        )
        source_path = root / "src" / "quanthus" / "backtest" / "baseline_research.py"
        cli_path = root / "src" / "quanthus" / "cli.py"
        feature_fields = _header_fields(feature_lld_path)
        replay_fields = _header_fields(replay_lld_path)
        feature_lld = feature_lld_path.read_text(encoding="utf-8")
        replay_lld = replay_lld_path.read_text(encoding="utf-8")
        v1_evidence = v1_evidence_path.read_text(encoding="utf-8")
        v2_evidence = v2_evidence_path.read_text(encoding="utf-8")
        diagnostic_evidence = diagnostic_evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")
        cli = cli_path.read_text(encoding="utf-8")
        v1_catalogue = json.loads(
            (root / "config" / "p3-baseline-strategies.research.v1.json").read_text(
                encoding="utf-8"
            )
        )
        v2_catalogue = json.loads(
            (root / "config" / "p3-baseline-strategies.research.v2.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("`P3-FEAT-002` accepted", feature_fields["Implementation status"])
        self.assertIn("three baseline suite accepted", replay_fields["Implementation status"])
        self.assertIn("data_quality_scope_selection_bias=true", feature_lld)
        self.assertIn("final T+1 execution session", replay_lld)
        self.assertIn("all three strategies failed the cash comparison", v1_evidence)
        self.assertIn("rejected v1 extreme-control evidence", v2_evidence)
        self.assertIn("Operations (B/S)", v2_evidence)
        self.assertIn("Max drawdown", v2_evidence)
        self.assertIn("Payoff ratio", v2_evidence)
        self.assertIn("`execution_authorized=false`", v2_evidence)
        self.assertIn("2022-2024 arbitration partition", v2_evidence)
        self.assertIn("buy-and-hold sentinel passed", diagnostic_evidence)
        self.assertIn("Zero-cost v1 counterfactual", diagnostic_evidence)
        self.assertIn("Days above 20% absolute", diagnostic_evidence)
        self.assertIn("post-selected winner", diagnostic_evidence)
        self.assertIn("-99.3918%", diagnostic_evidence)
        self.assertEqual(v1_catalogue["status"], "research-only")
        self.assertEqual(v2_catalogue["status"], "research-only")
        self.assertEqual(v1_catalogue["validation"]["development_end"], "2021-12-31")
        self.assertEqual(v2_catalogue["validation"]["development_end"], "2021-12-31")
        self.assertEqual(v2_catalogue["shared"]["position_fraction"], "0.30")
        self.assertEqual(v2_catalogue["shared"]["sizing_cash_buffer"], "0.21")
        self.assertEqual(v2_catalogue["shared"]["maximum_realized_volatility20"], "0.50")
        self.assertEqual(v2_catalogue["shared"]["maximum_entry_day_return"], "0.04")
        self.assertIn("p3-baseline-strategies.research.v2.json", cli)
        self.assertIn("render_baseline_scoreboard", cli)
        self.assertIn("--scoreboard-output", cli)
        self.assertIn('"operation_count"', source)
        self.assertIn('"buy_operation_count"', source)
        self.assertIn('"sell_operation_count"', source)
        self.assertIn(
            "feature-panel continuity scope must include the final execution date",
            source,
        )
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_baseline_research.py").is_file())

    def test_p3_label_card_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "03-label-feature-system.md"
        evidence_path = root / "docs" / "acceptance" / "p3-label-card-contract-slice-20260728.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("`P3-LABEL-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/label_registry.py`", lld)
        self.assertIn("`P3-LABEL-001`", evidence)
        self.assertIn("protocol, not a market judgment", evidence)
        self.assertTrue((root / "src" / "quanthus" / "label_registry.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_label_registry.py").is_file())

    def test_p3_label_audit_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "03-label-feature-system.md"
        evidence_path = (
            root / "docs" / "acceptance" / "p3-label-frequency-conflict-audit-slice-20260728.md"
        )
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("`P3-LABEL-003` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/label_audit.py`", lld)
        self.assertIn("`P3-LABEL-003` passes", evidence)
        self.assertIn("defines no production label", evidence)
        self.assertIn("twelve-state vocabulary remains non-governing", evidence)
        self.assertTrue((root / "src" / "quanthus" / "label_audit.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_label_audit.py").is_file())

    def test_p3_expert_protocol_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "04-experts-aggregator.md"
        evidence_path = root / "docs" / "acceptance" / "p3-expert-protocol-slice-20260728.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("`P3-EXP-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/expert_protocols.py`", lld)
        self.assertIn("`P3-EXP-001`", evidence)
        self.assertIn("approves no expert judgment", evidence)
        self.assertTrue((root / "src" / "quanthus" / "expert_protocols.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_expert_protocols.py").is_file())

    def test_p3_governance_aggregation_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "04-experts-aggregator.md"
        evidence_path = root / "docs" / "acceptance" / "p3-governance-aggregation-slice-20260728.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("`P3-AGG-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/aggregator.py`", lld)
        self.assertIn("`P3-AGG-001`", evidence)
        self.assertIn("does not approve score calculation", evidence)
        self.assertTrue((root / "src" / "quanthus" / "aggregator.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_aggregator.py").is_file())

    def test_p3_position_lifecycle_slice_is_bounded_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "05-position-execution.md"
        evidence_path = root / "docs" / "acceptance" / "p3-position-lifecycle-slice-20260728.md"
        source_path = root / "src" / "quanthus" / "position_lifecycle.py"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")

        self.assertIn("`P3-POS-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/position_lifecycle.py`", lld)
        self.assertIn("`P3-POS-001`", evidence)
        self.assertIn("approves state topology only", evidence)
        self.assertNotIn("BacktestSignal", source)
        self.assertNotIn("OrderIntentRecord", source)
        self.assertNotIn("OrderRecord", source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_position_lifecycle.py").is_file())

    def test_p3_concentration_slice_uses_frozen_limits_without_execution(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "05-position-execution.md"
        evidence_path = root / "docs" / "acceptance" / "p3-concentration-capacity-slice-20260728.md"
        source_path = root / "src" / "quanthus" / "concentration.py"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")
        policy = json.loads((root / "config" / "p0-policy.v1.json").read_text(encoding="utf-8"))

        self.assertIn("`P3-RISK-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/concentration.py`", lld)
        self.assertIn("`P3-RISK-001` passes", evidence)
        self.assertEqual(policy["risk"]["max_units_per_level1_industry"], 2)
        self.assertEqual(policy["risk"]["max_new_units_per_day"], 2)
        self.assertEqual(policy["data"]["level1_industry_mapping"], "static_for_concentration_only")
        for forbidden_import in (
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_concentration.py").is_file())

    def test_p3_capital_allocation_competes_without_execution_authority(self) -> None:
        root = find_project_root()
        lld = (root / "docs" / "design" / "lld" / "05-position-execution.md").read_text(
            encoding="utf-8"
        )
        evidence = (
            root / "docs" / "acceptance" / "p3-capital-allocation-slice-20260728.md"
        ).read_text(encoding="utf-8")
        source_path = root / "src" / "quanthus" / "capital_allocation.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertIn("`src/quanthus/capital_allocation.py`", lld)
        self.assertIn("`P3-RISK-001` passes a second bounded slice", evidence)
        for forbidden_import in (
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
            "FillRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_capital_allocation.py").is_file())

    def test_p3_exit_arbitration_uses_frozen_sources_without_execution(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "05-position-execution.md"
        evidence_path = (
            root / "docs" / "acceptance" / "p3-four-source-exit-arbitration-slice-20260728.md"
        )
        source_path = root / "src" / "quanthus" / "exit_arbitration.py"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")
        policy = json.loads((root / "config" / "p0-policy.v1.json").read_text(encoding="utf-8"))

        self.assertIn("`P3-EXIT-001` accepted", fields["Implementation status"])
        self.assertIn("`src/quanthus/exit_arbitration.py`", lld)
        self.assertIn("`P3-EXIT-001` passes", evidence)
        self.assertEqual(
            policy["risk"]["exit_triggers"],
            [
                "structural_break",
                "constraint_stop",
                "consecutive_deterioration",
                "maximum_holding_period",
            ],
        )
        self.assertIn("missing_data_structural_break", source)
        for forbidden_import in (
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_exit_arbitration.py").is_file())

    def test_p3_shadow_divergence_contract_is_non_executing_and_traceable(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "04-experts-aggregator.md"
        evidence_path = (
            root / "docs" / "acceptance" / "p3-shadow-divergence-contract-slice-20260728.md"
        )
        source_path = root / "src" / "quanthus" / "shadow_divergence.py"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")

        self.assertIn(
            "`P3-SHADOW-001` comparison contract accepted", fields["Implementation status"]
        )
        self.assertIn("`src/quanthus/shadow_divergence.py`", lld)
        self.assertIn("comparison-contract portion of `P3-SHADOW-001` passes", evidence)
        self.assertIn("`execution_authorized=false`", evidence)
        for forbidden_import in (
            "AuditEventStore",
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_shadow_divergence.py").is_file())

    def test_p3_opportunity_funnel_reconciles_without_execution_authority(self) -> None:
        root = find_project_root()
        lld_path = root / "docs" / "design" / "lld" / "10-monitoring-attribution.md"
        evidence_path = root / "docs" / "acceptance" / "p3-opportunity-funnel-slice-20260728.md"
        source_path = root / "src" / "quanthus" / "opportunity_funnel.py"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        source = source_path.read_text(encoding="utf-8")

        self.assertIn(
            "`P3-FUNNEL-001` reconciliation contracts accepted",
            fields["Implementation status"],
        )
        self.assertIn("`src/quanthus/opportunity_funnel.py`", lld)
        self.assertIn("reconciliation-contract portion of `P3-FUNNEL-001` passes", evidence)
        self.assertIn("`execution_authorized=false`", evidence)
        for stage in (
            "candidate_to_score",
            "score_to_veto",
            "entry_to_add",
            "hold_to_exit",
        ):
            self.assertIn(stage.upper(), source)
        for forbidden_import in (
            "AuditEventStore",
            "BacktestEngine",
            "BacktestSignal",
            "OrderIntentRecord",
            "OrderRecord",
            "FillRecord",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_opportunity_funnel.py").is_file())

    def test_two_window_profile_preserves_active_clock_and_manual_boundary(self) -> None:
        root = find_project_root()
        profile_path = root / "docs" / "design" / "execution-notification-profiles.md"
        fields = _header_fields(profile_path)
        policy = json.loads((root / "config" / "p0-policy.v1.json").read_text(encoding="utf-8"))
        session = json.loads(
            (root / "config" / "trading-session.v1.json").read_text(encoding="utf-8")
        )
        synthetic = json.loads(
            (root / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json").read_text(
                encoding="utf-8"
            )
        )
        content = profile_path.read_text(encoding="utf-8")

        self.assertEqual(fields["Design status"], "approved")
        self.assertEqual(fields["Active profile"], "`close-to-next-open-v1`")
        self.assertEqual(fields["Target profile"], "`operator-assisted-two-window-v1`")
        self.assertEqual(
            fields["Target schedule"],
            "`two-window-0945-1015-1415-close-v1` clock bounds approved",
        )
        self.assertTrue(fields["Target activation"].startswith("blocked"))
        self.assertEqual(fields["Automated broker execution"], "out-of-scope")
        self.assertIn("`config/p2-gates.v1.json`", fields["Target activation"])
        self.assertEqual(policy["clock"]["signal_time"], "T_after_close")
        self.assertEqual(policy["clock"]["first_execution_time"], "T_plus_1_open")
        target = synthetic["profiles"][1]
        self.assertEqual(
            [(window["start"], window["end"]) for window in target["windows"]],
            [("09:45:00", "10:15:00"), ("14:15:00", "15:00:00")],
        )
        self.assertEqual(
            target["windows"][1]["end"],
            session["regular_session"]["continuous_and_closing_call_auction_end"],
        )
        self.assertIn("exactly two", content)
        self.assertIn("Intervals are half-open", content)
        self.assertIn("`2024-07-01`", content)
        self.assertIn("Email delivery never creates a `Fill`", content)
        self.assertIn("OperatorExecutionReport", content)
        self.assertIn("`supersedes_report_id`", content)
        self.assertIn("accepted report may update the actual ledger", content)
        for blocker_id in load_p2_gate_report().open_activation_blockers:
            self.assertIn(f"`{blocker_id}`", content)
        self.assertIn("does not close these blockers or automatically activate", content)

    def test_planning_actionable_work_items_follow_current_phase(self) -> None:
        items = work_items(find_project_root())
        p3_items = [item for item in items if item.phase == "P3"]

        self.assertTrue(p3_items)
        self.assertTrue(all(item.status == "ready" for item in p3_items))
        self.assertFalse(any(item.phase in {"P4", "P5", "P6", "P7"} for item in items))

    def test_planning_epic_status_matches_document_delivery_gate(self) -> None:
        root = find_project_root()
        status_by_gate = {
            "accepted": "ready",
            "accepted-conditional": "ready",
            "open": "ready",
            "blocked": "blocked",
        }

        for chapter in CHAPTERS:
            if chapter.phase not in {"P1", "P2", "P3"}:
                continue
            fields = _header_fields(root / chapter.path)
            delivery_gate = fields.get("Delivery gate")
            if delivery_gate not in status_by_gate:
                continue
            self.assertEqual(
                chapter.status,
                status_by_gate[delivery_gate],
                f"LLD-{chapter.number} planning status disagrees with {delivery_gate}",
            )


if __name__ == "__main__":
    unittest.main()
