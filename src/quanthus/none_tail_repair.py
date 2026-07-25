"""Build an immutable OCI child from a checksummed none-tail repair revision."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.none_backfill import (
    NoneBackfillError,
    _load_object,
    _sha256,
    _single_int,
    _source,
    _sql_string,
    _verify_checksum_ledger,
)


@dataclass(frozen=True)
class NoneTailRepairResult:
    revision: str
    output: Path
    affected_securities: int
    parent_rows: int
    raw_rows: int
    overlap_rows: int
    inserted_rows: int
    output_rows: int
    checksums: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "affected_securities": self.affected_securities,
            "parent_rows": self.parent_rows,
            "raw_rows": self.raw_rows,
            "overlap_rows": self.overlap_rows,
            "inserted_rows": self.inserted_rows,
            "output_rows": self.output_rows,
            "checksums": dict(sorted(self.checksums.items())),
        }


def _require_inputs(parent: Path, raw: Path, output: Path) -> None:
    parent_names = (
        "daily_ohlcv_none.parquet",
        "daily_universe.parquet",
        "daily_isST.parquet",
        "st_status_pit.parquet",
        "ref_security_code_validity.parquet",
        "META.json",
        "CHECKSUMS.sha256",
    )
    raw_names = (
        "META.json",
        "manifest.json",
        "per_code_summary.parquet",
        "CHECKSUMS.sha256",
    )
    required = [*(parent / name for name in parent_names), *(raw / name for name in raw_names)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise NoneBackfillError("required tail-repair input is missing: " + ", ".join(missing))
    if output.exists():
        raise FileExistsError(f"refusing to overwrite none tail revision: {output}")


def _validate_raw_lineage(raw: Path, metadata: dict[str, Any]) -> tuple[list[str], int]:
    if metadata.get("kind") != "raw_none_tail_repair" or metadata.get("issue") != 40:
        raise NoneBackfillError("raw revision is not the #40 none-tail repair contract")
    if metadata.get("promoted") is not False:
        raise NoneBackfillError("raw tail repair must be an unpromoted revision")
    contract = metadata.get("source_contract") or {}
    expected_fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adjustflag",
        "turn",
        "pctChg",
        "tradestatus",
        "isST",
    ]
    if (
        contract.get("provider") != "baostock"
        or contract.get("api") != "query_history_k_data_plus"
        or contract.get("frequency") != "d"
        or contract.get("adjustflag") != "3"
        or str(contract.get("fields", "")).split(",") != expected_fields
    ):
        raise NoneBackfillError("raw tail source contract is invalid")
    codes = sorted(str(code).zfill(6) for code in metadata.get("codes") or [])
    if len(codes) != 3 or len(set(codes)) != 3:
        raise NoneBackfillError("#40 raw tail scope must contain exactly three securities")
    window = metadata.get("tail_window") or {}
    total = 0
    for code in codes:
        tail_paths = tuple((raw / "raw_shards").glob(f"{code}_tail_*.json"))
        full_paths = tuple((raw / "raw_shards").glob(f"{code}_full_*.json"))
        window_paths = tuple((raw / "raw_shards").glob(f"{code}_window_*.json"))
        if not (len(tail_paths) == len(full_paths) == len(window_paths) == 1):
            raise NoneBackfillError(f"raw tail evidence set is incomplete for {code}")
        tail = _load_object(tail_paths[0])
        full = _load_object(full_paths[0])
        prefix = _load_object(window_paths[0])
        for payload in (tail, full, prefix):
            request = (payload.get("meta") or {}).get("request") or {}
            if (
                str(payload.get("security_id", "")).zfill(6) != code
                or payload.get("fields") != expected_fields
                or request.get("adjustflag") != "3"
                or request.get("frequency") != "d"
                or (payload.get("meta") or {}).get("error_code") != "0"
                or int(payload.get("n_rows", -1)) != len(payload.get("rows") or [])
            ):
                raise NoneBackfillError(f"raw request evidence is invalid for {code}")
        if (
            ((tail.get("meta") or {}).get("request") or {}).get("start_date")
            != window.get("start")
            or ((tail.get("meta") or {}).get("request") or {}).get("end_date")
            != window.get("end")
        ):
            raise NoneBackfillError(f"tail request window does not match metadata for {code}")
        full_rows = full.get("rows") or []
        tail_rows = tail.get("rows") or []
        prefix_rows = prefix.get("rows") or []
        if prefix_rows + tail_rows != full_rows:
            raise NoneBackfillError(f"full-window evidence does not reconcile for {code}")
        total += len(tail_rows)
    declared_total = int((metadata.get("counts") or {}).get("tail_rows_total", -1))
    if total != declared_total:
        raise NoneBackfillError("raw tail row total does not match metadata")
    return codes, total


def build_none_tail_repair_revision(
    *, parent: Path, raw_revision: Path, output: Path, revision: str
) -> NoneTailRepairResult:
    """Retain every parent key and append only validated #40 tail keys."""

    parent = parent.resolve()
    raw = raw_revision.resolve()
    output = output.resolve()
    if not revision.strip():
        raise NoneBackfillError("revision must not be empty")
    _require_inputs(parent, raw, output)
    parent_verified = _verify_checksum_ledger(parent)
    raw_verified = _verify_checksum_ledger(raw)
    raw_meta = _load_object(raw / "META.json")
    codes, declared_raw_rows = _validate_raw_lineage(raw, raw_meta)

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise NoneBackfillError("P1 dependencies are missing; run make bootstrap-p1") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        parent_none = _source(parent / "daily_ohlcv_none.parquet")
        tail_glob = _sql_string(str(raw / "raw_shards" / "*_tail_*.json"))
        connection.execute(f"CREATE VIEW parent_none AS SELECT * FROM {parent_none}")
        connection.execute(f"CREATE VIEW raw_tail AS SELECT * FROM read_json_auto({tail_glob})")
        connection.execute(
            f"""
            CREATE VIEW normalized_raw_all AS
            SELECT
                security_id,
                security_id AS stock_code,
                exchange AS identity_exchange,
                try_cast(row[1] AS DATE) AS date,
                try_cast(nullif(row[3], '') AS DOUBLE) AS open,
                try_cast(nullif(row[4], '') AS DOUBLE) AS high,
                try_cast(nullif(row[5], '') AS DOUBLE) AS low,
                try_cast(nullif(row[6], '') AS DOUBLE) AS close,
                try_cast(nullif(row[7], '') AS DOUBLE) AS volume,
                try_cast(nullif(row[8], '') AS DOUBLE) AS amount,
                try_cast(nullif(row[10], '') AS DOUBLE) AS turnover_rate,
                try_cast(nullif(row[11], '') AS DOUBLE) AS pct_change,
                'none' AS adj_mode,
                {_sql_string(revision)} AS adj_version,
                'baostock.query_history_k_data_plus.adjustflag=3' AS source,
                CAST(meta.request.fetched_at_utc AS TIMESTAMPTZ) AS built_at,
                row[2] AS provider_code,
                row[9] AS row_adjustflag,
                row[12] AS tradestatus,
                CAST(meta.request.start_date AS DATE) AS request_start,
                CAST(meta.request.end_date AS DATE) AS request_end
            FROM raw_tail, unnest(rows) AS expanded(row)
            """
        )
        raw_rows = _single_int(connection, "SELECT count(*) FROM normalized_raw_all")
        if raw_rows != declared_raw_rows:
            raise NoneBackfillError("normalized raw row count does not match evidence")
        invalid_raw = _single_int(
            connection,
            """
            SELECT count(*) FROM normalized_raw_all
            WHERE date IS NULL OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
               OR volume IS NULL OR amount IS NULL OR open < 0 OR high < 0 OR low < 0
               OR close < 0 OR volume < 0 OR amount < 0 OR low > high
               OR open < low OR open > high OR close < low OR close > high
               OR row_adjustflag <> '3' OR tradestatus NOT IN ('0', '1')
               OR provider_code <> CASE WHEN identity_exchange = 'SSE'
                                        THEN 'sh.' || stock_code ELSE 'sz.' || stock_code END
               OR date < request_start OR date > request_end
            """,
        )
        if invalid_raw:
            raise NoneBackfillError(f"raw tail contains {invalid_raw} invalid rows")
        raw_duplicates = _single_int(
            connection,
            """
            SELECT count(*) FROM (
                SELECT security_id, date FROM normalized_raw_all
                GROUP BY security_id, date HAVING count(*) > 1
            )
            """,
        )
        if raw_duplicates:
            raise NoneBackfillError("raw tail contains duplicate security/date keys")
        connection.execute(
            """
            CREATE VIEW normalized_raw AS
            SELECT * EXCLUDE (
                provider_code, row_adjustflag, tradestatus, request_start, request_end
            )
            FROM normalized_raw_all
            """
        )
        parent_rows = _single_int(connection, "SELECT count(*) FROM parent_none")
        overlap_rows = _single_int(
            connection,
            "SELECT count(*) FROM normalized_raw r JOIN parent_none p USING (security_id, date)",
        )
        inserted_rows = raw_rows - overlap_rows
        if inserted_rows <= 0:
            raise NoneBackfillError("tail repair inserts no absent keys")
        merged_query = """
            SELECT * FROM parent_none
            UNION ALL
            SELECT r.* FROM normalized_raw r
            WHERE NOT EXISTS (
                SELECT 1 FROM parent_none p
                WHERE p.security_id = r.security_id AND p.date = r.date
            )
        """
        output_rows = _single_int(connection, f"SELECT count(*) FROM ({merged_query})")
        if output_rows != parent_rows + inserted_rows:
            raise NoneBackfillError("merged tail row count does not reconcile")
        duplicates = _single_int(
            connection,
            f"""
            SELECT count(*) FROM (
                SELECT security_id, date FROM ({merged_query})
                GROUP BY security_id, date HAVING count(*) > 1
            )
            """,
        )
        if duplicates:
            raise NoneBackfillError("merged tail revision contains duplicate keys")
        for code in codes:
            maximum = connection.execute(
                f"SELECT max(date) FROM ({merged_query}) WHERE security_id = ?", [code]
            ).fetchone()[0]
            if str(maximum) != str((raw_meta.get("tail_window") or {}).get("end")):
                raise NoneBackfillError(f"repaired tail does not reach declared end for {code}")
        connection.execute(
            f"COPY (SELECT * FROM ({merged_query}) ORDER BY security_id, date, stock_code) "
            f"TO {_sql_string(str(staging / 'daily_ohlcv_none.parquet'))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.close()

    linked_names = (
        "daily_universe.parquet",
        "daily_isST.parquet",
        "st_status_pit.parquet",
        "ref_security_code_validity.parquet",
    )
    try:
        for name in linked_names:
            os.link(parent / name, staging / name)
        data_names = ("daily_ohlcv_none.parquet", *linked_names)
        checksums = {name: f"sha256:{_sha256(staging / name)}" for name in data_names}
        meta = {
            "revision": revision,
            "kind": "oci_none_tail_repair_child",
            "issue": 40,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "parent_revision": parent.name,
            "raw_revision": raw.name,
            "lineage": {
                "parent": str(parent),
                "raw_revision": str(raw),
                "raw_meta_sha256": f"sha256:{_sha256(raw / 'META.json')}",
                "raw_manifest_sha256": f"sha256:{_sha256(raw / 'manifest.json')}",
            },
            "merge_policy": "retain_parent_and_fill_absent_security_date_keys",
            "scope": {"security_ids": codes, "tail_window": raw_meta.get("tail_window")},
            "counts": {
                "affected_securities": len(codes),
                "parent_rows": parent_rows,
                "raw_rows": raw_rows,
                "overlap_rows_retained_from_parent": overlap_rows,
                "inserted_rows": inserted_rows,
                "output_rows": output_rows,
                "parent_files_verified": parent_verified,
                "raw_files_verified": raw_verified,
            },
            "constraints": {
                "duplicate_security_date": 0,
                "invalid_inserted_rows": 0,
                "tradestatus_1_without_bar_for_raw_tail": 0,
                "full_window_reconciles_prefix_plus_tail": True,
                "no_parent_overwrite": True,
                "no_qfq_conversion": True,
                "no_pg_publication": True,
            },
            "checksums": checksums,
        }
        (staging / "META.json").write_text(
            json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "CHECKSUMS.sha256").write_text(
            "".join(
                f"{checksum.removeprefix('sha256:')}  {name}\n"
                for name, checksum in sorted(checksums.items())
            ),
            encoding="utf-8",
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return NoneTailRepairResult(
        revision=revision,
        output=output,
        affected_securities=len(codes),
        parent_rows=parent_rows,
        raw_rows=raw_rows,
        overlap_rows=overlap_rows,
        inserted_rows=inserted_rows,
        output_rows=output_rows,
        checksums=checksums,
    )
