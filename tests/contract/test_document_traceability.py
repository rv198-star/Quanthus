import json
import re
import unittest
from pathlib import Path

from quanthus.event_library.comparison_report import load_comparison_report_policy
from quanthus.event_library.label_cards import (
    load_outcome_label_card,
    load_structural_break_card,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.gates import load_gate_report, load_p2_gate_report, load_p3_gate_report
from quanthus.policy import find_project_root
from quanthus.research_adm import load_p4_research_adm_policy
from scripts.sync_github_planning import CHAPTERS, work_items

WORK_ITEM_ID_PATTERN = r"(?:META|P[1-7]|PK)-[A-Z0-9-]+-[0-9]{3}|[CS][0-9]+"
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
            "docs/architecture/adr-0010-platform-kernel-integration.md",
            "docs/architecture/strategy-attribution-publication.md",
            "docs/design/execution-notification-profiles.md",
            "docs/design/hld.md",
            "docs/design/lld/README.md",
            "docs/design/lld/01-data-foundation.md",
            "docs/design/lld/02-backtest-engine.md",
            "docs/design/lld/10-monitoring-attribution.md",
            "docs/design/lld/11-platform-kernel.md",
            "docs/design/source-precedence.md",
            "docs/work-items/index.md",
            "config/p2-gates.v1.json",
            "config/p3-gates.v1.json",
            "config/platform-kernel-gates.v1.json",
        )

        missing = [path for path in required if not (root / path).is_file()]

        self.assertEqual(missing, [], f"required documentation navigation nodes missing: {missing}")

    def test_current_design_delivery_fields_match_release_gate(self) -> None:
        root = find_project_root()
        gate = load_gate_report()
        p2_gate = load_p2_gate_report()
        p3_gate = load_p3_gate_report()
        gate_raw = json.loads(gate.path.read_text(encoding="utf-8"))
        docs_map = _header_fields(root / "docs" / "README.md")
        prd = _header_fields(root / "docs" / "product" / "prd.md")
        hld = _header_fields(root / "docs" / "design" / "hld.md")
        p1 = _header_fields(root / "docs" / "design" / "lld" / "01-data-foundation.md")
        p2 = _header_fields(root / "docs" / "design" / "lld" / "02-backtest-engine.md")
        p3_labels = _header_fields(root / "docs" / "design" / "lld" / "03-label-feature-system.md")
        p3_experts = _header_fields(root / "docs" / "design" / "lld" / "04-experts-aggregator.md")
        p3_positions = _header_fields(root / "docs" / "design" / "lld" / "05-position-execution.md")
        p4_events = _header_fields(root / "docs" / "design" / "lld" / "06-event-library.md")

        self.assertEqual(prd["Design status"], "approved")
        self.assertEqual(hld["Design status"], "approved")
        if p3_gate.p4_can_start:
            expected_phase = "P4 implementation open"
        elif p2_gate.p3_can_start:
            expected_phase = "P3 implementation open"
        else:
            expected_phase = "P2 implementation open"
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
        expected_p3 = (
            "accepted"
            if p3_gate.p3_acceptance_passed
            else ("open" if p2_gate.p3_can_start else "blocked")
        )
        for fields in (p3_labels, p3_experts, p3_positions):
            self.assertEqual(fields["Delivery gate"], expected_p3)
        self.assertEqual(p4_events["Delivery gate"], "open" if p3_gate.p4_can_start else "blocked")
        for fields in (
            docs_map,
            prd,
            hld,
            p1,
            p2,
            p3_labels,
            p3_experts,
            p3_positions,
            p4_events,
        ):
            gate_path = fields.get("Governing gate") or fields.get("Gate source")
            if gate_path is not None:
                self.assertTrue((root / gate_path.strip("`")).is_file(), gate_path)

    def test_stock_stage_draft_cannot_claim_delivery_authority(self) -> None:
        root = find_project_root()
        fields = _header_fields(root / "docs" / "design" / "stock-stage-state-machine.md")

        self.assertEqual(fields["Design status"], "draft")
        self.assertEqual(fields["Delivery gate"], "blocked")
        self.assertIn("Non-governing", fields["Authority"])

    def test_platform_kernel_boundary_is_machine_readable_and_bounded(self) -> None:
        root = find_project_root()
        gate_path = root / "config" / "platform-kernel-gates.v1.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        lld_path = root / "docs" / "design" / "lld" / "11-platform-kernel.md"
        fields = _header_fields(lld_path)
        lld = lld_path.read_text(encoding="utf-8")
        capability_ids = [item["id"] for item in gate["capabilities"]]
        dispositions = {item["disposition"] for item in gate["capabilities"]}
        gate_ids = [item["id"] for item in gate["gates"]]

        self.assertEqual(fields["Design status"], "approved")
        self.assertEqual(fields["Delivery gate"], "accepted")
        self.assertEqual(fields["Gate source"], "`config/platform-kernel-gates.v1.json`")
        self.assertEqual(gate["relationship_to_delivery_phases"], "orthogonal-to-P1-P7")
        self.assertEqual(gate["current_gate"], "PK6")
        self.assertEqual(gate_ids, ["PK0", "PK1", "PK2", "PK3", "PK4", "PK5", "PK6"])
        self.assertEqual(gate["gates"][0]["status"], "accepted")
        self.assertTrue(all(item["status"] == "accepted" for item in gate["gates"][1:4]))
        self.assertEqual(gate["gates"][4]["status"], "accepted")
        self.assertEqual(gate["gates"][5]["status"], "not-applicable")
        self.assertEqual(gate["gates"][6]["status"], "accepted")
        self.assertEqual(
            gate["gates"][6]["evidence"],
            "docs/acceptance/platform-kernel-pk6-cutover-recovery-20260813.md",
        )
        self.assertTrue((root / gate["gates"][6]["evidence"]).is_file())
        self.assertEqual(dispositions, {"keep", "replace", "defer", "remove"})
        self.assertEqual(len(capability_ids), len(set(capability_ids)))
        self.assertTrue(
            all(
                item["target_owner"] and item["source_scope"] and item["restore_condition"]
                for item in gate["capabilities"]
            )
        )
        self.assertEqual(
            gate["source_baseline"]["commit"],
            "e64e1c227bf3174e441a42143620179b286387e1",
        )
        self.assertIn("Platform endpoints call versioned Quanthus application ports", lld)
        self.assertIn("no direct edge to strategy registration", lld)

    def test_platform_kernel_gate_and_work_item_index_agree(self) -> None:
        root = find_project_root()
        gate = json.loads(
            (root / "config" / "platform-kernel-gates.v1.json").read_text(encoding="utf-8")
        )
        lld_ids = set(_table_ids(root / "docs" / "design" / "lld" / "11-platform-kernel.md"))
        index_ids = set(_table_ids(root / "docs" / "work-items" / "index.md"))
        configured_ids = set(gate["work_items"])

        self.assertEqual(configured_ids, lld_ids)
        self.assertTrue(configured_ids.issubset(index_ids))
        self.assertEqual(gate["work_items"]["PK-BOUNDARY-001"], "accepted")
        self.assertEqual(gate["work_items"]["PK-COMPOSE-001"], "accepted")
        accepted = {
            "PK-BOUNDARY-001",
            "PK-COMPOSE-001",
            "PK-BOOT-001",
            "PK-SCHEMA-001",
            "PK-DEPS-001",
            "PK-AUTH-001",
            "PK-OBS-001",
            "PK-JOBS-001",
            "PK-ADAPTER-001",
            "PK-AUTHORITY-001",
            "PK-WEB-001",
            "PK-WEB-002",
            "PK-WEB-003",
            "PK-WEB-004",
            "PK-CANDIDATE-001",
            "PK-WORKER-000",
            "PK-WORKER-001",
            "PK-WORKER-002",
            "PK-WORKER-003",
            "PK-WORKER-004",
            "PK-WORKER-005",
            "PK-WORKER-006",
            "PK-WORKER-007",
            "PK-WORKER-008",
            "PK-RECOVERY-001",
            "PK-CUTOVER-001",
        }
        self.assertTrue(all(gate["work_items"][item_id] == "accepted" for item_id in accepted))
        self.assertEqual(gate["work_items"]["PK-WEB-001"], "accepted")
        self.assertEqual(gate["work_items"]["PK-AUTHORING-001"], "not-applicable")
        self.assertEqual(gate["work_items"]["PK-RECOVERY-001"], "accepted")
        self.assertEqual(gate["work_items"]["PK-CUTOVER-001"], "accepted")
        self.assertTrue(
            all(
                state in {"blocked", "deferred"}
                for item_id, state in gate["work_items"].items()
                if item_id not in accepted
                | {
                    "PK-AUTHORING-001",
                }
            )
        )

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
        lld_path = root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
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
        self.assertTrue((root / "tests" / "unit" / "test_historical_frame_reader.py").is_file())

    def test_p3_baseline_suite_is_research_only_and_traceable(self) -> None:
        root = find_project_root()
        feature_lld_path = root / "docs" / "design" / "lld" / "03-label-feature-system.md"
        replay_lld_path = (
            root / "docs" / "design" / "lld" / "02-backtest-engine" / "strategy-replay.md"
        )
        v1_evidence_path = root / "docs" / "acceptance" / "p3-baseline-strategy-suite-20260729.md"
        v2_evidence_path = (
            root / "docs" / "acceptance" / "p3-baseline-strategy-suite-v2-20260729.md"
        )
        diagnostic_evidence_path = (
            root / "docs" / "acceptance" / "p3-backtest-sanity-diagnostics-20260729.md"
        )
        metric_evidence_path = (
            root / "docs" / "acceptance" / "p3-baseline-benchmark-metrics-20260729.md"
        )
        capital_evidence_path = (
            root / "docs" / "acceptance" / "p3-capital-matched-benchmark-20260729.md"
        )
        source_path = root / "src" / "quanthus" / "backtest" / "baseline_research.py"
        benchmark_path = root / "src" / "quanthus" / "backtest" / "benchmark.py"
        cli_path = root / "src" / "quanthus" / "cli.py"
        feature_fields = _header_fields(feature_lld_path)
        replay_fields = _header_fields(replay_lld_path)
        feature_lld = feature_lld_path.read_text(encoding="utf-8")
        replay_lld = replay_lld_path.read_text(encoding="utf-8")
        v1_evidence = v1_evidence_path.read_text(encoding="utf-8")
        v2_evidence = v2_evidence_path.read_text(encoding="utf-8")
        diagnostic_evidence = diagnostic_evidence_path.read_text(encoding="utf-8")
        metric_evidence = metric_evidence_path.read_text(encoding="utf-8")
        capital_evidence = capital_evidence_path.read_text(encoding="utf-8")
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
        self.assertIn("`sh000300`", metric_evidence)
        self.assertIn("Average invested capital", metric_evidence)
        self.assertIn("Return ex top 3", metric_evidence)
        self.assertIn("-89.049688%", metric_evidence)
        self.assertIn("static contribution subtraction", metric_evidence)
        self.assertIn("Capital-matched excess", capital_evidence)
        self.assertIn("+3.761204%", capital_evidence)
        self.assertIn("prior-close", capital_evidence)
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
        self.assertIn("--benchmark", cli)
        self.assertIn('"operation_count"', source)
        self.assertIn('"buy_operation_count"', source)
        self.assertIn('"sell_operation_count"', source)
        self.assertIn(
            "feature-panel continuity scope must include the final execution date",
            source,
        )
        self.assertTrue(source_path.is_file())
        self.assertTrue(benchmark_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_benchmark.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_baseline_research.py").is_file())

    def test_p3_governed_candidate_development_acceptance_is_bounded_and_traceable(
        self,
    ) -> None:
        root = find_project_root()
        card_path = root / "docs" / "design" / "strategies" / "market-breadth-trend-breakout-v1.md"
        evidence_path = (
            root / "docs" / "acceptance" / "p3-governed-strategy-development-20260729.md"
        )
        policy_path = root / "config" / "p3-governed-strategy.research.v1.json"
        source_path = root / "src" / "quanthus" / "backtest" / "portfolio_replay.py"
        card_fields = _header_fields(card_path)
        evidence_fields = _header_fields(evidence_path)
        card = card_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        source = source_path.read_text(encoding="utf-8")

        self.assertEqual(card_fields["Delivery gate"], "accepted")
        self.assertIn("frozen P3 capability fixture", card_fields["Implementation status"])
        self.assertEqual(
            card_fields["Strategy role"],
            "composition and regression evidence; not a formal strategy lineage",
        )
        self.assertIn("accepted; independent full invocation", evidence_fields["Evidence status"])
        self.assertIn("bounded preselection", card)
        self.assertIn("research_only=true", evidence)
        self.assertIn("execution_authorized=false", evidence)
        self.assertIn("-83.006028%", evidence)
        self.assertIn("one is a legal Trial", evidence)
        self.assertIn("d64f34179a275c17e5095b8a33036663cfe61299c211050d863ae1bb6e7f1f23", evidence)
        self.assertIn("53dc8030c0e0dcaaa514edb77ea9fdd59bd59c5de7e8c2eac83bd75451e5c552", evidence)
        self.assertEqual(policy["status"], "research-only")
        self.assertEqual(policy["validation"]["development_end"], "2021-12-31")
        self.assertEqual(policy["validation"]["arbitration_start"], "2022-01-01")
        self.assertIn("policy.portfolio.maximum_active_units", source)
        self.assertTrue(source_path.is_file())
        self.assertTrue((root / "tests" / "unit" / "test_portfolio_replay.py").is_file())

    def test_p3_phase_acceptance_freezes_fixture_and_opens_only_p4(self) -> None:
        root = find_project_root()
        gate = load_p3_gate_report()
        evidence_path = root / "docs" / "acceptance" / "p3-conformance-acceptance-20260729.md"
        fields = _header_fields(evidence_path)
        evidence = evidence_path.read_text(encoding="utf-8")

        self.assertEqual(fields["Evidence status"], "accepted")
        self.assertEqual(fields["Delivery gate"], "`config/p3-gates.v1.json`")
        self.assertTrue(gate.p4_can_start)
        self.assertIn("`promotion_authorized=false`", evidence)
        self.assertIn("`tuning_authorized=false`", evidence)
        self.assertIn("2022-2024 arbitration", evidence)
        self.assertIn("remain unopened", evidence)

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

    def test_strategy_attribution_design_is_blocked_and_traceable(self) -> None:
        root = find_project_root()
        parent_path = root / "docs" / "design" / "lld" / "10-monitoring-attribution.md"
        child_path = (
            root
            / "docs"
            / "design"
            / "lld"
            / "10-monitoring-attribution"
            / "strategy-attribution.md"
        )
        index_path = root / "docs" / "design" / "lld" / "README.md"
        gate_path = root / "config" / "p3-gates.v1.json"
        p0_path = root / "config" / "p0-policy.v1.json"
        acceptance_path = (
            root
            / "docs"
            / "acceptance"
            / "p3-attribution-synthetic-publication-20260731.md"
        )
        fields = _header_fields(child_path)
        child = child_path.read_text(encoding="utf-8")
        parent = parent_path.read_text(encoding="utf-8")
        index = index_path.read_text(encoding="utf-8")
        docs_map = (root / "docs" / "README.md").read_text(encoding="utf-8")
        hld = (root / "docs" / "design" / "hld.md").read_text(encoding="utf-8")
        architecture_index = (root / "docs" / "architecture" / "README.md").read_text(
            encoding="utf-8"
        )
        architecture_contract = (
            root / "docs" / "architecture" / "strategy-attribution-publication.md"
        ).read_text(encoding="utf-8")
        architecture_fields = _header_fields(
            root / "docs" / "architecture" / "strategy-attribution-publication.md"
        )
        work_items = (root / "docs" / "work-items" / "index.md").read_text(
            encoding="utf-8"
        )
        acceptance = acceptance_path.read_text(encoding="utf-8")
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        p0 = json.loads(p0_path.read_text(encoding="utf-8"))

        self.assertEqual(fields["Parent"], "`../10-monitoring-attribution.md`")
        self.assertEqual(fields["Design status"], "approved")
        self.assertEqual(fields["Delivery gate"], "blocked")
        self.assertIn("bounded synthetic closure accepted", fields["Implementation status"])
        self.assertEqual(fields["Counterfactual policy"], "`config/p0-policy.v1.json`")
        self.assertIn("`P3-ATTR-001`", fields["Governing work"])
        self.assertIn("`P3-DASH-001`", fields["Governing work"])
        self.assertEqual(gate["work_items"]["P3-ATTR-001"], "deferred")
        self.assertEqual(gate["work_items"]["P3-DASH-001"], "deferred")
        self.assertEqual(p0["counterfactual"]["exit_policy"], "same_version_as_baseline")
        self.assertIn("10-monitoring-attribution/strategy-attribution.md", parent)
        self.assertIn("10-monitoring-attribution/strategy-attribution.md", index)
        self.assertIn("counterfactual_replay", child)
        self.assertIn("AdapterCapabilityManifest", child)
        self.assertIn("does not require strategy code", child)
        self.assertIn("same exit-policy version", child)
        self.assertIn("three-layer explanation", child.lower())
        self.assertIn("cannot calculate, merge, or upgrade", child)
        self.assertIn("synthetic_fixture_not_strategy_evidence", child)
        self.assertIn("does not recompute", child)
        self.assertIn("effects", child)
        self.assertIn("`CHECKSUMS.sha256`", child)
        self.assertIn("`READY`", child)
        self.assertIn("outcome-blind immutable plan", docs_map)
        self.assertIn("### Attribution publication contract", hld)
        self.assertIn("hashes the exact raw", hld)
        self.assertIn("strategy-attribution-publication.md", architecture_index)
        self.assertEqual(architecture_fields["Delivery gate"], "blocked")
        self.assertIn("raw release/workspace responses", architecture_contract)
        self.assertIn("checkpointed local Pipeline execution", work_items)
        self.assertIn("no formal strategy", acceptance.lower())
        self.assertIn("`P3-ATTR-001` remains `deferred`", acceptance)
        self.assertTrue((root / "src" / "quanthus" / "strategy_attribution.py").is_file())
        self.assertTrue(
            (root / "src" / "quanthus" / "strategy_attribution_pipeline.py").is_file()
        )
        self.assertTrue(
            (root / "src" / "quanthus" / "strategy_attribution_publication.py").is_file()
        )
        self.assertTrue((root / "tests" / "unit" / "test_strategy_attribution.py").is_file())
        self.assertTrue(
            (root / "tests" / "unit" / "test_strategy_attribution_publication.py").is_file()
        )
        self.assertTrue((root / "web" / "app" / "attribution-workbench.tsx").is_file())
        self.assertTrue(
            (root / "web" / "app" / "published-workspace-catalog.ts").is_file()
        )

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

    def test_p4_event_contract_slice_is_traceable_and_stage_b_approved(self) -> None:
        root = find_project_root()
        lld = (root / "docs" / "design" / "lld" / "06-event-library.md").read_text(
            encoding="utf-8"
        )
        card = load_candidate_rule_card(
            root / "config" / "p4-candidate-price-volume-transition.research.v1.json"
        )
        split = load_split_policy(
            root / "config" / "p4-event-split-policy.research.v1.json"
        )

        self.assertIn("`P4-EVENT-001`", lld)
        self.assertIn("`P4-EVENT-002`", lld)
        self.assertIn("accepted through deterministic production A/B evidence", lld)
        self.assertIn("`P4-REPORT-001`", lld)
        self.assertIn("production A/B report acceptance still open", lld)
        self.assertIn("`CandidateRuleCard.require_stage_b_authority()`", lld)
        self.assertEqual(card.lifecycle, "approved")
        self.assertEqual(card.governance.approval_state, "approved")
        self.assertEqual(split.lifecycle, "approved")
        self.assertEqual(split.governance.approval_state, "approved")
        self.assertEqual(card.governance.decision_reference, split.governance.decision_reference)
        self.assertIn("#issuecomment-5196483760", card.governance.decision_reference)
        self.assertTrue((root / "src" / "quanthus" / "event_library" / "contracts.py").is_file())
        self.assertTrue((root / "src" / "quanthus" / "event_library" / "rule_card.py").is_file())
        self.assertTrue((root / "src" / "quanthus" / "event_library" / "splits.py").is_file())
        self.assertTrue(
            (root / "tests" / "unit" / "test_event_library_contracts.py").is_file()
        )
        self.assertTrue((root / "tests" / "unit" / "test_event_rule_card.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_event_splits.py").is_file())
        acceptance_path = root / "docs" / "acceptance" / "p4-event-contract-freeze-20260806.md"
        self.assertTrue(acceptance_path.is_file())
        acceptance = acceptance_path.read_text(encoding="utf-8")
        self.assertIn(card.semantic_digest, acceptance)
        self.assertIn(split.semantic_digest, acceptance)
        self.assertIn("no market-data partition was read", acceptance)

    def test_p4_forward_scan_first_slice_is_new_chain_only_and_traceable(self) -> None:
        root = find_project_root()
        lld = (root / "docs" / "design" / "lld" / "06-event-library.md").read_text(
            encoding="utf-8"
        )
        source = load_event_scan_source_policy(
            root / "config" / "p4-event-scan-source.research.v1.json",
            release_path=root / "config" / "releases" / "v2-build202607.json",
            provider_path=root / "config" / "replay-data-provider.development.v2.json",
        )
        adm = load_p4_research_adm_policy(root / "config" / "p4-research-adm.v1.json")

        self.assertIn("`dwd-base-202607-v2.1`", lld)
        self.assertIn("`p4-economic-availability-development-v1`", lld)
        self.assertIn("`src/quanthus/event_library/scan.py`", lld)
        self.assertIn("P4 Research ADM", lld)
        self.assertEqual(adm.adm_id, "p4-event-research-adm")
        self.assertFalse(adm.execution_authorized)
        self.assertEqual(source.authority, "direct_dwd_v2_1_plus_correction")
        self.assertEqual(source.base_release.package_id, "dwd-base-202607-v2.1")
        self.assertEqual(
            source.correction_release.correction_id,
            "dwd-base-202607-v2.1-correction-20260813-001",
        )
        self.assertEqual(
            source.daily_increment_mode,
            "disabled_for_initial_development_scan",
        )
        self.assertEqual(
            source.historical_visibility.status,
            "approved_development_retrospective",
        )
        self.assertFalse(source.historical_visibility.per_decision_publication_proven)
        self.assertIn("per_decision_publication_proven=false", lld)
        self.assertIn("legacy_operational_ads", source.forbidden_sources)
        self.assertIn("permanent_blind", source.historical_visibility.forbidden_uses)
        self.assertTrue((root / "src" / "quanthus" / "event_library" / "source.py").is_file())
        self.assertTrue((root / "src" / "quanthus" / "event_library" / "scan.py").is_file())
        self.assertTrue(
            (root / "src" / "quanthus" / "event_library" / "physical_scan.py").is_file()
        )
        self.assertTrue((root / "tests" / "unit" / "test_event_scan_source.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_event_scan.py").is_file())
        self.assertTrue((root / "tests" / "unit" / "test_event_physical_scan.py").is_file())
        self.assertTrue((root / "ops" / "bin" / "run-p4-event-scan").is_file())
        audit_path = root / "docs" / "acceptance" / "p4-event-dwd-pit-source-audit-20260806.md"
        self.assertTrue(audit_path.is_file())
        audit = audit_path.read_text(encoding="utf-8")
        self.assertIn("knowledge_after_t_close", audit)
        self.assertIn("No market-data file", audit)
        recovery_path = root / "docs" / "acceptance" / "p4-event-v21-source-recovery-20260819.md"
        self.assertTrue(recovery_path.is_file())
        recovery = recovery_path.read_text(encoding="utf-8")
        self.assertIn(
            "sha256:3b4992128ee40f8896e8b703806ea19b67da71e2ce60567a5f95021ee9a64489",
            recovery,
        )
        self.assertIn("74 passed", recovery)
        self.assertIn("1,158 passed and 3 skipped", recovery)
        physical_path = (
            root / "docs" / "acceptance" / "p4-event-physical-scan-implementation-20260819.md"
        )
        self.assertTrue(physical_path.is_file())
        physical = physical_path.read_text(encoding="utf-8")
        self.assertIn(
            "sha256:3b4992128ee40f8896e8b703806ea19b67da71e2ce60567a5f95021ee9a64489",
            physical,
        )
        self.assertIn("run-p4-event-scan", physical)
        self.assertIn("two independently", physical)
        remediation_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-production-zero-candidate-remediation-20260820.md"
        )
        self.assertTrue(remediation_path.is_file())
        remediation = remediation_path.read_text(encoding="utf-8")
        self.assertIn(
            "sha256:000bd6898903e87f55f3c5ca70f37afaa060029b464e379eead0884ca52a2d53",
            remediation,
        )
        self.assertIn("53,527", remediation)
        self.assertIn("46,522", remediation)
        supplement_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-research-adm-industry-supplement-20260820.md"
        )
        self.assertTrue(supplement_path.is_file())
        supplement = supplement_path.read_text(encoding="utf-8")
        self.assertIn(source.semantic_digest, supplement)
        self.assertIn(
            "sha256:ace9e9a864e143f6b37bdea7539d83114d2d0102e6acd4def4865441f316213c",
            supplement,
        )
        self.assertIn(
            "sha256:609fe99ea5eb2d29237a5c0112779ae58491270a80f390f099fb798de07382c9",
            supplement,
        )
        self.assertIn("259 / 259", supplement.replace("259/259", "259 / 259"))
        correction_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-research-adm-industry-source-digest-correction-20260820.md"
        )
        self.assertTrue(correction_path.is_file())
        correction = correction_path.read_text(encoding="utf-8")
        self.assertIn(adm.semantic_digest, correction)
        self.assertIn(adm.historical_industry_reference.source_content_digest, correction)
        entry_binding_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-entry-snapshot-binding-remediation-20260821.md"
        )
        self.assertTrue(entry_binding_path.is_file())
        entry_binding = entry_binding_path.read_text(encoding="utf-8")
        self.assertIn("job_26d5e6f679aa4852948af716c0d245a1", entry_binding)
        self.assertIn("candidate_snapshot_id", entry_binding)
        self.assertIn("entry_source_snapshot_id", entry_binding)
        adm_checkpoint_path = (
            root / "docs" / "acceptance" / "p4-research-adm-consolidation-20260820.md"
        )
        self.assertTrue(adm_checkpoint_path.is_file())
        adm_checkpoint = adm_checkpoint_path.read_text(encoding="utf-8")
        self.assertIn(
            "sha256:31020e184836e1981559aebd7d1c7620e29065d1038df696f932ec3f1d3555e3",
            adm_checkpoint,
        )
        self.assertIn("historical_only=259", adm_checkpoint)
        adm_adr = (
            root
            / "docs"
            / "architecture"
            / "adr-0013-p4-research-adm-and-legacy-ads-retirement.md"
        )
        self.assertTrue(adm_adr.is_file())

        outcome = load_outcome_label_card(
            root / "config" / "p4-event-outcome-label.research.v1.json"
        )
        structural = load_structural_break_card(
            root / "config" / "p4-event-structural-break.research.v1.json"
        )
        report_policy = load_comparison_report_policy(
            root / "config" / "p4-event-comparison-report.research.v1.json"
        )
        self.assertEqual(outcome.lifecycle, "approved")
        self.assertEqual(structural.lifecycle, "approved")
        self.assertEqual(outcome.governance.approval_state, "approved")
        self.assertEqual(structural.governance.approval_state, "approved")
        self.assertEqual(outcome.structural_break.semantic_digest, structural.semantic_digest)
        self.assertEqual(report_policy.lifecycle, "approved")
        self.assertEqual(report_policy.governance.approval_state, "approved")
        self.assertIn("#issuecomment-5370987743", report_policy.governance.decision_reference)
        self.assertIn(outcome.semantic_digest, lld)
        self.assertIn(structural.semantic_digest, lld)
        self.assertIn(report_policy.semantic_digest, lld)
        label_proposal_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-label-governance-proposal-20260821.md"
        )
        self.assertTrue(label_proposal_path.is_file())
        label_proposal = label_proposal_path.read_text(encoding="utf-8")
        self.assertIn(outcome.semantic_digest, label_proposal)
        self.assertIn(structural.semantic_digest, label_proposal)
        self.assertIn("No real future-outcome corpus may be read", label_proposal)
        label_approval_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-label-human-approval-20260821.md"
        )
        self.assertTrue(label_approval_path.is_file())
        label_approval = label_approval_path.read_text(encoding="utf-8")
        self.assertIn(outcome.semantic_digest, label_approval)
        self.assertIn(structural.semantic_digest, label_approval)
        self.assertIn("#issuecomment-5366610770", label_approval)
        label_implementation_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-label-corpus-implementation-20260821.md"
        )
        self.assertTrue(label_implementation_path.is_file())
        label_implementation = label_implementation_path.read_text(encoding="utf-8")
        self.assertIn(outcome.semantic_digest, label_implementation)
        self.assertIn(structural.semantic_digest, label_implementation)
        self.assertIn("quanthus.p4-event-run.v2", label_implementation)
        self.assertIn("run-p4-labeled-event-scan", label_implementation)
        self.assertTrue(
            (root / "src" / "quanthus" / "event_library" / "label_corpus.py").is_file()
        )
        label_acceptance_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-label-corpus-production-ab-20260821.md"
        )
        self.assertTrue(label_acceptance_path.is_file())
        label_acceptance = label_acceptance_path.read_text(encoding="utf-8")
        self.assertIn(outcome.semantic_digest, label_acceptance)
        self.assertIn(structural.semantic_digest, label_acceptance)
        self.assertIn("36,820", label_acceptance)
        self.assertIn("34,641", label_acceptance)
        self.assertIn(
            "sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580",
            label_acceptance,
        )
        self.assertIn(
            "sha256:31ed3e95771390c4df3cb4cff21ba2b3415458456cf6b6f02621c7c81bdc5b12",
            label_acceptance,
        )
        report_implementation_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-comparison-report-implementation-20260821.md"
        )
        self.assertTrue(report_implementation_path.is_file())
        report_implementation = report_implementation_path.read_text(encoding="utf-8")
        self.assertIn(report_policy.semantic_digest, report_implementation)
        self.assertIn("run-p4-reported-event-scan", report_implementation)
        self.assertIn("quanthus.p4-event-run.v3", report_implementation)
        self.assertIn("34,641", report_implementation)
        report_identity_remediation_path = (
            root
            / "docs"
            / "acceptance"
            / "p4-event-comparison-report-identity-remediation-20260822.md"
        )
        self.assertTrue(report_identity_remediation_path.is_file())
        report_identity_remediation = report_identity_remediation_path.read_text(encoding="utf-8")
        self.assertIn(report_policy.semantic_digest, report_identity_remediation)
        self.assertIn("panel_security_id", report_identity_remediation)
        self.assertIn("job_15a9f9c2d3934b96a1349b0f3ae2a288", report_identity_remediation)
        self.assertTrue(
            (root / "src" / "quanthus" / "event_library" / "comparison_report.py").is_file()
        )
        self.assertTrue(
            (root / "tests" / "unit" / "test_event_comparison_report.py").is_file()
        )
        self.assertIn("label-corpus", lld)
        self.assertIn("comparison-report", lld)
        self.assertIn("quanthus.p4-event-run.v3", lld)

    def test_planning_actionable_work_items_follow_current_phase(self) -> None:
        items = work_items(find_project_root())
        p3_items = [item for item in items if item.phase == "P3"]
        p4_items = [item for item in items if item.phase == "P4"]
        pk_items = [item for item in items if item.phase == "PK"]

        self.assertTrue(p3_items)
        self.assertEqual(
            {item.stable_id for item in p3_items if item.status == "blocked"},
            {
                "P3-LABEL-004",
                "P3-ATTR-001",
                "P3-DASH-001",
                "P3-EXPERT-004",
                "P3-EXPERT-005",
            },
        )
        self.assertEqual(
            {item.stable_id for item in p3_items if item.status == "ready"},
            {"P3-EXPERT-000"},
        )
        self.assertTrue(
            all(
                item.status == "closed"
                for item in p3_items
                if item.status not in {"blocked", "ready"}
            )
        )
        self.assertTrue(p4_items)
        self.assertTrue(all(item.status == "ready" for item in p4_items))
        self.assertTrue(
            all(item.status == "blocked" for item in items if item.phase in {"P5", "P6", "P7"})
        )
        self.assertTrue(pk_items)
        self.assertEqual({item.stable_id for item in pk_items if item.status == "ready"}, set())
        self.assertEqual(
            {item.stable_id for item in pk_items if item.status == "closed"},
            {
                "PK-BOUNDARY-001",
                "PK-COMPOSE-001",
                "PK-BOOT-001",
                "PK-SCHEMA-001",
                "PK-DEPS-001",
                "PK-AUTH-001",
                "PK-OBS-001",
                "PK-JOBS-001",
                "PK-ADAPTER-001",
                "PK-AUTHORITY-001",
                "PK-WEB-001",
                "PK-WEB-002",
                "PK-WEB-003",
                "PK-WEB-004",
                "PK-CANDIDATE-001",
                "PK-WORKER-000",
                "PK-WORKER-001",
                "PK-WORKER-002",
                "PK-WORKER-003",
                "PK-WORKER-004",
                "PK-WORKER-005",
                "PK-WORKER-006",
                "PK-WORKER-007",
                "PK-WORKER-008",
                "PK-RECOVERY-001",
                "PK-CUTOVER-001",
            },
        )
        self.assertTrue(
            all(
                item.status == "blocked"
                for item in pk_items
                if item.status not in {"ready", "closed"}
            )
        )

    def test_planning_epic_status_matches_document_delivery_gate(self) -> None:
        root = find_project_root()
        status_by_gate = {
            "accepted": "closed",
            "accepted-conditional": "ready",
            "open": "ready",
            "blocked": "blocked",
        }

        for chapter in CHAPTERS:
            if chapter.phase not in {"P1", "P2", "P3", "P4", "PK"}:
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
