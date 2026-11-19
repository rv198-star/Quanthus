"""CLI adapter over the same P12 desktop use cases and transport contract as HTTP.

Example:
  PYTHONPATH=src python -m quanthus.platform.p12.cli --state-dir /tmp/p12 workbench
  PYTHONPATH=src python -m quanthus.platform.p12.cli --state-dir /tmp/p12 \\
      submit-fill --request fill.json

The CLI runs on the operator's machine against the trusted local synthetic assembly; it
acts for the selected fixture account and prints the exact p12.desktop.v1 DTO.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from quanthus.platform.adapters.p12_synthetic_fixture import DEFAULT_FIXTURE, load_synthetic_fixture
from quanthus.platform.p12 import transport
from quanthus.platform.p12.assembly import synthetic_applications
from quanthus.platform.p12.validation import ContractViolation
from quanthus.policy import find_project_root
from quanthus.shared.fact_contracts import FactError

COMMANDS = ("workbench", "records", "funding-rules", "submit-fill", "correct-fill")


def run(
    command: str,
    *,
    state_dir: Path,
    account_id: str,
    request: dict[str, Any] | None = None,
    fixture_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[int, dict[str, Any]]:
    fixture = load_synthetic_fixture(fixture_path or find_project_root() / DEFAULT_FIXTURE)
    applications = synthetic_applications(state_dir=state_dir, fixture=fixture, clock=clock)
    try:
        app = applications.get(account_id)
        if app is None:
            raise FactError("ACCOUNT_NOT_ADMITTED", "account is not part of the local assembly")
        scope = app.scope
        if command == "workbench":
            return 0, transport.workbench_dto(app.workbench(scope))
        if command == "records":
            return 0, transport.records_dto(app.records(scope))
        if command == "funding-rules":
            return 0, transport.funding_rules_dto(app.funding_rules(scope))
        if request is None:
            raise FactError("INVALID_REPORT_SHAPE", "--request is required for write commands")
        if command == "submit-fill":
            return 0, transport.outcome_dto(
                app.submit_fill(scope, transport.parse_submit_fill(request))
            )
        if command == "correct-fill":
            return 0, transport.outcome_dto(
                app.correct_fill(scope, transport.parse_correct_fill(request))
            )
        raise FactError("INVALID_REPORT_SHAPE", f"unknown command {command}")
    except (FactError, ContractViolation) as exc:
        return 2, transport.error_dto(str(exc.reason_code), str(exc).split(": ", 1)[-1], "cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P12 desktop use cases over the CLI")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--clock",
        default=None,
        metavar="ISO8601",
        help="pin the synthetic clock to one aware instant for repeatable reads",
    )
    parser.add_argument("--account", default="SYN-P12-A")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8")) if args.request else None
    code, payload = run(
        args.command,
        state_dir=args.state_dir,
        account_id=args.account,
        request=request,
        fixture_path=args.fixture,
        clock=(
            (lambda: datetime.fromisoformat(args.clock))
            if args.clock is not None
            else None
        ),
    )
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
