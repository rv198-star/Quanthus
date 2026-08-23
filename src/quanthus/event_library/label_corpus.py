"""Deterministic P4 development label-corpus materialization.

The corpus is a sidecar to an already-sealed P4 Event Library.  Candidate identity is never
rewritten: this module binds approved outcome/structural cards to the immutable event package,
reads only the authorized development replay source, and emits one settlement row per event.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    ReplayDataSession,
    load_replay_data_provider_config,
)
from quanthus.contracts import EventOutcome
from quanthus.event_library.contracts import event_record_from_dict, sha256_digest
from quanthus.event_library.job_local import (
    JobLocalArtifactError,
    JobLocalFeaturePanel,
    require_job_local_session,
)
from quanthus.event_library.label_cards import (
    LabelSettlementStatus,
    OutcomeLabelCard,
    StructuralBreakCard,
    load_outcome_label_card,
    load_structural_break_card,
)
from quanthus.event_library.physical_scan import verify_physical_event_scan
from quanthus.feature_registry import load_feature_registry

LABEL_CORPUS_SCHEMA = "quanthus.p4-event-label-corpus.v1"
_DEVELOPMENT_START = date(2012, 1, 4)
_DEVELOPMENT_END = date(2021, 12, 31)


class LabelCorpusError(RuntimeError):
    """Raised when a real P4 label corpus cannot be materialized or verified safely."""


@dataclass(frozen=True)
class LabelCorpusResult:
    root: Path
    manifest_sha256: str
    content_digest: str
    total_events: int
    positive_events: int
    neutral_events: int
    failure_events: int
    not_executable_events: int
    censored_events: int
    unavailable_events: int

    @property
    def three_class_events(self) -> int:
        return self.positive_events + self.neutral_events + self.failure_events


@dataclass(frozen=True)
class _EventInput:
    event_id: str
    source_snapshot_id: str
    listing_code: str
    entry_date: date
    executable: bool
    entry_open_price: Decimal | None
    entry_reason_code: str
    entry_evidence_digest: str


@dataclass(frozen=True)
class _Observation:
    session_index: int
    session_date: date
    high_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None
    observed_tradestatus: str | None
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    total_return_index: Decimal | None
    sma20: Decimal | None
    sma60: Decimal | None
    feature_ready: bool
    exception_active: bool
    unexplained_price_transition: bool
    valid_to: date | None


@dataclass(frozen=True)
class _Settlement:
    status: LabelSettlementStatus
    outcome: EventOutcome | None
    reason_code: str
    terminal_session_index: int | None
    terminal_date: date | None
    favorable_excursion: Decimal | None
    adverse_excursion: Decimal | None
    evidence_digest: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            stream.write("\n")


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LabelCorpusError(f"invalid decimal outcome evidence: {value}") from exc
    if not parsed.is_finite():
        raise LabelCorpusError("outcome evidence must be finite")
    return parsed


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_parquet_expression(paths: Sequence[Path]) -> str:
    if not paths:
        raise LabelCorpusError("label corpus source dataset has no parquet path")
    rendered = ",".join(_sql_string(str(path)) for path in paths)
    return f"read_parquet([{rendered}], union_by_name=true, hive_partitioning=true)"


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise LabelCorpusError("label corpus file path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise LabelCorpusError("label corpus file path escapes package root")
    return path


def _load_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise LabelCorpusError(f"JSONL row is not an object: {path}")
            rows.append(value)
    except OSError as exc:
        raise LabelCorpusError(f"cannot read label corpus source: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LabelCorpusError(f"invalid JSONL source: {path}") from exc
    return tuple(rows)


def _event_inputs(event_root: Path) -> tuple[tuple[_EventInput, ...], Mapping[str, Any]]:
    verified = verify_physical_event_scan(event_root)
    try:
        manifest = json.loads((event_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabelCorpusError("event manifest is unreadable") from exc
    events = _load_jsonl(event_root / "events.jsonl")
    entries = _load_jsonl(event_root / "entry-partition.jsonl")
    entry_by_id: dict[str, Mapping[str, Any]] = {}
    for row in entries:
        event_id = str(row.get("event_id") or "")
        if not event_id or event_id in entry_by_id:
            raise LabelCorpusError("entry partition event ids must be unique")
        entry_by_id[event_id] = row
    output: list[_EventInput] = []
    observed_ids: set[str] = set()
    for row in events:
        raw_event = row.get("event")
        record = event_record_from_dict(raw_event)
        event_id = record.event_id
        if event_id in observed_ids:
            raise LabelCorpusError("event library contains duplicate event ids")
        observed_ids.add(event_id)
        if record.split_assignment.value != "development":
            raise LabelCorpusError("P4 label corpus may read development events only")
        entry = entry_by_id.get(event_id)
        if entry is None:
            raise LabelCorpusError("event library entry partition is incomplete")
        status = str(entry.get("status") or "")
        if status not in {"executable", "not_executable"}:
            raise LabelCorpusError("entry partition status is unsupported")
        entry_open = _decimal(entry.get("entry_open_price"))
        if status == "executable" and (entry_open is None or entry_open <= 0):
            raise LabelCorpusError("executable event requires positive entry open price")
        if status == "not_executable" and entry_open is not None:
            raise LabelCorpusError("not-executable event cannot retain entry open price")
        try:
            entry_date = date.fromisoformat(str(row["entry_date"]))
        except (KeyError, ValueError) as exc:
            raise LabelCorpusError("event entry_date is invalid") from exc
        output.append(
            _EventInput(
                event_id=event_id,
                source_snapshot_id=record.identity.snapshot_id,
                listing_code=str(row.get("listing_code") or ""),
                entry_date=entry_date,
                executable=status == "executable",
                entry_open_price=entry_open,
                entry_reason_code=str(entry.get("reason_code") or ""),
                entry_evidence_digest=str(entry.get("entry_evidence_digest") or ""),
            )
        )
    if set(entry_by_id) != observed_ids:
        raise LabelCorpusError("entry partition contains unknown event ids")
    if len(output) != verified.candidate_events:
        raise LabelCorpusError("event count differs from verified event package")
    event_identity = {
        "manifest_sha256": verified.manifest_sha256,
        "content_digest": verified.content_digest,
        "candidate_events": verified.candidate_events,
        "executable_events": verified.executable_events,
        "not_executable_events": verified.not_executable_events,
        "candidate_rule": manifest.get("candidate_rule"),
        "split_policy": manifest.get("split_policy"),
        "adm": manifest.get("adm"),
    }
    return tuple(sorted(output, key=lambda item: item.event_id)), event_identity


def _require_card_bindings(
    outcome: OutcomeLabelCard,
    structural: StructuralBreakCard,
    event_identity: Mapping[str, Any],
) -> None:
    outcome.require_labeling_authority()
    structural.require_labeling_authority()
    if outcome.structural_break.semantic_digest != structural.semantic_digest:
        raise LabelCorpusError("outcome card structural-break digest differs")
    candidate = event_identity.get("candidate_rule")
    split = event_identity.get("split_policy")
    if not isinstance(candidate, dict) or not isinstance(split, dict):
        raise LabelCorpusError("event package rule bindings are missing")
    if (
        candidate.get("id") != outcome.bindings.candidate_rule_id
        or candidate.get("version") != outcome.bindings.candidate_rule_version
        or candidate.get("digest") != outcome.bindings.candidate_rule_digest
    ):
        raise LabelCorpusError("outcome card candidate-rule binding differs from event package")
    if (
        split.get("id") != outcome.bindings.split_policy_id
        or split.get("version") != outcome.bindings.split_policy_version
        or split.get("digest") != outcome.bindings.split_policy_digest
    ):
        raise LabelCorpusError("outcome card split-policy binding differs from event package")
    if outcome.observation_sessions > outcome.bindings.maximum_overlap_sessions:
        raise LabelCorpusError("outcome horizon exceeds approved split overlap")


def _observation_payload(item: _Observation) -> dict[str, object]:
    return {
        "session_index": item.session_index,
        "session_date": item.session_date.isoformat(),
        "high_price": None if item.high_price is None else str(item.high_price),
        "low_price": None if item.low_price is None else str(item.low_price),
        "close_price": None if item.close_price is None else str(item.close_price),
        "observed_tradestatus": item.observed_tradestatus,
        "cash_dividend_per_share": str(item.cash_dividend_per_share),
        "bonus_share_ratio": str(item.bonus_share_ratio),
        "total_return_index": (
            None if item.total_return_index is None else str(item.total_return_index)
        ),
        "sma20": None if item.sma20 is None else str(item.sma20),
        "sma60": None if item.sma60 is None else str(item.sma60),
        "feature_ready": item.feature_ready,
        "exception_active": item.exception_active,
        "unexplained_price_transition": item.unexplained_price_transition,
        "valid_to": None if item.valid_to is None else item.valid_to.isoformat(),
    }


def _settle_event(
    event: _EventInput,
    observations: Sequence[_Observation],
    card: OutcomeLabelCard,
) -> _Settlement:
    if not event.executable:
        return _Settlement(
            status=LabelSettlementStatus.SETTLED,
            outcome=EventOutcome.NOT_EXECUTABLE,
            reason_code=event.entry_reason_code or "not_executable_at_t_plus_1_open",
            terminal_session_index=None,
            terminal_date=None,
            favorable_excursion=None,
            adverse_excursion=None,
            evidence_digest=sha256_digest(
                {
                    "event_id": event.event_id,
                    "entry_evidence_digest": event.entry_evidence_digest,
                    "entry_reason_code": event.entry_reason_code,
                }
            ),
        )
    if event.entry_open_price is None:
        raise LabelCorpusError("executable event lost its entry open price")
    ordered = tuple(sorted(observations, key=lambda item: item.session_index))
    evidence_digest = sha256_digest([_observation_payload(item) for item in ordered])
    if len(ordered) < card.observation_sessions:
        return _Settlement(
            status=LabelSettlementStatus.CENSORED,
            outcome=None,
            reason_code="incomplete_partition_tail",
            terminal_session_index=None,
            terminal_date=None,
            favorable_excursion=None,
            adverse_excursion=None,
            evidence_digest=evidence_digest,
        )
    if len(ordered) != card.observation_sessions or tuple(
        item.session_index for item in ordered
    ) != tuple(range(1, card.observation_sessions + 1)):
        raise LabelCorpusError("outcome path sessions are not consecutive")

    quantity = Decimal("1")
    cash = Decimal("0")
    for item in ordered:
        terminated = item.valid_to is not None and item.session_date > item.valid_to
        # An action effective on the entry date precedes a new T+1 open purchase.  The hypothetical
        # holding therefore receives only actions strictly after the entry date, matching P2 order.
        if item.session_date > event.entry_date:
            cash += quantity * item.cash_dividend_per_share
            quantity *= Decimal("1") + item.bonus_share_ratio
        if quantity <= 0:
            raise LabelCorpusError("corporate action produced non-positive outcome quantity")
        if terminated:
            return _Settlement(
                status=LabelSettlementStatus.SETTLED,
                outcome=EventOutcome.FAILURE,
                reason_code="structural_break_listing_termination",
                terminal_session_index=item.session_index,
                terminal_date=item.session_date,
                favorable_excursion=None,
                adverse_excursion=None,
                evidence_digest=evidence_digest,
            )
        suspended = item.observed_tradestatus == "0"
        if suspended:
            continue
        if (
            item.high_price is None
            or item.low_price is None
            or item.close_price is None
            or item.high_price <= 0
            or item.low_price <= 0
            or item.close_price <= 0
            or item.low_price > item.high_price
        ):
            return _Settlement(
                status=LabelSettlementStatus.UNAVAILABLE,
                outcome=None,
                reason_code="price_path_evidence_unavailable",
                terminal_session_index=item.session_index,
                terminal_date=item.session_date,
                favorable_excursion=None,
                adverse_excursion=None,
                evidence_digest=evidence_digest,
            )
        if (
            not item.feature_ready
            or item.exception_active
            or item.unexplained_price_transition
            or item.total_return_index is None
            or item.sma20 is None
            or item.sma60 is None
        ):
            return _Settlement(
                status=LabelSettlementStatus.UNAVAILABLE,
                outcome=None,
                reason_code="structural_break_evidence_unavailable",
                terminal_session_index=item.session_index,
                terminal_date=item.session_date,
                favorable_excursion=None,
                adverse_excursion=None,
                evidence_digest=evidence_digest,
            )
        economic_high = cash + quantity * item.high_price
        economic_low = cash + quantity * item.low_price
        favorable = economic_high / event.entry_open_price - Decimal("1")
        adverse = Decimal("1") - economic_low / event.entry_open_price
        structural = item.total_return_index < item.sma60 and item.sma20 <= item.sma60
        adverse_hit = adverse >= card.adverse_threshold
        positive_hit = favorable >= card.positive_threshold
        if structural or adverse_hit:
            if structural and adverse_hit:
                reason = "structural_break_and_adverse_threshold"
            elif structural:
                reason = "structural_break"
            else:
                reason = "adverse_threshold"
            return _Settlement(
                status=LabelSettlementStatus.SETTLED,
                outcome=EventOutcome.FAILURE,
                reason_code=reason,
                terminal_session_index=item.session_index,
                terminal_date=item.session_date,
                favorable_excursion=favorable,
                adverse_excursion=adverse,
                evidence_digest=evidence_digest,
            )
        if positive_hit:
            return _Settlement(
                status=LabelSettlementStatus.SETTLED,
                outcome=EventOutcome.POSITIVE,
                reason_code="positive_threshold",
                terminal_session_index=item.session_index,
                terminal_date=item.session_date,
                favorable_excursion=favorable,
                adverse_excursion=adverse,
                evidence_digest=evidence_digest,
            )
    return _Settlement(
        status=LabelSettlementStatus.SETTLED,
        outcome=EventOutcome.NEUTRAL,
        reason_code="observation_window_ended_without_terminal_condition",
        terminal_session_index=card.observation_sessions,
        terminal_date=ordered[-1].session_date,
        favorable_excursion=None,
        adverse_excursion=None,
        evidence_digest=evidence_digest,
    )


def _source_observations(
    *,
    bundle: Any,
    feature_panel_path: Path,
    events: Sequence[_EventInput],
) -> Mapping[str, tuple[_Observation, ...]]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise LabelCorpusError("P4 label corpus requires duckdb") from exc

    paths = {
        name: bundle.partition_paths(name)
        for name in (
            "corporate_actions_normalized",
            "daily_ohlcv_none",
            "price_limit_execution_daily",
            "security_code_validity",
            "trading_calendar",
        )
    }
    connection = duckdb.connect(":memory:")
    try:
        for name, dataset_paths in paths.items():
            connection.execute(
                f"CREATE VIEW source_{name} AS SELECT * FROM "
                f"{_read_parquet_expression(dataset_paths)}"
            )
        connection.execute(
            "CREATE VIEW source_feature_panel AS SELECT * FROM read_parquet("
            f"{_sql_string(str(feature_panel_path))})"
        )
        connection.execute(
            """
            CREATE VIEW code_map AS
            SELECT stock_code,
                   min(security_id) AS security_id,
                   max(CAST(valid_to AS DATE)) AS valid_to,
                   count(DISTINCT security_id) AS security_id_count
            FROM source_security_code_validity
            GROUP BY stock_code
            """
        )
        action_columns = {
            str(row[0])
            for row in connection.execute("DESCRIBE source_corporate_actions_normalized").fetchall()
        }
        if "security_id" in action_columns:
            connection.execute(
                "CREATE VIEW source_actions_mapped AS "
                "SELECT * FROM source_corporate_actions_normalized"
            )
        else:
            connection.execute(
                """
                CREATE VIEW source_actions_mapped AS
                SELECT actions.*, code_map.security_id
                FROM source_corporate_actions_normalized actions
                JOIN code_map ON code_map.stock_code=actions.security_code
                WHERE code_map.security_id_count=1
                """
            )
        connection.execute(
            """
            CREATE VIEW common_calendar AS
            SELECT CAST(trade_date AS DATE) AS trade_date
            FROM source_trading_calendar
            WHERE is_open=1 AND CAST(trade_date AS DATE) <= DATE '2021-12-31'
            GROUP BY CAST(trade_date AS DATE)
            HAVING count(DISTINCT exchange)=2
            """
        )
        connection.execute(
            """
            CREATE TEMP TABLE event_inputs (
                event_id VARCHAR,
                listing_code VARCHAR,
                entry_date DATE
            )
            """
        )
        executable = [item for item in events if item.executable]
        connection.executemany(
            "INSERT INTO event_inputs VALUES (?, ?, ?)",
            [(item.event_id, item.listing_code, item.entry_date) for item in executable],
        )
        bad_identity = connection.execute(
            """
            SELECT count(*)
            FROM event_inputs events
            LEFT JOIN code_map ON code_map.stock_code=events.listing_code
            WHERE code_map.security_id IS NULL OR code_map.security_id_count<>1
            """
        ).fetchone()
        if bad_identity is None or int(bad_identity[0]):
            raise LabelCorpusError("label corpus source identity mapping is missing or ambiguous")
        rows = connection.execute(
            """
            WITH event_mapped AS (
                SELECT events.*, code_map.security_id, code_map.valid_to
                FROM event_inputs events
                JOIN code_map ON code_map.stock_code=events.listing_code
                WHERE code_map.security_id_count=1
            ),
            session_rows AS (
                SELECT events.*,
                       calendar.trade_date AS session_date,
                       row_number() OVER (
                           PARTITION BY events.event_id ORDER BY calendar.trade_date
                       ) AS session_index
                FROM event_mapped events
                JOIN common_calendar calendar ON calendar.trade_date>=events.entry_date
            ),
            bounded AS (
                SELECT * FROM session_rows WHERE session_index<=20
            )
            SELECT bounded.event_id,
                   CAST(bounded.session_index AS INTEGER) AS session_index,
                   bounded.session_date,
                   bars.high,
                   bars.low,
                   bars.close,
                   limits.observed_tradestatus,
                   coalesce(actions.cash_dividend_per_share, 0) AS cash_dividend_per_share,
                   coalesce(actions.bonus_share_ratio, 0) AS bonus_share_ratio,
                   feature.total_return_index,
                   feature.sma20,
                   feature.sma60,
                   coalesce(feature.feature_ready, false) AS feature_ready,
                   coalesce(feature.exception_active, false) AS exception_active,
                   coalesce(feature.unexplained_price_transition, false)
                       AS unexplained_price_transition,
                   bounded.valid_to
            FROM bounded
            LEFT JOIN source_daily_ohlcv_none bars
              ON bars.security_id=bounded.security_id
             AND CAST(bars.date AS DATE)=bounded.session_date
            LEFT JOIN source_price_limit_execution_daily limits
              ON limits.security_id=bounded.security_id
             AND CAST(limits.execution_date AS DATE)=bounded.session_date
            LEFT JOIN source_actions_mapped actions
              ON actions.security_id=bounded.security_id
             AND CAST(actions.effective_date AS DATE)=bounded.session_date
            LEFT JOIN source_feature_panel feature
              ON feature.security_id=bounded.security_id
             AND feature.market_date=bounded.session_date
            ORDER BY bounded.event_id, bounded.session_index
            """
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, list[_Observation]] = {
        item.event_id: [] for item in events if item.executable
    }
    for row in rows:
        event_id = str(row[0])
        if event_id not in grouped:
            raise LabelCorpusError("outcome query returned unknown event id")
        grouped[event_id].append(
            _Observation(
                session_index=int(row[1]),
                session_date=row[2],
                high_price=_decimal(row[3]),
                low_price=_decimal(row[4]),
                close_price=_decimal(row[5]),
                observed_tradestatus=None if row[6] is None else str(row[6]),
                cash_dividend_per_share=_decimal(row[7]) or Decimal("0"),
                bonus_share_ratio=_decimal(row[8]) or Decimal("0"),
                total_return_index=_decimal(row[9]),
                sma20=_decimal(row[10]),
                sma60=_decimal(row[11]),
                feature_ready=bool(row[12]),
                exception_active=bool(row[13]),
                unexplained_price_transition=bool(row[14]),
                valid_to=row[15],
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _files(root: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"manifest.json", "READY"}:
            continue
        rows.append(
            {"path": relative, "sha256": _sha256(path), "size_bytes": path.stat().st_size}
        )
    return tuple(rows)


def build_physical_label_corpus(
    *,
    event_root: Path,
    provider_config_path: Path,
    outcome_card_path: Path,
    structural_card_path: Path,
    feature_registry_path: Path,
    output: Path,
    code_revision: str,
    provider_session: ReplayDataSession | None = None,
    feature_panel: JobLocalFeaturePanel | None = None,
) -> LabelCorpusResult:
    """Build one immutable development label sidecar from a sealed Event Library."""

    if not code_revision.strip():
        raise ValueError("code_revision must not be empty")
    events, event_identity = _event_inputs(event_root.resolve())
    outcome = load_outcome_label_card(outcome_card_path)
    structural = load_structural_break_card(structural_card_path)
    _require_card_bindings(outcome, structural, event_identity)
    registry = load_feature_registry(feature_registry_path)
    provider_config = load_replay_data_provider_config(provider_config_path)
    if provider_config.mode != "direct-dwd-job-local":
        raise LabelCorpusError("P4 label corpus requires direct DWD provider mode")
    provider = ReplayDataProvider(provider_config)
    if provider_session is not None:
        try:
            require_job_local_session(
                provider_session,
                provider_config,
                scope_start=_DEVELOPMENT_START,
                scope_end=date(2021, 12, 30),
            )
        except JobLocalArtifactError as exc:
            raise LabelCorpusError(str(exc)) from exc
    session_context = (
        provider.open(scope_start=_DEVELOPMENT_START, scope_end=date(2021, 12, 30))
        if provider_session is None
        else nullcontext(provider_session)
    )
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output_parent))
    feature_panel_path = staging / ".feature-panel.parquet"
    try:
        with session_context as session:
            owns_feature_panel = feature_panel is None
            if feature_panel is None:
                panel_artifact = BaselineFeaturePanel.materialize(
                    session.reader.bundle,
                    registry,
                    scope_start=_DEVELOPMENT_START,
                    scope_end=_DEVELOPMENT_END,
                    output=feature_panel_path,
                    data_content_digest=session.reader.data_content_digest,
                    data_manifest_sha256=session.reader.data_manifest_sha256,
                )
                panel_path = panel_artifact.path
                panel_report = panel_artifact.report
            else:
                try:
                    feature_panel.require_compatible(
                        session=session,
                        provider_config=provider_config,
                        registry=registry,
                        scope_start=_DEVELOPMENT_START,
                        scope_end=_DEVELOPMENT_END,
                    )
                except JobLocalArtifactError as exc:
                    raise LabelCorpusError(str(exc)) from exc
                panel_path = feature_panel.path
                panel_report = feature_panel.report
            observations = _source_observations(
                bundle=session.reader.bundle,
                feature_panel_path=panel_path,
                events=events,
            )
            label_rows: list[dict[str, Any]] = []
            for event in events:
                settlement = _settle_event(
                    event,
                    observations.get(event.event_id, ()),
                    outcome,
                )
                label_rows.append(
                    {
                        "event_id": event.event_id,
                        "candidate_snapshot_id": event.source_snapshot_id,
                        "entry_date": event.entry_date.isoformat(),
                        "settlement_status": settlement.status.value,
                        "outcome": None if settlement.outcome is None else settlement.outcome.value,
                        "reason_code": settlement.reason_code,
                        "terminal_session_index": settlement.terminal_session_index,
                        "terminal_date": (
                            None
                            if settlement.terminal_date is None
                            else settlement.terminal_date.isoformat()
                        ),
                        "terminal_favorable_excursion": (
                            None
                            if settlement.favorable_excursion is None
                            else str(settlement.favorable_excursion)
                        ),
                        "terminal_adverse_excursion": (
                            None
                            if settlement.adverse_excursion is None
                            else str(settlement.adverse_excursion)
                        ),
                        "outcome_evidence_digest": settlement.evidence_digest,
                    }
                )
            if owns_feature_panel and feature_panel_path.exists():
                feature_panel_path.unlink()
            label_rows.sort(key=lambda row: str(row["event_id"]))
            status_counts = Counter(str(row["settlement_status"]) for row in label_rows)
            outcome_counts = Counter(
                str(row["outcome"]) for row in label_rows if row["outcome"] is not None
            )
            labels_path = staging / "labels.jsonl"
            summary_path = staging / "summary.json"
            _write_jsonl(labels_path, label_rows)
            summary = {
                "schema_version": LABEL_CORPUS_SCHEMA,
                "total_events": len(label_rows),
                "settlement_status_counts": dict(sorted(status_counts.items())),
                "outcome_counts": dict(sorted(outcome_counts.items())),
                "three_class_events": (
                    outcome_counts[EventOutcome.POSITIVE.value]
                    + outcome_counts[EventOutcome.NEUTRAL.value]
                    + outcome_counts[EventOutcome.FAILURE.value]
                ),
            }
            _write_json(summary_path, summary)
            files = _files(staging)
            content_digest = _digest(files)
            manifest = {
                "schema_version": LABEL_CORPUS_SCHEMA,
                "package_id": f"p4-label-corpus-{content_digest.removeprefix('sha256:')[:24]}",
                "content_digest": content_digest,
                "code_revision": code_revision,
                "research_only": True,
                "execution_authorized": False,
                "scope": {"partition": "development", "start": "2012-01-04", "end": "2021-12-31"},
                "event_library": dict(event_identity),
                "outcome_card": {
                    "id": outcome.card_id,
                    "version": outcome.card_version,
                    "digest": outcome.semantic_digest,
                },
                "structural_break_card": {
                    "id": structural.card_id,
                    "version": structural.card_version,
                    "digest": structural.semantic_digest,
                },
                "feature_panel": {
                    "registry_version": registry.registry_version,
                    "registry_digest": registry.digest,
                    "report_digest": panel_report.digest,
                    "bundle_snapshot_id": panel_report.bundle_snapshot_id,
                    "bundle_digest": panel_report.bundle_digest,
                },
                "counts": summary,
                "files": list(files),
            }
            manifest_path = staging / "manifest.json"
            _write_json(manifest_path, manifest)
            manifest_sha = _sha256(manifest_path)
            (staging / "READY").write_text(manifest_sha + "\n", encoding="ascii")
            if output.exists():
                raise FileExistsError(f"refusing to overwrite P4 label corpus: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, output)
        return verify_physical_label_corpus(output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_physical_label_corpus(root: Path) -> LabelCorpusResult:
    """Verify a sealed P4 label corpus without trusting manifest counts or file hashes."""

    if root.is_symlink() or not root.is_dir():
        raise LabelCorpusError("P4 label corpus must be a physical directory")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabelCorpusError("P4 label corpus manifest or summary is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != LABEL_CORPUS_SCHEMA:
        raise LabelCorpusError("P4 label corpus manifest schema differs")
    manifest_sha = _sha256(root / "manifest.json")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise LabelCorpusError("P4 label corpus READY is unreadable") from exc
    if ready != manifest_sha:
        raise LabelCorpusError("P4 label corpus READY does not bind its manifest")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise LabelCorpusError("P4 label corpus file inventory is invalid")
    observed: list[dict[str, Any]] = []
    expected_paths = {"manifest.json", "READY"}
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise LabelCorpusError("P4 label corpus file row is invalid")
        relative = _safe_relative(raw.get("path"))
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise LabelCorpusError(f"P4 label corpus file is missing: {relative}")
        expected_paths.add(relative.as_posix())
        row = {
            "path": relative.as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        if raw != row:
            raise LabelCorpusError(f"P4 label corpus file identity differs: {relative}")
        observed.append(row)
    actual_paths = {
        item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()
    }
    if actual_paths != expected_paths:
        raise LabelCorpusError("P4 label corpus physical inventory differs from manifest")
    content_digest = _digest(tuple(observed))
    if manifest.get("content_digest") != content_digest:
        raise LabelCorpusError("P4 label corpus content digest differs")
    labels = _load_jsonl(root / "labels.jsonl")
    event_ids = [str(row.get("event_id") or "") for row in labels]
    if (
        not event_ids
        or len(event_ids) != len(set(event_ids))
        or any(not item for item in event_ids)
    ):
        raise LabelCorpusError("P4 label corpus event ids are missing or duplicated")
    status_counts = Counter(str(row.get("settlement_status")) for row in labels)
    outcome_counts = Counter(
        str(row.get("outcome")) for row in labels if row.get("outcome") is not None
    )
    recomputed = {
        "schema_version": LABEL_CORPUS_SCHEMA,
        "total_events": len(labels),
        "settlement_status_counts": dict(sorted(status_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "three_class_events": (
            outcome_counts[EventOutcome.POSITIVE.value]
            + outcome_counts[EventOutcome.NEUTRAL.value]
            + outcome_counts[EventOutcome.FAILURE.value]
        ),
    }
    if summary != recomputed or manifest.get("counts") != recomputed:
        raise LabelCorpusError("P4 label corpus counts do not reconcile")
    event_library = manifest.get("event_library")
    if not isinstance(event_library, dict) or event_library.get("candidate_events") != len(labels):
        raise LabelCorpusError("P4 label corpus does not cover the full event library")
    return LabelCorpusResult(
        root=root.resolve(),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        total_events=len(labels),
        positive_events=outcome_counts[EventOutcome.POSITIVE.value],
        neutral_events=outcome_counts[EventOutcome.NEUTRAL.value],
        failure_events=outcome_counts[EventOutcome.FAILURE.value],
        not_executable_events=outcome_counts[EventOutcome.NOT_EXECUTABLE.value],
        censored_events=status_counts[LabelSettlementStatus.CENSORED.value],
        unavailable_events=status_counts[LabelSettlementStatus.UNAVAILABLE.value],
    )
