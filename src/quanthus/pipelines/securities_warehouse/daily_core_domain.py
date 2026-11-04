"""Build a versioned immutable DWD increment from a sealed ODS product catalog."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release
from quanthus.pipelines.securities_warehouse.base22_sources import (
    SourceAssessmentError,
    disjoint_output,
    physical_path,
)
from quanthus.pipelines.securities_warehouse.base24_image import KIND as NATIVE_BASE24_KIND
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    DAILY_CORE_PRODUCT_SCOPE,
    LEGACY_PRODUCT_SCOPE,
    DailyOdsCatalogError,
    _daily_core_quality_sha256,
    verify_daily_ods_catalog,
)
from quanthus.pipelines.securities_warehouse.daily_domain import (
    DailyDomainBuildError,
    DailyDomainResult,
)
from quanthus.pipelines.securities_warehouse.native_base23_reader import (
    KIND as NATIVE_BASE23_KIND,
)
from quanthus.pipelines.securities_warehouse.native_base23_reader import (
    NativeBase23,
    NativeBase23Error,
    read_native_parent_references,
    require_daily_core_parent_admission,
    verify_native_prior_transition,
)
from quanthus.pipelines.securities_warehouse.native_base24_reader import (
    read_native_parent_references as read_base24_parents,
)
from quanthus.pipelines.securities_warehouse.native_base24_reader import (
    require_daily_core_governance,
)

# Shared with the focused SQL regression: unknown is never a normal ST observation.
ST_BUY_SQL = "(o.tradestatus='1' AND coalesce(o.isST IN ('0','1'),false))"
ST_REASON_SQL = """CASE WHEN o.tradestatus<>'1' THEN 'SOURCE_NOT_TRADABLE'
    WHEN o.isST IS NULL OR o.isST NOT IN ('0','1') THEN 'ST_UNKNOWN'
    WHEN o.isST='1' THEN 'ST_TRADABLE' ELSE 'NORMAL' END"""


_TABLE_KEYS = {
    "dwd_trading_calendar": ("venue_id", "trade_date", "session_type"),
    "dwd_security_bar": ("listing_id", "observation_start", "interval_size", "price_basis"),
    "dwd_security_valuation": (
        "security_id",
        "observation_start",
        "measure_type",
        "methodology",
    ),
    "dwd_index_bar": ("index_id", "observation_start", "interval_size"),
    "dwd_tradability_state": ("listing_id", "calendar_session_id", "observation_start"),
    "dwd_disclosure": ("disclosure_id", "effective_at"),
    "dwd_disclosure_subject": ("disclosure_id", "disclosure_subject_id", "effective_at"),
}

_RECOGNIZED_ODS_ONLY_DAILY_ARTIFACTS = {
    ("calendar_daily_events", "financial_disclosures_delta"): (
        "deadline-filtered quarterly snapshot rows are already modeled in the governed "
        "financial-reporting Base; publish_deadline is not disclosure-event evidence"
    ),
}

_DEFERRED_DAILY_ARTIFACTS = (
    ("market_daily_core", "adjustment_evidence_delta"),
    ("calendar_daily_events", "suspensions_delta"),
    ("calendar_daily_events", "corporate_actions_delta"),
    ("calendar_daily_events", "dividends_delta"),
    ("calendar_daily_events", "factor_evidence_delta"),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyDomainBuildError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DailyDomainBuildError(f"JSON root is not an object: {path}")
    return value


def _sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stable_id_sql(kind: str, *expressions: str) -> str:
    fields = [_sql("securities-warehouse-v1"), _sql(kind), *expressions]
    payload = f"concat_ws(chr(31), {','.join(fields)})"
    return f"{_sql(kind + '_')} || substr(sha256({payload}),1,32)"


def _safe_path(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise DailyDomainBuildError("relative path must be a string")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() == "." or ".." in relative.parts:
        raise DailyDomainBuildError("unsafe relative path")
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise DailyDomainBuildError("relative path escapes root")
    return path


def _write_atomic(path: Path, value: object) -> None:
    payload = value if isinstance(value, bytes) else _canonical_bytes(value) + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _base_files(
    base_root: Path,
    manifest: Mapping[str, Any] | NativeBase23,
    table: str,
) -> list[Path]:
    if isinstance(manifest, NativeBase23):
        return manifest.files(table)
    result = []
    for raw in manifest.get("files", []):
        if not isinstance(raw, Mapping):
            continue
        relative = str(raw.get("path", ""))
        if f"/{table}/" in relative or relative.endswith(f"/{table}.parquet"):
            result.append(_safe_path(base_root, relative))
    if not result:
        raise DailyDomainBuildError(f"DWD Base does not contain required table: {table}")
    return sorted(result)


def _read_catalog(catalog_root: Path, release_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        verified = verify_daily_ods_catalog(catalog_root)
    except DailyOdsCatalogError as exc:
        raise DailyDomainBuildError(str(exc)) from exc
    catalog = _object(verified.root / "catalog.json")
    selected: dict[str, Any] = {}
    for lane in catalog["lanes"]:
        release_dir = _safe_path(release_root, lane["release_path"])
        try:
            release = verify_ods_release(release_dir)
        except OdsReleaseError as exc:
            raise DailyDomainBuildError(f"selected ODS release failed verification: {exc}") from exc
        if release.manifest_sha256 != lane["release_manifest_sha256"]:
            raise DailyDomainBuildError(
                "selected ODS release manifest changed after catalog sealing"
            )
        artifacts = {
            artifact.name: (release_dir.joinpath(*artifact.relative_path.parts), artifact)
            for artifact in release.release.artifacts
        }
        selected[lane["lane"]] = {
            "release_id": release.release.release_id,
            "manifest_sha256": release.manifest_sha256,
            "artifacts": artifacts,
        }
    return catalog, selected


def _artifact(selected: Mapping[str, Any], lane: str, name: str) -> tuple[Path, Any]:
    try:
        path, artifact = selected[lane]["artifacts"][name]
        return Path(path), artifact
    except KeyError as exc:
        raise DailyDomainBuildError(f"required daily artifact is absent: {lane}/{name}") from exc


def _audit_non_output_daily_artifacts(
    selected: Mapping[str, Any], *, parquet: Any, closed_day: bool = False
) -> list[dict[str, Any]]:
    """Account for recognized ODS-only inputs and fail closed on deferred non-empty inputs."""

    audited: list[dict[str, Any]] = []
    if closed_day and set(selected) != {"calendar_daily_events"}:
        raise DailyDomainBuildError("closed-day source scope differs")
    scoped_exclusions = {}
    for lane, value in selected.items():
        quality = value["artifacts"].get("acquisition_quality")
        if quality:
            ledger = _object(quality[0])
            policy_path = (
                Path(__file__).resolve().parents[4]
                / "config/pipelines/p10-acquisition-prevention.v1.json"
            )
            policy = _object(policy_path)
            if ledger.get("policy_id") != policy["policy_id"] or ledger.get(
                "policy_sha256"
            ) != _sha256(policy_path):
                raise DailyDomainBuildError("acquisition quality policy differs")
            for entry in ledger["datasets"]:
                if entry["dataset"] in policy["daily_core_excluded_domains"]:
                    if (
                        entry.get("current_refresh_proven") is not False
                        or entry.get("watermark_advanced") is not False
                    ):
                        raise DailyDomainBuildError(
                            "excluded source falsely claims current refresh"
                        )
                    scoped_exclusions[lane, entry["dataset"]] = entry
                    audited.append(
                        {
                            "lane": lane,
                            "artifact": entry["dataset"],
                            "disposition": "unknown_source_excluded_from_daily_core",
                            "reason": entry["reason_code"],
                            "row_count": entry["projection_rows"],
                            "blocked_uses": entry["blocked_uses"],
                        }
                    )
    for (lane, name), reason in sorted(_RECOGNIZED_ODS_ONLY_DAILY_ARTIFACTS.items()):
        if (lane, name) in scoped_exclusions:
            continue
        path, artifact = _artifact(selected, lane, name)
        row_count = int(parquet.ParquetFile(path).metadata.num_rows)
        audited.append(
            {
                "lane": lane,
                "artifact": name,
                "disposition": "ods_only",
                "row_count": row_count,
                "source_artifact_sha256": str(artifact.sha256),
                "reason": reason,
            }
        )

    for lane, name in _DEFERRED_DAILY_ARTIFACTS:
        if closed_day and lane != "calendar_daily_events":
            continue
        if (lane, name) in scoped_exclusions:
            continue
        path, _artifact_contract = _artifact(selected, lane, name)
        row_count = int(parquet.ParquetFile(path).metadata.num_rows)
        if row_count:
            raise DailyDomainBuildError(
                f"non-empty daily artifact has no daily DWD handler: {lane}/{name} "
                f"({row_count} rows)"
            )
    return audited


def _one(connection: Any, query: str, parameters: Sequence[Any] | None = None) -> tuple[Any, ...]:
    cursor = (
        connection.execute(query) if parameters is None else connection.execute(query, parameters)
    )
    row = cursor.fetchone()
    if row is None:
        raise DailyDomainBuildError("daily audit query returned no result")
    return tuple(row)


def _copy(connection: Any, query: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"COPY ({query}) TO {_sql(str(output))} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
    )
    os.chmod(output, 0o640)


def _lineage(path: Path, artifact: Any, alias: str) -> str:
    return f"""
      {_sql(str(artifact.source_revision).split("@")[0])}::VARCHAR AS record_source,
      {alias}.source_record_id,
      {_sql(artifact.source_revision)}::VARCHAR AS source_revision,
      {_sql(artifact.sha256)}::VARCHAR AS source_artifact_sha256,
      CAST({alias}.acquired_at AS TIMESTAMPTZ) AS knowledge_from,
      NULL::TIMESTAMPTZ AS knowledge_to
    """


def _path_relation(paths: Sequence[Path]) -> str:
    return "read_parquet([" + ",".join(_sql(str(path)) for path in paths) + "])"


def _verify_base(
    base_root: Path,
    release_config: Path,
) -> tuple[dict[str, Any] | NativeBase23, dict[str, Any]]:
    manifest = _object(base_root / "manifest.json")
    config = _object(release_config)
    if manifest.get("kind") == NATIVE_BASE24_KIND:
        try:
            require_daily_core_governance(config)
            native = read_base24_parents(base_root, config)
        except NativeBase23Error as exc:
            raise DailyDomainBuildError(str(exc)) from exc
        return native, config
    if manifest.get("kind") == NATIVE_BASE23_KIND:
        try:
            native = read_native_parent_references(base_root, config)
            require_daily_core_parent_admission(config)
        except NativeBase23Error as exc:
            raise DailyDomainBuildError(str(exc)) from exc
        return native, config
    manifest_sha = _sha256(base_root / "manifest.json")
    try:
        ready = (base_root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DailyDomainBuildError("DWD Base READY is unreadable") from exc
    config = _object(release_config)
    expected = config.get("layers", {}).get("dwd", {})
    if (
        ready != manifest_sha
        or manifest.get("kind") != "dwd_domain_base_package"
        or manifest.get("package_id") != expected.get("package_id")
        or manifest_sha != expected.get("manifest_sha256")
        or manifest.get("content_digest") != expected.get("content_digest")
    ):
        raise DailyDomainBuildError("DWD Base does not match the governed release config")
    return manifest, config


def _prior_binding(pointer: Path | None, market_date: date, base_through: date) -> tuple[date, Any]:
    if pointer is None or not pointer.exists():
        return base_through, None
    descriptor = _object(pointer)
    manifest_path = _safe_path(pointer.parent.resolve(), descriptor.get("manifest_path"))
    if _sha256(manifest_path) != descriptor.get("manifest_sha256"):
        raise DailyDomainBuildError("daily CURRENT no longer binds its manifest")
    prior = _object(manifest_path)
    prior_date = date.fromisoformat(str(prior.get("market_date")))
    if prior_date >= market_date:
        raise DailyDomainBuildError("daily CURRENT must precede the requested market date")
    return prior_date, {
        "package_id": prior["package_id"],
        "manifest_sha256": descriptor["manifest_sha256"],
        "market_date": prior_date.isoformat(),
    }


def _verify_daily_core_calendar_continuity(
    calendar_path: Path,
    *,
    after: date,
    target: date,
    parquet: Any,
) -> None:
    """Do not silently advance a daily-only product over unbuilt open sessions.

    The source's own SHA/READY was already verified by the ODS catalog, but
    missing or disagreeing exchange calendar rows must fail closed. We require
    continuous market-date coverage and check both SSE/SZSE independently.
    """
    try:
        rows = parquet.read_table(
            calendar_path, columns=["exchange", "trade_date", "is_open", "session"]
        ).to_pylist()
    except (OSError, ValueError, KeyError) as exc:
        raise DailyDomainBuildError("daily-core calendar provenance is unreadable") from exc
    sessions: dict[date, dict[str, int]] = {}
    for row in rows:
        day = row.get("trade_date")
        venue = row.get("exchange")
        if not isinstance(day, date) or day <= after or day > target:
            continue
        if venue not in ("SSE", "SZSE") or row.get("session") != "day":
            continue
        if day in sessions and venue in sessions[day]:
            raise DailyDomainBuildError("duplicate daily-core exchange session")
        flag = row.get("is_open")
        if flag not in (0, 1) or isinstance(flag, bool):
            raise DailyDomainBuildError("unverified daily-core exchange calendar status")
        sessions.setdefault(day, {})[venue] = int(flag)
    cursor = after + timedelta(days=1)
    while cursor <= target:
        pair = sessions.get(cursor)
        if pair is None or set(pair) != {"SSE", "SZSE"}:
            raise DailyDomainBuildError(
                f"daily-core trading calendar date coverage missing: {cursor}"
            )
        if pair["SSE"] != pair["SZSE"]:
            raise DailyDomainBuildError(f"daily-core SSE/SZSE status disagreement: {cursor}")
        if cursor == target:
            if pair["SSE"] != 1:
                raise DailyDomainBuildError("daily-core target is not an open session")
        elif pair["SSE"] == 1:
            raise DailyDomainBuildError(
                f"daily-core would silently skip required open session: {cursor}"
            )
        cursor += timedelta(days=1)


def _reuse_base24_output(output: Path, identity: Mapping[str, Any]) -> DailyDomainResult:
    result = verify_daily_domain_increment(output)
    if _object(output / "manifest.json").get("job_identity") != dict(identity):
        raise DailyDomainBuildError("existing Base 2.4 Daily job identity differs")
    return result


def _project_announcements(
    connection: Any, *, path: Path, artifact: Any, work: Path,
    after: date, through: date, check_identity: bool,
) -> tuple[Path, Path, int]:
    """One source-preserving converter shared by Daily and Base closed days.

    Caller supplies already qualified source and parent views. The converter
    retains publish/acquisition clocks, stable ids and unavailable evidence.
    """
    pq = importlib.import_module("pyarrow.parquet")
    announcements_path, announcements_artifact = path, artifact
    covered_after, market_date = after, through
    universe_artifact = check_identity
    disclosure_id = _stable_id_sql("disclosure", _sql("announcement_archive"), "o.ann_id")
    subject_id = _stable_id_sql(
        "disclosure_subject", disclosure_id, _sql("SECURITY"), "l.security_id"
    )
    ann_source = f"""SELECT *, concat({_sql(announcements_path.name)},'#ann_id=',ann_id) source_record_id
    FROM read_parquet({_sql(str(announcements_path))})
    WHERE CAST(announce_date AS DATE)>{_sql(covered_after.isoformat())}::DATE
      AND CAST(announce_date AS DATE)<={_sql(market_date.isoformat())}::DATE"""
    effective = "CAST(o.publish_time AS TIMESTAMP) AT TIME ZONE 'Asia/Shanghai'"
    held_disclosure_count = 0
    if universe_artifact:
        identity_clause = """EXISTS (SELECT 1 FROM base_listing l JOIN base_security s ON s.security_id=l.security_id
            WHERE o.stock_code=l.listing_code
            AND CAST(o.announce_date AS DATE)>=CAST(l.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
            AND (l.effective_to IS NULL OR CAST(o.announce_date AS DATE)<CAST(l.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE))
            AND CAST(o.announce_date AS DATE)>=CAST(s.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
            AND (s.effective_to IS NULL OR CAST(o.announce_date AS DATE)<CAST(s.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE)))"""
        held_path = work / "quality/disclosure-unavailable.parquet"
        _copy(
            connection,
            f"SELECT o.*, 'LISTING_NOT_EFFECTIVE_OR_IDENTITY_UNAVAILABLE'::VARCHAR reason_code FROM ({ann_source}) o WHERE NOT {identity_clause}",
            held_path,
        )
        held_disclosure_count = pq.ParquetFile(held_path).metadata.num_rows
        ann_source = f"SELECT o.* FROM ({ann_source}) o WHERE {identity_clause}"
    disclosure_output = (
        work
        / f"payload/disclosure_company_event/dwd_disclosure/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT {_lineage(announcements_path, announcements_artifact, "o")},
        {effective} AS effective_at, {effective} AS announced_at,
        {disclosure_id} AS disclosure_id, s.company_id AS company_id,
        'announcement'::VARCHAR AS disclosure_type,
        o.ann_id AS source_document_id, o.title AS title,
        o.url AS document_uri, 'zh-CN'::VARCHAR AS "language"
        FROM ({ann_source}) o JOIN base_listing l ON o.stock_code=l.listing_code
        AND CAST(o.announce_date AS DATE)>=CAST(l.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
        AND (l.effective_to IS NULL OR CAST(o.announce_date AS DATE)<CAST(l.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE))
        JOIN base_security s ON s.security_id=l.security_id
        AND CAST(o.announce_date AS DATE)>=CAST(s.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
        AND (s.effective_to IS NULL OR CAST(o.announce_date AS DATE)<CAST(s.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE))""",
        disclosure_output,
    )
    subject_output = (
        work
        / f"payload/disclosure_company_event/dwd_disclosure_subject/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT {_lineage(announcements_path, announcements_artifact, "o")},
        {effective} effective_at, {effective} announced_at, {subject_id} disclosure_subject_id,
        {disclosure_id} disclosure_id, 'SECURITY'::VARCHAR subject_type, l.security_id,
        NULL::VARCHAR corporate_action_id, NULL::VARCHAR financial_report_id
        FROM ({ann_source}) o JOIN base_listing l ON o.stock_code=l.listing_code
        AND CAST(o.announce_date AS DATE)>=CAST(l.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
        AND (l.effective_to IS NULL OR CAST(o.announce_date AS DATE)<CAST(l.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE))""",
        subject_output,
    )

    return disclosure_output, subject_output, held_disclosure_count


def _project_trading_calendar(
    connection: Any, *, path: Path, artifact: Any, work: Path, market_date: date,
    record_filename: str | None = None,
) -> Path:
    """Project one observed calendar day, preserving source knowledge and clock."""
    trading_calendar_path, trading_calendar_artifact = path, artifact
    record_filename = record_filename or path.name
    calendar_session_id = _stable_id_sql(
        "calendar_session", "v.venue_id", "CAST(o.trade_date AS VARCHAR)", _sql("day")
    )
    calendar_output = (
        work
        / f"payload/tradability_state/dwd_trading_calendar/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT o.source::VARCHAR AS record_source,
        concat({_sql(record_filename)},'#exchange=',o.exchange,
               '&trade_date=',CAST(o.trade_date AS VARCHAR),'&session=day') AS source_record_id,
        {_sql(trading_calendar_artifact.source_revision)}::VARCHAR AS source_revision,
        {_sql(trading_calendar_artifact.sha256)}::VARCHAR AS source_artifact_sha256,
        CAST(o.built_at AS TIMESTAMPTZ) AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
        o.trade_date::TIMESTAMP AT TIME ZONE 'Asia/Shanghai' AS observation_start,
        (o.trade_date::TIMESTAMP AT TIME ZONE 'Asia/Shanghai')+INTERVAL 1 DAY AS observation_end,
        {calendar_session_id} AS calendar_session_id, v.venue_id, o.trade_date,
        'day'::VARCHAR AS session_type, CAST(o.is_open AS BOOLEAN) AS is_open,
        CASE WHEN o.is_open=1 THEN (o.trade_date+TIME '09:30:00') AT TIME ZONE 'Asia/Shanghai' END AS open_at,
        CASE WHEN o.is_open=1 THEN (o.trade_date+TIME '15:00:00') AT TIME ZONE 'Asia/Shanghai' END AS close_at,
        CASE WHEN o.is_open=1 THEN NULL ELSE 'SCHEDULED_CLOSED' END::VARCHAR AS calendar_reason
        FROM read_parquet({_sql(str(trading_calendar_path))}) o JOIN base_venue v
          ON v.mic_code=CASE o.exchange WHEN 'SSE' THEN 'XSHG'
             WHEN 'SZSE' THEN 'XSHE' WHEN 'BSE' THEN 'BJSE' END
        WHERE o.trade_date={_sql(market_date.isoformat())}::DATE""",
        calendar_output,
    )
    return calendar_output


def _project_daily_tables(
    connection: Any, *, selected: Mapping[str, Any], work: Path,
    market_date: date, covered_after: date, catalog_digest: str,
    bar_sources: list[tuple[Path, Any, str, str]], check_identity: bool,
) -> tuple[dict[str, Path], int]:
    """Shared exact source projection; callers own qualification and comparison."""
    valuation_path, valuation_artifact = _artifact(selected, "market_daily_core", "valuation_daily_delta")
    index_path, index_artifact = _artifact(selected, "market_daily_core", "index_daily_delta")
    status_path, status_artifact = _artifact(selected, "market_daily_core", "daily_status_observation_delta")
    trading_calendar_path, trading_calendar_artifact = _artifact(selected, "market_daily_core", "trading_calendar")
    announcements_path, announcements_artifact = _artifact(selected, "calendar_daily_events", "announcements_delta")
    catalog = {"catalog_digest": catalog_digest}
    universe_artifact = check_identity
    calendar_output = _project_trading_calendar(
        connection, path=trading_calendar_path, artifact=trading_calendar_artifact,
        work=work, market_date=market_date,
    )
    connection.execute(
        f"CREATE VIEW daily_calendar AS SELECT * FROM read_parquet({_sql(str(calendar_output))})"
    )

    bar_parts = []
    for path, artifact, interval, subtract in bar_sources:
        if interval == "1d":
            raw_start = "CAST(o.date AS DATE)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai'"
            raw_end = f"({raw_start}) + INTERVAL 1 DAY"
            source = f"SELECT *, concat({_sql(str(path.name))},'#code=',stock_code,'&date=',date) source_record_id FROM read_parquet({_sql(str(path))})"
        else:
            label = "CAST(o.ts AS TIMESTAMP) AT TIME ZONE 'Asia/Shanghai'"
            raw_start = f"({label}) - {subtract}"
            raw_end = label
            source = f"SELECT *, concat({_sql(str(path.name))},'#code=',stock_code,'&ts=',ts) source_record_id FROM read_parquet({_sql(str(path))})"
        bar_parts.append(
            f"""SELECT {_lineage(path, artifact, "o")},
            {raw_start} AS observation_start, {raw_end} AS observation_end,
            l.listing_id, c.calendar_session_id, {_sql(interval)}::VARCHAR interval_size,
            'none'::VARCHAR price_basis, l.listing_currency AS currency,
            CAST(o.open AS DECIMAL(38,10)) AS "open",
            CAST(o.high AS DECIMAL(38,10)) AS "high",
            CAST(o.low AS DECIMAL(38,10)) AS "low",
            CAST(o.close AS DECIMAL(38,10)) AS "close",
            CAST(o.volume AS DECIMAL(38,10)) AS volume,
            CAST(o.amount AS DECIMAL(38,10)) AS turnover,
            NULL::BIGINT AS trade_count
            FROM ({source}) o JOIN base_listing l ON o.stock_code=l.listing_code
            AND CAST(o.market_date AS DATE)>=CAST(l.effective_from AT TIME ZONE 'Asia/Shanghai' AS DATE)
            AND (l.effective_to IS NULL OR CAST(o.market_date AS DATE)<CAST(l.effective_to AT TIME ZONE 'Asia/Shanghai' AS DATE))
            JOIN daily_calendar c ON c.venue_id=l.venue_id AND c.trade_date=CAST(o.market_date AS DATE)
            AND c.session_type='day'"""
        )
    bar_output = (
        work
        / f"payload/security_market_observation/dwd_security_bar/market_date={market_date}/part-00000.parquet"
    )
    _copy(connection, " UNION ALL ".join(bar_parts), bar_output)

    valuation_source = f"""SELECT *, concat({_sql(valuation_path.name)},'#code=',stock_code,'&date=',date) source_record_id
    FROM read_parquet({_sql(str(valuation_path))})"""
    valuation_output = (
        work
        / f"payload/security_market_observation/dwd_security_valuation/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT {_lineage(valuation_path, valuation_artifact, "o")},
        CAST(o.date AS DATE)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai' observation_start,
        (CAST(o.date AS DATE)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai')+INTERVAL 1 DAY observation_end,
        l.security_id, l.listing_id, u.measure_type,
        CAST(u.measure_value AS DECIMAL(38,10)) measure_value,
        NULL::VARCHAR currency, 'provider_reported'::VARCHAR methodology,
        'ttm_or_reported'::VARCHAR fiscal_basis
        FROM ({valuation_source}) o JOIN base_listing l ON o.stock_code=l.listing_code
        CROSS JOIN LATERAL (VALUES ('pe_ttm',o.pe_ttm),('pb',o.pb),('ps_ttm',o.ps_ttm),('pcf_ttm',o.pcf_ttm)) u(measure_type,measure_value)""",
        valuation_output,
    )

    index_source = f"""SELECT *, concat({_sql(index_path.name)},'#index=',index_code,'&date=',date) source_record_id
    FROM read_parquet({_sql(str(index_path))})"""
    index_output = (
        work
        / f"payload/security_market_observation/dwd_index_bar/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT {_lineage(index_path, index_artifact, "o")},
        CAST(o.date AS DATE)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai' observation_start,
        (CAST(o.date AS DATE)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai')+INTERVAL 1 DAY observation_end,
        i.index_id, NULL::VARCHAR calendar_session_id, '1d'::VARCHAR interval_size,
        i.base_currency currency, CAST(o.open AS DECIMAL(38,10)) AS "open",
        CAST(o.high AS DECIMAL(38,10)) AS "high",
        CAST(o.low AS DECIMAL(38,10)) AS "low",
        CAST(o.close AS DECIMAL(38,10)) AS "close",
        CAST(o.volume AS DECIMAL(38,10)) AS volume,
        CAST(o.amount AS DECIMAL(38,10)) AS turnover
        FROM ({index_source}) o JOIN base_market_index i ON o.index_code=i.index_code""",
        index_output,
    )

    status_source = f"""SELECT *, concat({_sql(status_path.name)},'#code=',stock_code,'&date=',date) source_record_id
    FROM read_parquet({_sql(str(status_path))})"""
    status_output = (
        work
        / f"payload/tradability_state/dwd_tradability_state/market_date={market_date}/part-00000.parquet"
    )
    _copy(
        connection,
        f"""SELECT {_lineage(status_path, status_artifact, "o")},
        c.observation_start, c.observation_end, l.listing_id, c.calendar_session_id,
        o.tradestatus='1' is_tradable,
        {ST_BUY_SQL} can_buy,
        o.tradestatus='1' can_sell,
        {ST_REASON_SQL}::VARCHAR reason_code,
        NULL::DECIMAL(38,10) upper_price, NULL::DECIMAL(38,10) lower_price,
        {_sql(catalog["catalog_digest"])}::VARCHAR input_revision_digest
        FROM ({status_source}) o JOIN base_listing l ON o.stock_code=l.listing_code
        JOIN daily_calendar c ON c.venue_id=l.venue_id AND c.trade_date=CAST(o.market_date AS DATE)
        AND c.session_type='day'""",
        status_output,
    )

    disclosure_output, subject_output, held_disclosure_count = _project_announcements(
        connection, path=announcements_path, artifact=announcements_artifact, work=work,
        after=covered_after, through=market_date, check_identity=bool(universe_artifact),
    )

    outputs = {
        "dwd_trading_calendar": calendar_output,
        "dwd_security_bar": bar_output,
        "dwd_security_valuation": valuation_output,
        "dwd_index_bar": index_output,
        "dwd_tradability_state": status_output,
        "dwd_disclosure": disclosure_output,
        "dwd_disclosure_subject": subject_output,
    }
    return outputs, held_disclosure_count


def build_daily_domain_increment(
    *,
    catalog_root: Path,
    release_root: Path,
    base_root: Path,
    release_config: Path,
    output: Path,
    code_revision: str,
    prior_pointer: Path | None = None,
) -> DailyDomainResult:
    """Build and audit one Base-seeded daily DWD package without mutating Base."""

    requested_config = _object(release_config)
    is_base24 = requested_config.get("layers", {}).get("dwd", {}).get("format") == "native_base24"
    if is_base24:
        try:
            output = disjoint_output(output, [base_root, release_root, catalog_root])
            physical_path(release_config)
            if prior_pointer is not None:
                physical_path(prior_pointer)
        except SourceAssessmentError as exc:
            raise DailyDomainBuildError(str(exc)) from exc
    output = output.resolve()
    release_root = release_root.resolve()
    base_root = base_root.resolve()
    for source_root, label in ((release_root, "ODS"), (base_root, "DWD Base")):
        if output == source_root or output.is_relative_to(source_root):
            raise DailyDomainBuildError(f"daily output must be outside the {label} root")
    if output.exists() and not is_base24:
        if _object(output / "manifest.json").get("base", {}).get("kind") == NATIVE_BASE24_KIND:
            raise DailyDomainBuildError("Base 2.4 Daily requires its explicit governance config")
        return verify_daily_domain_increment(output)
    catalog, selected = _read_catalog(catalog_root.resolve(), release_root)
    market_date = date.fromisoformat(catalog["market_date"])
    base_manifest, config = _verify_base(base_root, release_config.resolve())
    if (
        isinstance(base_manifest, NativeBase23)
        and catalog.get("product_scope") != DAILY_CORE_PRODUCT_SCOPE
    ):
        raise DailyDomainBuildError("native Base parent references require daily-core-v1")
    base_through = date.fromisoformat(str(config["through_date"]))
    covered_after, prior = _prior_binding(prior_pointer, market_date, base_through)
    base_transition = None
    if isinstance(base_manifest, NativeBase23) and prior is not None:
        assert prior_pointer is not None
        descriptor = _object(prior_pointer)
        prior_path = _safe_path(prior_pointer.parent.resolve(), descriptor.get("manifest_path"))
        verify_daily_domain_increment(prior_path.parent)
        try:
            base_transition = verify_native_prior_transition(
                native=base_manifest,
                config=config,
                prior=_object(prior_path),
                prior_sha256=prior["manifest_sha256"],
            )
        except NativeBase23Error as exc:
            raise DailyDomainBuildError(str(exc)) from exc
    if market_date <= covered_after:
        raise DailyDomainBuildError("daily market_date does not advance Base or CURRENT")
    if len(code_revision) != 40 or any(ch not in "0123456789abcdef" for ch in code_revision):
        raise DailyDomainBuildError("code_revision must be a full lowercase Git revision")
    job_identity = None
    if is_base24:
        assert isinstance(base_manifest, NativeBase23)
        job_identity = {
            "base": dict(base_manifest.binding),
            "catalog": _sha256(catalog_root / "catalog.json"),
            "code": code_revision,
            "config": _sha256(release_config),
            "prior": prior,
        }
        if output.exists():
            return _reuse_base24_output(output, job_identity)

    try:
        import duckdb

        pq = importlib.import_module("pyarrow.parquet")
    except ModuleNotFoundError as exc:
        raise DailyDomainBuildError(
            "duckdb and pyarrow are required for daily DWD construction"
        ) from exc

    daily_path, daily_artifact = _artifact(selected, "market_daily_core", "daily_ohlcv_none_delta")
    valuation_path, valuation_artifact = _artifact(
        selected, "market_daily_core", "valuation_daily_delta"
    )
    index_path, index_artifact = _artifact(selected, "market_daily_core", "index_daily_delta")
    status_path, status_artifact = _artifact(
        selected, "market_daily_core", "daily_status_observation_delta"
    )
    trading_calendar_path, trading_calendar_artifact = _artifact(
        selected, "market_daily_core", "trading_calendar"
    )
    product_scope = catalog.get("product_scope", LEGACY_PRODUCT_SCOPE)
    if product_scope not in {LEGACY_PRODUCT_SCOPE, DAILY_CORE_PRODUCT_SCOPE}:
        raise DailyDomainBuildError("unsupported daily data product scope")
    if product_scope == DAILY_CORE_PRODUCT_SCOPE:
        _verify_daily_core_calendar_continuity(
            trading_calendar_path,
            after=covered_after,
            target=market_date,
            parquet=pq,
        )
    # A daily-core consumer declares NO dependency on intraday bars. We do not
    # replace the 1m source with guessed zeros, hidden 5m input, or an empty
    # 240-bar array. Other consumers must keep using the full legacy contract.
    bar_sources: list[tuple[Path, Any, str, str]] = [
        (daily_path, daily_artifact, "1d", "INTERVAL 0 SECOND"),
    ]
    if product_scope == LEGACY_PRODUCT_SCOPE:
        minute_path, minute_artifact = _artifact(selected, "intraday_1m", "intraday_bars")
        five_path, five_artifact = _artifact(selected, "intraday_5m", "intraday_bars")
        bar_sources.extend(
            [
                (minute_path, minute_artifact, "1m", "INTERVAL 1 MINUTE"),
                (five_path, five_artifact, "5m", "INTERVAL 5 MINUTE"),
            ]
        )
    announcements_path, announcements_artifact = _artifact(
        selected, "calendar_daily_events", "announcements_delta"
    )
    ods_only_artifacts = _audit_non_output_daily_artifacts(selected, parquet=pq)

    listing_files = _base_files(base_root, base_manifest, "dwd_listing")
    security_files = _base_files(base_root, base_manifest, "dwd_security")
    calendar_files = _base_files(base_root, base_manifest, "dwd_trading_calendar")
    market_index_files = _base_files(base_root, base_manifest, "dwd_market_index")
    venue_files = _base_files(base_root, base_manifest, "dwd_trading_venue")

    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect()
    connection.execute("SET threads=1")
    connection.execute("SET preserve_insertion_order=true")
    connection.execute("SET memory_limit='2GB'")
    try:
        universe_artifact = selected["market_daily_core"]["artifacts"].get("source_universe")
        if universe_artifact:
            from quanthus.pipelines.securities_warehouse.base23_identity_overlay import (
                build_overlay,
            )

            master, master_artifact = _artifact(selected, "market_daily_core", "security_master")
            overlay = build_overlay(
                universe=_object(universe_artifact[0]),
                master=master,
                listings=[
                    row for path in listing_files for row in pq.ParquetFile(path).read().to_pylist()
                ],
                venues=[
                    row for path in venue_files for row in pq.ParquetFile(path).read().to_pylist()
                ],
                output=work,
                source_sha256=str(master_artifact.sha256),
            )
            if "dwd_listing" in overlay:
                listing_files = [*listing_files, overlay["dwd_listing"]]
                security_files = [*security_files, overlay["dwd_security"]]
        connection.execute(
            f"CREATE VIEW base_listing AS SELECT * FROM {_path_relation(listing_files)}"
        )
        connection.execute(
            f"CREATE VIEW base_security AS SELECT * FROM {_path_relation(security_files)}"
        )
        connection.execute(
            f"CREATE VIEW base_calendar AS SELECT * FROM {_path_relation(calendar_files)}"
        )
        connection.execute(
            f"CREATE VIEW base_market_index AS SELECT * FROM {_path_relation(market_index_files)}"
        )
        connection.execute(f"CREATE VIEW base_venue AS SELECT * FROM {_path_relation(venue_files)}")

        outputs, held_disclosure_count = _project_daily_tables(
            connection, selected=selected, work=work, market_date=market_date,
            covered_after=covered_after, catalog_digest=catalog["catalog_digest"],
            bar_sources=bar_sources, check_identity=bool(universe_artifact),
        )
        row_counts: dict[str, int] = {}
        audit_tables = []
        for table, path in outputs.items():
            keys = ",".join(_TABLE_KEYS[table])
            rows, distinct_rows, missing_lineage = _one(
                connection,
                f"""SELECT count(*), count(DISTINCT ({keys})), count(*) FILTER (WHERE
                record_source IS NULL OR source_record_id IS NULL OR source_revision IS NULL OR
                source_artifact_sha256 IS NULL OR knowledge_from IS NULL) FROM read_parquet(?)""",
                [str(path)],
            )
            if rows != distinct_rows or missing_lineage:
                raise DailyDomainBuildError(f"{table} failed business-key or lineage audit")
            row_counts[table] = int(rows)
            audit_tables.append(
                {
                    "table": table,
                    "row_count": int(rows),
                    "distinct_business_keys": int(distinct_rows),
                }
            )
        expected = {
            "dwd_trading_calendar": _one(
                connection,
                "SELECT count(*) FROM read_parquet(?) WHERE trade_date=cast(? AS DATE)",
                [str(trading_calendar_path), market_date.isoformat()],
            )[0],
            "dwd_security_bar": sum(
                int(_one(connection, "SELECT count(*) FROM read_parquet(?)", [str(item[0])])[0])
                for item in bar_sources
            ),
            "dwd_security_valuation": _one(
                connection, "SELECT count(*)*4 FROM read_parquet(?)", [str(valuation_path)]
            )[0],
            "dwd_index_bar": _one(
                connection, "SELECT count(*) FROM read_parquet(?)", [str(index_path)]
            )[0],
            "dwd_tradability_state": _one(
                connection, "SELECT count(*) FROM read_parquet(?)", [str(status_path)]
            )[0],
            "dwd_disclosure": _one(
                connection,
                """SELECT count(*) FROM read_parquet(?)
                WHERE CAST(announce_date AS DATE)>CAST(? AS DATE)
                  AND CAST(announce_date AS DATE)<=CAST(? AS DATE)""",
                [str(announcements_path), covered_after.isoformat(), market_date.isoformat()],
            )[0],
        }
        full_disclosure_population = expected["dwd_disclosure"]
        expected["dwd_disclosure"] -= held_disclosure_count
        expected["dwd_disclosure_subject"] = expected["dwd_disclosure"]
        for table, count in expected.items():
            if row_counts[table] != count:
                raise DailyDomainBuildError(
                    f"{table} source-row conservation failed: expected {count}, got {row_counts[table]}"
                )
        audit = {
            "schema_version": 1,
            "kind": "cn01_daily_dwd_audit",
            "market_date": market_date.isoformat(),
            "covered_after_date": covered_after.isoformat(),
            "tables": audit_tables,
            "source_conservation": expected,
            "ods_only_artifacts": ods_only_artifacts,
            "disclosure_population": full_disclosure_population,
            "held_disclosures": held_disclosure_count,
            "held_disclosure_disposition": "unavailable; affected disclosure use held; original source preserved",
            "source_limitations": {
                "st_unknown": _one(
                    connection,
                    "SELECT count(*) FROM read_parquet(?) WHERE isST IS NULL OR isST NOT IN ('0','1')",
                    [str(status_path)],
                )[0],
                "valuation_null_fields": {
                    field: _one(
                        connection,
                        f"SELECT count(*) FROM read_parquet(?) WHERE {field} IS NULL",
                        [str(valuation_path)],
                    )[0]
                    for field in ("pe_ttm", "pb", "ps_ttm", "pcf_ttm")
                },
                "unknown_st_blocks_new_positions": True,
                "st_product_admission_unchanged": True,
            },
            "result": "accepted",
        }
        audit_path = work / "audit.json"
        _write_atomic(audit_path, audit)
        acquisition_universe = None
        universe_artifact = selected["market_daily_core"]["artifacts"].get("source_universe")
        if universe_artifact:
            acquisition_universe = _object(universe_artifact[0])
            if (
                acquisition_universe.get("market_date") != market_date.isoformat()
                or acquisition_universe.get("policy_id") != "p10-acquisition-prevention-v1"
            ):
                raise DailyDomainBuildError("daily universe has wrong date/policy")
            _write_atomic(work / "source-universe.json", acquisition_universe)
        files = []
        for path in sorted(item for item in work.rglob("*") if item.is_file()):
            files.append(
                {
                    "path": path.relative_to(work).as_posix(),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        content_digest = "sha256:" + hashlib.sha256(_canonical_bytes(files)).hexdigest()
        package_id = (
            f"dwd-daily-{market_date.strftime('%Y%m%d')}-"
            f"{catalog['catalog_digest'][7:19]}-{code_revision[:7]}"
        )
        manifest = {
            "schema_version": 2 if product_scope == DAILY_CORE_PRODUCT_SCOPE else 1,
            "kind": (
                "cn01_daily_core_dwd_increment"
                if product_scope == DAILY_CORE_PRODUCT_SCOPE
                else "cn01_daily_dwd_domain_increment"
            ),
            "package_id": package_id,
            "build_id": config["build_id"],
            "market_date": market_date.isoformat(),
            "covered_after_date": covered_after.isoformat(),
            "code_revision": code_revision,
            "catalog_digest": catalog["catalog_digest"],
            "catalog_sha256": _sha256(catalog_root / "catalog.json"),
            "base": (
                dict(base_manifest.binding)
                if isinstance(base_manifest, NativeBase23)
                else {
                    "package_id": base_manifest["package_id"],
                    "manifest_sha256": _sha256(base_root / "manifest.json"),
                    "content_digest": base_manifest["content_digest"],
                }
            ),
            "prior": prior,
            "selected_releases": {
                name: {
                    "release_id": value["release_id"],
                    "manifest_sha256": value["manifest_sha256"],
                }
                for name, value in sorted(selected.items())
            },
            "row_counts": row_counts,
            "files": files,
            "content_digest": content_digest,
        }
        if isinstance(base_manifest, NativeBase23):
            manifest["base"] = dict(base_manifest.binding)
            manifest["base_transition"] = base_transition
        if job_identity is not None:
            manifest["job_identity"] = job_identity
            manifest["purpose"] = "isolated_daily_governance"
            manifest["consumer_admission"] = "NOT_GRANTED"
            manifest["execution_authorized"] = False
        if acquisition_universe:
            manifest["acquisition_universe"] = acquisition_universe
        if product_scope == DAILY_CORE_PRODUCT_SCOPE:
            manifest["product_scope"] = DAILY_CORE_PRODUCT_SCOPE
            manifest["quality_policy"] = "p10-business-value-warehouse-quality.v1"
            manifest["quality_policy_sha256"] = _daily_core_quality_sha256()
            manifest["consumable_intervals"] = ["1d"]
            manifest["unavailable_products"] = {
                "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
                "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
            }
            audit["product_scope"] = DAILY_CORE_PRODUCT_SCOPE
            audit["consumable_intervals"] = ["1d"]
            _write_atomic(audit_path, audit)
            # Audit content changed: compute its new SHA and content digest,
            # while keeping the original v1 construction byte-identical.
            files = [
                {
                    "path": path.relative_to(work).as_posix(),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(item for item in work.rglob("*") if item.is_file())
            ]
            manifest["files"] = files
            manifest["content_digest"] = (
                "sha256:" + hashlib.sha256(_canonical_bytes(files)).hexdigest()
            )
        _write_atomic(work / "manifest.json", manifest)
        _write_atomic(work / "READY", (_sha256(work / "manifest.json") + "\n").encode("ascii"))
        os.replace(work, output)
    except BaseException as exc:
        if is_base24:
            (work / "READY").unlink(missing_ok=True)
            _write_atomic(
                work / "FAILURE.safe.json",
                {
                    "status": "FAILED_NOT_READY",
                    "exception_type": type(exc).__name__,
                    "job_identity": job_identity,
                    "source_prices_exported": False,
                },
            )
        else:
            shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        connection.close()
    return verify_daily_domain_increment(output)


def _verify_daily_core_payload(root: Path, manifest: Mapping[str, Any]) -> None:
    """Require declared core tables and reject actual intraday payload."""
    try:
        pq = importlib.import_module("pyarrow.parquet")

        counts = manifest.get("row_counts", {})
        if set(counts) != set(_TABLE_KEYS):
            raise DailyDomainBuildError("daily-core required table inventory is incomplete")
        for table in _TABLE_KEYS:
            paths = sorted(root.glob(f"payload/*/{table}/market_date=*/part-*.parquet"))
            if not paths:
                raise DailyDomainBuildError(f"daily-core required table file missing: {table}")
            actual = sum(pq.ParquetFile(path).metadata.num_rows for path in paths)
            if isinstance(counts[table], bool) or actual != counts[table]:
                raise DailyDomainBuildError(f"daily-core table row count differs: {table}")
            if (
                table in {"dwd_security_bar", "dwd_tradability_state", "dwd_trading_calendar"}
                and actual <= 0
            ):
                raise DailyDomainBuildError(f"daily-core required table is empty: {table}")
            if table == "dwd_security_bar":
                for path in paths:
                    rows = (
                        pq.ParquetFile(path)
                        .read(columns=["interval_size", "price_basis"])
                        .to_pylist()
                    )
                    if any(
                        row["interval_size"] != "1d" or row["price_basis"] != "none" for row in rows
                    ):
                        raise DailyDomainBuildError(
                            "daily-core payload contains non-daily or adjusted bars"
                        )
        audit = _object(root / "audit.json")
        if audit.get("result") != "accepted" or audit.get("market_date") != manifest.get(
            "market_date"
        ):
            raise DailyDomainBuildError("daily-core audit does not accept this market date")
    except DailyDomainBuildError:
        raise
    except (OSError, ValueError, KeyError, ImportError) as exc:
        raise DailyDomainBuildError(
            f"daily-core physical table verification failed: {exc}"
        ) from exc


def verify_daily_domain_increment(root: Path) -> DailyDomainResult:
    root = root.resolve()
    manifest = _object(root / "manifest.json")
    manifest_sha = _sha256(root / "manifest.json")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DailyDomainBuildError("daily DWD READY is unreadable") from exc
    if ready != manifest_sha:
        raise DailyDomainBuildError("daily DWD manifest or READY differs")
    if manifest.get("base", {}).get("kind") == NATIVE_BASE24_KIND:
        identity = manifest.get("job_identity")
        if (
            manifest.get("kind") != "cn01_daily_core_dwd_increment"
            or manifest.get("purpose") != "isolated_daily_governance"
            or manifest.get("consumer_admission") != "NOT_GRANTED"
            or manifest.get("execution_authorized") is not False
            or not isinstance(identity, dict)
            or set(identity) != {"base", "catalog", "code", "config", "prior"}
            or identity["base"] != manifest["base"]
            or identity["catalog"] != manifest.get("catalog_sha256")
            or identity["code"] != manifest.get("code_revision")
            or identity["prior"] != manifest.get("prior")
            or not isinstance(identity["config"], str)
            or len(identity["config"]) != 71
            or not identity["config"].startswith("sha256:")
            or any(ch not in "0123456789abcdef" for ch in identity["config"][7:])
        ):
            raise DailyDomainBuildError("Base 2.4 Daily governance/job identity differs")
    if manifest.get("kind") == "cn01_daily_core_dwd_increment":
        if (
            manifest.get("schema_version") != 2
            or manifest.get("product_scope") != DAILY_CORE_PRODUCT_SCOPE
            or manifest.get("consumable_intervals") != ["1d"]
            or manifest.get("quality_policy") != "p10-business-value-warehouse-quality.v1"
            or manifest.get("quality_policy_sha256") != _daily_core_quality_sha256()
            or set(manifest.get("selected_releases", {}))
            != {"market_daily_core", "calendar_daily_events"}
            or manifest.get("unavailable_products")
            != {
                "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
                "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
            }
        ):
            raise DailyDomainBuildError("daily-core DWD source/product contract differs")
    elif (
        manifest.get("kind") != "cn01_daily_dwd_domain_increment"
        or manifest.get("schema_version") != 1
    ):
        raise DailyDomainBuildError("daily DWD manifest or READY differs")
    expected = set()
    payload_bytes = 0
    files = manifest.get("files")
    if not isinstance(files, list):
        raise DailyDomainBuildError("daily DWD file inventory is invalid")
    for raw in files:
        path = _safe_path(root, raw.get("path"))
        relative = path.relative_to(root).as_posix()
        if relative in expected or not path.is_file() or path.is_symlink():
            raise DailyDomainBuildError("daily DWD physical inventory differs")
        expected.add(relative)
        if path.stat().st_size != raw.get("size_bytes") or _sha256(path) != raw.get("sha256"):
            raise DailyDomainBuildError(f"daily DWD file differs: {relative}")
        if relative.startswith("payload/"):
            payload_bytes += path.stat().st_size
    physical = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != expected | {"manifest.json", "READY"}:
        raise DailyDomainBuildError("daily DWD package contains undeclared files")
    digest = "sha256:" + hashlib.sha256(_canonical_bytes(files)).hexdigest()
    if digest != manifest.get("content_digest"):
        raise DailyDomainBuildError("daily DWD content digest differs")
    if manifest.get("kind") == "cn01_daily_core_dwd_increment":
        _verify_daily_core_payload(root, manifest)
    return DailyDomainResult(
        root=root,
        package_id=str(manifest["package_id"]),
        market_date=date.fromisoformat(str(manifest["market_date"])),
        manifest_sha256=manifest_sha,
        content_digest=digest,
        row_counts={str(key): int(value) for key, value in manifest["row_counts"].items()},
        payload_bytes=payload_bytes,
    )


def advance_daily_current(*, package: Path, pointer: Path) -> None:
    """Atomically advance the daily pointer after deep package verification."""

    result = verify_daily_domain_increment(package)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(result.root / "manifest.json", pointer.parent.resolve())
    manifest = _object(result.root / "manifest.json")
    is_core = manifest["kind"] == "cn01_daily_core_dwd_increment"
    if is_core and pointer.name != "CURRENT_DAILY_CORE.json":
        raise DailyDomainBuildError("daily-core product must not overwrite legacy CURRENT")
    if not is_core and pointer.name == "CURRENT_DAILY_CORE.json":
        raise DailyDomainBuildError("legacy DWD must not overwrite daily-core CURRENT")
    descriptor = {
        "schema_version": 2 if is_core else 1,
        "kind": "cn01_daily_core_dwd_current" if is_core else "cn01_daily_dwd_current",
        "package_id": result.package_id,
        "market_date": result.market_date.isoformat(),
        "manifest_path": relative,
        "manifest_sha256": result.manifest_sha256,
        "content_digest": result.content_digest,
    }
    if is_core:
        descriptor["product_scope"] = DAILY_CORE_PRODUCT_SCOPE
        if not result.root.resolve().is_relative_to(pointer.parent.resolve()):
            raise DailyDomainBuildError("daily-core package escapes its product root")
        import fcntl

        with (pointer.parent / ".daily-core-current.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if pointer.exists():
                previous = _object(pointer)
                if previous.get("kind") != "cn01_daily_core_dwd_current":
                    raise DailyDomainBuildError(
                        "existing daily-core pointer has another product kind"
                    )
                previous_day = date.fromisoformat(str(previous["market_date"]))
                if result.market_date < previous_day:
                    raise DailyDomainBuildError("daily-core CURRENT cannot move backwards")
                if result.market_date == previous_day:
                    if previous.get("manifest_sha256") != result.manifest_sha256:
                        raise DailyDomainBuildError("daily-core same-date publication is immutable")
                    return
                if (manifest.get("prior") or {}).get("manifest_sha256") != previous.get(
                    "manifest_sha256"
                ):
                    raise DailyDomainBuildError("daily-core CURRENT advanced since candidate build")
            dated = pointer.parent / "date-pointers" / f"{result.market_date.isoformat()}.json"
            dated.parent.mkdir(parents=True, exist_ok=True)
            if dated.exists():
                if _object(dated) != descriptor:
                    raise DailyDomainBuildError("daily-core dated pointer is immutable")
            else:
                _write_atomic(dated, descriptor)
            _write_atomic(pointer, descriptor)
        return
    _write_atomic(pointer, descriptor)
