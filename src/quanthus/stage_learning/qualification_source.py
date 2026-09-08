"""One-time arbitration reader for P3-STAGE-001-F external semantic evaluation.

This module is deliberately separate from the development reader.  It does not broaden
``InputPolicy`` or the accepted B2 development adapter.  It admits only a pre-registered
2021 warm-up plus 2022-2024 arbitration window, never 2025, and emits an explicit access
manifest for the single held-out evaluation budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.stage_learning import source as _development_source
from quanthus.structure_v2_inputs import (
    COORDINATE_POLICY,
    VIEW,
    Admission,
    DailyInput,
    admit_daily,
    digest,
    exact_sha,
)

_ARBITRATION_START = date(2022, 1, 1)
_ARBITRATION_END = date(2024, 12, 31)
_WARMUP_EARLIEST = date(2012, 1, 1)


class StageQualificationSourceError(ValueError):
    """Raised when the one-time F source contract would be exceeded."""


@dataclass(frozen=True)
class QualificationReadScope:
    warmup_start: date
    report_start: date
    report_end: date

    def __post_init__(self) -> None:
        if not self.warmup_start < self.report_start <= self.report_end:
            raise StageQualificationSourceError("qualification read dates are not ordered")
        if self.warmup_start < _WARMUP_EARLIEST:
            raise StageQualificationSourceError("qualification warm-up predates admitted history")
        if self.report_start < _ARBITRATION_START or self.report_end > _ARBITRATION_END:
            raise StageQualificationSourceError(
                "qualification reader admits only 2022-01-01..2024-12-31 result dates"
            )

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(range(self.warmup_start.year, self.report_end.year + 1))

    def to_dict(self) -> dict[str, str]:
        return {
            "warmup_start": self.warmup_start.isoformat(),
            "report_start": self.report_start.isoformat(),
            "report_end": self.report_end.isoformat(),
        }


@dataclass(frozen=True)
class QualificationInputPolicy:
    spec_digest: str
    security_codes: tuple[str, ...]
    calendar_ref: str
    view: str = VIEW
    finality: str = "final"
    partition: str = "arbitration"
    coordinate_policy: str = COORDINATE_POLICY

    def __post_init__(self) -> None:
        if not exact_sha(self.spec_digest) or not self.calendar_ref:
            raise StageQualificationSourceError(
                "qualification policy requires spec/calendar identity"
            )
        if (
            type(self.security_codes) is not tuple
            or not self.security_codes
            or len(set(self.security_codes)) != len(self.security_codes)
            or any(not _development_source._CODE.fullmatch(code) for code in self.security_codes)
        ):
            raise StageQualificationSourceError(
                "qualification securities must be unique six-digit codes"
            )
        if (self.view, self.finality, self.partition, self.coordinate_policy) != (
            VIEW,
            "final",
            "arbitration",
            COORDINATE_POLICY,
        ):
            raise StageQualificationSourceError("qualification policy lane differs")


def _partition_paths(base: Path, scope: QualificationReadScope) -> dict[str, tuple[str, ...]]:
    if any(year > 2024 for year in scope.years):
        raise StageQualificationSourceError("2025 partition path construction is forbidden")
    payload = base / "payload"
    return {
        "bar": tuple(
            str(
                payload
                / "security_market_observation/dwd_security_bar/interval_size=1d"
                / f"observation_start_year={year}/**/*.parquet"
            )
            for year in scope.years
        ),
        "state": tuple(
            str(
                payload
                / "tradability_state/dwd_tradability_state"
                / f"observation_start_year={year}/**/*.parquet"
            )
            for year in scope.years
        ),
        "calendar": tuple(
            str(
                payload
                / "tradability_state/dwd_trading_calendar"
                / f"trade_date_year={year}/*.parquet"
            )
            for year in scope.years
        ),
    }


def read_qualification_admissions(
    *,
    training_root: Path,
    node_config: Mapping[str, Any],
    scope: QualificationReadScope,
    security_codes: tuple[str, ...],
    spec_digest: str,
    calendar_ref: str = "reference.calendar.trading-session:SSE-SZSE-daily-v1",
) -> tuple[tuple[Admission, ...], dict[str, Any], QualificationInputPolicy]:
    """Read pre-2022 warm-up plus the exact 2022-2024 arbitration evaluation interval."""
    policy = QualificationInputPolicy(spec_digest, security_codes, calendar_ref)
    base, correction, source_binding = _development_source._source_roots(training_root, node_config)
    paths = _partition_paths(base, scope)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise StageQualificationSourceError("qualification reader requires duckdb") from exc

    payload = base / "payload"
    correction_payload = correction / "payload"
    codes = ",".join("?" for _ in security_codes)
    start = scope.warmup_start.isoformat()
    end = scope.report_end.isoformat()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads=8")
        connection.execute("SET memory_limit='16GB'")
        connection.execute("SET preserve_insertion_order=false")
        connection.execute(
            f"""
            CREATE VIEW dwd_listing AS
            SELECT * FROM read_parquet({
                _development_source._sql_literal(
                    str(payload / "listed_company/dwd_listing.parquet")
                )
            })
            WHERE knowledge_to IS NULL;
            CREATE VIEW dwd_security AS
            SELECT * FROM read_parquet({
                _development_source._sql_literal(
                    str(payload / "listed_company/dwd_security.parquet")
                )
            })
            WHERE knowledge_to IS NULL;
            CREATE VIEW dwd_bar AS
            SELECT * FROM read_parquet({
                _development_source._path_list(paths["bar"])
            }, hive_partitioning=true)
            WHERE knowledge_to IS NULL
              AND interval_size='1d' AND price_basis='UNADJUSTED'
              AND CAST(observation_start AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_calendar AS
            SELECT * FROM read_parquet({
                _development_source._path_list(paths["calendar"])
            }, hive_partitioning=true)
            WHERE knowledge_to IS NULL AND is_open
              AND CAST(trade_date AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW correction_state AS
            SELECT * FROM read_parquet({
                _development_source._sql_literal(
                    str(
                        correction_payload
                        / "tradability_state/dwd_tradability_state_replacement.parquet"
                    )
                )
            })
            WHERE knowledge_to IS NULL
              AND CAST(observation_start AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_state AS
            SELECT base_state.*
            FROM read_parquet({
                _development_source._path_list(paths["state"])
            }, hive_partitioning=true) base_state
            WHERE base_state.knowledge_to IS NULL
              AND CAST(base_state.observation_start AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              AND NOT EXISTS (
                SELECT 1 FROM correction_state replacement
                WHERE replacement.listing_id=base_state.listing_id
                  AND replacement.calendar_session_id=base_state.calendar_session_id
              )
            UNION ALL BY NAME SELECT * FROM correction_state;
            """
        )
        members = _development_source._rows(
            connection.execute(
                f"""
                SELECT listing.listing_code AS security_code,
                       security.security_id,
                       listing.listing_id,
                       listing.venue_id,
                       CASE listing.venue_id
                         WHEN '{_development_source._SSE}' THEN 'SSE'
                         WHEN '{_development_source._SZSE}' THEN 'SZSE'
                       END AS exchange,
                       CAST(calendar.trade_date AS DATE) AS market_date,
                       bar.high, bar.low, bar.close, bar.volume,
                       bar.source_record_id, bar.source_revision,
                       bar.source_artifact_sha256, bar.knowledge_from AS dwd_known_from,
                       state.is_tradable, state.can_buy, state.can_sell,
                       state.reason_code AS tradability_reason
                FROM dwd_state state
                JOIN dwd_listing listing USING (listing_id)
                JOIN dwd_security security
                  ON security.security_id=listing.security_id
                 AND security.effective_from <= listing.effective_from
                 AND (
                   security.effective_to IS NULL
                   OR (
                     listing.effective_to IS NOT NULL
                     AND listing.effective_to <= security.effective_to
                   )
                 )
                 AND security.knowledge_from <= listing.knowledge_from
                JOIN dwd_calendar calendar
                  ON calendar.calendar_session_id=state.calendar_session_id
                 AND calendar.venue_id=listing.venue_id
                LEFT JOIN dwd_bar bar
                  ON bar.listing_id=state.listing_id
                 AND bar.calendar_session_id=state.calendar_session_id
                WHERE listing.venue_id IN (
                    '{_development_source._SSE}','{_development_source._SZSE}'
                )
                  AND listing.listing_code IN ({codes})
                ORDER BY market_date, security_code
                """,
                list(security_codes),
            )
        )
        calendar_rows = _development_source._rows(
            connection.execute(
                f"""
                SELECT * FROM dwd_calendar
                WHERE venue_id IN ('{_development_source._SSE}','{_development_source._SZSE}')
                ORDER BY trade_date, venue_id
                """
            )
        )
        actions_path = correction_payload / "replay/corporate_actions_normalized.parquet"
        actions = _development_source._rows(
            connection.execute(
                f"""
                SELECT * FROM read_parquet({_development_source._sql_literal(str(actions_path))})
                WHERE security_code IN ({codes}) AND effective_date <= DATE '{end}'
                ORDER BY effective_date, security_code, action_id
                """,
                list(security_codes),
            )
        )
    finally:
        connection.close()

    member_map: dict[tuple[str, date], dict[str, Any]] = {}
    for item in members:
        key = str(item["security_code"]), item["market_date"]
        if key in member_map:
            raise StageQualificationSourceError(
                "qualification DWD projection has ambiguous security/date grain"
            )
        member_map[key] = item
    calendar: dict[date, list[dict[str, Any]]] = {}
    for item in calendar_rows:
        calendar.setdefault(item["trade_date"], []).append(item)
    if not calendar:
        raise StageQualificationSourceError("qualification calendar is empty")
    action_map: dict[str, list[dict[str, Any]]] = {code: [] for code in security_codes}
    for item in actions:
        action_map[str(item["security_code"])].append(item)

    output: list[Admission] = []
    previous_epoch: dict[str, str] = {}
    for session_index, day in enumerate(sorted(calendar)):
        for code in security_codes:
            member = member_map.get((code, day), {})
            known_actions = [item for item in action_map[code] if item["effective_date"] <= day]
            epoch = (
                max(item["effective_date"] for item in known_actions).isoformat()
                if known_actions
                else "original-price-quantity-epoch"
            )
            reset = None
            if code in previous_epoch and previous_epoch[code] != epoch:
                reset = "corporate_action_coordinate_change"
            previous_epoch[code] = epoch
            quality_reason = None
            if not member:
                quality_reason = "security_outside_decision_universe"
            elif member.get("source_record_id") is None:
                quality_reason = "missing_daily_bar"
            row = DailyInput(
                security_code=code,
                market_date=day,
                session_index=session_index,
                cutoff=datetime.combine(day, time(15, 30), ZoneInfo("Asia/Shanghai")),
                security_id=str(member.get("security_id") or ""),
                listing_id=str(member.get("listing_id") or ""),
                exchange=str(member.get("exchange") or ""),
                high=member.get("high"),
                low=member.get("low"),
                close=member.get("close"),
                volume=member.get("volume"),
                source_record_id=str(member.get("source_record_id") or ""),
                source_revision=str(member.get("source_revision") or ""),
                source_artifact_sha256=_development_source._normal_sha(
                    member.get("source_artifact_sha256")
                ),
                dwd_known_from=str(member.get("dwd_known_from") or ""),
                coordinate_epoch=epoch,
                finality="final",
                quality_reason=quality_reason,
                reset_reason=reset,
                calendar_member_digest=digest(calendar[day]),
                action_member_digest=digest(known_actions),
            )
            output.append(admit_daily(row, policy))

    admissions = tuple(output)
    if any(item.row.market_date > _ARBITRATION_END for item in admissions):
        raise StageQualificationSourceError("2025 business row escaped qualification guard")
    evaluation = [
        item
        for item in admissions
        if scope.report_start <= item.row.market_date <= scope.report_end
    ]
    if not evaluation:
        raise StageQualificationSourceError("qualification evaluation interval is empty")
    if any(item.row.market_date < _ARBITRATION_START for item in evaluation):
        raise StageQualificationSourceError("development row entered qualification result interval")

    rejections: dict[str, int] = {}
    for item in evaluation:
        if not item.admitted:
            rejections[item.reason] = rejections.get(item.reason, 0) + 1
    manifest = {
        "schema_version": "quanthus.stage-qualification-source.v1",
        "knowledge_view": "retrospective_dwd_record_view",
        "partition": "2022-2024-arbitration",
        "scope": scope.to_dict(),
        "security_codes": security_codes,
        "attempted": len(evaluation),
        "admitted": sum(item.admitted for item in evaluation),
        "rejected": sum(not item.admitted for item in evaluation),
        "rejection_reasons": dict(sorted(rejections.items())),
        "warmup_attempted": sum(item.row.market_date < scope.report_start for item in admissions),
        "source_member_closure_digest": digest([item.row.member_digest for item in admissions]),
        "base_manifest_sha256": source_binding["base"]["manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction"]["manifest_sha256"],
        "physical_holdout_exposure": bool(
            source_binding.get("full_base_physically_contains_holdout_periods")
        ),
        "read_guard": "pre_2022_warmup_plus_2022_2024_arbitration_only_no_2025",
        "parameter_search_authorized": False,
        "future_target_read": False,
        "permanent_blind_read": False,
    }
    return admissions, manifest, policy
