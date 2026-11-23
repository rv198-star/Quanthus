'''The T-evening daily advice job: input -> rule -> immutable package -> receipt.

Sequence per run:

1. Read the account's current holdings from the bound ledger (advisory only;
   the run never writes ledger facts).
2. Qualify the P10 daily-core descriptor for the target market date.
3. Run the bound strategy rule runner over the snapshot and holdings.
4. Publish the one immutable package for that market date (same-day reruns
   replay or refuse; corrections go through retraction).
5. Write the run receipt - including the INPUT_BLOCKED receipt with missing
   members, affected scope and recovery condition when input fails.

Every stage records elapsed time, row counts and reuse so the receipt shows
the work, and a failure before publication leaves the day publishable by a
later retry inside the job window.
'''

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.p12_daily.daily_core_input import (
    EXCHANGE_TZ,
    DailyCoreSnapshot,
    InputBlock,
    qualify_daily_input,
)
from quanthus.p12_daily.decision_loader import decision_set_to_dict
from quanthus.p12_daily.package_store import (
    RUN_SCHEMA,
    DailyPackageStore,
)
from quanthus.p12_daily.rule_runner import (
    RunnerOutput,
    RunnerRequest,
    StrategyRuleRunner,
)
from quanthus.shared.fact_contracts import FactError, digest

STATUS_PUBLISHED = "PUBLISHED"
STATUS_ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
STATUS_INPUT_BLOCKED = "INPUT_BLOCKED"
STATUS_FAILED = "FAILED"

DEFAULT_JOB_ROOT = "/srv/quanthus-base22-data/jobs/cn02-standard-daily-base251-a5"
ZERO_QUALIFIED_TABLES = ("dwd_security_bar",)


def _run_id(now: datetime) -> str:
    return now.astimezone(EXCHANGE_TZ).strftime("%Y%m%dT%H%M%S%f%z")


def _holdings_from_ledger(state_dir: Path, account_id: str) -> tuple[tuple[str, int], ...]:
    '''Read current positions from the paper ledger without writing any fact.

    A store that has not been initialized yields no holdings so the run still
    works on a fresh deployment before the first login.
    '''

    store_path = Path(state_dir) / "p12-paper-facts.sqlite"
    if not store_path.is_file():
        return ()
    try:
        from quanthus.capabilities.execution import SyntheticContext
        from quanthus.platform.adapters.product_daily_facts_sqlite import (
            build_synthetic_fact_service,
        )
        from quanthus.product_daily.application.models import (
            SyntheticRuntimeAuthorization,
        )

        context = SyntheticContext(
            account_id, "synthetic", "p12-daily-job", datetime.now(EXCHANGE_TZ),
            "complete-cash-only",
        )
        authorization = SyntheticRuntimeAuthorization(
            "local-paper:v1", frozenset({account_id})
        )
        facts = build_synthetic_fact_service(
            store_path,
            context=context,
            authorization=authorization,
            clock=lambda: datetime.now(EXCHANGE_TZ),
        )
        snapshot = facts.snapshot(context)
    except FactError:
        return ()
    except Exception:  # noqa: BLE001 - an unreadable ledger must not fabricate holdings
        return ()
    return tuple((p.security_code, int(p.quantity)) for p in snapshot.projection.positions)


def _blocked_receipt(
    receipt: dict,
    block: InputBlock,
    *,
    market_date: date,
    missing_members: list[str],
) -> dict:
    receipt["status"] = STATUS_INPUT_BLOCKED
    receipt["input_block"] = {
        "reason_code": block.reason_code,
        "detail": block.detail,
        "checked_at": block.checked_at.isoformat(),
        "expected_market_date": (
            block.expected_market_date.isoformat() if block.expected_market_date else None
        ),
        "observed_market_date": (
            block.observed_market_date.isoformat() if block.observed_market_date else None
        ),
        # Affected scope and recovery condition the workbench renders
        # verbatim: no new entries are evaluated, holdings stay as they are.
        "affected_scope": "new_entries",
        "missing_members": missing_members,
        "recovery_condition": (
            "P10 daily-core publishes a qualified descriptor for "
            f"{market_date.isoformat()}; the next scheduled run then emits the day's package"
        ),
    }
    return receipt


def run_daily(
    *,
    state_dir: Path,
    job_root: Path,
    runner: StrategyRuleRunner,
    market_date: date,
    next_session: date,
    account_id: str,
    now: datetime | None = None,
    holdings: tuple[tuple[str, int], ...] | None = None,
) -> dict:
    '''Execute one daily run and persist its receipt; returns the receipt.'''

    started = time.monotonic()
    moment = (now or datetime.now(EXCHANGE_TZ)).astimezone(EXCHANGE_TZ)
    store = DailyPackageStore(state_dir)
    receipt: dict[str, Any] = {
        "schema_version": RUN_SCHEMA,
        "run_id": _run_id(moment),
        "market_date": market_date.isoformat(),
        "next_session": next_session.isoformat(),
        "account_id": account_id,
        "runner": {
            "ref_id": runner.runner_ref.ref_id,
            "version": runner.runner_ref.version,
            "digest": runner.runner_ref.digest,
        },
        "started_at": moment.isoformat(),
        "stages": [],
        "status": STATUS_FAILED,
    }

    def stage(name: str, started_mono: float, **fields: Any) -> float:
        receipt["stages"].append(
            {
                "stage": name,
                "elapsed_ms": int((time.monotonic() - started_mono) * 1000),
                **fields,
            }
        )
        return time.monotonic()

    t = time.monotonic()
    resolved_holdings = (
        holdings
        if holdings is not None
        else _holdings_from_ledger(state_dir, account_id)
    )
    t = stage("holdings", t, positions=len(resolved_holdings))

    pointer = (
        job_root / 'CURRENT_DAILY_CORE_BASE24.json'
        if job_root.is_dir()
        else job_root
    )
    qualified = qualify_daily_input(
        pointer, expected_market_date=market_date, checked_at=moment
    )
    if isinstance(qualified, InputBlock):
        t = stage(
            "input",
            t,
            result="blocked",
            reason_code=qualified.reason_code,
            detail=qualified.detail,
        )
        _blocked_receipt(
            receipt, qualified, market_date=market_date,
            missing_members=list(qualified.missing_members) or ["daily-core-base24"],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    snapshot: DailyCoreSnapshot = qualified
    # A qualified descriptor whose required market tables are empty is still a
    # blocked input: evaluating zero rows would read as "no advice", which is a
    # different claim from "input was absent".
    empty_tables = [
        name for name in ZERO_QUALIFIED_TABLES if snapshot.row_counts.get(name, 0) == 0
    ]
    if empty_tables:
        t = stage("input", t, result="blocked", empty_tables=empty_tables)
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail="daily-core required market tables are empty: {}".format(
                    ", ".join(empty_tables)
                ),
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=empty_tables,
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    t = stage(
        "input",
        t,
        result="qualified",
        package_id=snapshot.package_id,
        manifest_sha256=snapshot.core_manifest_sha256,
        row_counts=snapshot.row_counts,
    )

    try:
        output: RunnerOutput = runner.run(
            RunnerRequest(
                snapshot=snapshot,
                next_session=next_session,
                holdings=resolved_holdings,
                published_at=moment,
            )
        )
    except FactError as exc:
        t = stage("rule", t, result="failed", reason_code=str(exc.reason_code))
        receipt["status"] = STATUS_FAILED
        receipt["error"] = {"reason_code": str(exc.reason_code), "detail": str(exc)}
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    decisions = output.decision_set
    t = stage(
        "rule",
        t,
        result="ok",
        decisions=len(decisions.decisions),
        exclusions=len(output.exclusions),
    )
    if output.exclusions:
        receipt["exclusions"] = [
            {
                "security_code": item.security_code,
                "reason_code": item.reason_code,
                "detail": item.detail,
            }
            for item in output.exclusions
        ]

    decision_set = decision_set_to_dict(decisions)
    outcome = store.publish(
        decision_set=decision_set,
        runner={
            "ref_id": runner.runner_ref.ref_id,
            "version": runner.runner_ref.version,
            "digest": runner.runner_ref.digest,
        },
        input_evidence={
            "package_id": snapshot.package_id,
            "market_date": snapshot.market_date.isoformat(),
            "manifest_sha256": snapshot.core_manifest_sha256,
            "content_digest": snapshot.content_digest,
            "row_counts": snapshot.row_counts,
        },
        published_at=moment,
    )
    t = stage(
        "publish",
        t,
        result=outcome.status,
        package_id=outcome.package.package_id if outcome.package else None,
    )
    receipt["status"] = (
        STATUS_PUBLISHED if outcome.status == "published" else STATUS_ALREADY_PUBLISHED
    )
    entry_plans = sum(1 for item in decisions.decisions if item.entry is not None)
    receipt["package"] = {
        "package_id": outcome.package.package_id,
        "file_sha256": outcome.package.file_sha256,
        "decision_count": len(decisions.decisions),
        "entry_plan_count": entry_plans,
        "admission_disposition": "empty_advice" if entry_plans == 0 else "publishable",
        "decision_set_digest": digest(decision_set),
    }
    receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    store.write_run(receipt)
    return receipt


def _load_runner(spec: str | None, candidates_path: Path | None) -> StrategyRuleRunner:
    '''Resolve the bound rule runner; the formal P11 runner plugs in here later.

    Batch A admits only the labelled synthetic runner built from an explicit
    candidates file.  Any other spec is refused rather than silently
    approximated - non-approved versions never run.
    '''

    from quanthus.p12_daily.rule_runner import SyntheticRuleRunner

    if spec and spec != "synthetic-replay":
        raise FactError(
            "RULE_VERSION_REJECTED",
            f"rule runner {spec!r} is not an admitted batch-A version",
        )
    path = candidates_path or Path(__file__).resolve().parents[3] / (
        "config/p12/daily-synthetic-candidates.v1.json"
    )
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
        raise FactError("INVALID_FIXTURE", "candidates file must carry a candidates list")
    return SyntheticRuleRunner(candidates=tuple(raw["candidates"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--job-root",
        type=Path,
        default=Path(os.environ.get("QUANTHUS_P12_DAILY_JOB_ROOT", DEFAULT_JOB_ROOT)),
    )
    parser.add_argument("--account", default="SYN-PAPER-A")
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--next-session", type=date.fromisoformat, required=True)
    parser.add_argument("--runner", default="synthetic-replay")
    parser.add_argument("--candidates", type=Path)
    parser.add_argument(
        "--retract",
        action="store_true",
        help="append a retraction for --market-date instead of running",
    )
    parser.add_argument("--retract-reason", default=None)
    args = parser.parse_args(argv)

    store = DailyPackageStore(args.state_dir)
    if args.retract:
        if not args.retract_reason:
            parser.error("--retract requires --retract-reason")
        record = store.retract(
            market_date=args.market_date,
            reason=args.retract_reason,
            retracted_at=datetime.now(EXCHANGE_TZ),
            actor="operator-cli",
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    runner = _load_runner(args.runner, args.candidates)
    receipt = run_daily(
        state_dir=args.state_dir,
        job_root=args.job_root,
        runner=runner,
        market_date=args.market_date,
        next_session=args.next_session,
        account_id=args.account,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] in (STATUS_PUBLISHED, STATUS_ALREADY_PUBLISHED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
