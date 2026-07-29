from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from quanthus import __version__
from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    BaselineResearchError,
    load_baseline_strategy_catalogue,
    render_baseline_scoreboard,
    run_baseline_strategy_suite,
)
from quanthus.backtest.benchmark import BenchmarkCurveError, load_benchmark_curve
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.historical_bundle import HistoricalReplayBundleError
from quanthus.backtest.historical_frames import (
    HistoricalFrameReaderError,
    load_historical_frame_reader,
)
from quanthus.backtest.policy import load_execution_policy
from quanthus.code_validity import (
    CodeValidityError,
    build_code_validity_table,
    load_code_validity_mapping,
)
from quanthus.corporate_action_corpus import (
    CorporateActionCorpusError,
    CorporateActionCorpusPaths,
    audit_corporate_action_corpus,
)
from quanthus.corporate_action_normalization import (
    CorporateActionNormalizationError,
    load_corporate_action_normalization_policy,
)
from quanthus.corporate_action_revision import (
    CorporateActionRevisionError,
    build_corporate_action_revision,
    check_corporate_action_revision,
    promote_corporate_action_revision,
)
from quanthus.curation import CuratedDailyResult, CurationError, build_curated_daily
from quanthus.data_foundation import SnapshotCatalog
from quanthus.data_quality import AuditError, DataAuditReport, run_p1_data_audit
from quanthus.delisted_coverage import (
    DelistedCoverageError,
    DelistedCoverageReport,
    run_delisted_coverage,
)
from quanthus.feature_registry import FeatureRegistryError, load_feature_registry
from quanthus.gates import GateReport, P2GateReport, load_gate_report, load_p2_gate_report
from quanthus.historical_replay_snapshot import (
    HistoricalReplaySnapshotError,
    publish_historical_replay_snapshot,
)
from quanthus.identity_coverage import IdentityCoverageError, build_identity_coverage
from quanthus.identity_normalization import (
    IdentityNormalizationError,
    build_identity_normalized_revision,
)
from quanthus.none_backfill import (
    NoneBackfillError,
    build_none_backfill_revision,
    write_none_current,
)
from quanthus.none_tail_repair import build_none_tail_repair_revision
from quanthus.p1_acceptance import (
    AcceptanceEvidenceError,
    P1AcceptanceReport,
    load_p1_acceptance_evidence,
)
from quanthus.p1_evidence_build import (
    EvidenceBuildError,
    build_p1_acceptance_evidence,
    write_p1_acceptance_evidence,
)
from quanthus.pipelines import (
    DailyWarehouseError,
    LaneRunError,
    OdsHandoffError,
    OdsReleaseError,
    PipelineConfigError,
    PipelineRunError,
    ScheduleContractError,
    build_daily_dwd_revision,
    build_daily_dws_revision,
    check_daily_dwd_revision,
    check_daily_dws_revision,
    load_ods_source_config,
    load_pipeline_config,
    load_schedule_contract,
    market_session_is_open,
    promote_daily_dwd_revision,
    promote_daily_dws_revision,
    publish_ods_release,
    pull_selected_ods_release,
    resolve_paths,
    run_lane,
    run_pipeline,
    run_shadow_matrix,
    verify_ods_release,
    wait_for_ods_ready,
)
from quanthus.policy import ConfigurationError, P0Policy, find_project_root, load_policy
from quanthus.price_limits import (
    PriceLimitBuildError,
    build_price_limit_revision,
    write_price_limit_current,
)
from quanthus.snapshot_publish import (
    MaterializedSnapshot,
    SnapshotBuildError,
    load_snapshot_plan,
    publish_snapshot,
)
from quanthus.st_status import (
    StStatusBuildError,
    build_st_status_revision,
    write_current_pointer,
)
from quanthus.tradability import (
    CoverageError,
    TradabilityCoverageResult,
    build_tradability_coverage,
)
from quanthus.universe import load_universe_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quanthus")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="validate the bootstrap environment")
    doctor.add_argument("--policy", type=Path)
    doctor.add_argument("--gates", type=Path)
    doctor.add_argument("--p2-gates", type=Path)

    policy = subparsers.add_parser("policy-check", help="validate the frozen P0 policy")
    policy.add_argument("--policy", type=Path)

    gates = subparsers.add_parser("gate-status", help="show P1 and P2 release gates")
    gates.add_argument("--gates", type=Path)
    gates.add_argument("--json", action="store_true")

    data_audit = subparsers.add_parser("data-audit", help="audit a stable local P1 snapshot")
    data_audit.add_argument("--root", type=Path, required=True)
    data_audit.add_argument("--bars-file", default="daily_ohlcv.parquet")
    data_audit.add_argument("--output", type=Path)
    data_audit.add_argument("--require-c1-c2", action="store_true")

    corporate_action_audit = subparsers.add_parser(
        "audit-corporate-actions",
        help="audit mixed-unit corporate actions against one immutable P1 snapshot",
    )
    corporate_action_audit.add_argument("--snapshot-dir", type=Path, required=True)
    corporate_action_audit.add_argument("--corporate-actions", type=Path, required=True)
    corporate_action_audit.add_argument("--eastmoney-raw-dir", type=Path)
    corporate_action_audit.add_argument("--policy", type=Path)
    corporate_action_audit.add_argument("--scope-start", type=date.fromisoformat, required=True)
    corporate_action_audit.add_argument("--scope-end", type=date.fromisoformat, required=True)
    corporate_action_audit.add_argument("--report-output", type=Path)
    corporate_action_audit.add_argument("--exception-ledger-output", type=Path)

    corporate_action_build = subparsers.add_parser(
        "build-corporate-action-revision",
        help="build an immutable normalized-action and exception-ledger revision",
    )
    corporate_action_build.add_argument("--snapshot-dir", type=Path, required=True)
    corporate_action_build.add_argument("--corporate-actions", type=Path, required=True)
    corporate_action_build.add_argument("--eastmoney-raw-dir", type=Path, required=True)
    corporate_action_build.add_argument("--policy", type=Path)
    corporate_action_build.add_argument("--scope-start", type=date.fromisoformat, required=True)
    corporate_action_build.add_argument("--scope-end", type=date.fromisoformat, required=True)
    corporate_action_build.add_argument(
        "--knowledge-cutoff", type=datetime.fromisoformat, required=True
    )
    corporate_action_build.add_argument("--output", type=Path, required=True)
    corporate_action_build.add_argument("--revision", required=True)
    corporate_action_build.add_argument("--previous-revision", type=Path)

    corporate_action_check = subparsers.add_parser(
        "check-corporate-action-revision",
        help="verify one immutable corporate-action replay revision",
    )
    corporate_action_check.add_argument("--revision-dir", type=Path, required=True)

    corporate_action_promote = subparsers.add_parser(
        "promote-corporate-action-revision",
        help="advance the accepted corporate-action replay revision pointer",
    )
    corporate_action_promote.add_argument("--revision-dir", type=Path, required=True)
    corporate_action_promote.add_argument("--pointer", type=Path, required=True)

    code_validity = subparsers.add_parser(
        "build-code-validity",
        help="materialize the versioned security code-validity table from the raw master",
    )
    code_validity.add_argument("--root", type=Path, required=True)
    code_validity.add_argument("--mapping", type=Path)
    code_validity.add_argument("--output", type=Path, required=True)

    normalize_identity = subparsers.add_parser(
        "normalize-security-identity",
        help="build an immutable OCI child revision cropped to code-validity intervals",
    )
    normalize_identity.add_argument("--root", type=Path, required=True)
    normalize_identity.add_argument("--code-validity", type=Path, required=True)
    normalize_identity.add_argument("--s2-candidate", type=Path, required=True)
    normalize_identity.add_argument("--output", type=Path, required=True)
    normalize_identity.add_argument("--revision", required=True)

    none_backfill = subparsers.add_parser(
        "merge-none-backfill",
        help="build an immutable none child by filling missing keys from raw evidence",
    )
    none_backfill.add_argument("--parent", type=Path, required=True)
    none_backfill.add_argument("--raw-revision", type=Path, required=True)
    none_backfill.add_argument("--detection-file", type=Path, required=True)
    none_backfill.add_argument("--output", type=Path, required=True)
    none_backfill.add_argument("--revision", required=True)
    none_backfill.add_argument("--promote-pointer", type=Path)

    none_tail = subparsers.add_parser(
        "merge-none-tail-repair",
        help="build an immutable none child from the checksummed #40 tail evidence",
    )
    none_tail.add_argument("--parent", type=Path, required=True)
    none_tail.add_argument("--raw-revision", type=Path, required=True)
    none_tail.add_argument("--output", type=Path, required=True)
    none_tail.add_argument("--revision", required=True)
    none_tail.add_argument("--promote-pointer", type=Path)

    identity_coverage = subparsers.add_parser(
        "build-identity-coverage",
        help="rebuild C2/S1 evidence from a normalized identity revision",
    )
    identity_coverage.add_argument("--revision-root", type=Path, required=True)
    identity_coverage.add_argument("--reference-root", type=Path, required=True)
    identity_coverage.add_argument("--knowledge-cutoff", type=date.fromisoformat, required=True)
    identity_coverage.add_argument("--coverage-output", type=Path, required=True)
    identity_coverage.add_argument("--coverage-metadata-output", type=Path, required=True)
    identity_coverage.add_argument("--s1-report-output", type=Path, required=True)

    curate_daily = subparsers.add_parser(
        "curate-daily",
        help="build an immutable curated view from a stable unadjusted daily input",
    )
    curate_daily.add_argument("--root", type=Path, required=True)
    curate_daily.add_argument("--bars-file", default="daily_ohlcv_none.parquet")
    curate_daily.add_argument("--universe-policy", type=Path)
    curate_daily.add_argument("--output", type=Path, required=True)
    curate_daily.add_argument("--metadata-output", type=Path)

    coverage = subparsers.add_parser(
        "build-tradability-coverage",
        help="build immutable P1 lifecycle coverage from stable unadjusted daily inputs",
    )
    coverage.add_argument("--root", type=Path, required=True)
    coverage.add_argument("--bars-file", default="daily_ohlcv_none.parquet")
    coverage.add_argument("--universe-policy", type=Path)
    coverage.add_argument("--knowledge-cutoff", type=date.fromisoformat, required=True)
    coverage.add_argument("--output", type=Path, required=True)
    coverage.add_argument("--metadata-output", type=Path)

    delisted = subparsers.add_parser(
        "delisted-coverage",
        help="reconcile P1 delisted-security daily history against lifecycle and suspensions",
    )
    delisted.add_argument("--root", type=Path, required=True)
    delisted.add_argument("--bars-file", default="daily_ohlcv_none.parquet")
    delisted.add_argument("--universe-policy", type=Path)
    delisted.add_argument("--knowledge-cutoff", type=date.fromisoformat, required=True)
    delisted.add_argument("--output", type=Path, required=True)

    evidence = subparsers.add_parser(
        "p1-acceptance-check",
        help="validate D1/D2/D3 evidence structure without changing release gates",
    )
    evidence.add_argument("--evidence", type=Path, required=True)
    evidence.add_argument("--json", action="store_true")

    evidence_build = subparsers.add_parser(
        "build-p1-evidence",
        help="revalidate D1/D2/D3 and assemble a bundle from one published P1 snapshot",
    )
    evidence_build.add_argument("--snapshot-dir", type=Path, required=True)
    evidence_build.add_argument("--coverage", type=Path, required=True)
    evidence_build.add_argument("--coverage-metadata", type=Path, required=True)
    evidence_build.add_argument("--data-audit", type=Path, required=True)
    evidence_build.add_argument("--s1-report", type=Path, required=True)
    evidence_build.add_argument("--prior-evidence", type=Path, required=True)
    evidence_build.add_argument("--d1-pack", type=Path, required=True)
    evidence_build.add_argument("--d3-seed", type=Path, required=True)
    evidence_build.add_argument("--c1-conditional-policy", type=Path, required=True)
    evidence_build.add_argument("--output", type=Path, required=True)

    snapshot = subparsers.add_parser(
        "publish-snapshot",
        help="hard-link a reviewed P1 publication plan into an immutable local snapshot",
    )
    snapshot.add_argument("--plan", type=Path, required=True)
    snapshot.add_argument("--catalog-root", type=Path, required=True)
    snapshot.add_argument("--metadata-output", type=Path)

    replay_snapshot = subparsers.add_parser(
        "publish-historical-replay-snapshot",
        help="extend one P1 snapshot with replay inputs and verify the complete bundle",
    )
    replay_snapshot.add_argument("--base-snapshot", type=Path, required=True)
    replay_snapshot.add_argument("--corporate-action-revision", type=Path, required=True)
    replay_snapshot.add_argument("--trading-calendar", type=Path, required=True)
    replay_snapshot.add_argument("--calendar-source-revision", required=True)
    replay_snapshot.add_argument(
        "--calendar-knowledge-cutoff", type=datetime.fromisoformat, required=True
    )
    replay_snapshot.add_argument("--snapshot-id", required=True)
    replay_snapshot.add_argument("--source-revision", required=True)
    replay_snapshot.add_argument("--knowledge-cutoff", type=datetime.fromisoformat, required=True)
    replay_snapshot.add_argument("--catalog-root", type=Path, required=True)
    replay_snapshot.add_argument("--policy", type=Path)
    replay_snapshot.add_argument("--metadata-output", type=Path)

    replay_frames = subparsers.add_parser(
        "audit-historical-replay-frames",
        help="audit continuous PIT decision-to-execution frames from a verified bundle",
    )
    replay_frames.add_argument("--snapshot-dir", type=Path, required=True)
    replay_frames.add_argument("--scope-start", type=date.fromisoformat, required=True)
    replay_frames.add_argument("--scope-end", type=date.fromisoformat, required=True)
    replay_frames.add_argument("--policy", type=Path)
    replay_frames.add_argument("--output", type=Path)

    baseline_suite = subparsers.add_parser(
        "run-baseline-strategy-suite",
        help="build PIT daily indicators and replay the three research-only P3 baselines",
    )
    baseline_suite.add_argument("--snapshot-dir", type=Path, required=True)
    baseline_suite.add_argument("--scope-start", type=date.fromisoformat, required=True)
    baseline_suite.add_argument("--scope-end", type=date.fromisoformat, required=True)
    baseline_suite.add_argument("--feature-panel", type=Path, required=True)
    baseline_suite.add_argument("--benchmark", type=Path, required=True)
    baseline_suite.add_argument("--output", type=Path, required=True)
    baseline_suite.add_argument("--scoreboard-output", type=Path)
    baseline_suite.add_argument("--audit-output-dir", type=Path)
    baseline_suite.add_argument("--catalogue", type=Path)
    baseline_suite.add_argument("--feature-registry", type=Path)
    baseline_suite.add_argument("--execution-policy", type=Path)
    baseline_suite.add_argument("--corporate-action-policy", type=Path)

    st_status = subparsers.add_parser(
        "build-st-status",
        help="publish an immutable OCI S2 status revision from a normalized candidate",
    )
    st_status.add_argument("--candidate", type=Path, required=True)
    st_status.add_argument("--output", type=Path, required=True)
    st_status.add_argument("--revision", required=True)
    st_status.add_argument("--policy", type=Path)
    st_status.add_argument("--parent", type=Path)
    st_status.add_argument("--promote-pointer", type=Path)

    price_limits = subparsers.add_parser(
        "build-price-limits",
        help="publish an immutable OCI S3 resolver joined to formal S2 execution state",
    )
    price_limits.add_argument("--rules", type=Path, required=True)
    price_limits.add_argument("--identity-revision", type=Path, required=True)
    price_limits.add_argument("--s2-revision", type=Path, required=True)
    price_limits.add_argument("--output", type=Path, required=True)
    price_limits.add_argument("--revision", required=True)
    price_limits.add_argument("--policy", type=Path)
    price_limits.add_argument("--promote-pointer", type=Path)

    ods_release = subparsers.add_parser(
        "verify-ods-release",
        help="verify one immutable Hermes ODS release and READY marker",
    )
    ods_release.add_argument("--release-dir", type=Path, required=True)

    ods_publish = subparsers.add_parser(
        "publish-ods-release",
        help="publish a checksummed Hermes ODS release from a versioned artifact spec",
    )
    ods_publish.add_argument("--spec", type=Path, required=True)
    ods_publish.add_argument("--output", type=Path, required=True)

    pipeline_check = subparsers.add_parser(
        "pipeline-check",
        help="validate a versioned OCI pipeline configuration without running it",
    )
    pipeline_check.add_argument("--config", type=Path, required=True)

    pipeline_run = subparsers.add_parser(
        "pipeline-run",
        help="run one market-date OCI pipeline with locking and a durable ledger",
    )
    pipeline_run.add_argument("--config", type=Path, required=True)
    pipeline_run.add_argument("--market-date", type=date.fromisoformat, required=True)

    ods_wait = subparsers.add_parser(
        "ods-wait-ready",
        help="select the first verified-ready ODS source and write a durable selection receipt",
    )
    ods_wait.add_argument("--sources", type=Path, required=True)
    ods_wait.add_argument("--schedule", choices=["daily", "weekly"], required=True)
    ods_wait.add_argument("--market-date", type=date.fromisoformat, required=True)
    ods_wait.add_argument("--selection", type=Path, required=True)
    ods_wait.add_argument("--timeout-seconds", type=int, required=True)
    ods_wait.add_argument("--poll-seconds", type=int, required=True)

    ods_pull = subparsers.add_parser(
        "ods-pull-selected",
        help="pull the exact ODS source and attempt recorded in a selection receipt",
    )
    ods_pull.add_argument("--sources", type=Path, required=True)
    ods_pull.add_argument("--schedule", choices=["daily", "weekly"], required=True)
    ods_pull.add_argument("--market-date", type=date.fromisoformat, required=True)
    ods_pull.add_argument("--selection", type=Path, required=True)
    ods_pull.add_argument("--destination-root", type=Path, required=True)

    schedule_check = subparsers.add_parser(
        "schedule-check",
        help="validate the versioned Hermes ODS scheduler contract",
    )
    schedule_check.add_argument("--config", type=Path, required=True)

    lane_run = subparsers.add_parser(
        "lane-run",
        help="run one Hermes ODS schedule lane attempt (shadow-safe by default)",
    )
    lane_run.add_argument("--config", type=Path, required=True)
    lane_run.add_argument(
        "--lane",
        required=True,
        choices=[
            "bootstrap",
            "market-daily-core",
            "calendar-daily-events",
            "intraday-archive",
            "weekly-corrections",
        ],
    )
    lane_run.add_argument("--market-date", type=date.fromisoformat, required=True)
    lane_run.add_argument("--stream", choices=["1m", "5m"])
    lane_run.add_argument(
        "--force-preflight",
        choices=["both_open", "both_closed", "missing_or_disagree"],
    )
    lane_run.add_argument("--issue-ref")
    lane_run.add_argument(
        "--recovery-deadline-at",
        type=datetime.fromisoformat,
        help="timezone-aware absolute deadline for one manual market recovery",
    )
    lane_run.add_argument("--home", type=Path)
    lane_run.add_argument("--shadow-root", type=Path)
    lane_run.add_argument("--calendar", type=Path)
    lane_run.add_argument("--symbol", action="append", default=[])

    shadow_matrix = subparsers.add_parser(
        "lane-shadow-matrix",
        help="run the minimum Hermes ODS Phase B shadow matrix into an isolated root",
    )
    shadow_matrix.add_argument("--config", type=Path, required=True)
    shadow_matrix.add_argument("--open-date", type=date.fromisoformat, required=True)
    shadow_matrix.add_argument("--closed-date", type=date.fromisoformat, required=True)
    shadow_matrix.add_argument("--weekly-date", type=date.fromisoformat, required=True)
    shadow_matrix.add_argument("--event-date", type=date.fromisoformat, required=True)
    shadow_matrix.add_argument("--home", type=Path)
    shadow_matrix.add_argument("--shadow-root", type=Path)
    shadow_matrix.add_argument("--calendar", type=Path)

    daily_dwd = subparsers.add_parser(
        "build-daily-dwd",
        help="normalize one verified Hermes daily delta into an immutable OCI DWD partition",
    )
    daily_dwd.add_argument("--release-dir", type=Path, required=True)
    daily_dwd.add_argument("--output", type=Path, required=True)
    daily_dwd.add_argument("--revision", required=True)
    daily_dwd.add_argument("--market-date", type=date.fromisoformat, required=True)
    daily_dwd.add_argument("--universe-policy", type=Path, required=True)
    daily_dwd.add_argument("--code-validity-mapping", type=Path, required=True)
    daily_dwd.add_argument("--code-revision", required=True)
    daily_dwd.add_argument("--parent-pointer", type=Path)
    daily_dwd.add_argument("--base-pointer", type=Path)

    daily_dwd_check = subparsers.add_parser(
        "check-daily-dwd",
        help="recheck a complete immutable OCI daily DWD partition",
    )
    daily_dwd_check.add_argument("--revision-dir", type=Path, required=True)

    daily_dwd_promote = subparsers.add_parser(
        "promote-daily-dwd",
        help="atomically advance the accepted OCI daily DWD partition chain",
    )
    daily_dwd_promote.add_argument("--revision-dir", type=Path, required=True)
    daily_dwd_promote.add_argument("--pointer", type=Path, required=True)

    daily_dws = subparsers.add_parser(
        "build-daily-dws",
        help="derive one immutable OCI daily market-summary DWS revision from DWD",
    )
    daily_dws.add_argument("--dwd-revision-dir", type=Path, required=True)
    daily_dws.add_argument("--output", type=Path, required=True)
    daily_dws.add_argument("--revision", required=True)
    daily_dws.add_argument("--market-date", type=date.fromisoformat, required=True)

    daily_dws_check = subparsers.add_parser(
        "check-daily-dws",
        help="recheck a complete immutable OCI daily DWS summary revision",
    )
    daily_dws_check.add_argument("--revision-dir", type=Path, required=True)

    daily_dws_promote = subparsers.add_parser(
        "promote-daily-dws",
        help="atomically advance the accepted OCI daily DWS summary chain",
    )
    daily_dws_promote.add_argument("--revision-dir", type=Path, required=True)
    daily_dws_promote.add_argument("--pointer", type=Path, required=True)

    market_session = subparsers.add_parser(
        "market-session-check",
        help="return success for one open SSE/SZSE session and 3 for a known closed date",
    )
    market_session.add_argument("--calendar", type=Path, required=True)
    market_session.add_argument("--market-date", type=date.fromisoformat, required=True)
    return parser


def _print_gate_report(report: GateReport) -> None:
    blockers = ", ".join(report.open_blockers) if report.open_blockers else "none"
    print(f"gate version: {report.version}")
    print(f"P1 implementation: {'ALLOWED' if report.p1_can_start else 'BLOCKED'}")
    print(f"P1 acceptance: {'PASSED' if report.p1_acceptance_passed else 'BLOCKED'}")
    print(f"open blockers: {blockers}")
    print(f"Chapter 01 acceptance: {report.chapter_01_status.upper()}")
    print(f"P2 implementation: {'ALLOWED' if report.p2_can_start else 'BLOCKED'}")


def _doctor(policy: P0Policy, gates: GateReport, p2_gates: P2GateReport) -> None:
    print("Quanthus environment: OK")
    print(f"policy: {policy.version}")
    print(f"policy digest: {policy.digest}")
    print(f"policy path: {policy.path}")
    _print_gate_report(gates)
    p2_blockers = (
        ", ".join(p2_gates.open_activation_blockers)
        if p2_gates.open_activation_blockers
        else "none"
    )
    print(f"P2 acceptance: {p2_gates.acceptance_status.upper()}")
    print(f"P3 implementation: {'ALLOWED' if p2_gates.p3_can_start else 'BLOCKED'}")
    print(f"target-profile blockers: {p2_blockers}")


def _print_data_audit(report: DataAuditReport) -> None:
    summary = report.to_dict()["summary"]
    result = "FAIL" if report.has_failures else "PASS"
    print(f"P1 data audit: {result}")
    print(f"source root: {report.source_root}")
    print(f"bars file: {report.bars_file}")
    print(f"checks: pass={summary['pass']} warn={summary['warn']} fail={summary['fail']}")
    for check in report.checks:
        if check.status.value != "pass":
            print(f"{check.status.value.upper()} {check.check_id}: {check.count} ({check.detail})")


def _print_curated_daily(result: CuratedDailyResult) -> None:
    print(f"curated output: {result.output}")
    print(f"rows: {result.row_count}")
    print(f"data-valid rows: {result.valid_row_count}")
    print(f"source version: {result.source_dataset_version}")
    print(f"universe policy: {result.universe_policy_version}")


def _print_tradability_coverage(result: TradabilityCoverageResult) -> None:
    statuses = ", ".join(
        f"{status}={count}" for status, count in sorted(result.status_counts.items())
    )
    print(f"tradability coverage output: {result.output}")
    print(f"rows: {result.row_count}")
    print(f"data-valid bars: {result.valid_bar_count}")
    print(f"coverage statuses: {statuses}")
    print(f"knowledge cutoff: {result.knowledge_cutoff.isoformat()}")


def _print_materialized_snapshot(result: MaterializedSnapshot) -> None:
    reference = result.snapshot.reference
    print(f"snapshot id: {reference.snapshot_id}")
    print(f"snapshot location: {result.location}")
    print(f"market date: {reference.market_date.isoformat()}")
    print(f"knowledge cutoff: {reference.knowledge_cutoff.isoformat()}")
    print(f"datasets: {', '.join(manifest.dataset for manifest in result.snapshot.datasets)}")


def _print_delisted_coverage(report: DelistedCoverageReport) -> None:
    summary = report.to_dict()["summary"]
    print(f"S1 delisted coverage: {summary['reference_delisted_security_count']} securities")
    print(f"history window: {report.history_start.isoformat()} to {report.history_end.isoformat()}")
    print(f"unexplained missing sessions: {summary['unexplained_missing_session_count']}")
    print(f"invalid observed sessions: {summary['invalid_observed_session_count']}")


def _print_p1_acceptance(report: P1AcceptanceReport, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), sort_keys=True))
        return
    print(f"P1 evidence snapshot: {report.snapshot_id}")
    print(f"P1 evidence: {'PASS' if not report.has_failures else 'FAIL'}")
    for check in report.checks:
        if not check.passed:
            print(f"FAIL {check.check_id}: {check.detail}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            _doctor(
                load_policy(args.policy),
                load_gate_report(args.gates),
                load_p2_gate_report(args.p2_gates),
            )
        elif args.command == "policy-check":
            policy = load_policy(args.policy)
            print(f"{policy.version} OK {policy.digest}")
        elif args.command == "gate-status":
            gate_report = load_gate_report(args.gates)
            if args.json:
                print(json.dumps(gate_report.to_dict(), sort_keys=True))
            else:
                _print_gate_report(gate_report)
        elif args.command == "data-audit":
            audit_report = run_p1_data_audit(
                args.root,
                args.bars_file,
                require_c1_c2=args.require_c1_c2,
            )
            if args.output is not None:
                audit_report.write_json(args.output)
            _print_data_audit(audit_report)
            return 1 if audit_report.has_failures else 0
        elif args.command == "audit-corporate-actions":
            corporate_action_report = audit_corporate_action_corpus(
                CorporateActionCorpusPaths(
                    snapshot_dir=args.snapshot_dir,
                    corporate_actions=args.corporate_actions,
                    eastmoney_raw_dir=args.eastmoney_raw_dir,
                ),
                load_corporate_action_normalization_policy(args.policy),
                scope_start=args.scope_start,
                scope_end=args.scope_end,
            )
            if args.report_output is not None:
                corporate_action_report.write_json(args.report_output)
            if args.exception_ledger_output is not None:
                if corporate_action_report.exception_ledger is None:
                    raise ValueError(
                        "the selected normalization policy does not produce an exception ledger"
                    )
                corporate_action_report.exception_ledger.write_json(args.exception_ledger_output)
            print(
                json.dumps(
                    corporate_action_report.summary_dict(),
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            return 0 if corporate_action_report.ready_for_snapshot else 1
        elif args.command == "build-corporate-action-revision":
            corporate_action_revision = build_corporate_action_revision(
                paths=CorporateActionCorpusPaths(
                    snapshot_dir=args.snapshot_dir,
                    corporate_actions=args.corporate_actions,
                    eastmoney_raw_dir=args.eastmoney_raw_dir,
                ),
                policy=load_corporate_action_normalization_policy(args.policy),
                scope_start=args.scope_start,
                scope_end=args.scope_end,
                output=args.output,
                revision=args.revision,
                knowledge_cutoff=args.knowledge_cutoff,
                previous_revision=args.previous_revision,
            )
            print(
                json.dumps(corporate_action_revision.to_dict(), ensure_ascii=True, sort_keys=True)
            )
        elif args.command == "check-corporate-action-revision":
            corporate_action_revision = check_corporate_action_revision(args.revision_dir)
            print(
                json.dumps(corporate_action_revision.to_dict(), ensure_ascii=True, sort_keys=True)
            )
        elif args.command == "promote-corporate-action-revision":
            corporate_action_revision = promote_corporate_action_revision(
                revision_dir=args.revision_dir,
                pointer=args.pointer,
            )
            print(
                json.dumps(corporate_action_revision.to_dict(), ensure_ascii=True, sort_keys=True)
            )
        elif args.command == "build-code-validity":
            mapping = load_code_validity_mapping(args.mapping)
            row_count, checksum = build_code_validity_table(
                root=args.root,
                mapping=mapping,
                output=args.output,
            )
            print(f"code validity output: {args.output}")
            print(f"rows: {row_count}")
            print(f"checksum: {checksum}")
        elif args.command == "normalize-security-identity":
            normalized = build_identity_normalized_revision(
                root=args.root,
                code_validity=args.code_validity,
                s2_candidate=args.s2_candidate,
                output=args.output,
                revision=args.revision,
            )
            print(json.dumps(normalized.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "merge-none-backfill":
            merged = build_none_backfill_revision(
                parent=args.parent,
                raw_revision=args.raw_revision,
                detection_file=args.detection_file,
                output=args.output,
                revision=args.revision,
            )
            if args.promote_pointer is not None:
                write_none_current(revision_dir=merged.output, pointer=args.promote_pointer)
            print(json.dumps(merged.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "merge-none-tail-repair":
            tail_repair = build_none_tail_repair_revision(
                parent=args.parent,
                raw_revision=args.raw_revision,
                output=args.output,
                revision=args.revision,
            )
            if args.promote_pointer is not None:
                write_none_current(revision_dir=tail_repair.output, pointer=args.promote_pointer)
            print(json.dumps(tail_repair.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "build-identity-coverage":
            result = build_identity_coverage(
                revision_root=args.revision_root,
                reference_root=args.reference_root,
                knowledge_cutoff=args.knowledge_cutoff,
                coverage_output=args.coverage_output,
                coverage_metadata_output=args.coverage_metadata_output,
                s1_report_output=args.s1_report_output,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "curate-daily":
            default_policy = find_project_root() / "config" / "p1-universe.v2.json"
            universe_policy = load_universe_policy(args.universe_policy or default_policy)
            curated = build_curated_daily(
                root=args.root,
                output=args.output,
                universe_policy=universe_policy,
                bars_file=args.bars_file,
            )
            if args.metadata_output is not None:
                args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(curated.to_dict(), ensure_ascii=True, indent=2, sort_keys=True)
                args.metadata_output.write_text(f"{payload}\n", encoding="utf-8")
            _print_curated_daily(curated)
        elif args.command == "build-tradability-coverage":
            default_policy = find_project_root() / "config" / "p1-universe.v2.json"
            universe_policy = load_universe_policy(args.universe_policy or default_policy)
            coverage = build_tradability_coverage(
                root=args.root,
                output=args.output,
                universe_policy=universe_policy,
                knowledge_cutoff=args.knowledge_cutoff,
                bars_file=args.bars_file,
            )
            if args.metadata_output is not None:
                args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(
                    coverage.to_dict(), ensure_ascii=True, indent=2, sort_keys=True
                )
                args.metadata_output.write_text(f"{payload}\n", encoding="utf-8")
            _print_tradability_coverage(coverage)
        elif args.command == "delisted-coverage":
            default_policy = find_project_root() / "config" / "p1-universe.v2.json"
            report = run_delisted_coverage(
                root=args.root,
                bars_file=args.bars_file,
                knowledge_cutoff=args.knowledge_cutoff,
                universe_policy=load_universe_policy(args.universe_policy or default_policy),
            )
            report.write_json(args.output)
            _print_delisted_coverage(report)
        elif args.command == "p1-acceptance-check":
            evidence_report = load_p1_acceptance_evidence(args.evidence)
            _print_p1_acceptance(evidence_report, as_json=args.json)
            return 1 if evidence_report.has_failures else 0
        elif args.command == "build-p1-evidence":
            evidence = build_p1_acceptance_evidence(
                snapshot_dir=args.snapshot_dir,
                coverage_path=args.coverage,
                coverage_metadata_path=args.coverage_metadata,
                data_audit_path=args.data_audit,
                s1_report_path=args.s1_report,
                prior_evidence_path=args.prior_evidence,
                d1_pack_path=args.d1_pack,
                d3_seed_path=args.d3_seed,
                c1_conditional_policy_path=args.c1_conditional_policy,
            )
            write_p1_acceptance_evidence(args.output, evidence)
            print(f"P1 evidence written: {args.output}")
        elif args.command == "publish-snapshot":
            materialized = publish_snapshot(
                catalog=SnapshotCatalog(args.catalog_root),
                plan=load_snapshot_plan(args.plan),
            )
            if args.metadata_output is not None:
                args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(
                    materialized.to_dict(), ensure_ascii=True, indent=2, sort_keys=True
                )
                args.metadata_output.write_text(f"{payload}\n", encoding="utf-8")
            _print_materialized_snapshot(materialized)
        elif args.command == "publish-historical-replay-snapshot":
            replay_publication = publish_historical_replay_snapshot(
                catalog=SnapshotCatalog(args.catalog_root),
                base_snapshot_dir=args.base_snapshot,
                corporate_action_revision_dir=args.corporate_action_revision,
                trading_calendar=args.trading_calendar,
                calendar_source_revision=args.calendar_source_revision,
                calendar_knowledge_cutoff=args.calendar_knowledge_cutoff,
                snapshot_id=args.snapshot_id,
                source_revision=args.source_revision,
                knowledge_cutoff=args.knowledge_cutoff,
                policy=load_corporate_action_normalization_policy(args.policy),
            )
            replay_payload = replay_publication.to_dict()
            if args.metadata_output is not None:
                args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
                args.metadata_output.write_text(
                    json.dumps(replay_payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            print(json.dumps(replay_payload, ensure_ascii=True, sort_keys=True))
        elif args.command == "audit-historical-replay-frames":
            with load_historical_frame_reader(
                args.snapshot_dir,
                load_corporate_action_normalization_policy(args.policy),
            ) as replay_reader:
                replay_frame_report = replay_reader.audit(args.scope_start, args.scope_end)
            replay_frame_payload = replay_frame_report.to_dict()
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(
                        replay_frame_payload,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            print(json.dumps(replay_frame_payload, ensure_ascii=True, sort_keys=True))
            return 0 if replay_frame_report.ready_for_strategy_replay else 1
        elif args.command == "run-baseline-strategy-suite":
            project_root = find_project_root()
            baseline_catalogue = load_baseline_strategy_catalogue(
                args.catalogue
                or project_root / "config" / "p3-baseline-strategies.research.v2.json"
            )
            baseline_registry = load_feature_registry(
                args.feature_registry
                or project_root / "config" / "p3-baseline-features.research.v1.json"
            )
            baseline_engine = BacktestEngine(
                load_execution_policy(
                    args.execution_policy
                    or project_root / "config" / "p2-execution-policy.research.v1.json"
                )
            )
            with load_historical_frame_reader(
                args.snapshot_dir,
                load_corporate_action_normalization_policy(args.corporate_action_policy),
            ) as baseline_reader:
                panel_scope_end = baseline_reader.frames(
                    args.scope_end,
                    args.scope_end,
                )[0].execution_date
                with BaselineFeaturePanel.build(
                    baseline_reader.bundle,
                    baseline_registry,
                    scope_start=args.scope_start,
                    scope_end=panel_scope_end,
                    output=args.feature_panel,
                ) as baseline_panel:
                    baseline_result = run_baseline_strategy_suite(
                        baseline_reader,
                        baseline_engine,
                        baseline_catalogue,
                        baseline_registry,
                        baseline_panel,
                        load_benchmark_curve(args.benchmark),
                        scope_start=args.scope_start,
                        scope_end=args.scope_end,
                    )
            baseline_payload = baseline_result.to_dict()
            if args.audit_output_dir is not None:
                args.audit_output_dir.mkdir(parents=True, exist_ok=True)
                for strategy_result in baseline_result.strategies:
                    audit_path = args.audit_output_dir / (
                        f"{strategy_result.strategy_id}-{strategy_result.strategy_version}.json"
                    )
                    audit_path.write_bytes(strategy_result.canonical_bytes() + b"\n")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(baseline_payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            baseline_scoreboard = render_baseline_scoreboard(baseline_result)
            if args.scoreboard_output is not None:
                args.scoreboard_output.parent.mkdir(parents=True, exist_ok=True)
                args.scoreboard_output.write_text(
                    baseline_scoreboard + "\n",
                    encoding="utf-8",
                )
            print(baseline_scoreboard, file=sys.stderr)
            print(json.dumps(baseline_payload, ensure_ascii=True, sort_keys=True))
        elif args.command == "build-st-status":
            default_policy = find_project_root() / "config" / "st-status.v1.json"
            st_result = build_st_status_revision(
                candidate=args.candidate,
                output=args.output,
                revision=args.revision,
                policy_path=args.policy or default_policy,
                parent=args.parent,
            )
            if args.promote_pointer is not None:
                write_current_pointer(revision_dir=st_result.output, pointer=args.promote_pointer)
            print(json.dumps(st_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "build-price-limits":
            default_policy = find_project_root() / "config" / "price-limit.v2.json"
            limit_result = build_price_limit_revision(
                rules=args.rules,
                identity_revision=args.identity_revision,
                s2_revision=args.s2_revision,
                output=args.output,
                revision=args.revision,
                policy_path=args.policy or default_policy,
            )
            if args.promote_pointer is not None:
                write_price_limit_current(
                    revision_dir=limit_result.output, pointer=args.promote_pointer
                )
            print(json.dumps(limit_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "verify-ods-release":
            ods_result = verify_ods_release(args.release_dir)
            print(json.dumps(ods_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "publish-ods-release":
            ods_result = publish_ods_release(spec_path=args.spec, output=args.output)
            print(json.dumps(ods_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "pipeline-check":
            config = load_pipeline_config(args.config)
            print(f"pipeline config OK: {config.pipeline_id} ({len(config.stages)} stages)")
        elif args.command == "pipeline-run":
            config = load_pipeline_config(args.config)
            pipeline_result = run_pipeline(config, market_date=args.market_date)
            print(json.dumps(pipeline_result.to_dict(), ensure_ascii=True, sort_keys=True))
            if pipeline_result.status == "skipped_closed_session":
                return 0
            return 0 if pipeline_result.sla_met else 4
        elif args.command == "ods-wait-ready":
            selection = wait_for_ods_ready(
                source_config=load_ods_source_config(args.sources),
                schedule=args.schedule,
                market_date=args.market_date.isoformat(),
                selection_path=args.selection,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            print(json.dumps(selection.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "ods-pull-selected":
            selection = pull_selected_ods_release(
                source_config=load_ods_source_config(args.sources),
                schedule=args.schedule,
                market_date=args.market_date.isoformat(),
                selection_path=args.selection,
                destination_root=args.destination_root,
            )
            print(json.dumps(selection.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "schedule-check":
            schedule = load_schedule_contract(args.config)
            print(
                f"schedule contract OK: {schedule.schedule_id} "
                f"({len(schedule.lanes)} lanes, {schedule.timezone})"
            )
        elif args.command == "lane-run":
            paths = resolve_paths(
                home=args.home,
                shadow_root=args.shadow_root,
                calendar=args.calendar,
            )
            lane_result = run_lane(
                lane_id=args.lane,
                market_date=args.market_date,
                schedule_config=args.config,
                paths=paths,
                stream=args.stream,
                force_preflight=args.force_preflight,
                issue_ref=args.issue_ref,
                symbols=args.symbol or None,
                recovery_deadline_at=args.recovery_deadline_at,
            )
            print(json.dumps(lane_result.to_dict(), ensure_ascii=True, sort_keys=True))
            if lane_result.status in {"ready", "closed_session"}:
                return 0
            if lane_result.status == "partial":
                return 4
            return 2
        elif args.command == "lane-shadow-matrix":
            paths = resolve_paths(
                home=args.home,
                shadow_root=args.shadow_root,
                calendar=args.calendar,
            )
            matrix = run_shadow_matrix(
                schedule_config=args.config,
                open_date=args.open_date,
                closed_date=args.closed_date,
                weekly_date=args.weekly_date,
                event_date=args.event_date,
                paths=paths,
            )
            print(json.dumps(matrix, ensure_ascii=True, sort_keys=True))
            # bootstrap / missing-preflight expected failed; partial never success.
            unexpected = []
            for run in matrix["runs"]:
                lane = run.get("lane_id")
                status = run.get("status")
                preflight = run.get("preflight")
                if lane == "bootstrap" and status == "failed":
                    continue
                if preflight == "missing_or_disagree" and status == "failed":
                    continue
                if status in {"ready", "closed_session"}:
                    continue
                unexpected.append(run)
            return 0 if not unexpected else 4
        elif args.command == "build-daily-dwd":
            dwd_result = build_daily_dwd_revision(
                release_dir=args.release_dir,
                output=args.output,
                revision=args.revision,
                market_date=args.market_date,
                universe_policy_path=args.universe_policy,
                code_validity_mapping_path=args.code_validity_mapping,
                code_revision=args.code_revision,
                parent_pointer=args.parent_pointer,
                base_pointer=args.base_pointer,
            )
            print(json.dumps(dwd_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "check-daily-dwd":
            dwd_result = check_daily_dwd_revision(args.revision_dir)
            print(json.dumps(dwd_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "promote-daily-dwd":
            dwd_result = promote_daily_dwd_revision(
                revision_dir=args.revision_dir,
                pointer=args.pointer,
            )
            print(json.dumps(dwd_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "build-daily-dws":
            dws_result = build_daily_dws_revision(
                dwd_revision_dir=args.dwd_revision_dir,
                output=args.output,
                revision=args.revision,
                market_date=args.market_date,
            )
            print(json.dumps(dws_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "check-daily-dws":
            dws_result = check_daily_dws_revision(args.revision_dir)
            print(json.dumps(dws_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "promote-daily-dws":
            dws_result = promote_daily_dws_revision(
                revision_dir=args.revision_dir,
                pointer=args.pointer,
            )
            print(json.dumps(dws_result.to_dict(), ensure_ascii=True, sort_keys=True))
        elif args.command == "market-session-check":
            is_open = market_session_is_open(
                calendar=args.calendar,
                market_date=args.market_date,
            )
            print(f"{args.market_date.isoformat()} {'OPEN' if is_open else 'CLOSED'}")
            return 0 if is_open else 3
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (
        AuditError,
        ConfigurationError,
        CodeValidityError,
        CoverageError,
        CurationError,
        DelistedCoverageError,
        AcceptanceEvidenceError,
        EvidenceBuildError,
        SnapshotBuildError,
        IdentityNormalizationError,
        NoneBackfillError,
        IdentityCoverageError,
        StStatusBuildError,
        PriceLimitBuildError,
        OdsReleaseError,
        OdsHandoffError,
        PipelineConfigError,
        PipelineRunError,
        ScheduleContractError,
        DailyWarehouseError,
        LaneRunError,
        CorporateActionCorpusError,
        CorporateActionNormalizationError,
        CorporateActionRevisionError,
        HistoricalReplaySnapshotError,
        HistoricalReplayBundleError,
        HistoricalFrameReaderError,
        BaselineResearchError,
        BenchmarkCurveError,
        BacktestInputError,
        FeatureRegistryError,
    ) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
