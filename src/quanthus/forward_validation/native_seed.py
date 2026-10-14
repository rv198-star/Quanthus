"""Immutable, offline P6 Seed construction from a pinned native Base 2.3.

Only 2026 daily partitions and identity/factor reference tables are opened. This
entrypoint never submits a Job, changes activation, or qualifies a trading source.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.pipelines.securities_warehouse.native_base23_reader import (
    KIND,
    NativeBase23Error,
    _hash,
    _member,
    _object,
)

SCHEMA = "quanthus.p6-base-seed-binding.v1"
ARTIFACT_SCHEMA = "quanthus.p6-native-offline-seed.v1"
PURPOSE = "p6_historical_seed_offline_validation"
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_VENUES = {
    "venue_bec59e31eb6bb28111a10d7e6d2c689f": "SSE",
    "venue_8c0bd9088a0dc88f28e5e7fa8efdce87": "SZSE",
}
_PARTITIONS = {
    "dwd_security_bar": "interval_size=1d/observation_start_year=2026",
    "dwd_tradability_state": "observation_start_year=2026",
    "dwd_trading_calendar": "trade_date_year=2026",
    "dwd_listing": "all",
    "dwd_security": "all",
    "dwd_adjustment_factor": "all",
}
_COMMON = {"knowledge_from", "knowledge_to", "source_artifact_sha256"}
_COLUMNS = {
    "dwd_security_bar": {
        "listing_id", "calendar_session_id", "observation_start", "interval_size",
        "price_basis", "open", "high", "low", "close", "volume", "turnover",
        "source_record_id", "source_revision",
    },
    "dwd_tradability_state": {
        "listing_id", "calendar_session_id", "observation_start",
        "is_tradable", "can_buy", "can_sell",
    },
    "dwd_trading_calendar": {"venue_id", "calendar_session_id", "trade_date", "is_open"},
    "dwd_listing": {
        "listing_id", "security_id", "venue_id", "listing_code", "effective_from", "effective_to",
    },
    "dwd_security": {"security_id", "company_id", "instrument_type", "effective_from", "effective_to"},
    "dwd_adjustment_factor": {"security_id", "effective_from", "effective_to", "factor_value"},
}


class P6SeedError(ValueError):
    """A bounded source, projection or immutable artifact failed validation."""


@dataclass(frozen=True)
class P6BaseSeedBinding:
    raw: dict[str, Any]
    digest: str
    history_start: date
    history_end: date

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> P6BaseSeedBinding:
        try:
            source, scope, authority = raw["source"], raw["scope"], raw["authority"]
            start = date.fromisoformat(scope["history_start"])
            end = date.fromisoformat(scope["history_end"])
            if (
                raw["schema_version"] != SCHEMA or raw["purpose"] != PURPOSE
                or raw["work_item"] != "P6-BASE-001"
                or source["kind"] != KIND or source["version"] != "2.3"
                or source["package_id"] != "dwd-base-202609-v2.3"
                or source["business_through"] != "2026-09-30"
                or not date(2026, 1, 1) <= start <= end <= date(2026, 9, 30)
                or scope["venues"] != ["SSE", "SZSE"]
                or scope["interval_size"] != "1d" or scope["price_basis"] != "UNADJUSTED"
                or scope["minimum_open_sessions"] != 120
                or scope["knowledge_policy"] != "source_observed_at_or_before_each_session_close"
                or canonical_json_bytes(authority) != canonical_json_bytes({
                    "offline_construction_only": True, "consumer_admission": "NOT_GRANTED",
                    "official_cycle_authorized": False, "execution_authorized": False,
                    "protected_partition_access": False,
                })
                or any(not _SHA.fullmatch(source[k]) for k in (
                    "manifest_sha256", "content_digest", "materialization_sha256",
                ))
            ):
                raise ValueError("scope, source or authority differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise P6SeedError("invalid_seed_binding") from exc
        # Own the nested values so later caller mutation cannot change a frozen binding.
        owned = json.loads(canonical_json_bytes(raw))
        return cls(owned, digest_value(owned), start, end)

    @classmethod
    def load(cls, path: Path) -> P6BaseSeedBinding:
        return cls.from_dict(_object(path))


def verify_seed_source(
    root: Path, binding: P6BaseSeedBinding,
) -> tuple[dict[str, Any], dict[str, tuple[Path, ...]], dict[str, int]]:
    """Hash the explicit 2026 subset once; never walk protected price partitions."""
    if digest_value(binding.raw) != binding.digest:
        raise P6SeedError("seed_binding_mutated")
    root = root.resolve(strict=True)
    source = binding.raw["source"]
    manifest_path = _member(root, "manifest.json")
    if _hash(manifest_path) != source["manifest_sha256"]:
        raise P6SeedError("base_manifest_digest_mismatch")
    manifest = _object(manifest_path)
    if (
        any(manifest.get(k) != source[k] for k in (
            "kind", "version", "business_through", "content_digest", "materialization_sha256",
        ))
        or manifest.get("business_timezone") != "Asia/Shanghai"
        or manifest.get("purpose") != "p10_governance_baseline_with_disclosed_coverage"
        or _member(root, "READY").read_text(encoding="ascii").strip() != source["manifest_sha256"]
    ):
        raise P6SeedError("base_identity_or_ready_mismatch")
    layout_path = _member(root, "MATERIALIZATION.json")
    if _hash(layout_path) != source["materialization_sha256"]:
        raise P6SeedError("base_materialization_digest_mismatch")
    materialization = _object(layout_path)
    if materialization.get("kind") != KIND or materialization.get("purpose") != manifest["purpose"]:
        raise P6SeedError("base_materialization_identity_mismatch")
    paths: dict[str, tuple[Path, ...]] = {}
    counts: dict[str, int] = {}
    for table, partition in _PARTITIONS.items():
        entry = materialization.get("layout", {}).get(table, {})
        records = entry.get("files")
        if not isinstance(records, list):
            raise P6SeedError(f"missing_seed_table:{table}")
        prefix = f"payload/{table}/{partition}/"
        selected = []
        rows: Counter[str] = Counter()
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise P6SeedError(f"invalid_seed_inventory:{table}")
            # Select in sealed metadata BEFORE stat/hash/footer/row access.
            if not record["path"].startswith(prefix):
                continue
            path = _member(root, record["path"])
            count = record.get("rows")
            if (
                record.get("table") != table or record.get("partition") != partition
                or type(count) is not int or count < 0 or path in selected
                or path.stat().st_size != record.get("size_bytes")
                or _hash(path) != record.get("sha256")
            ):
                raise P6SeedError(f"seed_member_mismatch:{table}")
            selected.append(path)
            rows[partition] += count
        if not selected:
            raise P6SeedError(f"missing_seed_partition:{table}:{partition}")
        if (
            entry.get("expected_partition_rows", {}).get(partition) != rows[partition]
            or manifest.get("summaries", {}).get(table, {}).get(partition, {}).get("rows")
            != rows[partition]
        ):
            raise P6SeedError(f"seed_partition_count_mismatch:{table}")
        # Reject additions/symlinks only under this selected subtree.
        physical = set()
        for path in (root / "payload" / table / partition).rglob("*"):
            if path.is_symlink():
                raise P6SeedError("seed_partition_symlink")
            if path.is_file():
                physical.add(path)
        if physical != set(selected):
            raise P6SeedError(f"seed_physical_inventory_mismatch:{table}")
        paths[table] = tuple(sorted(selected))
        counts[table] = rows[partition]
    return manifest, paths, counts


def _text(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _visible(alias: str) -> str:
    return (
        f"{alias}.knowledge_from <= c.close_at AND "
        f"({alias}.knowledge_to IS NULL OR {alias}.knowledge_to > c.close_at)"
    )


def _effective(alias: str) -> str:
    return (
        f"{alias}.effective_from <= c.close_at AND "
        f"({alias}.effective_to IS NULL OR {alias}.effective_to > c.close_at)"
    )


def _construct(connection: Any, paths: dict[str, tuple[Path, ...]], counts: dict[str, int],
               binding: P6BaseSeedBinding, output: Path) -> dict[str, Any]:
    for table, members in paths.items():
        files = "[" + ",".join(_text(p) for p in members) + "]"
        connection.execute(
            f"CREATE VIEW {table} AS SELECT * FROM read_parquet({files}, hive_partitioning=false)"
        )
        schema = {r[0]: r[1] for r in connection.execute(f"DESCRIBE {table}").fetchall()}
        if (_COLUMNS[table] | _COMMON) - schema.keys():
            raise P6SeedError(f"seed_schema_missing_columns:{table}")
        for field in ("knowledge_from", "knowledge_to", "effective_from", "effective_to"):
            if field in schema and schema[field] != "TIMESTAMP WITH TIME ZONE":
                raise P6SeedError(f"seed_timestamp_not_aware:{table}:{field}")
        if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != counts[table]:
            raise P6SeedError(f"seed_physical_row_count_mismatch:{table}")
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        wrong_year = connection.execute(
            f"SELECT count(*) FROM {table} WHERE observation_start IS NULL OR "
            "year(timezone('Asia/Shanghai', observation_start)) <> 2026"
        ).fetchone()[0]
        if wrong_year:
            raise P6SeedError(f"seed_partition_date_mismatch:{table}")
    if connection.execute(
        "SELECT count(*) FROM dwd_security_bar WHERE interval_size IS DISTINCT FROM '1d' "
        "OR price_basis IS DISTINCT FROM 'UNADJUSTED'"
    ).fetchone()[0]:
        raise P6SeedError("seed_interval_or_price_basis_mismatch")
    venues = ",".join(_text(v) for v in _VENUES)
    start, end = _text(binding.history_start), _text(binding.history_end)
    connection.execute(f"""
        CREATE TEMP TABLE calendar AS
        SELECT *, CAST(trade_date AS DATE) AS session_date,
          timezone('Asia/Shanghai', CAST(trade_date AS DATE) + TIME '15:00:00') AS close_at
        FROM dwd_trading_calendar
        WHERE CAST(trade_date AS DATE) BETWEEN CAST({start} AS DATE) AND CAST({end} AS DATE)
          AND venue_id IN ({venues}) AND is_open AND knowledge_to IS NULL;
        CREATE TEMP TABLE factor AS
        SELECT security_id, CAST(timezone('Asia/Shanghai', effective_from) AS DATE) AS factor_date,
          min(effective_from) AS effective_from, max(knowledge_from) AS knowledge_from,
          count(*) AS boundary_count
        FROM dwd_adjustment_factor WHERE knowledge_to IS NULL
        GROUP BY security_id, factor_date;
        CREATE TEMP TABLE seed AS
        WITH keys AS (
          SELECT listing_id, calendar_session_id FROM dwd_security_bar
            WHERE CAST(timezone('Asia/Shanghai',observation_start) AS DATE)
              BETWEEN CAST({start} AS DATE) AND CAST({end} AS DATE)
          UNION SELECT listing_id, calendar_session_id FROM dwd_tradability_state
            WHERE CAST(timezone('Asia/Shanghai',observation_start) AS DATE)
              BETWEEN CAST({start} AS DATE) AND CAST({end} AS DATE)
        )
        SELECT s.security_id AS dwd_security_id, k.listing_id AS dwd_listing_id,
          l.listing_code, s.company_id AS dwd_company_id,
          CASE c.venue_id WHEN {_text(next(iter(_VENUES)))} THEN 'SSE' ELSE 'SZSE' END AS venue_id,
          s.instrument_type, c.session_date, c.close_at AS knowledge_cutoff,
          b.open AS open_price, b.high AS high_price, b.low AS low_price, b.close AS close_price,
          b.turnover, b.volume,
          t.is_tradable, t.can_buy, t.can_sell,
          row_number() OVER (PARTITION BY k.listing_id ORDER BY c.session_date) AS listing_sessions,
          f.boundary_count IS NOT NULL AS adjustment_effective,
          f.effective_from AS factor_effective_from, f.boundary_count AS factor_boundary_count,
          b.knowledge_from AS dwd_bar_knowledge_from,
          s.knowledge_from AS dwd_security_knowledge_from,
          l.knowledge_from AS dwd_listing_knowledge_from,
          t.knowledge_from AS dwd_state_knowledge_from,
          f.knowledge_from AS dwd_factor_knowledge_from,
          c.knowledge_from AS dwd_calendar_knowledge_from,
          b.source_record_id AS dwd_bar_source_record_id,
          b.source_revision AS dwd_bar_source_revision,
          b.source_artifact_sha256 AS dwd_bar_source_artifact_sha256,
          l.source_artifact_sha256 AS dwd_listing_source_artifact_sha256,
          s.source_artifact_sha256 AS dwd_security_source_artifact_sha256,
          t.source_artifact_sha256 AS dwd_state_source_artifact_sha256,
          c.source_artifact_sha256 AS dwd_calendar_source_artifact_sha256,
          s.effective_from AS dwd_security_effective_from,
          l.effective_from AS dwd_listing_effective_from,
          c.calendar_session_id,
          CAST(timezone('Asia/Shanghai',b.observation_start) AS DATE) AS bar_session_date,
          CAST(timezone('Asia/Shanghai',t.observation_start) AS DATE) AS state_session_date,
          b.listing_id IS NULL AS bar_missing_or_not_visible,
          t.listing_id IS NULL AS state_missing_or_not_visible,
          l.listing_id IS NULL OR s.security_id IS NULL AS identity_missing_or_not_visible
        FROM keys k JOIN calendar c USING (calendar_session_id)
        LEFT JOIN dwd_listing l ON l.listing_id=k.listing_id AND l.venue_id=c.venue_id
          AND {_visible('l')} AND {_effective('l')}
        LEFT JOIN dwd_security s ON s.security_id=l.security_id
          AND {_visible('s')} AND {_effective('s')}
        LEFT JOIN dwd_security_bar b ON b.listing_id=k.listing_id
          AND b.calendar_session_id=k.calendar_session_id AND {_visible('b')}
        LEFT JOIN dwd_tradability_state t ON t.listing_id=k.listing_id
          AND t.calendar_session_id=k.calendar_session_id AND {_visible('t')}
        LEFT JOIN factor f ON f.security_id=s.security_id AND f.factor_date=c.session_date;
    """)
    diagnostics = _diagnose(connection, binding)
    diagnostics["reason_counts"]["open_session_without_seed"] = connection.execute(
        "SELECT count(*) FROM calendar c WHERE NOT EXISTS "
        "(SELECT 1 FROM seed s WHERE s.calendar_session_id=c.calendar_session_id)"
    ).fetchone()[0]
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        diagnostics["reason_counts"][f"{table}_calendar_key_missing"] = connection.execute(f"""
            SELECT count(*) FROM {table} raw
            WHERE CAST(timezone('Asia/Shanghai',raw.observation_start) AS DATE)
              BETWEEN CAST({start} AS DATE) AND CAST({end} AS DATE)
              AND NOT EXISTS (SELECT 1 FROM calendar c
                              WHERE c.calendar_session_id=raw.calendar_session_id)
        """).fetchone()[0]
    diagnostics["input_validation_passed"] = not any(diagnostics["reason_counts"].values())
    connection.execute(
        "COPY (SELECT * FROM seed ORDER BY dwd_security_id, dwd_listing_effective_from, "
        f"dwd_listing_id, session_date) TO {_text(output)} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return diagnostics


def _diagnose(connection: Any, binding: P6BaseSeedBinding) -> dict[str, Any]:
    conditions = {
        "bar_missing_or_not_visible": "bar_missing_or_not_visible",
        "state_missing_or_not_visible": "state_missing_or_not_visible",
        "identity_missing_or_not_visible": "identity_missing_or_not_visible",
        "calendar_not_visible": "dwd_calendar_knowledge_from IS NULL OR dwd_calendar_knowledge_from > knowledge_cutoff",
        "factor_not_visible": (
            "adjustment_effective AND (dwd_factor_knowledge_from IS NULL OR "
            "dwd_factor_knowledge_from > knowledge_cutoff)"
        ),
        "invalid_ohlcv": (
            "NOT bar_missing_or_not_visible AND (open_price IS NULL OR high_price IS "
            "NULL OR low_price IS NULL OR close_price IS NULL OR volume IS NULL OR "
            "turnover IS NULL OR NOT isfinite(open_price) OR NOT isfinite(high_price) OR "
            "NOT isfinite(low_price) OR NOT isfinite(close_price) OR NOT "
            "isfinite(volume) OR NOT isfinite(turnover) OR low_price<=0 OR "
            "high_price<greatest(open_price,close_price,low_price) OR "
            "low_price>least(open_price,close_price) OR volume<0 OR turnover<0)"
        ),
        "source_lineage_missing": (
            "NOT bar_missing_or_not_visible AND (dwd_bar_source_record_id IS NULL OR "
            "dwd_bar_source_revision IS NULL OR dwd_bar_source_artifact_sha256 IS NULL "
            "OR NOT regexp_full_match(dwd_bar_source_artifact_sha256, "
            "'(sha256:)?[0-9a-f]{64}'))"
        ),
        "tradability_invalid": (
            "NOT state_missing_or_not_visible AND (is_tradable IS NULL OR can_buy IS "
            "NULL OR can_sell IS NULL OR (NOT is_tradable AND (can_buy OR can_sell)))"
        ),
        "session_date_mismatch": "bar_session_date<>session_date OR state_session_date<>session_date",
    }
    for table in ("listing", "security", "state", "calendar"):
        field = f"dwd_{table}_source_artifact_sha256"
        conditions[f"{table}_lineage_missing"] = (
            f"{field} IS NULL OR NOT regexp_full_match({field}, '(sha256:)?[0-9a-f]{{64}}')"
        )
    reasons = {
        name: connection.execute(f"SELECT count(*) FROM seed WHERE {expr}").fetchone()[0]
        for name, expr in conditions.items()
    }
    reasons["duplicate_listing_session"] = connection.execute(
        "SELECT count(*) FROM (SELECT dwd_listing_id,session_date FROM seed "
        "GROUP BY ALL HAVING count(*)<>1)"
    ).fetchone()[0]
    dates = connection.execute("SELECT DISTINCT session_date FROM seed ORDER BY 1").fetchall()
    rows = connection.execute("SELECT count(*) FROM seed").fetchone()[0]
    reasons["insufficient_open_sessions"] = int(len(dates) < 120)
    reasons["history_end_missing"] = int(not dates or dates[-1][0] != binding.history_end)
    reasons["empty_seed"] = int(rows == 0)
    return {"rows": rows, "open_sessions": len(dates), "reason_counts": reasons,
            "input_validation_passed": not any(reasons.values())}


def build_seed(*, root: Path, binding: P6BaseSeedBinding, output: Path,
               code_revision: str) -> dict[str, Any]:
    """Build a new immutable diagnostic Seed; refuse replacement or implicit reuse."""
    if not _REVISION.fullmatch(code_revision):
        raise P6SeedError("invalid_code_revision")
    root, output = root.resolve(strict=True), output.absolute()
    resolved_output = output.resolve()
    if resolved_output.is_relative_to(root) or root.is_relative_to(resolved_output):
        raise P6SeedError("seed_output_overlaps_source")
    if output.exists() or output.is_symlink():
        raise P6SeedError("seed_output_exists_use_verify")
    started = perf_counter()
    manifest, paths, counts = verify_seed_source(root, binding)
    source_seconds = perf_counter() - started
    try:
        import duckdb
    except ImportError as exc:
        raise P6SeedError("dependency_missing:duckdb") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".p6-seed-", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads=1")
        connection.execute("SET memory_limit='1GB'")
        connection.execute(f"SET temp_directory={_text(staging / 'spill')}")
        connection.execute("SET max_temp_directory_size='6GB'")
        build_started = perf_counter()
        diagnostics = _construct(connection, paths, counts, binding, staging / "candidate-daily.parquet")
        payload = {
            "schema_version": ARTIFACT_SCHEMA, "purpose": PURPOSE,
            "binding": binding.raw, "binding_digest": binding.digest,
            "code_revision": code_revision, "duckdb_version": duckdb.__version__,
            "source_consumer_admission": manifest.get("consumer_admission"),
            "consumer_authorized": False, "official_observations_added": 0,
            "execution_authorized": False, "protected_price_partitions_opened": [],
            "diagnostics": diagnostics, "source_row_counts": counts,
            "files": {"candidate-daily.parquet": _hash(staging / "candidate-daily.parquet")},
            "source_members": {t: [str(p.relative_to(root)) for p in pp] for t, pp in paths.items()},
        }
        payload["content_digest"] = digest_value(payload)
        payload["stages"] = [
            {"stage": "verify_source", "elapsed_seconds": f"{source_seconds:.6f}",
             "disposition": "built_once", "bytes": sum(p.stat().st_size for pp in paths.values() for p in pp)},
            {"stage": "candidate_seed_and_validation", "elapsed_seconds": f"{perf_counter()-build_started:.6f}",
             "disposition": "one_shared_TMP_seed", "rows": diagnostics["rows"],
             "bytes": (staging / "candidate-daily.parquet").stat().st_size},
        ]
        (staging / "manifest.json").write_bytes(canonical_json_bytes(payload) + b"\n")
        if diagnostics["input_validation_passed"]:
            (staging / "OFFLINE_VALIDATED").write_text(_hash(staging / "manifest.json") + "\n")
        connection.close()
        connection = None
        shutil.rmtree(staging / "spill", ignore_errors=True)
        # mkdir provides a no-clobber publication fence, including concurrent builders.
        output.mkdir()
        for file in staging.iterdir():
            file.rename(output / file.name)
        return payload
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(staging, ignore_errors=True)


def verify_seed_artifact(output: Path) -> dict[str, Any]:
    """Verify a retained artifact without rebuilding any market prerequisite."""
    root = output.resolve(strict=True)
    payload = _object(_member(root, "manifest.json"))
    if (
        payload.get("schema_version") != ARTIFACT_SCHEMA
        or payload.get("purpose") != PURPOSE or payload.get("consumer_authorized") is not False
        or payload.get("execution_authorized") is not False
        or payload.get("official_observations_added") != 0
        or payload.get("protected_price_partitions_opened") != []
        or payload.get("files", {}).keys() != {"candidate-daily.parquet"}
    ):
        raise P6SeedError("invalid_seed_artifact")
    binding = P6BaseSeedBinding.from_dict(payload["binding"])
    if binding.digest != payload.get("binding_digest"):
        raise P6SeedError("seed_artifact_binding_mismatch")
    identity = {k: v for k, v in payload.items() if k not in {"content_digest", "stages"}}
    if digest_value(identity) != payload.get("content_digest"):
        raise P6SeedError("seed_artifact_digest_mismatch")
    for name, expected in payload["files"].items():
        if _hash(_member(root, name)) != expected:
            raise P6SeedError("seed_artifact_file_mismatch")
    marker = root / "OFFLINE_VALIDATED"
    if payload["diagnostics"]["input_validation_passed"]:
        if _member(root, marker.name).read_text().strip() != _hash(root / "manifest.json"):
            raise P6SeedError("seed_artifact_validation_marker_mismatch")
    elif marker.exists():
        raise P6SeedError("unqualified_seed_has_validation_marker")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--binding", required=True, type=Path)
    build.add_argument("--root", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--code-revision", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_seed(root=args.root, binding=P6BaseSeedBinding.load(args.binding),
                                output=args.output, code_revision=args.code_revision)
        else:
            result = verify_seed_artifact(args.output)
        print(json.dumps({k: result[k] for k in (
            "content_digest", "diagnostics", "consumer_authorized", "official_observations_added",
        )}, sort_keys=True))
        return 0 if result["diagnostics"]["input_validation_passed"] else 2
    except (P6SeedError, NativeBase23Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "consumer_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
