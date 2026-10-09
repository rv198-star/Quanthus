"""New governance observations of retained typed Daily, without raw/PIT claims.

The exact original package and rows remain local evidence. This bounded adapter
does not recreate missing ODS, provider acquisition, accepted-child history or
minute qualification. It preserves every business atom except the reviewed daily
price-basis alias and an explicitly checked calendar reference.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import instant
from .base22_sources import _tree
from .base23_core import SHANGHAI, _day, _sha
from .base23_sources import START, THROUGH

CONTRACT_ID = "p10-base23-retained-typed-daily-core-v1"
QUALIFICATION = "RETAINED_TYPED_WAREHOUSE_GOVERNANCE_NOT_RAW_OR_PROVIDER_PIT"
TABLES = {
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_security_valuation",
    "dwd_index_bar",
}
PROVENANCE = {
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
}
MAX_ROWS = 32768


def reobserve_partition(
    rows: list[dict[str, Any]],
    *,
    table: str,
    day: date,
    schema: Any,
    rule: dict[str, Any],
    listings: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    original_calendars: list[dict[str, Any]],
    qualified_calendars: list[dict[str, Any]],
    observed_at: datetime,
    member_sha256: str,
    package_manifest_sha256: str,
    qualification: str,
    input_digest: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One exact day/table; callers archive and hash the whole source closure.

    A row's original warehouse knowledge is preserved in evidence, not asserted
    as supplier knowledge. New knowledge starts at the real processing clock.
    Both original and replacement calendar references require that row's Shanghai
    date and listing venue. Status has no interval_size gate. Consumers remain
    blocked by their own frozen admission contracts.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    _day(day)
    for identity in [member_sha256, package_manifest_sha256, input_digest]:
        _sha(identity)
    if (
        table not in TABLES
        or qualification != QUALIFICATION
        or not 0 < len(rows) <= MAX_ROWS
        or len(listings) > 8192
        or len(indexes) > 8192
        or len(original_calendars) > 90
        or len(qualified_calendars) > 90
    ):
        raise BoundedError("RETAINED_CORE_SCOPE_OR_QUALIFICATION_INVALID")
    clock = instant(observed_at)
    by_listing = {p["listing_id"]: p for p in listings}
    by_index = {p["index_id"]: p for p in indexes}
    old_cal = {p["calendar_session_id"]: p for p in original_calendars}
    if (
        len(by_listing) != len(listings)
        or len(by_index) != len(indexes)
        or len(old_cal) != len(original_calendars)
    ):
        raise BoundedError("RETAINED_CORE_PARENT_IDENTITIES_NOT_UNIQUE")
    output, evidence = [], []
    seen = set()
    classifications: Counter[str] = Counter()
    for raw in rows:
        if set(raw) != set(schema.names):
            raise BoundedError("RETAINED_CORE_EXACT_COLUMNS_REQUIRED")
        if any(not field.nullable and raw[field.name] is None for field in schema):
            raise BoundedError("RETAINED_CORE_REQUIRED_ATOM_NULL")
        start = instant(raw["observation_start"])
        if start.astimezone(SHANGHAI).date() != day:
            raise BoundedError("RETAINED_CORE_SHANGHAI_BUSINESS_DATE_INVALID")
        if raw["observation_end"] is not None and instant(raw["observation_end"]) <= start:
            raise BoundedError("RETAINED_CORE_OBSERVATION_INTERVAL_INVALID")
        if instant(raw["knowledge_from"]) > clock or (
            raw["knowledge_to"] is not None
            and instant(raw["knowledge_to"]) <= instant(raw["knowledge_from"])
        ):
            raise BoundedError("RETAINED_CORE_ORIGINAL_KNOWLEDGE_INTERVAL_INVALID")
        if table in {"dwd_security_bar", "dwd_index_bar"} and raw["interval_size"] != "1d":
            raise BoundedError("RETAINED_CORE_MINUTE_USE_NOT_AUTHORIZED")
        key = digest([raw[k] for k in rule["business_key"]])
        if key in seen:
            raise BoundedError("RETAINED_CORE_DUPLICATE_BUSINESS_KEY")
        seen.add(key)
        parent = (
            by_index.get(raw["index_id"])
            if table == "dwd_index_bar"
            else by_listing.get(raw["listing_id"])
        )
        if parent is None or (
            parent["effective_from"] > start
            or parent["effective_to"] is not None
            and start >= parent["effective_to"]
            or parent["knowledge_from"] > clock
            or parent["knowledge_to"] is not None
            and clock >= parent["knowledge_to"]
        ):
            raise BoundedError("RETAINED_CORE_DATED_PARENT_UNAVAILABLE")
        row = dict(raw)
        allowed = set(PROVENANCE)
        if table == "dwd_security_valuation" and raw["security_id"] != parent["security_id"]:
            raise BoundedError("RETAINED_CORE_VALUATION_SECURITY_LISTING_DISAGREES")
        if table in {"dwd_security_bar", "dwd_tradability_state"}:
            original = old_cal.get(raw["calendar_session_id"])
            if (
                original is None
                or original["trade_date"] != day
                or original["venue_id"] != parent["venue_id"]
            ):
                raise BoundedError("RETAINED_CORE_ORIGINAL_CALENDAR_DATE_VENUE_INVALID")
            matches = [
                c
                for c in qualified_calendars
                if c["trade_date"] == day
                and c["venue_id"] == parent["venue_id"]
                and c["session_type"] == "day"
            ]
            if len(matches) != 1:
                raise BoundedError("RETAINED_CORE_NEW_CALENDAR_DATE_VENUE_NOT_UNIQUE")
            calendar = matches[0]
            if (
                calendar["knowledge_from"] > clock
                or calendar["knowledge_to"] is not None
                and clock >= calendar["knowledge_to"]
                or type(calendar["is_open"]) is not bool
                or type(original["is_open"]) is not bool
                or original["is_open"] != calendar["is_open"]
            ):
                raise BoundedError("RETAINED_CORE_CALENDAR_KNOWLEDGE_OR_FLAGS_INVALID")
            if table == "dwd_tradability_state" and raw["is_tradable"] and not calendar["is_open"]:
                raise BoundedError("RETAINED_CORE_TRADABLE_ON_CLOSED_SESSION")
            row["calendar_session_id"] = calendar["calendar_session_id"]
            allowed.add("calendar_session_id")
        if table == "dwd_security_bar":
            if raw["price_basis"] not in {"none", "UNADJUSTED"}:
                raise BoundedError("RETAINED_CORE_DAILY_PRICE_BASIS_UNQUALIFIED")
            row["price_basis"] = "UNADJUSTED"
            allowed.add("price_basis")
        row.update(
            record_source="base23_retained_typed/" + table,
            source_record_id=key,
            source_revision=CONTRACT_ID + ":" + package_manifest_sha256,
            source_artifact_sha256=member_sha256,
            knowledge_from=clock,
            knowledge_to=None,
        )
        try:
            typed = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            raise BoundedError("RETAINED_CORE_NATIVE_TYPE_OR_PRECISION_UNREPRESENTABLE") from None
        if typed != row or any(typed[k] != raw[k] for k in set(raw) - allowed):
            raise BoundedError("RETAINED_CORE_TYPED_OR_BUSINESS_ATOM_CHANGED")
        for field in schema:
            before, after = raw[field.name], typed[field.name]
            if isinstance(before, Decimal):
                if not isinstance(after, Decimal) or before.as_tuple() != after.as_tuple():
                    raise BoundedError("RETAINED_CORE_DECIMAL_PRECISION_CHANGED")
                classifications["EXACT_RETAINED_TYPED_DECIMAL"] += 1
                if before == 0:
                    classifications["ZERO"] += 1
            elif pa.types.is_decimal(field.type) and before is None:
                classifications["NULL_UNAVAILABLE"] += 1
        output.append(typed)
        evidence.append(
            {
                "business_key_sha256": key,
                "original_row_sha256": digest(raw),
                "output_row_sha256": digest(typed),
                "original_warehouse_knowledge_from": raw["knowledge_from"],
                "original_warehouse_knowledge_to": raw["knowledge_to"],
                "changed_fields": sorted(k for k in raw if raw[k] != typed[k]),
            }
        )
    proof = {
        "contract_id": CONTRACT_ID,
        "qualification": QUALIFICATION,
        "table": table,
        "business_date": day,
        "member_sha256": member_sha256,
        "package_manifest_sha256": package_manifest_sha256,
        "input_digest": input_digest,
        "observed_at": clock,
        "rows": len(output),
        "numeric_classifications": dict(classifications),
        "original_raw_core_recovered": False,
        "provider_acquisition": None,
        "historical_PIT_admission": False,
        "accepted_child_inferred": False,
        "consumer_admission": "NOT_GRANTED",
        "row_evidence": evidence,
    }
    proof["identity"] = digest(proof)
    return output, proof


def audit_retained_scope(root: Path, budget: Budget) -> dict[str, Any]:
    """Offline whole-package integrity, four-table replay and global key closure."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .daily_domain import verify_daily_domain_increment

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    plan = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(plan)
        or plan.get("purpose") != CONTRACT_ID
        or plan.get("qualification") != QUALIFICATION
        or plan.get("original_raw_core_recovered") is not False
        or plan.get("provider_acquisition") is not None
        or body.get("projection_module_sha256") != file_sha(Path(__file__))
        or body.get("complete_Base23") is not False
        or body.get("READY") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != TABLES
        or set(plan.get("source_ids_by_table", {})) != TABLES
        or any(len(ids) != 1 for ids in plan["source_ids_by_table"].values())
    ):
        raise BoundedError("RETAINED_CORE_LOCAL_ADMISSION_INVALID")
    physical = body["physical_members"]
    if _tree(root) != {"SCOPE.json", *physical}:
        raise BoundedError("RETAINED_CORE_EXACT_PHYSICAL_SET_CHANGED")
    if {p for p in physical if p.startswith("payload/")} != {
        v["path"] for v in body["outputs"].values()
    }:
        raise BoundedError("RETAINED_CORE_PHYSICAL_PARTITION_SET_CHANGED")
    for rel, member in physical.items():
        path = _physical(root, rel, independent=True)
        if path.stat().st_size != member["size_bytes"] or file_sha(path) != member["sha256"]:
            raise BoundedError("RETAINED_CORE_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    package = _physical(root, body["original_manifest"], independent=True).parent
    if file_sha(package / "manifest.json") != plan["package_manifest_sha256"]:
        raise BoundedError("RETAINED_CORE_ORIGINAL_PACKAGE_IDENTITY_CHANGED")
    result = verify_daily_domain_increment(package)
    day = date.fromisoformat(body["business_date"])
    if result.market_date != day:
        raise BoundedError("RETAINED_CORE_ORIGINAL_PACKAGE_DATE_CHANGED")
    original = json.loads((package / "manifest.json").read_bytes())
    files = {f["path"]: f for f in original["files"]}
    for relative in files:
        if not relative.endswith(".parquet"):
            continue
        tables = [name for name in original["row_counts"] if f"/{name}/" in "/" + relative]
        if (
            len(tables) != 1
            or pq.ParquetFile(package / relative).metadata.num_rows
            != original["row_counts"][tables[0]]
        ):
            raise BoundedError("RETAINED_CORE_ORIGINAL_ROW_CENSUS_CHANGED")

    def read(rel: str, bound: int) -> list[dict[str, Any]]:
        path = _physical(root, rel, independent=True)
        pf = pq.ParquetFile(path)
        if pf.metadata.num_rows > bound:
            raise BoundedError("RETAINED_CORE_OFFLINE_ROW_BOUND_EXCEEDED")
        rows: list[dict[str, Any]] = []
        for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
            rows.extend(batch.to_pylist())
            budget.check()
        return rows

    if set(body["parents"]) != {"dwd_listing", "dwd_market_index", "dwd_trading_calendar"}:
        raise BoundedError("RETAINED_CORE_EXACT_LOCAL_PARENTS_REQUIRED")
    parents = {t: read(rel, 8192) for t, rel in body["parents"].items()}
    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != plan.get("history_materialization_sha256"):
        raise BoundedError("RETAINED_CORE_HISTORY_PARENT_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    if set(body["history_parents"]) != {"dwd_listing", "dwd_market_index"}:
        raise BoundedError("RETAINED_CORE_HISTORY_PARENT_SET_CHANGED")
    for table, item in body["history_parents"].items():
        matches = [
            f
            for f in history["layout"][table]["files"]
            if f["path"] == item["archive_path"] and f["sha256"] == item["sha256"]
        ]
        if len(matches) != 1 or file_sha(root / body["parents"][table]) != item["sha256"]:
            raise BoundedError("RETAINED_CORE_HISTORY_PARENT_MEMBERSHIP_CHANGED")
    calendar_path = _physical(root, body["calendar_scope"], independent=True)
    calendar_scope = json.loads(calendar_path.read_bytes())
    if (
        file_sha(calendar_path) != plan.get("calendar_scope_sha256")
        or calendar_scope.get("identity")
        != digest({k: v for k, v in calendar_scope.items() if k != "identity"})
        or calendar_scope.get("source_qualification")
        != "RETAINED_BYTE_VERIFIED_GOVERNANCE_NOT_PROVIDER_PIT"
        or calendar_scope.get("calendar_projection_proof", {}).get("full_month_coverage")
        is not True
        or calendar_scope.get("physical_members", {})
        .get(body["calendar_archive_path"], {})
        .get("sha256")
        != file_sha(root / body["parents"]["dwd_trading_calendar"])
    ):
        raise BoundedError("RETAINED_CORE_QUALIFIED_CALENDAR_MEMBERSHIP_CHANGED")
    for table, rel in body["parents"].items():
        if not pq.ParquetFile(root / rel).schema_arrow.equals(ss[table], check_metadata=False):
            raise BoundedError("RETAINED_CORE_PARENT_SCHEMA_CHANGED")
    original_calendar = read(body["original_calendar"], 90)
    calendar_member = str((root / body["original_calendar"]).relative_to(package))
    if calendar_member not in files:
        raise BoundedError("RETAINED_CORE_ORIGINAL_CALENDAR_NOT_IN_PACKAGE")
    summaries, registry = {}, {}
    for table in sorted(TABLES):
        source = body["sources"][table]
        path = _physical(root, source["path"], independent=True)
        relative = str(path.relative_to(package))
        declared = files.get(relative)
        if (
            declared is None
            or source["sha256"] != declared["sha256"]
            or plan["source_ids_by_table"][table] != [source["sha256"]]
            or f"/{table}/" not in "/" + relative
        ):
            raise BoundedError("RETAINED_CORE_TABLE_SOURCE_MEMBERSHIP_CHANGED")
        source_file = pq.ParquetFile(path)
        if set(source_file.schema_arrow.names) != set(ss[table].names) or any(
            field.type != ss[table].field(field.name).type for field in source_file.schema_arrow
        ):
            raise BoundedError("RETAINED_CORE_ORIGINAL_TYPED_SCHEMA_CHANGED")
        selected: list[dict[str, Any]] = []
        if source_file.metadata.num_rows > 2_000_000:
            raise BoundedError("RETAINED_CORE_ARCHIVED_MEMBER_BOUND_EXCEEDED")
        for batch in source_file.iter_batches(batch_size=budget.limits.batch_rows):
            selected.extend(
                row
                for row in batch.to_pylist()
                if table not in {"dwd_security_bar", "dwd_index_bar"}
                or row["interval_size"] == "1d"
            )
            if len(selected) > MAX_ROWS:
                raise BoundedError("RETAINED_CORE_SELECTED_DAY_BOUND_EXCEEDED")
            budget.check()
        rows, proof = reobserve_partition(
            selected,
            table=table,
            day=day,
            schema=ss[table],
            rule=contract["tables"][table],
            listings=parents["dwd_listing"],
            indexes=parents["dwd_market_index"],
            original_calendars=original_calendar,
            qualified_calendars=parents["dwd_trading_calendar"],
            observed_at=body["observed_at"],
            member_sha256=source["sha256"],
            package_manifest_sha256=plan["package_manifest_sha256"],
            qualification=QUALIFICATION,
            input_digest=body["input_digest"],
        )
        evidence = json.loads(_physical(root, body["proofs"][table], independent=True).read_bytes())
        out = body["outputs"][table]
        path = _physical(root, out["path"], independent=True)
        if (
            digest(proof) != digest(evidence)
            or digest(read(out["path"], MAX_ROWS)) != digest(rows)
            or any(partition(row, contract["tables"][table]) != out["partition"] for row in rows)
            or str(path.parent.relative_to(root)) != str(Path("payload") / table / out["partition"])
            or semantic_scan([path], ss[table], check=budget.check) != out["semantic_scan"]
        ):
            raise BoundedError("RETAINED_CORE_EXACT_TYPED_REPLAY_CHANGED")
        summaries[table] = out["semantic_scan"]
        registry[table] = [
            (
                "base23_retained_typed/" + table,
                CONTRACT_ID + ":" + plan["package_manifest_sha256"],
                source["sha256"],
            )
        ]
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in TABLES:
            scan.add(table, [root / body["outputs"][table]["path"]])
            scan.keys(
                table,
                contract["tables"][table]["business_key"],
                contract["tables"][table]["version_key"],
            )
            scan.lineage(table, registry[table])
            scan.reject(
                f"SELECT 1 FROM {table} WHERE "
                f"CAST(timezone('Asia/Shanghai',observation_start) AS DATE)<DATE '{START}' "
                f"OR CAST(timezone('Asia/Shanghai',observation_start) AS DATE)>DATE '{THROUGH}' "
                "OR knowledge_to<=knowledge_from OR observation_end<=observation_start LIMIT 1",
                "RETAINED_CORE_BUSINESS_OR_KNOWLEDGE_INTERVAL_INVALID",
            )
    receipt = {
        "status": "FULL_RETAINED_TYPED_DAILY_CORE_REPLAY_PASS_NOT_RAW_OR_COMPLETE_BASE23",
        "scope_identity": body["identity"],
        "summaries": summaries,
        "original_raw_core_recovered": False,
        "provider_acquisition": None,
        "historical_PIT_admission": False,
        "complete_Base23": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
