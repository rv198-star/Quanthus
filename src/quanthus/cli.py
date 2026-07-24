from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from quanthus import __version__
from quanthus.curation import CuratedDailyResult, CurationError, build_curated_daily
from quanthus.data_foundation import SnapshotCatalog
from quanthus.data_quality import AuditError, DataAuditReport, run_p1_data_audit
from quanthus.gates import GateReport, load_gate_report
from quanthus.policy import ConfigurationError, P0Policy, find_project_root, load_policy
from quanthus.snapshot_publish import (
    MaterializedSnapshot,
    SnapshotBuildError,
    load_snapshot_plan,
    publish_snapshot,
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

    snapshot = subparsers.add_parser(
        "publish-snapshot",
        help="hard-link a reviewed P1 publication plan into an immutable local snapshot",
    )
    snapshot.add_argument("--plan", type=Path, required=True)
    snapshot.add_argument("--catalog-root", type=Path, required=True)
    snapshot.add_argument("--metadata-output", type=Path)
    return parser


def _print_gate_report(report: GateReport) -> None:
    blockers = ", ".join(report.open_blockers) if report.open_blockers else "none"
    print(f"gate version: {report.version}")
    print(f"P1 implementation: {'ALLOWED' if report.p1_can_start else 'BLOCKED'}")
    print(f"P1 acceptance: {'PASSED' if report.p1_acceptance_passed else 'BLOCKED'}")
    print(f"open blockers: {blockers}")
    print(f"Chapter 01 acceptance: {report.chapter_01_status.upper()}")
    print(f"P2 implementation: {'ALLOWED' if report.p2_can_start else 'BLOCKED'}")


def _doctor(policy: P0Policy, gates: GateReport) -> None:
    print("Quanthus environment: OK")
    print(f"policy: {policy.version}")
    print(f"policy digest: {policy.digest}")
    print(f"policy path: {policy.path}")
    _print_gate_report(gates)


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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            _doctor(load_policy(args.policy), load_gate_report(args.gates))
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
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (
        AuditError,
        ConfigurationError,
        CoverageError,
        CurationError,
        SnapshotBuildError,
    ) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 0
