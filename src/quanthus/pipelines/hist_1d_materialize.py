"""Materialize independent historical DWD from verified archive and lexical captures."""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import importlib
import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from datetime import time as daytime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_audit import BASE251_MANIFEST

SHANGHAI = ZoneInfo("Asia/Shanghai")
METHOD = "PROVIDER_ADJUST_FACTOR"
DIRECTION = "PROVIDER_NATIVE"


def identifier(kind: str, *parts: str) -> str:
    raw = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode()
    return kind + "_" + hashlib.sha256(raw).hexdigest()[:32]


def midnight(day: date) -> datetime:
    return datetime.combine(day, daytime.min, SHANGHAI).astimezone(timezone.utc)


def decimal_value(text: str, *, positive: bool = False) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("INVALID_SOURCE_DECIMAL") from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError("INVALID_SOURCE_DECIMAL")
    return value


def official_names(path: Path) -> dict[str, list[tuple[date, str]]]:
    if not path.exists():
        return {}
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        strings = []
        for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("x:si", ns):
            strings.append("".join(t.text or "" for t in node.findall(".//x:t", ns)))
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result: dict[str, list[tuple[date, str]]] = {}
    for index, row in enumerate(sheet.findall(".//x:row", ns)):
        values = []
        for cell in row.findall("x:c", ns):
            value_node = cell.find("x:v", ns)
            value = (
                value_node.text
                if value_node is not None
                else "".join(t.text or "" for t in cell.findall(".//x:t", ns))
            )
            if cell.get("t") == "s" and value:
                value = strings[int(value)]
            values.append(value or "")
        if index == 0:
            if values != ["变更日期", "证券代码", "证券简称", "变更前简称", "变更后简称"]:
                raise ValueError("SZSE_NAME_SCHEMA_CHANGED")
            continue
        if len(values) != 5:
            raise ValueError("SZSE_NAME_ROW_INVALID")
        code = values[1].zfill(6)
        result.setdefault(code, []).append((date.fromisoformat(values[0]), values[4]))
    for rows in result.values():
        rows.sort()
    return result


def subtype(flag: str, day: date, changes: list[tuple[date, str]]) -> tuple[str, str]:
    if flag == "0":
        if day < date(2005, 1, 1):
            return "UNKNOWN", "REFERENCE_REGIME_HISTORY_NOT_QUALIFIED"
        return "NORMAL", "PROVIDER_NON_ST_CLASS"
    if flag != "1":
        return "UNKNOWN", "PROVIDER_ST_OBSERVATION_UNAVAILABLE"
    position = bisect.bisect_right([item[0] for item in changes], day) - 1
    if position < 0:
        return "UNKNOWN", "ST_CLASS_OBSERVED_SUBTYPE_UNRESOLVED"
    same_day = {name for event_day, name in changes if event_day == changes[position][0]}
    if len(same_day) != 1:
        return "UNKNOWN", "OFFICIAL_NAME_SAME_DATE_CONFLICT"
    name = changes[position][1].upper().replace("＊", "*").replace(" ", "")
    if re.match(r"^S?\*ST", name):
        return "DELISTING_RISK", "OFFICIAL_SZSE_STAR_ST_NAME_AND_PROVIDER_ST_CLASS"
    if re.match(r"^(ST|SST)", name):
        return "ST", "OFFICIAL_SZSE_ST_NAME_AND_PROVIDER_ST_CLASS"
    return "UNKNOWN", "OFFICIAL_NAME_AND_PROVIDER_ST_CLASS_DIFFER"


def capture_payload(path: Path, plan_sha: str) -> tuple[dict[str, Any], str]:
    receipt = json.loads(path.with_suffix(path.suffix + ".receipt.json").read_bytes())
    if receipt["plan_sha256"] != plan_sha or file_sha256(path) != receipt["sha256"]:
        raise ValueError("RAW_CAPTURE_CHANGED")
    if receipt["error_code"] == "10001001":
        recovery = path.with_name(path.name.replace(".json.gz", ".session-recovery-1.json.gz"))
        if recovery.is_file():
            new_receipt = json.loads(
                recovery.with_suffix(recovery.suffix + ".receipt.json").read_bytes()
            )
            if (
                new_receipt["plan_sha256"] != plan_sha
                or file_sha256(recovery) != new_receipt["sha256"]
                or new_receipt["error_code"] != "0"
            ):
                raise ValueError("SESSION_RECOVERY_CAPTURE_INVALID")
            new_payload = json.loads(gzip.decompress(recovery.read_bytes()))
            if new_payload.get("supersedes_failed_capture_sha256") != receipt["sha256"]:
                raise ValueError("SESSION_RECOVERY_LINEAGE_INVALID")
            path, receipt = recovery, new_receipt
    payload: dict[str, Any] = json.loads(gzip.decompress(path.read_bytes()))
    payload["_capture_relative_path"] = "/".join(path.parts[path.parts.index("raw") :])
    return payload, "sha256:" + receipt["sha256"]


def common(
    payload: dict[str, Any], digest: str, task: dict[str, Any], method: str
) -> dict[str, Any]:
    return {
        "record_source": "baostock."
        + ("query_history_k_data_plus" if method == "daily" else "query_adjust_factor"),
        "source_record_id": payload.get(
            "_capture_relative_path", f"raw/baostock/{task['listing_id']}/{method}.json.gz"
        ),
        "source_revision": "hist1d-baostock-v1@" + digest.removeprefix("sha256:")[:16],
        "source_artifact_sha256": digest,
        "knowledge_from": datetime.fromisoformat(payload["acquisition_completed_at"]),
        "knowledge_to": None,
    }


def daily_records(
    payload: dict[str, Any],
    digest: str,
    task: dict[str, Any],
    sessions: dict[tuple[str, date], tuple[Any, ...]],
    changes: list[tuple[date, str]],
    names_sha: str | None,
    names_acquired: datetime | None,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {
        "bar": [],
        "listing-status": [],
        "security-status": [],
        "exceptions": [],
    }
    if payload["error_code"] != "0":
        output["exceptions"].append({"code": task["code"], "reason": "SOURCE_QUERY_ERROR"})
        return output
    if not payload["rows"]:
        output["exceptions"].append(
            {"code": task["code"], "reason": "PROVIDER_DAILY_HISTORY_EMPTY"}
        )
        return output
    required = {
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "tradestatus",
        "isST",
    }
    if not required <= set(payload["fields"]) or len(set(payload["fields"])) != len(
        payload["fields"]
    ):
        raise ValueError("DAILY_SOURCE_SCHEMA_INVALID")
    base = common(payload, digest, task, "daily")
    observations: list[tuple[date, int, str, str, str]] = []
    seen: set[date] = set()
    for values in payload["rows"]:
        if len(values) != len(payload["fields"]):
            raise ValueError("DAILY_SOURCE_ROW_INVALID")
        row = dict(zip(payload["fields"], values, strict=True))
        day = date.fromisoformat(row["date"])
        if (
            row["code"] != task["code"]
            or not task["start_date"] <= str(day) <= task["end_date"]
            or day in seen
        ):
            raise ValueError("DAILY_SOURCE_IDENTITY_INVALID")
        seen.add(day)
        session = sessions.get((task["venue_id"], day))
        if session is None:
            output["exceptions"].append(
                {
                    "code": task["code"],
                    "date": str(day),
                    "reason": "FROZEN_CALENDAR_SESSION_UNAVAILABLE",
                }
            )
            continue
        state, reason = subtype(row["isST"], day, changes)
        observations.append(
            (day, int(session[3]), row["tradestatus"], row["isST"], state + "|" + reason)
        )
        if row["tradestatus"] == "0":
            continue
        if row["tradestatus"] != "1":
            output["exceptions"].append(
                {"code": task["code"], "date": str(day), "reason": "TRADESTATUS_UNAVAILABLE"}
            )
            continue
        try:
            prices = {
                field: decimal_value(row[field], positive=True)
                for field in ["open", "high", "low", "close"]
            }
            volume, amount = decimal_value(row["volume"]), decimal_value(row["amount"])
        except ValueError:
            output["exceptions"].append(
                {
                    "code": task["code"],
                    "date": str(day),
                    "reason": "SOURCE_PRICE_OR_QUANTITY_INVALID",
                }
            )
            continue
        if (
            prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
            or prices["low"] > prices["high"]
        ):
            raise ValueError("SOURCE_OHLC_RELATION_INVALID")
        output["bar"].append(
            {
                **base,
                "source_record_id": base["source_record_id"] + "#date=" + str(day),
                "observation_start": session[1],
                "observation_end": session[2],
                "listing_id": task["listing_id"],
                "calendar_session_id": session[0],
                "interval_size": "1d",
                "price_basis": "UNADJUSTED",
                "currency": "CNY",
                **prices,
                "volume": volume,
                "turnover": amount,
                "trade_count": None,
            }
        )
    observations.sort()
    for field, index in [("suspension", 2), ("provider", 3), ("special", 4)]:
        groups: list[list[tuple[date, int, str, str, str]]] = []
        for obs in observations:
            if field == "suspension" and obs[index] != "0":
                continue
            if groups and obs[index] == groups[-1][-1][index] and obs[1] == groups[-1][-1][1] + 1:
                groups[-1].append(obs)
            else:
                groups.append([obs])
        for group in groups:
            start, end = group[0][0], group[-1][0] + timedelta(days=1)
            record = {
                **base,
                "effective_from": midnight(start),
                "effective_to": midnight(end),
                "source_record_id": base["source_record_id"]
                + f"#from={start}&through={group[-1][0]}",
            }
            if field == "suspension":
                output["listing-status"].append(
                    {
                        **record,
                        "listing_status_id": identifier(
                            "listing_status", task["listing_id"], "SUSPENSION", str(start), digest
                        ),
                        "listing_id": task["listing_id"],
                        "status_type": "SUSPENSION",
                        "status_value": "SUSPENDED",
                        "reason_code": "BAOSTOCK_TRADESTATUS_ZERO",
                    }
                )
            else:
                if field == "provider":
                    state = {"0": "NORMAL", "1": "ST"}.get(group[0][3], "UNKNOWN")
                    reason, kind = "BAOSTOCK_BINARY_ST_OBSERVATION", "PROVIDER_DAILY_STATE"
                else:
                    state, reason = group[0][4].split("|", 1)
                    kind = "SPECIAL_TREATMENT"
                    if reason.startswith("OFFICIAL_SZSE_") and names_sha:
                        record["record_source"] = "szse.short-name-change+baostock.isST"
                        record["source_revision"] += "|szse=" + names_sha[:16]
                        record["source_artifact_sha256"] = "sha256:" + names_sha
                        if names_acquired is not None:
                            record["knowledge_from"] = max(record["knowledge_from"], names_acquired)
                output["security-status"].append(
                    {
                        **record,
                        "security_status_id": identifier(
                            "security_status", task["security_id"], kind, str(start), digest
                        ),
                        "security_id": task["security_id"],
                        "status_type": kind,
                        "status_value": state,
                        "reason_code": reason,
                    }
                )
    return output


def factor_records(
    payload: dict[str, Any], digest: str, task: dict[str, Any]
) -> list[dict[str, Any]]:
    if payload["error_code"] != "0" or not payload["rows"]:
        return []
    if not {"code", "dividOperateDate", "adjustFactor"} <= set(payload["fields"]):
        raise ValueError("FACTOR_SOURCE_SCHEMA_INVALID")
    base = common(payload, digest, task, "factor")
    result = []
    for values in payload["rows"]:
        row = dict(zip(payload["fields"], values, strict=True))
        if row["code"] != task["code"]:
            raise ValueError("FACTOR_SOURCE_IDENTITY_INVALID")
        day = date.fromisoformat(row["dividOperateDate"])
        if not date(1990, 12, 19) <= day <= date(2011, 1, 31):
            raise ValueError("FACTOR_SOURCE_DATE_INVALID")
        value = decimal_value(row["adjustFactor"], positive=True)
        result.append(
            {
                **base,
                "source_record_id": base["source_record_id"] + "#date=" + str(day),
                "effective_from": midnight(day),
                "effective_to": None,
                "adjustment_factor_id": identifier(
                    "adjustment_factor", task["security_id"], METHOD, DIRECTION, "hist1d-bs-v1"
                ),
                "security_id": task["security_id"],
                "adjustment_method": METHOD,
                "adjustment_direction": DIRECTION,
                "factor_value": value,
                "anchor_date": day,
                "evidence_set_digest": digest,
            }
        )
    return sorted(result, key=lambda row: row["effective_from"])


def _files(root: Path, table: str) -> list[Path]:
    return sorted((root / "payload" / table).rglob("*.parquet"))


def materialize(
    workspace: Path, base251: Path, output: Path, limit: int | None = None
) -> dict[str, Any]:
    if file_sha256(base251 / "manifest.json") != BASE251_MANIFEST:
        raise ValueError("BASE251_REFERENCE_IDENTITY_MISMATCH")
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    ds = importlib.import_module("pyarrow.dataset")
    duckdb = importlib.import_module("duckdb")
    output.mkdir(parents=True, exist_ok=False)
    scratch = output / "scratch"
    scratch.mkdir()
    c = duckdb.connect(str(scratch / "build.duckdb"), config={"threads": 2, "memory_limit": "2GB"})
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("SET preserve_insertion_order=false")
    tables = [
        "dwd_security_bar",
        "dwd_listing_status_interval",
        "dwd_security_status_interval",
        "dwd_adjustment_factor",
    ]
    schemas = {t: pq.ParquetFile(_files(base251, t)[0]).schema_arrow for t in tables}
    for t in [
        "dwd_listing",
        "dwd_listing_status_interval",
        "dwd_trading_calendar",
        "dwd_adjustment_factor",
    ]:
        c.read_parquet([str(p) for p in _files(base251, t)], hive_partitioning=False).create_view(
            "base_" + t
        )
    listing_rows = c.execute(
        "SELECT listing_id,venue_id FROM base_dwd_listing WHERE knowledge_to IS NULL"
    ).fetchall()
    venue_for = dict(listing_rows)
    calendar_rows = c.execute("""SELECT venue_id,trade_date,calendar_session_id,open_at,close_at
        FROM base_dwd_trading_calendar WHERE knowledge_to IS NULL AND is_open
        AND session_type='REGULAR_DAY' AND trade_date<=DATE '2019-12-31'
        ORDER BY venue_id,trade_date""").fetchall()
    ordinals: dict[str, int] = {}
    sessions = {}
    for venue, day, sid, opens, closes in calendar_rows:
        ordinals[venue] = ordinals.get(venue, 0) + 1
        sessions[(venue, day)] = (sid, opens, closes, ordinals[venue])
    names_path = workspace / "raw/source-probes/szse-short-name-changes.xlsx"
    names = official_names(names_path)
    names_sha = file_sha256(names_path) if names_path.exists() else None
    names_acquired = None
    if names_path.exists():
        name_receipt = json.loads(
            (workspace / "raw/source-probes/SZSE-ACQUISITION.json").read_bytes()
        )
        if name_receipt["sha256"] != names_sha:
            raise ValueError("SZSE_NAME_ACQUISITION_IDENTITY_CHANGED")
        names_acquired = datetime.fromisoformat(name_receipt["knowledge_verified_at"])
    plan = json.loads((workspace / "COLLECTION-PLAN.json").read_bytes())
    plan_sha = file_sha256(workspace / "COLLECTION-PLAN.json")
    selected = plan["tasks"] if limit is None else plan["tasks"][:limit]
    normalized = scratch / "normalized"
    normalized.mkdir()
    exceptions = []
    totals = {name: 0 for name in ["bar", "listing-status", "security-status", "factor"]}
    kind_tables = dict(zip(totals, tables, strict=True))
    started = time.monotonic()
    for index, original in enumerate(selected):
        task = {**original, "venue_id": venue_for[original["listing_id"]]}
        root = workspace / "raw/baostock" / task["listing_id"]
        daily, digest = capture_payload(root / "daily.json.gz", plan_sha)
        records = daily_records(
            daily,
            digest,
            task,
            sessions,
            names.get(task["listing_code"], []),
            names_sha,
            names_acquired,
        )
        factor, factor_sha = capture_payload(root / "factor.json.gz", plan_sha)
        records["factor"] = factor_records(factor, factor_sha, task)
        exceptions.extend(records.pop("exceptions"))
        for kind, table in kind_tables.items():
            directory = normalized / kind
            directory.mkdir(exist_ok=True)
            values = records[kind]
            totals[kind] += len(values)
            pq.write_table(
                pa.Table.from_pylist(values, schema=schemas[table]),
                directory / f"{index:04d}.parquet",
                compression="zstd",
            )
        if (index + 1) % 100 == 0 or index + 1 == len(selected):
            print(
                json.dumps({"normalized": index + 1, "target": len(selected), "rows": totals}),
                flush=True,
            )
    for kind in totals:
        c.read_parquet(
            [str(p) for p in sorted((normalized / kind).glob("*.parquet"))], hive_partitioning=False
        ).create_view("supp_" + kind.replace("-", "_"))
    archive = workspace / "raw/base23/extracted/DWD"
    old_files = []
    for path in _files(archive, "dwd_security_bar"):
        match = re.search(r"observation_start_year=(\d+)", str(path))
        if match is None:
            raise ValueError("SOURCE_PARTITION_COORDINATE_MISSING")
        if int(match[1]) <= 2019:
            old_files.append(path)
    c.read_parquet([str(p) for p in old_files], hive_partitioning=False).create_view("old_bars")
    c.execute("""CREATE TABLE hist_bars AS SELECT * FROM old_bars UNION ALL
        SELECT n.* FROM supp_bar n WHERE NOT EXISTS (SELECT 1 FROM old_bars o
        WHERE o.listing_id=n.listing_id AND o.observation_start=n.observation_start
        AND o.interval_size=n.interval_size AND o.price_basis=n.price_basis)""")
    bar_schema = schemas["dwd_security_bar"]
    with_partitions = bar_schema.append(
        pa.field("observation_start_year", pa.int32(), False)
    ).append(pa.field("observation_start_month", pa.int32(), False))
    source_reader = c.execute("""SELECT *,
        year(observation_start)::INTEGER AS observation_start_year,
        month(observation_start)::INTEGER AS observation_start_month FROM hist_bars
        ORDER BY observation_start,listing_id""").fetch_record_batch(131072)
    reader = pa.RecordBatchReader.from_batches(
        with_partitions, (batch.cast(with_partitions) for batch in source_reader)
    )
    ds.write_dataset(
        reader,
        str(output / "payload/dwd_security_bar/interval_size=1d"),
        format="parquet",
        partitioning=ds.partitioning(
            pa.schema(
                [
                    with_partitions.field("observation_start_year"),
                    with_partitions.field("observation_start_month"),
                ]
            ),
            flavor="hive",
        ),
        file_options=ds.ParquetFileFormat().make_write_options(compression="zstd"),
        max_rows_per_file=262144,
        min_rows_per_group=32768,
        max_rows_per_group=131072,
        max_open_files=4,
        use_threads=False,
        existing_data_behavior="error",
    )

    def write_table(table: str, sql: str) -> None:
        schema = pq.ParquetFile(_files(base251, table)[0]).schema_arrow
        target = output / "payload" / table / "all/part-000000.parquet"
        target.parent.mkdir(parents=True)
        arrow = c.execute(sql).fetch_arrow_table().cast(schema)
        pq.write_table(arrow, target, compression="zstd")

    c.execute("""CREATE TABLE universe AS SELECT DISTINCT l.* FROM base_dwd_listing l
        JOIN base_dwd_listing_status_interval s USING(listing_id)
        WHERE l.knowledge_to IS NULL AND s.knowledge_to IS NULL AND l.listing_currency='CNY'
        AND l.board_code IN ('SSE_main','SZ_main','ChiNext','CHINEXT','STAR')
        AND s.status_type='LISTING_LIFECYCLE' AND s.status_value='LISTED'
        AND s.effective_from<TIMESTAMPTZ '2020-01-01 00:00:00+08'
        AND (s.effective_to IS NULL OR s.effective_to>TIMESTAMPTZ '1990-12-19 00:00:00+08')""")
    write_table("dwd_listing", "SELECT * FROM universe")
    for table in [
        "dwd_security",
        "dwd_venue",
        "dwd_trading_calendar",
        "dwd_price_limit_regime",
        "dwd_corporate_action",
        "dwd_corporate_action_term",
    ]:
        files = _files(base251, table)
        if not files:
            continue
        c.read_parquet([str(p) for p in files], hive_partitioning=False).create_view("ref_" + table)
        fields = set(pq.ParquetFile(files[0]).schema_arrow.names)
        where = ["knowledge_to IS NULL"] if "knowledge_to" in fields else ["true"]
        for field in ["listing_id", "security_id", "venue_id"]:
            if field in fields:
                where.append(f"{field} IN (SELECT {field} FROM universe)")
        if table not in ["dwd_security", "dwd_venue"]:
            if "effective_from" in fields:
                where.append("effective_from<TIMESTAMPTZ '2020-01-01 00:00:00+08'")
            if "effective_at" in fields:
                where.append("effective_at<TIMESTAMPTZ '2020-01-01 00:00:00+08'")
            if "trade_date" in fields:
                where.append("trade_date BETWEEN DATE '1990-12-19' AND DATE '2019-12-31'")
        if table == "dwd_corporate_action_term":
            where.append(
                "corporate_action_id IN (SELECT corporate_action_id FROM eligible_actions)"
            )
        sql = "SELECT * FROM ref_" + table + " WHERE " + " AND ".join(where)
        write_table(table, sql)
        if table == "dwd_corporate_action":
            c.execute("CREATE TEMP VIEW eligible_actions AS " + sql)
    write_table(
        "dwd_listing_status_interval",
        """SELECT * FROM base_dwd_listing_status_interval
        WHERE knowledge_to IS NULL AND listing_id IN (SELECT listing_id FROM universe)
        AND effective_from<TIMESTAMPTZ '2020-01-01 00:00:00+08'
        UNION ALL SELECT * FROM supp_listing_status""",
    )
    c.read_parquet(
        [str(p) for p in _files(base251, "dwd_security_status_interval")], hive_partitioning=False
    ).create_view("base_status")
    write_table(
        "dwd_security_status_interval",
        """SELECT * REPLACE (
        CASE WHEN security_id IN (SELECT DISTINCT security_id FROM supp_security_status)
             THEN greatest(effective_from,TIMESTAMPTZ '2011-01-01 00:00:00+08')
             ELSE effective_from END AS effective_from) FROM base_status
        WHERE knowledge_to IS NULL AND security_id IN (SELECT security_id FROM universe)
        AND effective_from<TIMESTAMPTZ '2020-01-01 00:00:00+08'
        AND (effective_to IS NULL OR effective_to>TIMESTAMPTZ '2011-01-01 00:00:00+08')
        UNION ALL SELECT * FROM supp_security_status""",
    )
    factor_conflicts = c.execute("""SELECT count(*) FROM supp_factor n
        JOIN base_dwd_adjustment_factor b
        USING(security_id,effective_from,adjustment_method,adjustment_direction)
        WHERE b.knowledge_to IS NULL AND n.factor_value<>b.factor_value""").fetchone()[0]
    write_table(
        "dwd_adjustment_factor",
        """WITH factors AS (
        SELECT * FROM base_dwd_adjustment_factor WHERE knowledge_to IS NULL
        AND adjustment_method='PROVIDER_ADJUST_FACTOR' AND adjustment_direction='PROVIDER_NATIVE'
        AND security_id IN (SELECT security_id FROM universe)
        AND effective_from<TIMESTAMPTZ '2020-01-01 00:00:00+08'
        UNION ALL SELECT n.* FROM supp_factor n WHERE NOT EXISTS (
           SELECT 1 FROM base_dwd_adjustment_factor b WHERE b.knowledge_to IS NULL
           AND b.security_id=n.security_id AND b.effective_from=n.effective_from
            AND b.adjustment_method=n.adjustment_method
            AND b.adjustment_direction=n.adjustment_direction))
        SELECT * REPLACE (lead(effective_from) OVER (
        PARTITION BY security_id,adjustment_method,adjustment_direction
        ORDER BY effective_from) AS effective_to)
        FROM factors ORDER BY security_id,effective_from""",
    )
    evidence = output / "evidence"
    evidence.mkdir()
    (evidence / "normalization-exceptions.json").write_text(json.dumps(exceptions, indent=2) + "\n")
    summary = {
        "status": "MATERIALIZED_CANDIDATE",
        "source_securities": len(selected),
        "normalization_rows": totals,
        "normalization_exceptions": len(exceptions),
        "factor_overlap_conflicts": factor_conflicts,
        "bar_rows": c.execute("SELECT count(*) FROM hist_bars").fetchone()[0],
        "collection_plan_sha256": plan_sha,
        "base251_manifest_sha256": BASE251_MANIFEST,
        "szse_names_sha256": names_sha,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output / "MATERIALIZATION.json").write_text(json.dumps(summary, indent=2) + "\n")
    c.close()
    shutil.rmtree(scratch)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base251", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(
        json.dumps(materialize(args.workspace, args.base251, args.output, args.limit)), flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
