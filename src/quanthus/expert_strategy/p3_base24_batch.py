"""Job-local native P3 prerequisites and the existing four-variant P2 runtime.

This is a consumer, not a historical clock qualifier or a D activation endpoint.
Each cutoff consumes one ordered native stream, evaluates the existing feature SQL
once, and supplies the same candidate population/panel/mask to B/T/R/TR. No legacy
bundle, P6 receipt, provider cache or inferred acquisition clock is used.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from importlib.metadata import version
from itertools import groupby
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
    _feature_panel_query,
    _sql_string,
)
from quanthus.backtest.contracts import (
    CorporateAction,
    CorporateActionStep,
    MarketSession,
    OpenExecutionState,
)
from quanthus.backtest.historical_frames import PRICE_TICK
from quanthus.backtest.inputs import market_session_from_p1
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.event_library.contracts import event_record_from_dict
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import _finalize_raw_candidates, _scan_security_series
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy import p3_base24_bridge as bridge
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.expert_strategy.p3_strat_003 import (
    P3Strat003CompositeBackend,
    P3Strat003ReplayRuntime,
    P3Strat003Request,
)
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    _RecentSplitPolicy,
    _RecentVisibilityPolicy,
)
from quanthus.feature_registry import load_feature_registry
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping

VERSION = "quanthus.p3-base24-job-prerequisites.v1"
FROZEN_SCOPE = (date(2025, 1, 1), date(2026, 9, 17), date(2026, 9, 18))


@dataclass(frozen=True)
class NativeAction:
    """Already normalized, separately qualified terms; never a raw-unit inference."""

    source_manifest_sha256: str
    evidence_sha256: str
    action: CorporateAction

    def payload(self) -> dict[str, Any]:
        a = self.action
        return {
            "source_manifest_sha256": self.source_manifest_sha256,
            "evidence_sha256": self.evidence_sha256,
            "action_id": a.action_id,
            "security_code": a.security_code,
            "effective_date": a.effective_date.isoformat(),
            "known_at": a.known_at.isoformat(),
            "cash_dividend_per_share": str(a.cash_dividend_per_share),
            "bonus_share_ratio": str(a.bonus_share_ratio),
            "source_revision": a.source_revision,
            "snapshot_id": a.snapshot_id,
        }

    def validate(self, source: str, cutoff: datetime) -> None:
        if (
            self.source_manifest_sha256 != source
            or not native._SHA.fullmatch(self.evidence_sha256)
            or self.action.known_at > cutoff
            or self.action.known_at.astimezone(native.TZ).date() >= self.action.effective_date
        ):
            raise native.InputError("NATIVE_ACTION_BINDING_OR_VISIBILITY")


@dataclass(frozen=True)
class NativeCutoff:
    cutoff: datetime
    history: Iterable[bridge.DecisionPort]
    execution: Iterable[bridge.DecisionPort]
    next_session: date
    next_open: datetime
    next_close: datetime
    history_actions: tuple[NativeAction, ...] = ()
    execution_actions: tuple[NativeAction, ...] = ()


def cutoffs_from_native(
    view: native.NativeView,
    pairs: Sequence[tuple[datetime, datetime, datetime]],
    *,
    support_for: Callable[[Mapping[str, Any], datetime, bool], bridge.DailySupport | None],
    static_industry: Mapping[str, str],
    actions_for: Callable[[datetime, bool], Sequence[NativeAction]] | None = None,
) -> Iterator[NativeCutoff]:
    """Wire a verified native closure to the batch without rereading old bundles.

    The caller binds authoritative T-close/next-open pairs and qualified support.
    Unknown native clocks remain rejected by NativeView and decision_port.
    """
    manifest = view.prepared_manifest
    if manifest is None:
        raise native.InputError("BRIDGE_VERIFIED_PREPARED_INPUT_REQUIRED")
    plan_path = view.root / "run-plan.json"
    native._check_member(plan_path, manifest["files"]["run-plan.json"])
    warmup = native._json(plan_path)["warmup_calendar"]
    native._warmup_binding(warmup)
    source_sha = manifest["identity"]["source_manifest_sha256"]

    for cutoff, next_open, next_close in pairs:

        def ports(
            execution: bool, current: datetime = cutoff, after: datetime = next_close
        ) -> Iterator[bridge.DecisionPort]:
            if native._sha(view.root / "manifest.json") != manifest["manifest_sha256"]:
                raise native.InputError("BRIDGE_PREPARED_METADATA_CHANGED")
            selected = after if execution else current
            batches = (
                view.execution_batches(selected, decision_date=current.astimezone(native.TZ).date())
                if execution
                else view.market_batches(selected)
            )
            for block in batches:
                for row in block.to_pylist():
                    yield bridge.decision_port(
                        row,
                        cutoff=selected,
                        source_manifest_sha256=source_sha,
                        support=support_for(row, selected, execution),
                        static_industry_id=static_industry.get(row["security_id"]),
                        warmup_authority=warmup,
                        execution_only=execution,
                    )
            view._unchanged()
            native._check_member(plan_path, manifest["files"]["run-plan.json"])

        yield NativeCutoff(
            cutoff,
            ports(False),
            ports(True),
            next_close.astimezone(native.TZ).date(),
            next_open,
            next_close,
            () if actions_for is None else tuple(actions_for(cutoff, False)),
            () if actions_for is None else tuple(actions_for(next_close, True)),
        )


def _code_closure(repository: Path) -> dict[str, str]:
    """Small ordinary-code/config closure, including transitive policy dependencies."""
    return {
        p.relative_to(repository).as_posix(): native._sha(p)
        for parent in (repository / "src/quanthus", repository / "config")
        for p in sorted(parent.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".json"}
    }


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")


def _session_dict(s: MarketSession) -> dict[str, Any]:
    return {
        "session_date": s.session_date.isoformat(),
        "open_at": s.open_at.isoformat(),
        "close_at": s.close_at.isoformat(),
        "security_code": s.security_code,
        "open_price": None if s.open_price is None else str(s.open_price),
        "close_price": None if s.close_price is None else str(s.close_price),
        "open_execution_state": s.open_execution_state.value,
        "source_revision": s.source_revision,
        "snapshot_id": s.snapshot_id,
    }


def _session(value: Mapping[str, Any]) -> MarketSession:
    return MarketSession(
        session_date=date.fromisoformat(value["session_date"]),
        open_at=datetime.fromisoformat(value["open_at"]),
        close_at=datetime.fromisoformat(value["close_at"]),
        security_code=value["security_code"],
        open_price=None if value["open_price"] is None else Decimal(value["open_price"]),
        close_price=None if value["close_price"] is None else Decimal(value["close_price"]),
        open_execution_state=OpenExecutionState(value["open_execution_state"]),
        source_revision=value["source_revision"],
        snapshot_id=value["snapshot_id"],
    )


@dataclass(frozen=True)
class _ExecutionSource:
    sessions: Mapping[str, MarketSession]
    blocked_actions: frozenset[str]
    empty: MarketSession
    actions: tuple[NativeAction, ...] = ()

    def session(self, security_code: str) -> MarketSession:
        return self.sessions.get(security_code, replace(self.empty, security_code=security_code))

    def block_reason(self, security_code: str) -> str | None:
        return (
            "NATIVE_ACTION_EXECUTION_UNQUALIFIED"
            if security_code not in self.sessions or security_code in self.blocked_actions
            else None
        )

    def corporate_actions(
        self, security_code: str, prior_mark_price: Decimal
    ) -> tuple[CorporateActionStep, ...]:
        # A missing action closure may block a held-position step; it must never
        # silently mark an unadjusted price or cause a liquidation.
        if self.block_reason(security_code):
            raise native.InputError("NATIVE_ACTION_EXECUTION_UNQUALIFIED:" + security_code)
        steps = []
        mark = prior_mark_price
        applied = self.empty.open_at.replace(hour=9, minute=0, second=0, microsecond=0)
        for term in sorted(self.actions, key=lambda t: t.action.action_id):
            action = term.action
            if action.security_code != security_code:
                continue
            term.validate(self.empty.source_revision, applied)
            # Same accepted P2 reference-mark calculation and tick as
            # HistoricalFrameReader._corporate_actions; original terms are retained.
            mark = (
                (mark - action.cash_dividend_per_share) / (1 + action.bonus_share_ratio)
            ).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
            if mark <= 0:
                raise native.InputError("NATIVE_ACTION_NONPOSITIVE_REFERENCE")
            steps.append(
                CorporateActionStep(
                    action, applied, mark, self.empty.snapshot_id + ".action." + action.action_id
                )
            )
        return tuple(steps)


def _execution_frame(part: NativeCutoff, source: str, identity: str) -> dict[str, Any]:
    day = part.cutoff.astimezone(native.TZ).date()
    if not (
        day < part.next_session
        and part.next_open.astimezone(native.TZ).date() == part.next_session
        and part.next_close.astimezone(native.TZ).date() == part.next_session
        and part.cutoff < part.next_open < part.next_close
    ):
        raise native.InputError("NATIVE_NEXT_SESSION")
    snapshot = SnapshotRef(part.next_session, part.next_close, source, identity)
    sessions: dict[str, MarketSession] = {}
    blocked = []
    actions_by_security: dict[str, list[NativeAction]] = {}
    action_ids = set()
    for term in part.execution_actions:
        term.validate(source, part.next_open)
        if term.action.effective_date != part.next_session or term.action.action_id in action_ids:
            raise native.InputError("NATIVE_ACTION_DATE_OR_DUPLICATE")
        if term.action.security_code in actions_by_security:
            raise native.InputError("NATIVE_ACTION_AGGREGATION_REQUIRED")
        action_ids.add(term.action.action_id)
        actions_by_security.setdefault(term.action.security_code, []).append(term)
    for port in part.execution:
        evidence = bridge.execution_evidence(
            port,
            snapshot=snapshot,
            open_at=part.next_open,
            close_at=part.next_close,
            decision_date=day,
            next_session_date=part.next_session,
        )
        if evidence.security_code in sessions:
            raise native.InputError("NATIVE_DUPLICATE_EXECUTION")
        s = market_session_from_p1(evidence)
        if port.adjustment_effective is False and evidence.security_code in actions_by_security:
            raise native.InputError("NATIVE_ACTION_CONFLICTS_WITH_NO_ACTION_SUPPORT")
        if port.adjustment_effective is True and evidence.security_code in actions_by_security:
            pass  # Existing conservative entry gate remains; held actions can replay.
        elif port.adjustment_effective is not False:
            blocked.append(evidence.security_code)
            s = replace(
                s,
                open_price=None,
                close_price=None,
                open_execution_state=OpenExecutionState.MISSING_EVIDENCE,
            )
        sessions[evidence.security_code] = s
    empty = MarketSession(
        part.next_session,
        part.next_open,
        part.next_close,
        "missing",
        None,
        None,
        OpenExecutionState.MISSING_EVIDENCE,
        source,
        identity,
    )
    return {
        "decision_at": part.cutoff.isoformat(),
        "execution_date": part.next_session.isoformat(),
        "sessions": [_session_dict(s) for _, s in sorted(sessions.items())],
        "blocked_actions": sorted(blocked),
        "actions": [t.payload() for t in part.execution_actions],
        "empty": _session_dict(empty),
    }


def _history_file(
    part: NativeCutoff,
    path: Path,
    source: str,
    dates: Sequence[date],
    industry: StaticIndustryMapping,
) -> tuple[dict[str, tuple[bridge.DecisionPort, ...]], str]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    names = ("open", "high", "low", "close", "volume", "amount", "upper", "lower")
    schema = pa.schema(
        [
            ("security_id", pa.string()),
            ("market_date", pa.date32()),
            *[(n, pa.string()) for n in names],
            ("entry", pa.bool_()),
            ("executable", pa.bool_()),
            ("action_unknown_or_effective", pa.bool_()),
        ]
    )
    windows = {}
    digest = hashlib.sha256()
    allowed = set(dates)
    day = part.cutoff.astimezone(native.TZ).date()
    previous = ""
    with pq.ParquetWriter(path, schema) as writer:
        buffer = []
        for sid, values in groupby(part.history, lambda p: p.bar.security_code):
            if sid <= previous or sid not in industry.by_security_id:
                raise native.InputError("NATIVE_ORDER_OR_INDUSTRY")
            previous = sid
            rows = []
            prior_date = date.min
            for p in values:
                b, c = p.bar, p.candidate
                if (
                    p.execution_only
                    or c is None
                    or p.source_manifest_sha256 != source
                    or p.selected_at != part.cutoff
                    or b.market_date <= prior_date
                    or b.market_date not in allowed
                    or b.market_date > day
                    or c.static_level1_industry_id
                    != industry.by_security_id[sid].level1_industry_id
                ):
                    raise native.InputError("NATIVE_HISTORY_BINDING")
                prior_date = b.market_date
                rows.append(p)
                digest.update(p.source_evidence_digest.encode("ascii"))
                raw = (
                    b.open_price,
                    b.high_price,
                    b.low_price,
                    b.close_price,
                    b.volume,
                    b.amount,
                    b.upper_limit_price,
                    b.lower_limit_price,
                )
                buffer.append(
                    {
                        "security_id": sid,
                        "market_date": b.market_date,
                        **dict(
                            zip(names, (None if v is None else str(v) for v in raw), strict=True)
                        ),
                        "entry": b.entry_policy_allows,
                        "executable": b.is_executable,
                        "action_unknown_or_effective": c.adjustment_effective is not False,
                    }
                )
                if len(buffer) == 8192:
                    writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                    buffer.clear()
            windows[sid] = tuple(rows[-22:])
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
    return windows, "sha256:" + digest.hexdigest()


def _feature_cutoff(
    repository: Path,
    part: NativeCutoff,
    history: Path,
    output: Path,
    dates: Sequence[date],
    start: date,
    windows: Mapping[str, Sequence[bridge.DecisionPort]],
    masks: Any,
) -> None:
    import duckdb
    import pyarrow as pa

    day = part.cutoff.astimezone(native.TZ).date()
    history_sha = native._sha(history)
    actions_digest = digest_value([t.payload() for t in part.history_actions])
    for term in part.history_actions:
        term.validate(part.history_actions[0].source_manifest_sha256, part.cutoff)
        if term.action.effective_date not in dates:
            raise native.InputError("NATIVE_ACTION_SCOPE")
    action_keys = [(t.action.security_code, t.action.effective_date) for t in part.history_actions]
    if len(set(action_keys)) != len(action_keys):
        raise native.InputError("NATIVE_ACTION_AGGREGATION_REQUIRED")
    card = load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB'; SET threads=2")
        c.execute(f"SET temp_directory={_sql_string(str(output.parent / 'spill'))}")
        c.execute("SET max_temp_directory_size='1GB'")
        c.execute(f"CREATE VIEW input AS SELECT * FROM read_parquet({_sql_string(str(history))})")
        c.register("dates", pa.table({"market_date": list(dates)}))
        c.execute("""CREATE VIEW source_daily_universe AS
            SELECT security_id, dates.market_date FROM
            (SELECT security_id, min(market_date) AS first FROM input GROUP BY security_id) s
            CROSS JOIN dates WHERE dates.market_date >= s.first""")
        c.execute(
            "CREATE VIEW source_daily_ohlcv_none AS SELECT security_id, "
            "market_date AS date, "
            + ", ".join(
                f"CAST({name} AS DOUBLE) AS {name}"
                for name in ("open", "high", "low", "close", "volume", "amount")
            )
            + " FROM input"
        )
        c.execute("""CREATE VIEW source_price_limit_execution_daily AS SELECT
            security_id, market_date AS execution_date, entry AS entry_policy_allows,
            executable AS is_executable, NULL::DOUBLE AS upper_limit_pct,
            NULL::DOUBLE AS lower_limit_pct, upper AS upper_limit_price,
            lower AS lower_limit_price FROM input""")
        # Only separately qualified no-action observations can take the zero-action
        # branch. Both a known action without terms and an unknown boundary are exceptions.
        action_schema = pa.schema(
            [
                ("security_id", pa.string()),
                ("effective_date", pa.date32()),
                ("cash_dividend_per_share", pa.string()),
                ("bonus_share_ratio", pa.string()),
                ("known_at", pa.timestamp("us", tz="Asia/Shanghai")),
            ]
        )
        c.register(
            "qualified_actions",
            pa.Table.from_pylist(
                [
                    {
                        "security_id": t.action.security_code,
                        "effective_date": t.action.effective_date,
                        "cash_dividend_per_share": str(t.action.cash_dividend_per_share),
                        "bonus_share_ratio": str(t.action.bonus_share_ratio),
                        "known_at": t.action.known_at,
                    }
                    for t in part.history_actions
                ],
                schema=action_schema,
            ),
        )
        c.execute(
            "CREATE VIEW source_corporate_actions_normalized_mapped "
            "AS SELECT * FROM qualified_actions"
        )
        c.execute("""CREATE VIEW source_corporate_action_exceptions_mapped AS SELECT
            security_id, market_date AS blocked_from, market_date AS blocked_through
            FROM input WHERE action_unknown_or_effective AND NOT EXISTS
            (SELECT 1 FROM qualified_actions a WHERE a.security_id=input.security_id
             AND a.effective_date=input.market_date)""")
        query = _feature_panel_query(start, day)
        c.execute(
            f"CREATE TABLE current_features AS SELECT * FROM ({query}) WHERE market_date=?", [day]
        )
        cur = c.execute("SELECT * FROM current_features ORDER BY security_id")
        columns = [v[0] for v in cur.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        expected = tuple(dates[-22:])
        eligible = []
        for row in rows:
            sid = row["security_id"]
            feature = None if row["close_price"] is None else BaselineFeatureRow.from_mapping(row)
            mask = bridge.common_entry_mask(
                windows.get(sid, ()),
                expected_dates=expected,
                card=card,
                feature=feature,
                feature_known_at=part.cutoff,
                feature_evidence_digest=digest_value(
                    {
                        "history": history_sha,
                        "actions": actions_digest,
                        "feature": bridge._digest(row),
                    }
                ),
            )
            masks.write(
                canonical_json_bytes(
                    {
                        "market_date": day.isoformat(),
                        "security_id": sid,
                        "eligible": mask.eligible,
                        "reasons": mask.reasons,
                        "evidence_digest": mask.evidence_digest,
                    }
                )
                + b"\n"
            )
            eligible.append({"security_id": sid, "allowed": mask.eligible})
        c.register(
            "mask",
            pa.Table.from_pylist(
                eligible, schema=pa.schema([("security_id", pa.string()), ("allowed", pa.bool_())])
            ),
        )
        c.execute(f"""COPY (SELECT current_features.* REPLACE
            (entry_eligible AND allowed AS entry_eligible)
            FROM current_features JOIN mask USING(security_id) ORDER BY security_id)
            TO {_sql_string(str(output))} (FORMAT PARQUET)""")


@dataclass(frozen=True)
class NativeJob:
    root: Path
    manifest: Mapping[str, Any]
    events: tuple[_EventInput, ...]
    industry: StaticIndustryMapping
    frames: tuple[StrategyReplayFrame, ...]
    report: BaselineFeaturePanelReport

    @property
    def digest(self) -> str:
        return str(self.manifest["semantic_digest"])


def materialize_job(
    repository: Path,
    output: Path,
    *,
    cutoffs: Iterable[NativeCutoff],
    trading_dates: Sequence[date],
    industry: StaticIndustryMapping,
    source_manifest_sha256: str,
    qualification_digest: str,
    scope: tuple[date, date, date] = FROZEN_SCOPE,
    synthetic: bool = False,
    source_roots: Sequence[Path],
) -> NativeJob:
    """Once-only TMP prerequisite. Caller must already hold new B/D data authority.

    Synthetic scope is explicit and cannot be mistaken for frozen-window acceptance.
    A qualification digest binds the supplied support; it does not manufacture its truth.
    """
    import duckdb

    start, end, execution_end = scope
    if (not synthetic and scope != FROZEN_SCOPE) or not start <= end < execution_end:
        raise native.InputError("NATIVE_SCOPE")
    if tuple(sorted(set(trading_dates))) != tuple(trading_dates) or not trading_dates:
        raise native.InputError("NATIVE_CALENDAR")
    if trading_dates[-1] != execution_end or (
        not synthetic and len([d for d in trading_dates if d < start]) != 60
    ):
        raise native.InputError("NATIVE_CALENDAR_SCOPE")
    for sha in (source_manifest_sha256, qualification_digest):
        if not native._SHA.fullmatch(sha):
            raise native.InputError("NATIVE_INPUT_DIGEST")
    output = native.physical(output)
    if not source_roots:
        raise native.InputError("NATIVE_SOURCE_ROOTS_REQUIRED")
    for root in (*source_roots, industry.path):
        native._isolate(native.physical(root), output, output)
    if output.exists() or output.is_symlink():
        raise native.InputError("NATIVE_OUTPUT_EXISTS")
    repository = repository.resolve()
    code_members = _code_closure(repository)
    code = digest_value(code_members)
    registry = load_feature_registry(repository / "config/p3-baseline-features.research.v1.json")
    identity = {
        "schema_version": VERSION,
        "request": native.REQUEST,
        "source": source_manifest_sha256,
        "qualification": qualification_digest,
        "industry": industry.artifact_digest,
        "code": code,
        "code_members": code_members,
        "runtime": {
            "python": sys.version.split()[0],
            "duckdb": version("duckdb"),
            "pyarrow": version("pyarrow"),
        },
        "feature_registry": registry.digest,
        "scope": [d.isoformat() for d in scope],
        "synthetic": synthetic,
        "calendar": [d.isoformat() for d in trading_dates],
    }
    snapshot = "native-p3-" + digest_value(identity).removeprefix("sha256:")
    card = load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    policy: Any = _RecentVisibilityPolicy(
        trading_dates[0], start, end, execution_end, qualification_digest
    )
    split: Any = _RecentSplitPolicy(trading_dates[0], start, end, execution_end)
    output.mkdir(parents=True)
    _write(output / "plan.json", identity)
    # Preserve a failed job for diagnosis; only successful consumed history TMP is removed.
    raw = []
    reasons: Counter[str] = Counter()
    evaluated, emitted = 0, []
    index = {d: i for i, d in enumerate(trading_dates)}
    expected = tuple(d for d in trading_dates if start <= d <= end)
    with (
        (output / "frames.jsonl").open("xb") as frames,
        (output / "masks.jsonl").open("xb") as masks,
    ):
        for number, part in enumerate(cutoffs):
            day = part.cutoff.astimezone(native.TZ).date()
            if (
                number >= len(expected)
                or day != expected[number]
                or part.next_session != trading_dates[index[day] + 1]
            ):
                raise native.InputError("NATIVE_DECISION_CENSUS")
            emitted.append(day)
            for term in part.history_actions:
                term.validate(source_manifest_sha256, part.cutoff)
            dates = tuple(d for d in trading_dates if d <= day)
            history = output / "history-TMP.parquet"
            windows, history_digest = _history_file(
                part, history, source_manifest_sha256, dates, industry
            )
            for ports in windows.values():
                series = tuple(p.candidate for p in ports if p.candidate is not None)
                if not series or series[-1].session_date != day:
                    continue
                found, reason_counts, n = _scan_security_series(
                    series,
                    ordered_dates=trading_dates,
                    date_index=index,
                    history_sessions=22,
                    snapshot_id=snapshot,
                    snapshot_manifest_digest=source_manifest_sha256,
                    card=card,
                    source_policy=policy,
                )
                raw.extend(found)
                reasons.update(reason_counts)
                evaluated += n
            _feature_cutoff(
                repository,
                part,
                history,
                output / f"feature-{number:04d}.parquet",
                dates,
                start,
                windows,
                masks,
            )
            frame = _execution_frame(part, source_manifest_sha256, snapshot)
            frame["history_evidence_digest"] = history_digest
            frames.write(canonical_json_bytes(frame) + b"\n")
            history.unlink()
    if tuple(emitted) != expected or not expected or expected[-1] >= execution_end:
        raise native.InputError("NATIVE_DECISION_CENSUS")
    result = _finalize_raw_candidates(
        raw,
        reasons=reasons,
        evaluated=evaluated,
        ordered_dates=trading_dates,
        date_index=index,
        snapshot_id=snapshot,
        snapshot_manifest_digest=source_manifest_sha256,
        card=card,
        split_policy=split,
    )
    with (output / "events.jsonl").open("xb") as events:
        for candidate in result.candidates:
            events.write(
                canonical_json_bytes(
                    {
                        "event": candidate.record.to_dict(),
                        "static_industry_id": candidate.static_level1_industry_id,
                        "panel_security_id": candidate.record.identity.security_id,
                    }
                )
                + b"\n"
            )
    (output / "industry.json").write_bytes(industry.path.read_bytes())
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB'; SET threads=2")
        c.execute(
            "COPY (SELECT * FROM read_parquet("
            f"{_sql_string(str(output / 'feature-*.parquet'))}) "
            "ORDER BY market_date, security_id) TO "
            f"{_sql_string(str(output / 'features.parquet'))} (FORMAT PARQUET)"
        )
        counts = c.execute(
            "SELECT count(*), count(*) FILTER (WHERE feature_ready), "
            "count(*) FILTER (WHERE entry_eligible), count(*) FILTER (WHERE exception_active), "
            "count(*) FILTER (WHERE unexplained_price_transition), "
            "count(DISTINCT security_id) FILTER (WHERE scope_continuity_excluded) "
            f"FROM read_parquet({_sql_string(str(output / 'features.parquet'))})"
        ).fetchone()
    for part_path in output.glob("feature-*.parquet"):
        part_path.unlink()
    assert counts is not None
    report = BaselineFeaturePanelReport(
        VERSION,
        registry.registry_version,
        registry.digest,
        snapshot,
        source_manifest_sha256,
        start,
        end,
        counts[0],
        counts[1],
        counts[2],
        counts[3],
        counts[4],
        counts[5],
        data_content_digest=source_manifest_sha256,
        data_manifest_sha256=source_manifest_sha256,
    )
    _write(output / "report.json", {"payload": report.payload_dict(), "digest": report.digest})
    _write(
        output / "scan.json",
        {
            "candidates": len(result.candidates),
            "reasons": dict(sorted(reasons.items())),
            "evaluated": evaluated,
        },
    )
    files = {
        p.name: {"sha256": native._sha(p), "bytes": p.stat().st_size}
        for p in sorted(output.iterdir())
        if p.is_file()
    }
    unsigned = {
        "identity": identity,
        "files": files,
        "snapshot_id": snapshot,
        "B_accepted": False,
        "D_activated": False,
        "lifecycle": "TMP",
    }
    manifest = {**unsigned, "semantic_digest": digest_value(unsigned)}
    _write(output / "manifest.json", manifest)
    _write(output / "READY", {"manifest_sha256": native._sha(output / "manifest.json")})
    return load_job(output)


def load_job(root: Path) -> NativeJob:
    """Offline job input read: only this directory, no provider/path fallback."""
    root = native.physical(root)
    m = native._json(root / "manifest.json")
    if (
        native._json(root / "READY")["manifest_sha256"] != native._sha(root / "manifest.json")
        or m["semantic_digest"]
        != digest_value({k: v for k, v in m.items() if k != "semantic_digest"})
        or m["identity"]["schema_version"] != VERSION
        or m["identity"]["request"] != native.REQUEST
    ):
        raise native.InputError("NATIVE_JOB_IDENTITY")
    if native._tree(root) != set(m["files"]) | {"manifest.json", "READY"}:
        raise native.InputError("NATIVE_JOB_FILES")
    for name, sha in m["files"].items():
        native._check_member(native._member(root, name), sha)
    industry = load_static_industry_mapping(
        root / "industry.json", expected_version="static-level1-concentration-research-v1"
    )
    if industry.artifact_digest != m["identity"]["industry"]:
        raise native.InputError("NATIVE_INDUSTRY_IDENTITY")
    events = tuple(
        _EventInput(
            event_record_from_dict(v["event"]), v["static_industry_id"], v["panel_security_id"]
        )
        for v in _lines(root / "events.jsonl")
    )
    frames = []
    for v in _lines(root / "frames.jsonl"):
        cutoff = datetime.fromisoformat(v["decision_at"])
        snapshot = SnapshotRef(
            cutoff.astimezone(native.TZ).date(), cutoff, m["identity"]["source"], m["snapshot_id"]
        )
        sessions = [_session(s) for s in v["sessions"]]
        if len({s.security_code for s in sessions}) != len(sessions):
            raise native.InputError("NATIVE_DUPLICATE_EXECUTION")
        source = _ExecutionSource(
            {s.security_code: s for s in sessions},
            frozenset(v["blocked_actions"]),
            _session(v["empty"]),
            tuple(_action(t) for t in v["actions"]),
        )
        frames.append(
            StrategyReplayFrame(
                snapshot, cutoff, date.fromisoformat(v["execution_date"]), (), source=source
            )
        )
    r = native._json(root / "report.json")
    p = r["payload"]
    counts = p["counts"]
    report = BaselineFeaturePanelReport(
        p["evaluator_version"],
        p["feature_registry_version"],
        p["feature_registry_digest"],
        p["bundle_snapshot_id"],
        p["bundle_digest"],
        date.fromisoformat(p["scope"]["start"]),
        date.fromisoformat(p["scope"]["end"]),
        counts["rows"],
        counts["feature_ready"],
        counts["entry_eligible"],
        counts["exception_active"],
        counts["unexplained_price_transition"],
        counts["continuity_excluded_securities"],
        data_content_digest=p["data_content_digest"],
        data_manifest_sha256=p["data_manifest_sha256"],
    )
    if report.digest != r["digest"]:
        raise native.InputError("NATIVE_PANEL_REPORT")
    return NativeJob(root, m, events, industry, tuple(frames), report)


def _lines(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def _action(value: Mapping[str, Any]) -> NativeAction:
    return NativeAction(
        value["source_manifest_sha256"],
        value["evidence_sha256"],
        CorporateAction(
            value["action_id"],
            value["security_code"],
            date.fromisoformat(value["effective_date"]),
            datetime.fromisoformat(value["known_at"]),
            Decimal(value["cash_dividend_per_share"]),
            Decimal(value["bonus_share_ratio"]),
            value["source_revision"],
            value["snapshot_id"],
        ),
    )


class _NativePanel(BaselineFeaturePanel):
    """Same feature API; bounded offline reads without a second full materialization."""

    def __init__(self, path: Path, report: BaselineFeaturePanelReport) -> None:
        import duckdb

        self.path, self.report = path.resolve(), report
        self._connection: Any = duckdb.connect(":memory:")
        try:
            self._connection.execute("SET memory_limit='2GB'; SET threads=2")
            self._connection.execute(
                "CREATE VIEW feature_panel AS SELECT * FROM read_parquet("
                + _sql_string(str(self.path))
                + ")"
            )
            columns = self._connection.execute("SELECT * FROM feature_panel LIMIT 0").description
            if tuple(c[0] for c in columns) != self._OUTPUT_COLUMNS:
                raise native.InputError("NATIVE_FEATURE_SCHEMA")
            duplicate_row = self._connection.execute(
                "SELECT count(*) FROM (SELECT market_date,security_id FROM feature_panel "
                "GROUP BY ALL HAVING count(*)<>1)"
            ).fetchone()
            count_row = self._connection.execute("SELECT count(*) FROM feature_panel").fetchone()
            assert duplicate_row is not None and count_row is not None
            duplicates, count = duplicate_row[0], count_row[0]
            if duplicates or count != report.row_count:
                raise native.InputError("NATIVE_FEATURE_KEY_OR_COUNT")
        except BaseException:
            self.close()
            raise

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow | None:
        rows = self._rows(
            "SELECT * FROM feature_panel WHERE market_date=? AND security_id=?",
            (market_date, security_id),
        )
        if not rows or rows[0]["close_price"] is None:
            return None
        if len(rows) != 1:
            raise native.InputError("NATIVE_FEATURE_KEY_OR_COUNT")
        return BaselineFeatureRow.from_mapping(rows[0])


class NativeReplayRuntime(P3Strat003ReplayRuntime):
    """Use accepted P2 experts/allocation/ledger with a native job prerequisite."""

    def __init__(self, repository_root: Path, job: NativeJob) -> None:
        self.native_job = load_job(job.root)
        if _code_closure(repository_root.resolve()) != job.manifest["identity"]["code_members"]:
            raise native.InputError("NATIVE_CODE_OR_POLICY_CHANGED")
        if job.manifest["identity"]["runtime"] != {
            "python": sys.version.split()[0],
            "duckdb": version("duckdb"),
            "pyarrow": version("pyarrow"),
        }:
            raise native.InputError("NATIVE_RUNTIME_CHANGED")
        self.request = P3Strat003Request.load(repository_root)
        self.candidate_input = None
        self.candidate_input_manifest = None
        start, end, execution = map(date.fromisoformat, job.manifest["identity"]["scope"])
        try:
            FormalP2ReplayRuntime.__init__(
                self,
                repository_root,
                decision_start=start,
                decision_end=end,
                execution_end=execution,
                parent_event_run_digest=None,
                provider_config="native-job-local",
            )
        except BaseException:
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _load_event_population(
        self, event_root: Path
    ) -> tuple[tuple[_EventInput, ...], StaticIndustryMapping]:
        return self.native_job.events, self.native_job.industry

    def _open_data_inputs(self) -> None:
        self.frames = self.native_job.frames
        self.data_snapshot_id = self.native_job.manifest["snapshot_id"]
        self.data_content_digest = self.native_job.digest
        self.data_manifest_sha256 = native._sha(self.native_job.root / "manifest.json")
        self.panel = self.stack.enter_context(
            _NativePanel(self.native_job.root / "features.parquet", self.native_job.report)
        )

    def _population_manifest_fields(self) -> dict[str, Any]:
        return {"native_job_digest": self.native_job.digest, "parent_event_run_digest": None}

    def _code_version(self) -> str:
        return VERSION + ":" + str(self.native_job.manifest["identity"]["code"])

    def batch_input_identity(self) -> dict[str, Any]:
        return {
            "request_digest": self.request.digest,
            "native_job_digest": self.native_job.digest,
            "formal_strategy_digest": self.registry.card.semantic_digest,
            "code_version": self._code_version(),
            "scope": self.native_job.manifest["identity"]["scope"],
        }

    @property
    def feature_panel_bytes(self) -> int:
        return (self.native_job.root / "features.parquet").stat().st_size


class NativeCompositeBackend(P3Strat003CompositeBackend):
    def __init__(self, repository_root: Path, workspace_root: Path, job: NativeJob) -> None:
        self.native_job = job
        start, end, execution = map(date.fromisoformat, job.manifest["identity"]["scope"])
        super().__init__(
            repository_root,
            workspace_root,
            decision_start=start,
            decision_end=end,
            execution_end=execution,
            provider_config="native-job-local",
        )

    def _open_runtime(self, *args: Any, **kwargs: Any) -> P3Strat003ReplayRuntime:
        return NativeReplayRuntime(self.repository_root, self.native_job)
