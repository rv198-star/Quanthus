"""Build one immutable DWD domain increment from a sealed four-lane ODS catalog."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DailyOdsCatalogError,
    verify_daily_ods_catalog,
)


class DailyDomainBuildError(RuntimeError):
    """Raised when a daily input cannot become a governed DWD increment."""


@dataclass(frozen=True)
class DailyDomainResult:
    root: Path
    package_id: str
    market_date: date
    manifest_sha256: str
    content_digest: str
    row_counts: Mapping[str, int]
    payload_bytes: int


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


def _base_files(base_root: Path, manifest: Mapping[str, Any], table: str) -> list[Path]:
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
        return selected[lane]["artifacts"][name]
    except KeyError as exc:
        raise DailyDomainBuildError(f"required daily artifact is absent: {lane}/{name}") from exc


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


def _verify_base(base_root: Path, release_config: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _object(base_root / "manifest.json")
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

    output = output.resolve()
    if output.exists():
        return verify_daily_domain_increment(output)
    catalog, selected = _read_catalog(catalog_root.resolve(), release_root.resolve())
    market_date = date.fromisoformat(catalog["market_date"])
    base_manifest, config = _verify_base(base_root.resolve(), release_config.resolve())
    base_through = date.fromisoformat(str(config["through_date"]))
    covered_after, prior = _prior_binding(prior_pointer, market_date, base_through)
    if market_date <= covered_after:
        raise DailyDomainBuildError("daily market_date does not advance Base or CURRENT")
    if len(code_revision) != 40 or any(ch not in "0123456789abcdef" for ch in code_revision):
        raise DailyDomainBuildError("code_revision must be a full lowercase Git revision")

    try:
        import duckdb
        import pyarrow.parquet as pq
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
    minute_path, minute_artifact = _artifact(selected, "intraday_1m", "intraday_bars")
    five_path, five_artifact = _artifact(selected, "intraday_5m", "intraday_bars")
    announcements_path, announcements_artifact = _artifact(
        selected, "calendar_daily_events", "announcements_delta"
    )
    deferred_daily_artifacts = [
        (lane, name, _artifact(selected, lane, name)[0])
        for lane, name in (
            ("market_daily_core", "adjustment_evidence_delta"),
            ("calendar_daily_events", "suspensions_delta"),
            ("calendar_daily_events", "corporate_actions_delta"),
            ("calendar_daily_events", "dividends_delta"),
            ("calendar_daily_events", "factor_evidence_delta"),
            ("calendar_daily_events", "financial_disclosures_delta"),
        )
    ]

    listing_files = _base_files(base_root, base_manifest, "dwd_listing")
    security_files = _base_files(base_root, base_manifest, "dwd_security")
    calendar_files = _base_files(base_root, base_manifest, "dwd_trading_calendar")
    market_index_files = _base_files(base_root, base_manifest, "dwd_market_index")
    venue_files = _base_files(base_root, base_manifest, "dwd_trading_venue")

    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect()
    connection.execute("SET threads=4")
    connection.execute("SET preserve_insertion_order=false")
    connection.execute("SET memory_limit='2GB'")
    try:
        for lane, name, path in deferred_daily_artifacts:
            row_count = pq.ParquetFile(path).metadata.num_rows
            if row_count:
                raise DailyDomainBuildError(
                    f"non-empty daily artifact has no daily DWD handler: {lane}/{name} "
                    f"({row_count} rows)"
                )
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
        connection.execute(
            f"CREATE VIEW base_venue AS SELECT * FROM {_path_relation(venue_files)}"
        )

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
            concat({_sql(trading_calendar_path.name)},'#exchange=',o.exchange,
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
        connection.execute(
            f"CREATE VIEW daily_calendar AS SELECT * FROM read_parquet({_sql(str(calendar_output))})"
        )

        bar_parts = []
        for path, artifact, interval, subtract in (
            (daily_path, daily_artifact, "1d", "INTERVAL 0 SECOND"),
            (minute_path, minute_artifact, "1m", "INTERVAL 1 MINUTE"),
            (five_path, five_artifact, "5m", "INTERVAL 5 MINUTE"),
        ):
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
            o.tradestatus='1' is_tradable, o.tradestatus='1' can_buy, o.tradestatus='1' can_sell,
            CASE WHEN o.tradestatus<>'1' THEN 'SOURCE_NOT_TRADABLE'
                 WHEN o.isST='1' THEN 'ST_TRADABLE' ELSE 'NORMAL' END::VARCHAR reason_code,
            NULL::DECIMAL(38,10) upper_price, NULL::DECIMAL(38,10) lower_price,
            {_sql(catalog["catalog_digest"])}::VARCHAR input_revision_digest
            FROM ({status_source}) o JOIN base_listing l ON o.stock_code=l.listing_code
            JOIN daily_calendar c ON c.venue_id=l.venue_id AND c.trade_date=CAST(o.market_date AS DATE)
            AND c.session_type='day'""",
            status_output,
        )

        disclosure_id = _stable_id_sql("disclosure", _sql("announcement_archive"), "o.ann_id")
        subject_id = _stable_id_sql(
            "disclosure_subject", disclosure_id, _sql("SECURITY"), "l.security_id"
        )
        ann_source = f"""SELECT *, concat({_sql(announcements_path.name)},'#ann_id=',ann_id) source_record_id
        FROM read_parquet({_sql(str(announcements_path))})
        WHERE CAST(announce_date AS DATE)>{_sql(covered_after.isoformat())}::DATE
          AND CAST(announce_date AS DATE)<={_sql(market_date.isoformat())}::DATE"""
        effective = "CAST(o.publish_time AS TIMESTAMP) AT TIME ZONE 'Asia/Shanghai'"
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

        outputs = {
            "dwd_trading_calendar": calendar_output,
            "dwd_security_bar": bar_output,
            "dwd_security_valuation": valuation_output,
            "dwd_index_bar": index_output,
            "dwd_tradability_state": status_output,
            "dwd_disclosure": disclosure_output,
            "dwd_disclosure_subject": subject_output,
        }
        row_counts: dict[str, int] = {}
        audit_tables = []
        for table, path in outputs.items():
            keys = ",".join(_TABLE_KEYS[table])
            rows, distinct_rows, missing_lineage = connection.execute(
                f"""SELECT count(*), count(DISTINCT ({keys})), count(*) FILTER (WHERE
                record_source IS NULL OR source_record_id IS NULL OR source_revision IS NULL OR
                source_artifact_sha256 IS NULL OR knowledge_from IS NULL) FROM read_parquet(?)""",
                [str(path)],
            ).fetchone()
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
            "dwd_trading_calendar": connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE trade_date=cast(? AS DATE)",
                [str(trading_calendar_path), market_date.isoformat()],
            ).fetchone()[0],
            "dwd_security_bar": connection.execute(
                "SELECT (SELECT count(*) FROM read_parquet(?))+(SELECT count(*) FROM read_parquet(?))+(SELECT count(*) FROM read_parquet(?))",
                [str(daily_path), str(minute_path), str(five_path)],
            ).fetchone()[0],
            "dwd_security_valuation": connection.execute(
                "SELECT count(*)*4 FROM read_parquet(?)", [str(valuation_path)]
            ).fetchone()[0],
            "dwd_index_bar": connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(index_path)]
            ).fetchone()[0],
            "dwd_tradability_state": connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(status_path)]
            ).fetchone()[0],
            "dwd_disclosure": connection.execute(
                """SELECT count(*) FROM read_parquet(?)
                WHERE CAST(announce_date AS DATE)>CAST(? AS DATE)
                  AND CAST(announce_date AS DATE)<=CAST(? AS DATE)""",
                [str(announcements_path), covered_after.isoformat(), market_date.isoformat()],
            ).fetchone()[0],
        }
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
            "result": "accepted",
        }
        audit_path = work / "audit.json"
        _write_atomic(audit_path, audit)
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
        package_id = f"dwd-daily-{market_date.strftime('%Y%m%d')}-{catalog['catalog_digest'][7:19]}"
        manifest = {
            "schema_version": 1,
            "kind": "cn01_daily_dwd_domain_increment",
            "package_id": package_id,
            "build_id": config["build_id"],
            "market_date": market_date.isoformat(),
            "covered_after_date": covered_after.isoformat(),
            "code_revision": code_revision,
            "catalog_digest": catalog["catalog_digest"],
            "catalog_sha256": _sha256(catalog_root / "catalog.json"),
            "base": {
                "package_id": base_manifest["package_id"],
                "manifest_sha256": _sha256(base_root / "manifest.json"),
                "content_digest": base_manifest["content_digest"],
            },
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
        _write_atomic(work / "manifest.json", manifest)
        _write_atomic(work / "READY", (_sha256(work / "manifest.json") + "\n").encode("ascii"))
        os.replace(work, output)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        connection.close()
    return verify_daily_domain_increment(output)


def verify_daily_domain_increment(root: Path) -> DailyDomainResult:
    root = root.resolve()
    manifest = _object(root / "manifest.json")
    manifest_sha = _sha256(root / "manifest.json")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DailyDomainBuildError("daily DWD READY is unreadable") from exc
    if ready != manifest_sha or manifest.get("kind") != "cn01_daily_dwd_domain_increment":
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
    descriptor = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_current",
        "package_id": result.package_id,
        "market_date": result.market_date.isoformat(),
        "manifest_path": relative,
        "manifest_sha256": result.manifest_sha256,
        "content_digest": result.content_digest,
    }
    _write_atomic(pointer, descriptor)
