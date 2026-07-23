from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from quanthus import __version__
from quanthus.gates import GateReport, load_gate_report
from quanthus.policy import ConfigurationError, P0Policy, load_policy


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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            _doctor(load_policy(args.policy), load_gate_report(args.gates))
        elif args.command == "policy-check":
            policy = load_policy(args.policy)
            print(f"{policy.version} OK {policy.digest}")
        elif args.command == "gate-status":
            report = load_gate_report(args.gates)
            if args.json:
                print(json.dumps(report.to_dict(), sort_keys=True))
            else:
                _print_gate_report(report)
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 0
