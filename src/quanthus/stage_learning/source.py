"""Development-only DWD reader for the stock-stage historical harness."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.structure_v2_inputs import Admission, DailyInput, InputPolicy, admit_daily, digest

_DEVELOPMENT_START = date(2012, 1, 1)
_DEVELOPMENT_END = date(2021, 12, 31)
_SSE = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
_SZSE = "venue_8c0bd9088a0dc88f28e5e7fa8efdce87"
_CODE = re.compile(r"^[0-9]{6}$")
_SHA = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class StageDevelopmentSourceError(ValueError):
    """Raised when a development read would cross its source or partition contract."""


@dataclass(frozen=True)
class DevelopmentReadScope:
    scan_start: date
    report_start: date
    report_end: date

    def __post_init__(self) -> None:
        if not self.scan_start <= self.report_start <= self.report_end:
            raise StageDevelopmentSourceError("development read dates are not ordered")
        if self.scan_start < _DEVELOPMENT_START or self.report_end > _DEVELOPMENT_END:
            raise StageDevelopmentSourceError(
                "development reader refuses dates outside 2012-01-01..2021-12-31"
            )

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(range(self.scan_start.year, self.report_end.year + 1))

    def to_dict(self) -> dict[str, str]:
        return {
            "scan_start": self.scan_start.isoformat(),
            "report_start": self.report_start.isoformat(),
            "report_end": self.report_end.isoformat(),
        }


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageDevelopmentSourceError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageDevelopmentSourceError(f"{label} must be a JSON object")
    return value


def _expected_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise StageDevelopmentSourceError(f"{label} SHA-256 is invalid")
    return value if value.startswith("sha256:") else "sha256:" + value


def _source_roots(
    training_root: Path, node_config: Mapping[str, Any]
) -> tuple[Path, Path, dict[str, Any]]:
    data = node_config.get("data_source")
    if not isinstance(data, Mapping):
        raise StageDevelopmentSourceError("training node data_source is missing")
    if data.get("read_guard_required") is not True:
        raise StageDevelopmentSourceError("development source requires an explicit read guard")
    source_relative = data.get("source_relative_path")
    if not isinstance(source_relative, str) or not source_relative:
        raise StageDevelopmentSourceError("training source relative path is invalid")
    source_root = training_root.expanduser().resolve() / source_relative
    base_cfg = data.get("base")
    correction_cfg = data.get("correction")
    if not isinstance(base_cfg, Mapping) or not isinstance(correction_cfg, Mapping):
        raise StageDevelopmentSourceError("base/correction bindings are missing")
    base_relative = base_cfg.get("relative_path")
    correction_relative = correction_cfg.get("relative_path")
    if not isinstance(base_relative, str) or not isinstance(correction_relative, str):
        raise StageDevelopmentSourceError("base/correction relative path is invalid")
    base = source_root / base_relative
    correction = source_root / correction_relative
    for root, cfg, label in (
        (base, base_cfg, "base"),
        (correction, correction_cfg, "correction"),
    ):
        if not root.is_dir():
            raise StageDevelopmentSourceError(f"{label} source root is missing: {root}")
        expected = _expected_sha(cfg.get("manifest_sha256"), f"{label} manifest")
        observed = _sha256(root / "manifest.json")
        if observed != expected:
            raise StageDevelopmentSourceError(f"{label} manifest SHA-256 differs")
        try:
            ready = (root / "READY").read_text(encoding="ascii").strip()
        except OSError as exc:
            raise StageDevelopmentSourceError(f"{label} READY is unreadable") from exc
        if ready != expected:
            raise StageDevelopmentSourceError(f"{label} READY does not bind its manifest")
    return base, correction, dict(data)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _path_list(paths: tuple[str, ...]) -> str:
    return "[" + ",".join(_sql_literal(item) for item in paths) + "]"


def _partition_paths(base: Path, scope: DevelopmentReadScope) -> dict[str, tuple[str, ...]]:
    payload = base / "payload"
    paths = {
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
    if any(year > _DEVELOPMENT_END.year for year in scope.years):
        raise StageDevelopmentSourceError("post-development partition path was constructed")
    return paths


def _rows(cursor: Any) -> list[dict[str, Any]]:
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _normal_sha(value: object) -> str:
    text = str(value or "")
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return "sha256:" + text
    return text


def read_development_admissions(
    *,
    training_root: Path,
    node_config: Mapping[str, Any],
    scope: DevelopmentReadScope,
    security_codes: tuple[str, ...],
    spec_digest: str,
    calendar_ref: str = "reference.calendar.trading-session:SSE-SZSE-daily-v1",
) -> tuple[tuple[Admission, ...], dict[str, Any], InputPolicy]:
    """Read only admitted Development dates and project them to the accepted StructureV2 input.

    The full Base may physically contain later periods, but all date-partition path construction
    and returned business rows are guarded by DevelopmentReadScope before DuckDB is opened.
    Unpartitioned identity/action tables are filtered by the same end date before values enter the
    returned objects.
    """
    if (
        type(security_codes) is not tuple
        or not security_codes
        or len(set(security_codes)) != len(security_codes)
        or any(not _CODE.fullmatch(code) for code in security_codes)
    ):
        raise StageDevelopmentSourceError("security_codes must be unique six-digit codes")
    base, correction, source_binding = _source_roots(training_root, node_config)
    paths = _partition_paths(base, scope)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise StageDevelopmentSourceError("development reader requires duckdb") from exc

    payload = base / "payload"
    correction_payload = correction / "payload"
    codes = ",".join("?" for _ in security_codes)
    start = scope.scan_start.isoformat()
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
                _sql_literal(str(payload / 'listed_company/dwd_listing.parquet'))
            })
            WHERE knowledge_to IS NULL;
            CREATE VIEW dwd_security AS
            SELECT * FROM read_parquet({
                _sql_literal(str(payload / 'listed_company/dwd_security.parquet'))
            })
            WHERE knowledge_to IS NULL;
            CREATE VIEW dwd_bar AS
            SELECT * FROM read_parquet({_path_list(paths['bar'])}, hive_partitioning=true)
            WHERE knowledge_to IS NULL
              AND interval_size='1d' AND price_basis='UNADJUSTED'
              AND CAST(observation_start AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_calendar AS
            SELECT * FROM read_parquet({_path_list(paths['calendar'])}, hive_partitioning=true)
            WHERE knowledge_to IS NULL AND is_open
              AND CAST(trade_date AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW correction_state AS
            SELECT * FROM read_parquet({
                _sql_literal(
                    str(
                        correction_payload
                        / 'tradability_state/dwd_tradability_state_replacement.parquet'
                    )
                )
            })
            WHERE knowledge_to IS NULL
              AND CAST(observation_start AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_state AS
            SELECT base_state.*
            FROM read_parquet({_path_list(paths['state'])}, hive_partitioning=true) base_state
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
        members = _rows(
            connection.execute(
                f"""
                SELECT listing.listing_code AS security_code,
                       security.security_id,
                       listing.listing_id,
                       listing.venue_id,
                       CASE listing.venue_id
                         WHEN '{_SSE}' THEN 'SSE'
                         WHEN '{_SZSE}' THEN 'SZSE'
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
                WHERE listing.venue_id IN ('{_SSE}','{_SZSE}')
                  AND listing.listing_code IN ({codes})
                ORDER BY market_date, security_code
                """,
                list(security_codes),
            )
        )
        calendar_rows = _rows(
            connection.execute(
                f"""
                SELECT * FROM dwd_calendar
                WHERE venue_id IN ('{_SSE}','{_SZSE}')
                ORDER BY trade_date, venue_id
                """
            )
        )
        actions_path = correction_payload / "replay/corporate_actions_normalized.parquet"
        actions = _rows(
            connection.execute(
                f"""
                SELECT * FROM read_parquet({_sql_literal(str(actions_path))})
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
            raise StageDevelopmentSourceError("DWD projection has ambiguous security/date grain")
        member_map[key] = item
    calendar: dict[date, list[dict[str, Any]]] = {}
    for item in calendar_rows:
        calendar.setdefault(item["trade_date"], []).append(item)
    if not calendar:
        raise StageDevelopmentSourceError("development calendar is empty")
    action_map: dict[str, list[dict[str, Any]]] = {code: [] for code in security_codes}
    for item in actions:
        action_map[str(item["security_code"])].append(item)

    policy = InputPolicy(spec_digest, security_codes, calendar_ref)
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
                source_artifact_sha256=_normal_sha(member.get("source_artifact_sha256")),
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
    if any(item.row.market_date > _DEVELOPMENT_END for item in admissions):
        raise StageDevelopmentSourceError("post-development business row escaped read guard")
    rejections: dict[str, int] = {}
    for item in admissions:
        if not item.admitted:
            rejections[item.reason] = rejections.get(item.reason, 0) + 1
    manifest = {
        "schema_version": "quanthus.stage-development-source.v1",
        "knowledge_view": "retrospective_dwd_record_view",
        "scope": scope.to_dict(),
        "security_codes": security_codes,
        "attempted": len(admissions),
        "admitted": sum(item.admitted for item in admissions),
        "rejected": sum(not item.admitted for item in admissions),
        "rejection_reasons": dict(sorted(rejections.items())),
        "source_member_closure_digest": digest([item.row.member_digest for item in admissions]),
        "base_manifest_sha256": source_binding["base"]["manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction"]["manifest_sha256"],
        "physical_holdout_exposure": bool(
            source_binding.get("full_base_physically_contains_holdout_periods")
        ),
        "read_guard": "development_business_dates_only_2012_2021",
        "no_future_target_read": True,
        "no_classifier_training": True,
    }
    return admissions, manifest, policy
