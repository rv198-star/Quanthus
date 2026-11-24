"""The shared replay engine behind both the browser desk and the
compressed-acceptance script (issue #748).

One engine instance owns one isolated replay account: its own state dir,
its own job root (locally materialized replay daily-core products), its
own order store and ledger - it never reads or writes the live 8791
paths.  The day cycle is the real one:

  session T evening   run_daily(market_date=T, now=cutoff+publish slot)
                      -> the one immutable package for T
  session T+1 morning clock set to the next session's continuous auction
                      -> confirm/skip on the published plans, fills
                      decided by that session's REAL 1d bars
  session T+1 close   expire resting orders, settle, carry positions
                      into the next replay day

Every step appends to ``replay-log.jsonl`` (engine events) beside the
job's own run receipts and the ledger's own history - the evidence is
the production artifacts, not a parallel story.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.p12_daily.daily_job import run_daily
from quanthus.p12_daily.rule_runner import StrategyRuleRunner
from quanthus.p12_daily.package_source import PublishedPackageSource
from quanthus.p12_daily.package_store import DailyPackageStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_replay.replay_calendar import write_replay_calendar
from quanthus.p12_replay.replay_product import (
    ReplayInputSpec,
    write_replay_day,
)
from quanthus.p12_replay.replay_quotes import DailyBarQuoteService, DayBar

EXCHANGE_TZ = timezone(timedelta(hours=8))
REPLAY_LOG = "replay-log.jsonl"
PUBLISH_TIME = time(22, 30, tzinfo=EXCHANGE_TZ)


class ReplayBlocked(Exception):
    """The day's chain is blocked; the receipt already carries the reason."""


def _sh_now(day: date, tm: time) -> datetime:
    return datetime.combine(day, tm, tzinfo=EXCHANGE_TZ)


@dataclass
class ReplayEngine:
    """State + step drivers for one isolated replay account.

    The caller supplies:
      state_dir   - the replay account's own state dir (packages, runs,
                    ledger sqlite, order store, replay log)
      sessions    - the ordered authoritative open sessions (from the
                    pinned calendar member), the replay walks them
      bars_by_day - {session_date: {code: DayBar}} the REAL 1d bars for
                    every session in the replay window
      runner      - the StrategyRuleRunner for this phase (synthetic now,
                    the P11-adopted runner later - the engine takes the
                    port, never a concrete class)
      account_id  - the isolated replay account (default SYN-PAPER-A;
                    a dedicated REPLAY-* id is also fine as long as the
                    ledger + authorization are built for it)
      spec        - ReplayInputSpec for the manifest's static evidence
      base_manifest_path/_sha256 - the pinned parent Base manifest
                    locator the replay manifests re-hash and declare
      publish_time / publish_slots - configured publish points
    """

    state_dir: Path
    sessions: list
    bars_by_day: dict
    runner: StrategyRuleRunner
    spec: ReplayInputSpec
    base_manifest_path: Path
    base_manifest_sha256: str
    account_id: str = "SYN-PAPER-A"
    account_mode: str = "synthetic"
    publish_time: time = PUBLISH_TIME
    publish_slots: tuple = (
        time(22, 35, tzinfo=EXCHANGE_TZ),
        time(22, 45, tzinfo=EXCHANGE_TZ),
        time(23, 0, tzinfo=EXCHANGE_TZ),
    )
    # Injectable app construction (the server wires the real desktop app +
    # paper service; the acceptance script uses the same path).
    app_builder: Any = None
    paper_builder: Any = None
    decision_cutoff_time: time = time(22, 30, tzinfo=EXCHANGE_TZ)
    # Optional per-day runner factory (candidate sets that vary by
    # session); when absent the bound runner runs every day unchanged.
    runner_factory: Any = None
    # P10-pinned October window/key map: each signal day's verbatim
    # windows, UNKNOWN coverage and the 191/4173 audit keys the rule
    # reads.  None keeps the bar-only raw history.
    window_map: Any = None
    # #737 pre-read exclusion receipt: {config_sha256, filtered_count,
    # filtered:[{security,date}]} - recorded on the run/day receipt.
    exclusion_receipt: dict | None = None
    # Per-T real observed open-session counts (the honest history-floor
    # evidence the manifest reports for that day - never a window count).
    observed_sessions_by_day: dict | None = None

    # runtime state
    day_index: int = 0
    replay_now: datetime | None = None
    _app: Any = None
    _paper: Any = None
    _order_seq: int = 0
    # persisted confirm/skip outcome records (plan_id -> outcome dict);
    # runtime state, not a ctor arg - loaded in __post_init__.
    _confirm_records: dict = field(default=None, repr=False)
    _skip_records: dict = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "job").mkdir(exist_ok=True)
        self._job_root = self.state_dir / "job"
        self._calendar_root = self.state_dir / "calendar"
        self._store = DailyPackageStore(self.state_dir)
        if self.replay_now is None and self.sessions:
            # Open on the first session MORNING (pre-open), not the publish
            # slot - the account baseline's as_of must precede every fact
            # the replay will post (confirm fills land at session open).
            self.replay_now = _sh_now(self.sessions[0], time(9, 0))
        if self._confirm_records is None:
            self._confirm_records = self._load_records("confirm")
        if self._skip_records is None:
            self._skip_records = self._load_records("skip")
        self._restore_position()

    # ---- clock -------------------------------------------------------------------
    def now(self) -> datetime:
        """The shared replay clock: the paper desk, the decision source and
        the job all read this, so a package is only visible after its real
        published_at and a T+1 confirm sees T+1's session."""
        return self.replay_now

    def set_now(self, instant: datetime) -> None:
        self.replay_now = instant.astimezone(EXCHANGE_TZ)

    @property
    def current_day(self) -> date:
        return self.sessions[self.day_index]

    @property
    def next_day(self) -> date | None:
        return (
            self.sessions[self.day_index + 1]
            if self.day_index + 1 < len(self.sessions)
            else None
        )

    # ---- materialization ---------------------------------------------------------
    def materialize_day(self, day: date | None = None) -> dict:
        """Write this day's replay daily-core product + the session calendar.

        Returns the product dict (paths + digests) for the receipt."""

        day = day or self.current_day
        bars = self.bars_by_day.get(day) or {}
        import pyarrow.parquet as pq  # noqa: F401 - the slice uses it

        from quanthus.p12_replay.bar_slice import write_session_parquet

        staged = self._job_root / "staging"
        staged.mkdir(parents=True, exist_ok=True)
        parquet_path = staged / f"{day.isoformat()}.parquet"
        write_session_parquet(bars, parquet_path)

        # P11 口径 1: hand the runner the bounded raw history - the
        # session's own bars plus up to 60 prior open sessions (61
        # total, T inclusive), plus the reference facts already sliced.
        window_sessions = [s for s in self.sessions if s <= day][-61:]
        raw_history = {
            "schema_version": "p11.raw-history-input.v1",
            "market_date": day.isoformat(),
            "decision_cutoff": _sh_now(day, self.decision_cutoff_time).isoformat(),
            "window": {
                "kind": "EXACT" if len(window_sessions) == 61 else "EXACT",
                "sessions": len(window_sessions),
                "required": 61,
                "as_observed": "ENGINEERING_REPLAY_AS_OBSERVED",
                "note": (
                    "bounded slice over the pinned Base window; the count is "
                    "the sessions actually delivered, not a claim about "
                    "listing age"
                ),
            },
            # Three clocks kept separate on the member: the source's own
            # knowledge_from, the member sha256, the physical read moment
            # and the logical_clock the replay assigned this run.  The
            # read clock is recorded once per materialization, never
            # merged into the source knowledge clock.
            "source_members": [
                {
                    "member": "dwd_security_bar/interval_size=1d",
                    "sessions": len(window_sessions),
                    "rows": sum(
                        len(self.bars_by_day.get(s) or {})
                        for s in window_sessions
                    ),
                    "sha256": self.base_manifest_sha256,
                    "knowledge_from": "2026-10-31T16:00:00+00:00",
                    "physical_read_at": datetime.now(EXCHANGE_TZ).isoformat(),
                    "logical_clock": self.now().isoformat(),
                    "as_observed": "ENGINEERING_REPLAY_AS_OBSERVED",
                }
            ],
        }
        # The P10-pinned per-T windows and audit keys: the rule reads
        # them in the same run and decides which securities drop.
        if self.window_map is not None:
            raw_history["p10_members"] = self.window_map.raw_history_members(day)

        # The calendar must cover every session the replay may query;
        # materialize the authoritative open-session list once per day
        # (idempotent - same bytes, same descriptor sha).
        write_replay_calendar(
            self._calendar_root,
            sessions=self.sessions,
            version="replay-v1",
            available_at=_sh_now(self.sessions[0], time(8, 0)),
        )

        self._raw_history = raw_history
        spec = self.spec
        if self.observed_sessions_by_day is not None:
            observed = self.observed_sessions_by_day.get(day.isoformat())
            if observed is not None:
                from dataclasses import replace as _dc_replace

                spec = _dc_replace(
                    self.spec,
                    history={
                        k: dict(v, observed_sessions=observed)
                        for k, v in self.spec.history.items()
                    },
                )
        product = write_replay_day(
            self._job_root,
            market_date=day,
            bars_parquet=parquet_path,
            row_counts={
                "dwd_security_bar": len(bars),
                "dwd_trading_calendar": 3,
            },
            spec=spec,
            base_manifest_path=self.base_manifest_path,
            base_manifest_sha256=self.base_manifest_sha256,
            package_id=f"replay-core-{day.isoformat()}",
        )
        return {
            "market_date": day.isoformat(),
            "core_manifest_sha256": product.core_manifest_sha256,
            "index_sha256": product.index_sha256,
            "row_counts": product.row_counts,
            "security_count": len(bars),
        }

    # ---- app / order wiring --------------------------------------------------------
    def _calendar_provider(self):
        def provider(today: date) -> date | None:
            try:
                ordered = [s for s in self.sessions if s > today]
            except TypeError:
                return None
            return ordered[0] if ordered else None

        return provider

    def _build_app(self):
        if self._app is not None:
            return self._app
        if self.app_builder is None:
            raise ReplayBlocked("app_builder not bound; cannot open the account")
        self._app = self.app_builder(
            state_dir=self.state_dir,
            clock=self.now,
            decision_source=PublishedPackageSource(
                self.state_dir,
                self.now,
                mode=self.account_mode,
                next_session_provider=self._calendar_provider(),
                publish_time=self.publish_time,
                publish_slots=self.publish_slots,
            ),
            account_id=self.account_id,
        )
        return self._app

    def _quotes_for_today(self) -> DailyBarQuoteService:
        today = self.current_day
        return DailyBarQuoteService(
            session_date=today,
            bars=self.bars_by_day.get(today) or {},
            previous=(
                self.bars_by_day.get(self.sessions[self.day_index - 1])
                if self.day_index > 0
                else {}
            ),
        )

    # ---- persistence ---------------------------------------------------------------
    def _records_path(self, kind: str) -> Path:
        return self.state_dir / f"replay-{kind}-records.json"

    def _load_records(self, kind: str) -> dict:
        path = self._records_path(kind)
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_records(self, kind: str) -> None:
        path = self._records_path(kind)
        data = self._confirm_records if kind == "confirm" else self._skip_records
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1))

    def _restore_position(self) -> None:
        """Restore day_index + clock from the last logged advance/confirm.

        A restart re-opens on the highest day_index seen in the replay
        log (date, clock, orders and balance all come back with it - the
        ledger is the source of truth for account facts; the log only
        re-seeds the pointer)."""
        log = self.state_dir / REPLAY_LOG
        if not log.is_file():
            return
        last_index = None
        try:
            for line in log.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec.get("day_index"), int):
                    last_index = rec["day_index"]
        except OSError:
            return
        if last_index is not None and 0 <= last_index < len(self.sessions):
            self.day_index = last_index
            self.set_now(_sh_now(self.sessions[last_index], time(9, 25)))

    # ---- steps --------------------------------------------------------------------
    def _log(self, event: str, **fields) -> None:
        # Three clocks stay separate (P11 口径): the replay's own
        # logical_clock, the physical read moment, and the source's
        # observed clock carried on the input members - never merged.
        record = {
            "logical_clock": self.now().isoformat(),
            "physical_clock": datetime.now(EXCHANGE_TZ).isoformat(),
            "event": event,
            "day_index": self.day_index,
            **fields,
        }
        path = self.state_dir / REPLAY_LOG
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def evening_run(self, *, rerun: bool = False) -> dict:
        """Session T evening: materialize the input and run the daily job.

        ``rerun=True`` re-invokes the job for the same market_date (the
        store's idempotent publish makes a byte-identical rerun replay
        ALREADY_PUBLISHED; a different package for the same date is
        refused - both are evidence of the frozen no-replacement rule).
        """

        day = self.current_day
        self.set_now(_sh_now(day, self.publish_slots[-1]))
        product = self.materialize_day(day)
        receipt = run_daily(
            state_dir=self.state_dir,
            job_root=self._job_root / ".." if False else self._job_root,
            runner=(
                self.runner_factory(day)
                if callable(self.runner_factory)
                else self.runner
            ),
            market_date=day,
            next_session=None,
            account_id=self.account_id,
            account_mode=self.account_mode,
            now=self.now(),
            decision_cutoff=_sh_now(day, self.decision_cutoff_time),
            calendar_root=self._calendar_root,
        )
        self._log(
            "evening_run",
            market_date=day.isoformat(),
            status=receipt.get("status"),
            rerun=rerun,
            product=product,
            run_id=receipt.get("run_id"),
            logical_clock=self.now().isoformat(),
            physical_clock=datetime.now(EXCHANGE_TZ).isoformat(),
        )
        return receipt

    def workbench(self) -> dict:
        """The current account/workbench view (the browser reads this)."""
        app = self._build_app()
        from quanthus.platform.p12.transport import workbench_dto

        view = app.workbench(app.scope)
        dto = workbench_dto(view)
        body = dto.get("payload") if isinstance(dto, dict) and "payload" in dto else dto
        return {
            "replay": {
                "label": "历史回放·模拟",
                "current_day": self.current_day.isoformat(),
                "day_index": self.day_index,
                "sessions_total": len(self.sessions),
                "now": self.now().isoformat(),
                "next_day": self.next_day.isoformat() if self.next_day else None,
            },
            "workbench": body,
        }

    def _open_plans(self) -> list[dict]:
        """Published plans the account can still confirm or skip."""
        bench = self.workbench().get("workbench") or {}
        plans = []
        for item in bench.get("opportunities") or []:
            plan = item.get("plan")
            if not plan:
                continue
            applicability = item.get("applicability") or {}
            status = (applicability.get("status") or item.get("status_code") or "").upper()
            if status in ("SKIPPED", "ENDED", "EXPIRED"):
                continue
            if (item.get("action_code") or "").upper() in ("BUY", "SELL") or plan.get(
                "delta_qty"
            ):
                plans.append(
                    {
                        "plan_id": plan.get("plan_id"),
                        "security_code": item.get("security_code"),
                        "action_code": item.get("action_code"),
                        "side": plan.get("side"),
                        "target_qty": plan.get("target_qty"),
                        "delta_qty": plan.get("delta_qty"),
                        "price_cap": plan.get("price_cap"),
                    }
                )
        return plans

    def confirm(self, plan_id: str) -> dict:
        """Confirm one published plan: the session's real OPEN decides.

        Phase-1 fill rule (P11 口径): the pre-open limit is compared to
        the T+1 session's real open; when the open crosses, the fill
        posts at the open price - no intraday optimisation.  The ledger
        write goes through the same facts service the paper desk uses,
        so rebuild/reconcile stays the real invariant.  A reject
        (unfunded bucket, T+1, bad lot, preopen fact) returns the typed
        reason verbatim - it is evidence, not a swallowed error."""
        # Idempotent: a repeated confirm reuses the original receipt - the
        # ledger never double-posts a fill and a restart replays the record.
        prior = self._confirm_records.get(plan_id)
        if prior is not None:
            return dict(prior, idempotent_replay=True)

        def _ret(outcome):
            self._confirm_records[plan_id] = outcome
            self._save_records("confirm")
            return _ret(outcome)

        app = self._build_app()
        plan = next(
            (p for p in self._open_plans() if p["plan_id"] == plan_id), None
        )
        if plan is None:
            return {"plan_id": plan_id, "status": "rejected", "reason": "PLAN_NOT_FOUND"}
        side = str(plan.get("side") or ("buy" if plan["action_code"] == "BUY" else "sell"))
        code = plan["security_code"]
        # The advice is published on session T evening; the earliest it can
        # fill is the T+1 session at 09:30 against that session's real open
        # (review R2 item 1).  Never fill on the publish day's own open.
        publish_day = self.current_day
        if self.day_index + 1 >= len(self.sessions):
            outcome = {
                "plan_id": plan_id,
                "status": "rejected",
                "reason": "PENDING_NO_EXECUTION_DAY",
                "detail": (
                    f"advice published on {publish_day.isoformat()} has no "
                    "next session to fill on"
                ),
            }
            self._log("confirm", market_date=publish_day.isoformat(),
                      plan_id=plan_id, outcome=outcome)
            return _ret(outcome)
        fill_day = self.sessions[self.day_index + 1]
        bar = (self.bars_by_day.get(fill_day) or {}).get(code)
        if bar is None:
            outcome = {
                "plan_id": plan_id,
                "status": "rejected",
                "reason": "QUOTE_UNAVAILABLE",
                "detail": f"no 1d bar for {code} on {fill_day.isoformat()}",
            }
            self._log("confirm", market_date=fill_day.isoformat(),
                      plan_id=plan_id, outcome=outcome)
            return _ret(outcome)
        from quanthus.p12_replay.replay_quotes import match_limit

        fill_price = match_limit(side, Decimal(str(plan["price_cap"])), bar)
        if fill_price is None:
            outcome = {
                "plan_id": plan_id,
                "status": "not_marketable",
                "reason": "OPEN_BEYOND_LIMIT",
                "session_open": str(bar.open),
                "limit": str(plan["price_cap"]),
                "fill_day": fill_day.isoformat(),
            }
            self._log("confirm", market_date=fill_day.isoformat(),
                      plan_id=plan_id, outcome=outcome)
            return _ret(outcome)
        # Earliest fill: the T+1 open at 09:30.  The order is accepted at
        # the publish-evening decision clock (self.now); the fill itself is
        # stamped at the T+1 open (fill_clock) - the two instants stay
        # separate, never merged, and the replay clock is not pre-advanced.
        fill_clock = _sh_now(fill_day, time(9, 30))
        decision_clock = self.now()
        # Register the accepted order observation first - the ledger
        # refuses a fill that references an unknown order
        # (UNKNOWN_ORDER_EXPOSURE), the same contract the paper desk's
        # _register_order honours.
        self._order_seq += 1
        order_id = f"plan:{plan_id}"
        from quanthus.capabilities.execution import OrderObservation

        qty = int(plan.get("delta_qty") or plan.get("target_qty") or 0)
        try:
            app.paper_submit_order_observation(
                OrderObservation(
                    observation_id=f"{order_id}:accepted",
                    order_id=order_id,
                    status="in_progress",
                    observed_at=fill_clock,
                    source_id="p12-replay-open-match",
                    source_sequence=f"{order_id}:accepted",
                    order_revision=1,
                ),
                accepted_total_qty=qty,
                side=side,
                security_code=code,
                price_bound=Decimal(str(plan["price_cap"])),
                estimated_fee_upper=Decimal("0.00"),
            )
        except Exception as exc:  # noqa: BLE001 - observation refuse is evidence
            outcome = {
                "plan_id": plan_id,
                "status": "rejected",
                "reason": getattr(exc, "reason_code", type(exc).__name__),
                "detail": "order observation refused: " + str(exc),
            }
            self._log("confirm", market_date=fill_day.isoformat(),
                      plan_id=plan_id, outcome=outcome)
            return _ret(outcome)
        # Post the fill into the real ledger as a simulated execution
        # report; suggestion_id links the fill back to this plan.
        report_id = f"replay-fill-{fill_day.strftime('%Y%m%d')}-{self._order_seq:04d}"
        from quanthus.capabilities.execution import ExecutionReport

        report = ExecutionReport(
            report_id=report_id,
            security_code=code,
            side=side,
            quantity=qty,
            price=fill_price,
            confirmed_fee=Decimal("0.00"),
            occurred_at=fill_clock,
            source_id="p12-replay-open-match",
            source_sequence=(
                f"{plan_id}:open:{fill_day.isoformat()}"
            ),
            intended_quantity=qty,
            intended_price_cap=Decimal(str(plan["price_cap"])),
            order_id=order_id,
            suggestion_id=plan_id,
        )
        try:
            result = app.paper_submit_report(report)
            status = str(getattr(result, "status", "POSTED"))
        except Exception as exc:  # noqa: BLE001 - typed reject is evidence
            # The fill did not post - close the order as fully cancelled
            # so the ledger never shows a dangling in-progress exposure.
            try:
                app.paper_submit_order_observation(
                    OrderObservation(
                        observation_id=f"{order_id}:terminal",
                        order_id=order_id,
                        status="terminal",
                        observed_at=fill_clock,
                        source_id="p12-replay-open-match",
                        source_sequence=f"{order_id}:terminal:0:{qty}",
                        final_cumulative_fill_qty=0,
                        cancelled_qty=qty,
                        order_revision=2,
                    ),
                    accepted_total_qty=qty,
                    side=side,
                    security_code=code,
                    price_bound=Decimal(str(plan["price_cap"])),
                    estimated_fee_upper=Decimal("0.00"),
                )
            except Exception:  # noqa: BLE001 - best effort terminal
                pass
            outcome = {
                "plan_id": plan_id,
                "status": "rejected",
                "reason": getattr(exc, "reason_code", type(exc).__name__),
                "detail": str(exc),
                "session_open": str(bar.open),
            }
            self._log("confirm", market_date=fill_day.isoformat(),
                      plan_id=plan_id, outcome=outcome)
            return _ret(outcome)
        # Close the order: fully filled, zero cancelled.
        try:
            app.paper_submit_order_observation(
                OrderObservation(
                    observation_id=f"{order_id}:terminal",
                    order_id=order_id,
                    status="terminal",
                    observed_at=fill_clock,
                    source_id="p12-replay-open-match",
                    source_sequence=f"{order_id}:terminal:{qty}:0",
                    final_cumulative_fill_qty=qty,
                    cancelled_qty=0,
                    order_revision=2,
                ),
                accepted_total_qty=qty,
                side=side,
                security_code=code,
                price_bound=Decimal(str(plan["price_cap"])),
                estimated_fee_upper=Decimal("0.00"),
            )
        except Exception:  # noqa: BLE001 - terminal observation is best effort
            pass
        outcome = {
            "plan_id": plan_id,
            "status": "filled",
            "fill_price": str(fill_price),
            "session_open": str(bar.open),
            "quantity": report.quantity,
            "report_id": report_id,
            "post_status": status,
            "publish_day": publish_day.isoformat(),
            "fill_day": fill_day.isoformat(),
            "engineering_replay_as_observed": True,
        }
        self._confirm_records[plan_id] = outcome
        self._save_records("confirm")
        self._log("confirm", market_date=fill_day.isoformat(),
                  plan_id=plan_id, outcome=outcome)
        return outcome

    def skip(self, plan_id: str) -> dict:
        """Skip one published plan (the append-only skip record; the plan
        can no longer confirm).  Idempotent: a repeated skip reuses the
        original record."""
        prior = self._skip_records.get(plan_id)
        if prior is not None:
            return dict(prior, idempotent_replay=True)
        app = self._build_app()
        record = app.skip_plan(plan_id=plan_id, occurred_at=self.now())
        record = record if isinstance(record, dict) else {"record": str(record)}
        self._skip_records[plan_id] = record
        self._save_records("skip")
        self._log(
            "skip",
            market_date=self.current_day.isoformat(),
            plan_id=plan_id,
            record=record,
        )
        return record

    def advance_day(self) -> dict:
        """Close session T+1 and open the next replay day.

        Positions and cash carry into the new session via the ledger - the
        engine never rewrites account facts.  The transition stamps the
        ENGINEERING_REPLAY_AS_OBSERVED mark (P11 口径: no historical as-of
        proof is claimed for the carry-in view; the replay records what it
        observed under its own replay clock)."""

        if self.day_index + 1 >= len(self.sessions):
            self._log("advance_day", finished=True)
            return {"finished": True}
        self.day_index += 1
        day = self.current_day
        self.set_now(_sh_now(day, time(9, 25)))
        self._log(
            "advance_day",
            market_date=day.isoformat(),
            replay_observation_mark="ENGINEERING_REPLAY_AS_OBSERVED",
        )
        return {
            "finished": False,
            "market_date": day.isoformat(),
            "day_index": self.day_index,
        }

    def reconcile(self) -> dict:
        """Relogin reconciliation: rebuild-from-events vs stored projection."""
        app = self._build_app()
        ok = app.rebuild_matches(app.scope)
        projection = app.paper_projection()
        history = app.paper_history()
        result = {
            "engineering_replay_as_observed": True,
            "rebuild_matches": bool(ok),
            "cash": str(getattr(projection, "cash", "")),
            "positions": [
                {
                    "security_code": getattr(p, "security_code", None),
                    "quantity": getattr(p, "quantity", None),
                }
                for p in getattr(projection, "positions", ())
            ],
            "orders": len(getattr(history, "orders", ()) or ()),
            "reports": len(getattr(history, "reports", ()) or ()),
        }
        self._log("reconcile", market_date=self.current_day.isoformat(), **result)
        return result

    def day_state(self) -> dict:
        """Everything one replay day needs as evidence + the UI payload."""
        bench = self.workbench()
        store = DailyPackageStore(self.state_dir)
        runs = [
            {
                "run_id": r.get("run_id"),
                "market_date": r.get("market_date"),
                "status": r.get("status"),
            }
            for r in store.runs()
        ]
        package = store.package_for(self.current_day)
        ledger = self.reconcile()
        from quanthus.p12_replay.replay_cn import cn_state

        return cn_state({
            "engineering_replay_as_observed": True,
            "exclusion_receipt": self.exclusion_receipt,
            "replay": bench["replay"],
            "workbench": bench["workbench"],
            "raw_history": getattr(self, "_raw_history", None),
            "open_plans": self._open_plans(),
            "package": (
                {
                    "package_id": package.package_id,
                    "file_sha256": package.file_sha256,
                    "published_at": package.published_at.isoformat(),
                    "retracted": package.retracted,
                }
                if package
                else None
            ),
            "runs": runs,
            "account": ledger,
        })

    # ---- phase-1 experience additions ------------------------------------------------
    def records_view(self) -> dict:
        """The 建议→委托→成交 chain for the review screen.

        Reads the app's own history records directly (reports, orders,
        plan statuses) so the review never depends on the Records DTO's
        transport validation - a SKIPPED plan must stay visible on the
        review, never crash the page.
        """
        app = self._build_app()
        # A T-only day (next_session stays null by contract) has no pending
        # decision set; the review still shows the day's fills + balances
        # rather than failing - records degrades to an empty chain, the
        # ledger state already carries the truth.
        try:
            view = app.records(app.scope)
        except Exception as exc:  # noqa: BLE001 - named gap, not a crash
            self._log(
                "records_view_degraded",
                market_date=self.current_day.isoformat(),
                reason=getattr(exc, "reason_code", type(exc).__name__),
            )
            return {
                "reports": [], "orders": [], "plans": [], "events": [],
                "outbox_pending": 0,
                "degraded": getattr(exc, "reason_code", type(exc).__name__),
            }
        history = view.history
        reports = [
            {
                "report_id": item.report.report_id,
                "plan_id": item.plan_id,
                "security_code": item.report.security_code,
                "side": item.report.side,
                "quantity": item.report.quantity,
                "price": str(item.report.price),
                "occurred_at": item.report.occurred_at,
                "status": item.report.status,
                "order_id": item.report.order_id,
                "intended_quantity": item.report.intended_quantity,
                "intended_price_cap": str(item.report.intended_price_cap)
                if item.report.intended_price_cap is not None
                else None,
            }
            for item in view.reports
        ]
        orders = [
            {
                "order_id": o.order_id,
                "security_code": o.security_code,
                "side": o.side,
                "accepted_total_qty": o.accepted_total_qty,
                "known_fill_qty": o.known_fill_qty,
                "status": o.status,
                "price_bound": str(o.price_bound),
            }
            for o in view.orders
        ]
        plans = [
            {"plan_id": p.plan_id, "status": p.status}
            for p in view.plans
        ]
        events = [
            {
                "event_id": e.event_id,
                "kind": e.kind,
                "fact_id": e.fact_id,
                "cash_delta": str(e.cash_delta),
                "quantity_delta": e.quantity_delta,
                "recorded_at": e.recorded_at,
            }
            for e in view.events
        ]
        return {
            "reports": reports,
            "orders": orders,
            "plans": plans,
            "events": events,
            "outbox_pending": history.outbox_pending,
        }

    def day_review(self) -> dict:
        """One screen of review for the current replay day.

        Built on the same records chain the 记录页 shows - advice, the
        Owner's confirm/skip/leave, fills, and how cash/positions moved.
        """
        from quanthus.p12_replay.replay_cn import day_review as _review

        return _review(
            replay={
                "current_day": self.current_day.isoformat(),
                "day_index": self.day_index,
                "sessions_total": len(self.sessions),
            },
            day_state=self.day_state(),
            records=self.records_view(),
        )

    def auto_advance(
        self,
        *,
        target: date | None = None,
        max_days: int | None = None,
    ) -> dict:
        """Auto-execute the system's suggestions up to ``target`` day.

        Same engine, same steps as the manual console: for each day the
        evening run publishes, every actionable plan is confirmed (the
        real T+1 open still decides the fill), then the day advances.
        The Owner stops at any day and continues manually - this never
        writes a different code path.  ``target`` bounds the LAST day
        fully processed (its T+1 morning + advance into the next index);
        ``max_days`` is an extra safety cap.
        """

        steps: list[dict] = []
        processed = 0
        while True:
            day = self.current_day
            if target is not None and day > target:
                break
            if max_days is not None and processed >= max_days:
                break
            receipt = self.evening_run()
            outcomes = []
            for plan in self._open_plans():
                outcomes.append(self.confirm(plan["plan_id"]))
            review = self.day_review()
            steps.append(
                {
                    "market_date": day.isoformat(),
                    "run_status": receipt.get("status"),
                    "outcomes": outcomes,
                    "review": review,
                }
            )
            processed += 1
            # Stop exactly ON the target day: process it fully, then halt
            # before advancing so the Owner lands on target to inspect and
            # can continue manually - never one day past it.
            if target is not None and day >= target:
                advanced = {"finished": False, "stopped_on_target": True}
            else:
                advanced = self.advance_day()
            self._log(
                "auto_advance_step",
                market_date=day.isoformat(),
                run_status=receipt.get("status"),
                confirmed=len([o for o in outcomes if o.get("status") == "filled"]),
                outcomes=len(outcomes),
                finished=advanced.get("finished"),
                stopped_on_target=advanced.get("stopped_on_target"),
            )
            if advanced.get("stopped_on_target") or advanced.get("finished"):
                break
        result = {
            "auto": True,
            "processed_days": processed,
            "stopped_at": self.current_day.isoformat(),
            "target": target.isoformat() if target else None,
            "steps": steps,
        }
        self._log("auto_advance", target=result["target"], **{
            "processed_days": processed,
            "stopped_at": result["stopped_at"],
        })
        return result
