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
import dataclasses
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.capabilities.decision_access import OpportunityStage
from quanthus.p12_daily.consumer_sidecar import (
    DISPOSITION_INPUT_OR_CLOCK_BLOCKED,
    SidecarError,
    read_sidecar,
)
from quanthus.p12_daily.daily_core_input import (
    EXCHANGE_TZ,
    DailyCoreSnapshot,
    InputBlock,
    qualify_daily_input,
)
from quanthus.p12_daily.decision_loader import decision_set_to_dict
from quanthus.p12_daily.forward_calendar import (
    DEFAULT_CALENDAR_ROOT,
    load_forward_calendar,
)
from quanthus.p12_daily.forward_calendar import (
    next_session as calendar_next_session,
)
from quanthus.p12_daily.package_store import (
    RUN_SCHEMA,
    DailyPackageStore,
)
from quanthus.p12_daily.rule_runner import (
    SESSION_CLOSE,
    HeldPositionFact,
    PendingSecurity,
    RunnerOutput,
    RunnerRequest,
    StrategyRuleRunner,
    _candidate_sort_key,
)
from quanthus.shared.fact_contracts import FactError, digest

STATUS_PUBLISHED = "PUBLISHED"
STATUS_ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
STATUS_INPUT_BLOCKED = "INPUT_BLOCKED"
STATUS_FAILED = "FAILED"
# A run before the configured decision cutoff is a readiness precheck only:
# it writes a run receipt, never a package (issue #719 publish-time note).
STATUS_PRECHECK = "PRECHECK"

DEFAULT_JOB_ROOT = "/srv/quanthus-base22-data/jobs/cn02-standard-daily-base251-a5"
ZERO_QUALIFIED_TABLES = ("dwd_security_bar",)
PUBLISH_GATE_CONFIG = (
    Path(__file__).resolve().parents[3] / "config/p12/daily-publish-gate.v1.json"
)


def _run_id(now: datetime) -> str:
    return now.astimezone(EXCHANGE_TZ).strftime("%Y%m%dT%H%M%S%f%z")


def _trusted_producer_ids(config_path: Path, account_mode: str) -> tuple[str, ...]:
    '''Trusted rule-producer ids bound to this account mode (issue #719 seam).

    The list comes from config, never code; a synthetic producer id is only
    admitted under the synthetic mode.  A mode with no entry, or a config that
    cannot be read, fails closed as an empty trusted set.'''

    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        mapping = raw.get("trusted_producer_ids_by_mode") or {}
        value = mapping.get(account_mode) or ()
        return tuple(str(item) for item in value)
    except (OSError, json.JSONDecodeError, TypeError):
        return ()


def _publish_gate_block(
    output: RunnerOutput,
    *,
    trusted_ids: tuple[str, ...],
    expected_run_id: str,
    expected_manifest_sha256: str,
) -> tuple[str, dict] | None:
    '''Run-seam publish gate (issue #719): publish only when all three hold.

    1. coverage summary consistent with this run (receipt recomputes its own
       coverage digest over producer/run/evaluated-universe and this exact
       manifest scope, bound to the job-minted run_id);
    2. producer_id inside the account mode's trusted list;
    3. decisions correspond one-to-one with the receipt's evaluated candidates;
    4. the runner declares the run publishable and supplies a non-empty,
       matching source closure.

    Returns (reason_code, detail) on any breach, else None.'''

    # The runner's own publishable declaration leads: a non-publishable run
    # never reaches the package even when every other check holds (audit R11).
    if output.publishable is False:
        return (
            "run_not_publishable",
            {
                "disposition": output.disposition,
                "diagnostics": output.diagnostics,
            },
        )
    receipt = output.evaluation_receipt
    if not isinstance(receipt, dict):
        return ("evaluation_receipt_missing", {"detail": "runner produced no evaluation receipt"})
    # The receipt must echo the run identity this job minted; a receipt
    # minted by a prior or different run can never pass (audit R10).
    if receipt.get("run_id") != expected_run_id:
        return (
            "candidate_evaluation_run_mismatch",
            {"expected_run_id": expected_run_id, "actual_run_id": receipt.get("run_id")},
        )
    closure = output.source_closure
    if not isinstance(closure, dict) or not closure:
        return ("source_closure_missing", {"detail": "runner produced no source closure"})
    if closure.get("core_manifest_sha256") != expected_manifest_sha256:
        return (
            "source_closure_manifest_mismatch",
            {
                "expected": expected_manifest_sha256,
                "actual": closure.get("core_manifest_sha256"),
            },
        )
    # 2. trusted producer for this account mode.
    producer_id = receipt.get("producer_id")
    if not producer_id or (trusted_ids and producer_id not in trusted_ids):
        return (
            "untrusted_evaluation_producer",
            {"producer_id": producer_id, "trusted": sorted(trusted_ids)},
        )
    if not trusted_ids:
        # A mode with an empty trusted list trusts no producer at all.
        return (
            "untrusted_evaluation_producer",
            {"producer_id": producer_id, "trusted": sorted(trusted_ids)},
        )
    # 1. coverage digest consistent with this run: recompute the producer's
    #    own coverage identity and compare; a self-minted or stale digest fails.
    coverage_payload = {
        "producer_id": producer_id,
        "producer_version": receipt.get("producer_version"),
        "run_id": receipt.get("run_id"),
        "evaluated_universe": sorted(receipt.get("evaluated_universe") or ()),
        "manifest_sha256": expected_manifest_sha256,
        "package_id": closure.get("package_id"),
    }
    if receipt.get("coverage_digest") != digest(coverage_payload):
        return (
            "candidate_evaluation_coverage_mismatch",
            {"expected": digest(coverage_payload), "actual": receipt.get("coverage_digest")},
        )
    # 3. buy decisions correspond one-to-one with the receipt's qualified
    #    candidates; hold and exit decisions correspond one-to-one with the
    #    receipt's holding signals.  A holdings-only package has an empty
    #    candidate list but a real holding binding and still publishes
    #    (issue #723).  Audit r2: the receipt's COUNTS must agree with its
    #    own content too - qualified_count equals the buy-decision count,
    #    evaluated_count equals the evaluated-universe size; a fabricated
    #    count refuses the day even when the bindings themselves match.
    emitted_buys = sorted(
        ({
            "opportunity_id": d.opportunity_id,
            "security_code": d.security_code,
        }
        for d in output.decision_set.decisions
        if d.entry is not None),
        key=_candidate_sort_key,
    )
    if receipt.get("qualified_count") != len(emitted_buys):
        return (
            "evaluation_count_mismatch",
            {"field": "qualified_count",
             "claimed": receipt.get("qualified_count"),
             "actual": len(emitted_buys)},
        )
    if receipt.get("evaluated_count") != len(receipt.get("evaluated_universe") or ()):
        return (
            "evaluation_count_mismatch",
            {"field": "evaluated_count",
             "claimed": receipt.get("evaluated_count"),
             "actual": len(receipt.get("evaluated_universe") or ())},
        )
    claimed_candidates = sorted(
        ({
            "opportunity_id": c.get("opportunity_id"),
            "security_code": c.get("security_code"),
        }
        for c in (receipt.get("evaluated_candidates") or ())
        if isinstance(c, dict)),
        key=_candidate_sort_key,
    )
    if emitted_buys != claimed_candidates:
        return (
            "candidate_evaluation_binding_mismatch",
            {"emitted": emitted_buys, "claimed": claimed_candidates},
        )
    emitted_holds = sorted(
        ({
            "opportunity_id": d.opportunity_id,
            "security_code": d.security_code,
        }
        for d in output.decision_set.decisions
        if d.entry is None
        and d.stage is not OpportunityStage.ENDED),
        key=_candidate_sort_key,
    )
    claimed_signals = sorted(
        ({
            "opportunity_id": c.get("opportunity_id"),
            "security_code": c.get("security_code"),
        }
        for c in (receipt.get("holding_signals") or ())
        if isinstance(c, dict)),
        key=_candidate_sort_key,
    )
    if emitted_holds != claimed_signals:
        return (
            "holding_signal_binding_mismatch",
            {"emitted": emitted_holds, "claimed": claimed_signals},
        )
    # Issue #723 comment-7117663809: every package exit_plan must match one
    # held_signal by opportunity_id (a reduce and a full exit share the
    # exit_plan stage; the held_signal's action alone tells them apart).
    # A stray exit_plan, or a held_signal with no decision, refuses the day.
    signal_ids = {
        str(sig.opportunity_id)
        for sig in (output.held_signals or ())
    }
    emitted_exits = sorted(
        str(d.opportunity_id)
        for d in output.decision_set.decisions
        if d.stage is OpportunityStage.EXIT_PLAN
    )
    unmatched = [oid for oid in emitted_exits if oid not in signal_ids]
    if unmatched:
        return (
            "exit_plan_signal_mismatch",
            {"unmatched_exit_plans": unmatched},
        )
    decided_ids = {
        str(d.opportunity_id)
        for d in output.decision_set.decisions
        if d.stage is not OpportunityStage.ENDED
    }
    orphan_signals = [oid for oid in signal_ids if oid not in decided_ids]
    if orphan_signals:
        return (
            "exit_plan_signal_mismatch",
            {"orphan_held_signals": sorted(orphan_signals)},
        )
    return None


class AccountFactsUnavailable(FactError):
    '''Ledger read failed: the file exists but cannot be opened, a
    permission error or a FactError was raised while materializing the
    snapshot.  The run blocks the whole day - it never fabricates empty
    holdings from an unreadable ledger (issue #723 comment-7119006547).'''

    def __init__(self, detail: str):
        super().__init__("ACCOUNT_FACTS_UNAVAILABLE", detail)


def _account_facts(
    state_dir: Path, account_id: str
) -> tuple[
    tuple[HeldPositionFact, ...],
    tuple[PendingSecurity, ...],
    str,
    str,
]:
    '''Read account facts from the paper ledger (M2 F9 + #723 pending).

    Returns (holdings, pending_securities, account_state, snapshot_digest).

    A ledger file that exists but cannot be read (permission, corrupt store or
    a FactError during snapshot) is a whole-day block - it never fabricates
    holdings.  Only a ledger file that does not exist AND an account with no
    recorded baseline returns empty holdings, marked account_state=
    FRESH_NO_BASELINE on the receipt.  pending_securities are the in-flight
    commitments (open buy orders, RESERVED buckets, unconfirmed fills) read
    separately from holdings, never merged into them.
    '''

    store_path = Path(state_dir) / "p12-paper-facts.sqlite"
    try:
        store_exists = store_path.is_file()
    except OSError as exc:
        raise AccountFactsUnavailable(
            f"ledger store path unreadable: {exc}"
        ) from exc
    if not store_exists:
        # Fresh deployment before the first baseline: empty holdings are a
        # real account state, never a read failure.
        return (), (), "FRESH_NO_BASELINE", digest({"fresh": account_id})
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
    except FactError as exc:
        raise AccountFactsUnavailable(
            f"ledger snapshot refused: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - unreadable ledger is a hard block
        raise AccountFactsUnavailable(
            f"ledger store unreadable: {type(exc).__name__}: {exc}"
        ) from exc

    holdings = tuple(
        HeldPositionFact(
            security_code=p.security_code,
            quantity=int(p.quantity),
            unavailable_reason=(
                "ledger records security_code/quantity only; holding_sessions, "
                "marked_return and prior_strategy_reduction_count are not "
                "separately materialized for this account"
            ),
        )
        for p in snapshot.projection.positions
        if int(p.quantity) > 0
    )

    # In-flight commitments (issue #723): open buy orders, RESERVED funding
    # buckets and unconfirmed fills.  Each names its security and source kind;
    # quantities come straight from the order/bucket record.
    pending: list[PendingSecurity] = []
    history = snapshot.history
    for order in getattr(history, "orders", ()) or ():
        side = str(getattr(order, "side", "")).lower()
        status = str(getattr(order, "status", "")).lower()
        remaining = max(
            0,
            int(getattr(order, "accepted_total_qty", 0) or 0)
            - int(getattr(order, "known_fill_qty", 0) or 0),
        )
        if side == "buy" and status != "terminal" and remaining > 0:
            pending.append(
                PendingSecurity(
                    security_code=order.security_code,
                    kind="open_buy_order",
                    quantity=remaining,
                )
            )
        unreported = int(getattr(order, "unreported_fill_qty", 0) or 0)
        needs_recon = bool(getattr(order, "needs_reconciliation", False))
        if unreported > 0 or needs_recon:
            pending.append(
                PendingSecurity(
                    security_code=order.security_code,
                    kind="unconfirmed_fill",
                    quantity=max(unreported, remaining),
                )
            )
    # RESERVED funding buckets: the bucket_securities binding names which
    # security a reserved bucket is held out for.
    try:
        binding = facts.funding_binding(context)
    except Exception:  # noqa: BLE001 - bindings are optional, never fatal
        binding = None
    if binding:
        securities = dict(binding.get("bucket_securities") or {})
        for _bucket_id, sec in securities.items():
            if sec:
                pending.append(
                    PendingSecurity(
                        security_code=str(sec),
                        kind="reserved_bucket",
                        quantity=0,
                    )
                )
    snapshot_digest = digest({
        "positions": [(p.security_code, int(p.quantity))
                      for p in snapshot.projection.positions],
        "pending": [(p.security_code, p.kind, p.quantity) for p in pending],
    })
    return holdings, tuple(pending), "OK", snapshot_digest


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
    next_session: date | None,
    account_id: str,
    account_mode: str = "synthetic",
    now: datetime | None = None,
    holdings: tuple[HeldPositionFact, ...] | None = None,
    sidecar_path: Path | None = None,
    decision_cutoff: datetime | None = None,
    publish_gate_config: Path | None = None,
    calendar_root: Path | None = None,
) -> dict:
    '''Execute one daily run and persist its receipt; returns the receipt.'''

    started = time.monotonic()
    moment = (now or datetime.now(EXCHANGE_TZ)).astimezone(EXCHANGE_TZ)
    store = DailyPackageStore(state_dir)
    receipt: dict[str, Any] = {
        "schema_version": RUN_SCHEMA,
        "run_id": _run_id(moment),
        "market_date": market_date.isoformat(),
        "next_session": next_session.isoformat() if next_session else None,
        "decision_cutoff": (
            decision_cutoff.isoformat() if decision_cutoff is not None else None
        ),
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

    # The decision cutoff is configured per use (e.g. forward-day-1 22:30+08),
    # carried with its timezone.  A run without it cannot fix the package
    # knowledge cutoff, so the whole day is blocked before any input read.
    if decision_cutoff is None or decision_cutoff.tzinfo is None:
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=(
                    "decision_cutoff is not configured with a timezone; the "
                    "package knowledge cutoff cannot be fixed"
                ),
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=None,
            ),
            market_date=market_date,
            missing_members=["decision_cutoff"],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt

    # Publish-time rule (issue #719 comment-7114915793, audit R2): a run before
    # the configured decision cutoff is a readiness precheck only.  It may
    # qualify the input and stage a receipt, but it never writes the publish
    # directory or produces a package.  Only a run at or after the cutoff
    # publishes; the package's published_at is the real publish moment, never
    # earlier than the cutoff.
    if moment < decision_cutoff:
        receipt["status"] = STATUS_PRECHECK
        receipt["precheck"] = {
            "reason": "run before configured decision cutoff; readiness only",
            "decision_cutoff": decision_cutoff.isoformat(),
            "earliest_publish_at": decision_cutoff.isoformat(),
        }
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt

    # Account facts (issue #723 comment-7119006547): an unreadable ledger is a
    # whole-day block, never an empty-holdings fabrication.  Only a missing
    # ledger file AND no recorded baseline returns empty holdings, marked
    # account_state=FRESH_NO_BASELINE.  pending_securities are the in-flight
    # commitments reported beside holdings, never merged into them.
    pending_securities: tuple[PendingSecurity, ...] = ()
    account_state = "OK"
    ledger_snapshot_digest = None
    if holdings is not None:
        resolved_holdings = holdings
    else:
        try:
            (
                resolved_holdings,
                pending_securities,
                account_state,
                ledger_snapshot_digest,
            ) = _account_facts(state_dir, account_id)
        except AccountFactsUnavailable as exc:
            _blocked_receipt(
                receipt,
                InputBlock(
                    reason_code="ACCOUNT_FACTS_UNAVAILABLE",
                    detail=str(exc),
                    checked_at=moment,
                    expected_market_date=market_date,
                    observed_market_date=None,
                ),
                market_date=market_date,
                missing_members=["account_facts"],
            )
            receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            store.write_run(receipt)
            return receipt
    t = stage(
        "holdings",
        t,
        positions=len(resolved_holdings),
        pending_securities=len(pending_securities),
    )
    receipt["account_facts"] = {
        "account_state": account_state,
        "holdings_count": len(resolved_holdings),
        "pending_securities_count": len(pending_securities),
        "snapshot_digest": ledger_snapshot_digest,
    }

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
    # Audit R5 (r2): a verified 1d payload that reads but carries NO
    # security-code column cannot name a listing universe - the whole day
    # blocks here rather than letting the runner treat the absence as an
    # open universe.  UNREADABLE/ABSENT flow to the runner as fail-closed
    # evidence (the input was verified but the fact could not be read -
    # every new candidate is then an input breach).
    if snapshot.listed_securities_status == "NO_CODE_COLUMN":
        t = stage("input", t, result="blocked",
                  listed_status="NO_CODE_COLUMN")
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail="verified 1d payload carries no security-code column; "
                "the listing universe cannot be established",
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=["payload/1d.parquet"],
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
    # The job completion time and the source's own knowledge_cutoff are
    # carried verbatim on the receipt.  Neither is used as a cutoff; a missing
    # source cutoff stays null with its reason recorded.
    receipt["input_timing"] = {
        # Frozen receipt timing fields (issue #719 comment-7112514921,
        # task #726): the price-close anchor (15:00), the full-day
        # volume/amount end (15:30), the source's real clock and the
        # pre-configured consumer cutoff are separate values.  15:00 never
        # stands in for volume completeness, and no two volume sets are spliced.
        "price_close_anchor": datetime.combine(
            snapshot.market_date, SESSION_CLOSE, tzinfo=EXCHANGE_TZ
        ).isoformat(),
        # Audit R1: 15:30 is not asserted as a fact; only the supplier's own
        # declared observation_end counts.  Absent that declaration the end
        # stays null with its reason, never filled by the session anchor.
        "full_day_volume_amount_end": (
            snapshot.full_day_volume_amount_observation_end.isoformat()
            if snapshot.full_day_volume_amount_observation_end
            else None
        ),
        "full_day_volume_amount_end_note": (
            None
            if snapshot.full_day_volume_amount_observation_end
            else "supplier did not declare a full_day_volume_amount_observation_end"
        ),
        # M2 v3: the source-declared volume scope and observation end carried
        # verbatim; the 15:30 constant above is the session clock anchor, this
        # is what the input actually declared.  UNKNOWN stays UNKNOWN - it is
        # never promoted, and a missing end never becomes FULL_DAY.
        "volume_scope": snapshot.volume_scope,
        "full_day_volume_amount_observation_end": (
            snapshot.full_day_volume_amount_observation_end.isoformat()
            if snapshot.full_day_volume_amount_observation_end
            else None
        ),
        "volume_member_breach": snapshot.volume_member_breach,
        "completed_at": (
            snapshot.completed_at.isoformat() if snapshot.completed_at else None
        ),
        "source_knowledge_cutoff": (
            snapshot.knowledge_cutoff.isoformat()
            if snapshot.knowledge_cutoff
            else None
        ),
        "source_knowledge_cutoff_note": (
            None
            if snapshot.knowledge_cutoff
            else "source did not declare a tz-aware knowledge_cutoff"
        ),
    }

    # Publish-time rule (issue #719 comment-7114915793): a hard required
    # member whose source knowledge clock lands after the configured decision
    # cutoff was not visible at the cutoff - the whole day is blocked rather
    # than published with post-cutoff information.
    if (
        snapshot.knowledge_cutoff is not None
        and snapshot.knowledge_cutoff > decision_cutoff
    ):
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=(
                    "source knowledge_cutoff "
                    + snapshot.knowledge_cutoff.isoformat()
                    + " is after the configured decision cutoff "
                    + decision_cutoff.isoformat()
                    + "; input not visible at the cutoff"
                ),
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=["source_knowledge_cutoff"],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt

    # M2 section 3: the three history floors are checked separately.  A floor
    # the qualified input cannot prove is a whole-day block, never a pass.
    failed_gates = [g for g in snapshot.history_gates if not g.satisfied]
    if failed_gates:
        reasons = [
            g.gate_id + (": " + g.unavailable_reason if g.unavailable_reason else "")
            for g in failed_gates
        ]
        t = stage("history", t, result="blocked", gates=reasons)
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail="daily input history floors not satisfied: " + "; ".join(reasons),
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=[g.gate_id for g in failed_gates],
        )
        receipt["history_gates"] = [
            {
                "gate_id": g.gate_id,
                "satisfied": g.satisfied,
                "observed_sessions": g.observed_sessions,
                "required_sessions": g.required_sessions,
                "unavailable_reason": g.unavailable_reason,
            }
            for g in snapshot.history_gates
        ]
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    receipt["history_gates"] = [
        {
            "gate_id": g.gate_id,
            "satisfied": g.satisfied,
            "observed_sessions": g.observed_sessions,
            "required_sessions": g.required_sessions,
            "unavailable_reason": g.unavailable_reason,
        }
        for g in snapshot.history_gates
    ]

    # M2 F11 / audit R7: each declared corporate-action / adjustment field is
    # checked individually against the input - never trusted by the
    # fields_complete flag alone.  Audit R7 (r2): each value must also be
    # VALID - a numeric field holding a non-numeric string, or exceptions
    # holding a non-list, blocks.  Two admitted input paths: the per-field
    # corporate-action fields, OR a valid effective_adjustment_factor alone.
    ca = snapshot.corporate_actions
    required_ca_fields = (
        "cash_dividend_per_share",
        "bonus_share_ratio",
        "exceptions",
    )

    def _ca_numeric(value) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value)
            except ValueError:
                return False
            return True
        return False

    ca_invalid_fields: list[str] = []
    ca_missing_fields: list[str] = []
    if ca is None:
        ca_missing_fields = list(required_ca_fields)
    else:
        # Path B: a valid effective_adjustment_factor alone satisfies F11.
        factor = ca.get("effective_adjustment_factor")
        factor_only_ok = factor is not None and _ca_numeric(factor)
        if factor is not None and not _ca_numeric(factor):
            ca_invalid_fields.append("effective_adjustment_factor")
        if not factor_only_ok:
            for field in ("cash_dividend_per_share", "bonus_share_ratio"):
                value = ca.get(field)
                if field not in ca or value is None:
                    ca_missing_fields.append(field)
                elif not _ca_numeric(value):
                    ca_invalid_fields.append(field)
            exc = ca.get("exceptions")
            if "exceptions" not in ca or exc is None:
                ca_missing_fields.append("exceptions")
            elif not isinstance(exc, list):
                ca_invalid_fields.append("exceptions")
        else:
            # The factor path still refuses declared-but-invalid siblings.
            for field in ("cash_dividend_per_share", "bonus_share_ratio"):
                value = ca.get(field)
                if value is not None and not _ca_numeric(value):
                    ca_invalid_fields.append(field)
            exc = ca.get("exceptions")
            if exc is not None and not isinstance(exc, list):
                ca_invalid_fields.append("exceptions")
        for field in ca.get("unmapped_fields") or ():
            ca_missing_fields.append(str(field))
    if ca_missing_fields or ca_invalid_fields:
        detail_parts = []
        if ca_missing_fields:
            detail_parts.append(
                "absent: " + ", ".join(sorted(set(ca_missing_fields)))
            )
        if ca_invalid_fields:
            detail_parts.append(
                "invalid value: " + ", ".join(sorted(set(ca_invalid_fields)))
            )
        detail = (
            "corporate-action/adjustment fields unqualified in the daily "
            "input (" + "; ".join(detail_parts) + ")"
        )
        t = stage("corporate_actions", t, result="blocked")
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=detail,
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=["corporate_actions"],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    receipt["corporate_actions"] = {
        k: v for k, v in ca.items() if k != "fields_complete"
    }

    # Issue #743 / M2 F2 (r2): next_session comes ONLY from the P10 forward
    # calendar - the pointer at calendar_root (default: the CN02 product
    # root; a job_root-local "calendar/" replica when present, for replay).
    # SSE and SZSE must agree; null, out-of-coverage or post-cutoff versions
    # block the whole day.  The retired manifest calendar.next_session and
    # dwd_trading_calendar members are never consulted.
    if calendar_root is None:
        job_local = Path(job_root) / "calendar"
        calendar_root = (
            job_local if job_local.is_dir() else DEFAULT_CALENDAR_ROOT
        )
    cal, cal_block, cal_receipt = load_forward_calendar(
        calendar_root, decision_cutoff=decision_cutoff
    )
    receipt.update(cal_receipt)
    if cal is None:
        t = stage("calendar", t, result="blocked", detail=cal_block.detail)
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=cal_block.detail,
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=[cal_block.missing_member],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    nxt, cal_err = calendar_next_session(cal, market_date)
    if cal_err is not None:
        t = stage("calendar", t, result="blocked", detail=cal_err.detail)
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=cal_err.detail,
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=[cal_err.missing_member],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    if next_session is not None and next_session != nxt:
        t = stage("calendar", t, result="blocked", caller=next_session.isoformat())
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail=(
                    "caller next_session "
                    + next_session.isoformat()
                    + " disagrees with the forward calendar next_session "
                    + nxt.isoformat()
                ),
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=["forward_calendar"],
        )
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    next_session = nxt
    receipt["next_session"] = next_session.isoformat()
    # BSE venue unavailability is a per-security fact the rule consumes -
    # it drops BSE-coded new candidates and alerts BSE holdings, never a
    # whole-day block.
    snapshot_bse_unavailable = (
        ("BSE forward calendar unavailable: " + str(cal.bse_reason))
        if cal.bse_status == "unavailable"
        else None
    )
    snapshot = dataclasses.replace(snapshot, bse_unavailable=snapshot_bse_unavailable)

    # P10 consumer sidecar (task #726): when present, each consumed member's
    # clocks must order source<=validated<=published<=available<=cutoff.  A
    # missing sidecar leaves the package publish time null in the receipt; a
    # REVISE/WITHDRAW event never becomes a same-day replacement package.
    sidecar_read = None
    if sidecar_path is not None:
        try:
            sidecar_read = read_sidecar(
                sidecar_path,
                consumer_now=moment,
                decision_cutoff=decision_cutoff,
                # Member paths are relative to the fixture root (the dir
                # that contains "synthetic/x.json"), one level above the
                # record's own directory.
                member_files_root=sidecar_path.parent.parent
                if sidecar_path.parent.name in ("synthetic", "fixtures")
                else sidecar_path.parent,
            )
        except SidecarError as exc:
            t = stage("sidecar", t, result="blocked", detail=str(exc))
            _blocked_receipt(
                receipt,
                InputBlock(
                    reason_code="INPUT_UNAVAILABLE",
                    detail="consumer sidecar not consumable: " + str(exc),
                    checked_at=moment,
                    expected_market_date=market_date,
                    observed_market_date=snapshot.market_date,
                ),
                market_date=market_date,
                missing_members=["consumer_sidecar"],
            )
            receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            store.write_run(receipt)
            return receipt
        receipt["consumer_receipt"] = sidecar_read.to_dict()
        receipt["source_published_at"] = (
            sidecar_read.published_at[1] or sidecar_read.published_at[0]
        )
        if sidecar_read.event == "WITHDRAW":
            # A source withdraw is recorded on the receipt; the day's advice
            # still comes from the last qualified publish, never a same-day
            # replacement (frozen decision b).
            t = stage("sidecar", t, result="withdraw_recorded")
        elif sidecar_read.disposition == DISPOSITION_INPUT_OR_CLOCK_BLOCKED:
            t = stage(
                "sidecar", t, result="blocked",
                reasons=list(sidecar_read.blocked_reasons),
            )
            _blocked_receipt(
                receipt,
                InputBlock(
                    reason_code="INPUT_UNAVAILABLE",
                    detail="consumer sidecar clocks incomplete: "
                    + "; ".join(sidecar_read.blocked_reasons),
                    checked_at=moment,
                    expected_market_date=market_date,
                    observed_market_date=snapshot.market_date,
                ),
                market_date=market_date,
                missing_members=["consumer_sidecar"],
            )
            receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            store.write_run(receipt)
            return receipt
        else:
            t = stage("sidecar", t, result=sidecar_read.disposition)
    else:
        # No sidecar: the receipt's publish time stays null - P12 records only
        # what it actually read, never a file mtime as a publish time.
        receipt["consumer_receipt"] = None
        receipt["source_published_at"] = None

    try:
        output: RunnerOutput = runner.run(
            RunnerRequest(
                snapshot=snapshot,
                next_session=next_session,
                holdings=resolved_holdings,
                pending_securities=pending_securities,
                published_at=moment,
                # The job mints this run's identity; the runner echoes it in
                # its evaluation receipt so the gate binds this exact run.
                run_id=receipt["run_id"],
                decision_cutoff=decision_cutoff,
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
                "metric": item.metric,
                "window_start": item.window_start,
                "window_end": item.window_end,
                "regime_version": item.regime_version,
            }
            for item in output.exclusions
        ]
    # Frozen cross-regime dispositions for open positions: HOLD_AND_ALERT only
    # where the holding evaluation depends on the missing volume input; a
    # qualified volume-independent exit still publishes its EXIT_PLAN while
    # the boundary reason is recorded here verbatim.
    if output.holding_directives:
        receipt["holding_directives"] = [
            {
                "security_code": item.security_code,
                "action": item.action,
                "reason_code": item.reason_code,
                "detail": item.detail,
            }
            for item in output.holding_directives
        ]
    # Issue #723 comment-7117663809: the producer's held_signals ride the run
    # receipt verbatim - action and target_maximum_position are the E1
    # adapter's only way to tell a reduce exit_plan from a full exit.
    if output.held_signals:
        receipt["held_signals"] = [
            {
                "security_code": item.security_code,
                "opportunity_id": item.opportunity_id,
                "action": item.action,
                "target_maximum_position": item.target_maximum_position,
            }
            for item in output.held_signals
        ]

    decision_set = decision_set_to_dict(decisions)

    # Run-seam publish gate (issue #719): the runner's own evaluation receipt
    # and source closure must pass coverage/producer/candidate checks before
    # the package publishes; any breach is a whole-day block, receipt only.
    gate_config = publish_gate_config or PUBLISH_GATE_CONFIG
    trusted_ids = _trusted_producer_ids(gate_config, account_mode)
    gate_block = _publish_gate_block(
        output,
        trusted_ids=trusted_ids,
        expected_run_id=receipt["run_id"],
        expected_manifest_sha256=snapshot.core_manifest_sha256,
    )
    if gate_block is not None:
        reason_code, detail = gate_block
        t = stage("publish_gate", t, result="blocked", reason_code=reason_code)
        _blocked_receipt(
            receipt,
            InputBlock(
                reason_code="INPUT_UNAVAILABLE",
                detail="publish gate refused the run: " + reason_code,
                checked_at=moment,
                expected_market_date=market_date,
                observed_market_date=snapshot.market_date,
            ),
            market_date=market_date,
            missing_members=["evaluation_receipt"],
        )
        receipt["publish_gate"] = {"reason_code": reason_code, **detail}
        # Audit R11: carry the runner's own disposition and diagnostics
        # verbatim onto the P12 run receipt when the gate refuses the run.
        if output.disposition is not None:
            receipt["disposition"] = output.disposition
        if output.diagnostics is not None:
            receipt["diagnostics"] = output.diagnostics
        receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        store.write_run(receipt)
        return receipt
    # The runner's own evaluation receipt and source closure are carried on
    # the P12 run receipt verbatim - P12 transports them, never constructs or
    # rewrites them; the client never rebuilds them either.
    receipt["evaluation_receipt"] = output.evaluation_receipt
    receipt["source_closure"] = output.source_closure

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
            # The runner-produced source closure rides inside input evidence
            # verbatim; it attests the exact input closure this package came from.
            "source_closure": output.source_closure,
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
    # Issue #723 count semantics (R10/R11): a package that holds live
    # positions but adds no new buy is publishable and is NOT an empty
    # advice.  ``empty_advice`` is reserved for the package whose only record
    # is the G1 no-qualified-candidate noop (no live decision at all); a
    # holdings-only package reads ``holdings_only`` so the workbench shows
    # "今天无新买入，持仓继续持有" rather than "今天无须下单".
    live = [d for d in decisions.decisions if d.stage is not OpportunityStage.ENDED]
    if entry_plans:
        disposition = "publishable"
    elif live:
        disposition = "holdings_only"
    else:
        disposition = "empty_advice"
    # F10: P11-bound trend observations carried verbatim into the receipt;
    # abstained observations are kept, not dropped.
    if output.trend_observations:
        receipt["trend_observations"] = [
            {
                "opportunity_id": obs.opportunity_id,
                "observation_digest": obs.observation_digest,
                "applicability": obs.applicability,
                "raw_value": obs.raw_value,
            }
            for obs in output.trend_observations
        ]
    receipt["package"] = {
        "package_id": outcome.package.package_id,
        "file_sha256": outcome.package.file_sha256,
        "decision_count": len(decisions.decisions),
        "entry_plan_count": entry_plans,
        "admission_disposition": disposition,
        "decision_set_digest": digest(decision_set),
    }
    # The receipt's own publish time is the run moment P12 committed the
    # package; the P10 supply-side publish time (source_published_at) comes
    # only from a real sidecar clock, else null - never a file mtime.
    receipt["published_at"] = moment.isoformat()
    if receipt.get("source_published_at") is None and sidecar_read is not None:
        receipt["source_published_at"] = (
            sidecar_read.published_at[1] or sidecar_read.published_at[0]
        )
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
    parser.add_argument(
        "--account-mode",
        default="synthetic",
        help="account mode binding the trusted producer list (synthetic|simulation|live)",
    )
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--next-session",
        type=date.fromisoformat,
        default=None,
        help="optional; the governed calendar decides next_session (M2 F2)",
    )
    parser.add_argument("--runner", default="synthetic-replay")
    parser.add_argument("--candidates", type=Path)
    parser.add_argument(
        "--decision-cutoff",
        type=lambda v: datetime.fromisoformat(v),
        default=None,
        help="configured tz-aware decision cutoff, e.g. 2026-11-20T22:30:00+08:00",
    )
    parser.add_argument(
        "--calendar-root",
        type=Path,
        default=None,
        help="P10 forward-calendar product root (pointer + versioned "
        "descriptors); default: the CN02 product path, or a "
        "job_root-local 'calendar/' replica when present",
    )
    parser.add_argument(
        "--sidecar",
        type=Path,
        default=None,
        help="optional P10 daily consumer sidecar record for this run",
    )
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
        account_mode=args.account_mode,
        sidecar_path=args.sidecar,
        decision_cutoff=args.decision_cutoff,
        calendar_root=args.calendar_root,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    # A blocked or precheck run is a successful execution of the fail-closed
    # contract - the timer unit must not record it as a service failure.
    # Only an actual FAILED receipt (a crash or a write that never
    # completed) returns nonzero (audit r2).
    return 0 if receipt["status"] != STATUS_FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
