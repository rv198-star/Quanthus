"""Build the listed-company identity backbone from one sealed ODS input plan."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import tempfile
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo


class ListedCompanyBuildError(RuntimeError):
    """Raised when the listed-company candidate cannot be built without ambiguity."""


@dataclass(frozen=True)
class ListedCompanyBuildResult:
    root: Path
    semantic_digest: str
    row_counts: dict[str, int]
    primary_listed_count: int
    bse_listed_count: int


_OWNED_TABLES = (
    "dwd_listed_company",
    "dwd_company_identifier",
    "dwd_security",
    "dwd_security_identifier",
    "dwd_listing",
    "dwd_trading_venue",
)
_SHA256 = "sha256:"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ListedCompanyBuildError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ListedCompanyBuildError(f"JSON root must be an object: {path}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ListedCompanyBuildError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ListedCompanyBuildError(f"{field} must be an array")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ListedCompanyBuildError(f"{key} must be a non-empty string")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ListedCompanyBuildError(f"cannot hash file: {path}") from exc
    return f"sha256:{digest.hexdigest()}"


def _normalize(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        _normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ListedCompanyBuildError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ListedCompanyBuildError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListedCompanyBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date_value(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ListedCompanyBuildError(f"{field} must be a date") from exc
    raise ListedCompanyBuildError(f"{field} must be a date")


def _effective_start(value: date) -> datetime:
    return datetime.combine(value, datetime_time.min, _SHANGHAI).astimezone(timezone.utc)


def _effective_end(value: date | None) -> datetime | None:
    return _effective_start(value + timedelta(days=1)) if value is not None else None


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ListedCompanyBuildError("source path must be a relative string")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise ListedCompanyBuildError(f"unsafe source path: {relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ListedCompanyBuildError(f"source path escapes ODS package: {relative}")
    return resolved


def _verify_plan_package(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _object(manifest_path)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ListedCompanyBuildError("input plan READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise ListedCompanyBuildError("input plan READY does not match manifest")
    declared: set[str] = set()
    for raw in _sequence(manifest.get("files"), "plan manifest files"):
        item = _mapping(raw, "plan manifest file")
        relative = _text(item, "path")
        if "/" in relative or relative in declared:
            raise ListedCompanyBuildError("input plan has an invalid file inventory")
        declared.add(relative)
        path = root / relative
        if (
            not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise ListedCompanyBuildError(f"input plan file failed verification: {relative}")
    required = {"input-plan.json", "source-disposition-ledger.json"}
    if not required.issubset(declared):
        raise ListedCompanyBuildError("input plan package is missing required files")
    plan = _object(root / "input-plan.json")
    ledger = _object(root / "source-disposition-ledger.json")
    if manifest.get("plan_digest") != plan.get("plan_digest"):
        raise ListedCompanyBuildError("input plan digest differs from package manifest")
    return plan, ledger


def _verify_source(record: Mapping[str, Any], ods_root: Path) -> Path:
    path = _safe_file(ods_root, record.get("path"))
    size = record.get("size_bytes")
    digest = record.get("sha256")
    if (
        not path.is_file()
        or not isinstance(size, int)
        or path.stat().st_size != size
        or not isinstance(digest, str)
        or _sha256(path) != digest
    ):
        raise ListedCompanyBuildError(f"ODS source failed verification: {record.get('path')}")
    return path


def _seed_contract(path: Path) -> tuple[dict[str, Any], str]:
    seed = _object(path)
    if (
        seed.get("schema_version") != 1
        or seed.get("status") != "frozen"
        or len(_sequence(seed.get("venues"), "seed venues")) != 3
    ):
        raise ListedCompanyBuildError("reference seed is not the frozen v1 venue contract")
    exchanges = {
        _text(_mapping(raw, "seed venue"), "exchange_code")
        for raw in _sequence(seed.get("venues"), "seed venues")
    }
    if exchanges != {"SSE", "SZSE", "BSE"}:
        raise ListedCompanyBuildError("reference seed must own SSE, SZSE, and BSE")
    return seed, _sha256(path)


def _board(seed: Mapping[str, Any], exchange: str, code: str) -> str:
    for raw in _sequence(seed.get("board_rules"), "board rules"):
        rule = _mapping(raw, "board rule")
        if rule.get("exchange_code") != exchange:
            continue
        prefixes = _sequence(rule.get("prefixes"), "board prefixes")
        if "*" in prefixes or any(
            isinstance(prefix, str) and code.startswith(prefix) for prefix in prefixes
        ):
            return _text(rule, "board_code")
    raise ListedCompanyBuildError(f"no board rule for {exchange}:{code}")


def _table_schemas() -> dict[str, Any]:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise ListedCompanyBuildError("pyarrow is required for DWD construction") from exc
    common = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    return {
        "dwd_listed_company": pa.schema(
            common
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("display_name", pa.string(), nullable=False),
                pa.field("company_type", pa.string(), nullable=False),
                pa.field("resolution_scope", pa.string(), nullable=False),
            ]
        ),
        "dwd_company_identifier": pa.schema(
            common
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("identifier_namespace", pa.string(), nullable=False),
                pa.field("identifier_value", pa.string(), nullable=False),
                pa.field("identifier_authority", pa.string(), nullable=False),
            ]
        ),
        "dwd_security": pa.schema(
            common
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("instrument_type", pa.string(), nullable=False),
                pa.field("instrument_currency", pa.string(), nullable=False),
            ]
        ),
        "dwd_security_identifier": pa.schema(
            common
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("identifier_namespace", pa.string(), nullable=False),
                pa.field("identifier_value", pa.string(), nullable=False),
                pa.field("venue_id", pa.string(), nullable=True),
            ]
        ),
        "dwd_listing": pa.schema(
            common
            + [
                pa.field("listing_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("venue_id", pa.string(), nullable=False),
                pa.field("listing_code", pa.string(), nullable=False),
                pa.field("board_code", pa.string(), nullable=False),
                pa.field("listing_currency", pa.string(), nullable=False),
                pa.field("lot_size", pa.int64(), nullable=True),
            ]
        ),
        "dwd_trading_venue": pa.schema(
            common
            + [
                pa.field("venue_id", pa.string(), nullable=False),
                pa.field("mic_code", pa.string(), nullable=False),
                pa.field("venue_name", pa.string(), nullable=False),
                pa.field("country_code", pa.string(), nullable=False),
                pa.field("timezone", pa.string(), nullable=False),
            ]
        ),
    }


def _row_key(table: str, row: Mapping[str, Any]) -> tuple[object, ...]:
    keys = {
        "dwd_listed_company": ("company_id", "effective_from", "knowledge_from"),
        "dwd_company_identifier": (
            "identifier_namespace",
            "identifier_value",
            "effective_from",
            "knowledge_from",
        ),
        "dwd_security": ("security_id", "effective_from", "knowledge_from"),
        "dwd_security_identifier": (
            "identifier_namespace",
            "identifier_value",
            "effective_from",
            "knowledge_from",
        ),
        "dwd_listing": ("listing_id", "effective_from", "knowledge_from"),
        "dwd_trading_venue": ("venue_id", "effective_from", "knowledge_from"),
    }[table]
    return tuple(row[key] for key in keys)


def _validate_rows(rows: Mapping[str, list[dict[str, Any]]]) -> None:
    for table, values in rows.items():
        keys = [_row_key(table, row) for row in values]
        if len(keys) != len(set(keys)):
            raise ListedCompanyBuildError(f"duplicate row key in {table}")
        for row in values:
            end = row.get("effective_to")
            if end is not None and end <= row["effective_from"]:
                raise ListedCompanyBuildError(f"invalid effective interval in {table}")
    companies = {str(row["company_id"]) for row in rows["dwd_listed_company"]}
    securities = {str(row["security_id"]) for row in rows["dwd_security"]}
    venues = {str(row["venue_id"]) for row in rows["dwd_trading_venue"]}
    if any(row["company_id"] not in companies for row in rows["dwd_company_identifier"]):
        raise ListedCompanyBuildError("orphan company identifier")
    if any(row["company_id"] not in companies for row in rows["dwd_security"]):
        raise ListedCompanyBuildError("orphan security company")
    if any(row["security_id"] not in securities for row in rows["dwd_security_identifier"]):
        raise ListedCompanyBuildError("orphan security identifier")
    if any(row["security_id"] not in securities for row in rows["dwd_listing"]):
        raise ListedCompanyBuildError("orphan listing security")
    if any(row["venue_id"] not in venues for row in rows["dwd_listing"]):
        raise ListedCompanyBuildError("orphan listing venue")


def _write_json_atomic(path: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _verify_existing_output(output: Path, run_id: str, semantic_digest: str) -> None:
    manifest_path = output / "manifest.json"
    manifest = _object(manifest_path)
    try:
        ready = (output / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ListedCompanyBuildError("existing output READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise ListedCompanyBuildError("existing output READY does not match manifest")
    if manifest.get("run_id") != run_id or manifest.get("semantic_digest") != semantic_digest:
        raise ListedCompanyBuildError("existing output belongs to another run or semantic build")
    for raw in _sequence(manifest.get("files"), "output manifest files"):
        item = _mapping(raw, "output manifest file")
        relative = _text(item, "path")
        path = _safe_file(output, relative)
        if (
            not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise ListedCompanyBuildError(f"existing output file failed verification: {relative}")


def build_listed_company(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    reference_seed_path: Path,
    code_validity_path: Path,
    output: Path,
    run_id: str,
) -> ListedCompanyBuildResult:
    """Build six deterministic listed-company DWD objects from an exact Base plan."""

    started_at = datetime.now(timezone.utc)
    started_ns = time.perf_counter_ns()
    plan, ledger = _verify_plan_package(input_plan)
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    if semantic_plan.get("mode") != "base":
        raise ListedCompanyBuildError("listed-company v1 currently requires a Base input plan")
    if plan.get("run_id") != run_id:
        raise ListedCompanyBuildError("builder run id must match the sealed input plan")
    model = _object(model_path)
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    model_sha = _sha256(model_path)
    if model_identity.get("sha256") != model_sha or model_identity.get("contract_id") != model.get(
        "contract_id"
    ):
        raise ListedCompanyBuildError("model contract differs from the sealed input plan")
    knowledge_from = _timestamp(semantic_plan.get("knowledge_cutoff"), "knowledge_cutoff")
    seed, seed_sha = _seed_contract(reference_seed_path)
    code_validity = _object(code_validity_path)
    code_validity_sha = _sha256(code_validity_path)
    if code_validity.get("status") != "frozen":
        raise ListedCompanyBuildError("security-code validity mapping is not frozen")

    ledger_records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    modeled = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "modeled"
        and set(record.get("target_tables", [])).intersection(_OWNED_TABLES)
    ]
    stock_list_evidence = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "superseded"
        and record.get("mapping_identity") == "stock_list"
        and record.get("path") == "payload/canonical/stock_list.parquet"
    ]
    if len(stock_list_evidence) != 1:
        raise ListedCompanyBuildError(
            "Base plan must declare one superseded canonical stock-list evidence file"
        )
    relevant = [*modeled, *stock_list_evidence]
    verified_paths = {
        str(record["path"]): _verify_source(record, ods_package) for record in relevant
    }
    by_path = {str(record["path"]): record for record in relevant}
    required_paths = {
        "payload/canonical/ref_security_master.parquet",
        "payload/canonical/stock_list.parquet",
        "payload/canonical/ref_trading_calendar.parquet",
        "payload/canonical/cninfo_orgid_map.json",
    }
    if not required_paths.issubset(verified_paths):
        raise ListedCompanyBuildError("Base plan is missing a canonical identity source")

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise ListedCompanyBuildError("pyarrow is required for DWD construction") from exc

    master_relative = "payload/canonical/ref_security_master.parquet"
    master_rows = pq.read_table(verified_paths[master_relative]).to_pylist()
    master_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    business_fields = (
        "security_id",
        "stock_code",
        "exchange",
        "name",
        "list_date",
        "delist_date",
        "status",
        "bs_code",
        "source",
    )
    for raw in master_rows:
        row = dict(raw)
        key = (str(row.get("exchange")), str(row.get("stock_code")))
        if key in master_by_key:
            raise ListedCompanyBuildError(f"duplicate canonical security-master key: {key}")
        for field in business_fields:
            if field not in row:
                raise ListedCompanyBuildError(f"security master is missing {field}")
        master_by_key[key] = row

    reconciliation: list[dict[str, Any]] = []
    for relative, path in sorted(verified_paths.items()):
        record = by_path[relative]
        name = PurePosixPath(relative).name
        if name not in {"ref_security_master.parquet", "security_lifecycle.parquet"}:
            continue
        snapshot_rows = pq.read_table(path).to_pylist()
        for raw in snapshot_rows:
            row = dict(raw)
            key = (str(row.get("exchange")), str(row.get("stock_code")))
            canonical = master_by_key.get(key)
            if canonical is None or any(
                canonical.get(field) != row.get(field) for field in business_fields
            ):
                raise ListedCompanyBuildError(
                    f"security-master snapshot conflicts with canonical row: {key}"
                )
        reconciliation.append(
            {
                "dataset_id": record.get("dataset_id"),
                "path": relative,
                "source_rows": len(snapshot_rows),
                "outcome": "selected_authority"
                if relative == master_relative
                else "corroborated_snapshot",
                "unmatched_rows": 0,
            }
        )

    org_relative = "payload/canonical/cninfo_orgid_map.json"
    org_map_raw = json.loads(verified_paths[org_relative].read_text(encoding="utf-8"))
    if not isinstance(org_map_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in org_map_raw.items()
    ):
        raise ListedCompanyBuildError("CNINFO organization map is invalid")
    org_map = {str(key): str(value) for key, value in org_map_raw.items()}
    missing_org = sorted(
        str(row["stock_code"]) for row in master_rows if not org_map.get(str(row["stock_code"]))
    )
    if missing_org:
        raise ListedCompanyBuildError("security master has rows without CNINFO organization id")
    reconciliation.append(
        {
            "dataset_id": by_path[org_relative].get("dataset_id"),
            "path": org_relative,
            "source_rows": len(org_map),
            "outcome": "selected_company_identity_map",
            "matched_rows": len({str(row["stock_code"]) for row in master_rows}),
            "filtered_non_security_master_rows": len(
                set(org_map) - {str(row["stock_code"]) for row in master_rows}
            ),
        }
    )

    for relative, path in sorted(verified_paths.items()):
        if PurePosixPath(relative).name != "stock_list.parquet":
            continue
        values = pq.read_table(path, columns=["stock_code"]).to_pylist()
        unknown = sorted(
            str(row["stock_code"])
            for row in values
            if str(row["stock_code"]) not in {str(item["stock_code"]) for item in master_rows}
        )
        if unknown:
            raise ListedCompanyBuildError(
                "stock-list row is absent from the canonical security master"
            )
        reconciliation.append(
            {
                "dataset_id": by_path[relative].get("dataset_id"),
                "path": relative,
                "source_rows": len(values),
                "outcome": "corroborated_collection_scope",
                "unmatched_rows": 0,
            }
        )

    venue_seed = {
        _text(_mapping(raw, "seed venue"), "exchange_code"): dict(_mapping(raw, "seed venue"))
        for raw in _sequence(seed.get("venues"), "seed venues")
    }
    for relative, path in sorted(verified_paths.items()):
        if PurePosixPath(relative).name != "ref_trading_calendar.parquet":
            continue
        values = pq.read_table(path, columns=["exchange"]).to_pylist()
        unknown = sorted({str(row["exchange"]) for row in values}.difference(venue_seed))
        if unknown:
            raise ListedCompanyBuildError(f"calendar references unknown venues: {unknown}")
        reconciliation.append(
            {
                "dataset_id": by_path[relative].get("dataset_id"),
                "path": relative,
                "source_rows": len(values),
                "outcome": "folded_venue_reference_evidence",
                "unmatched_rows": 0,
            }
        )

    validity_by_code: dict[tuple[str, str], dict[str, Any]] = {}
    group_by_code: dict[tuple[str, str], str] = {}
    for raw in _sequence(code_validity.get("intervals"), "code validity intervals"):
        item = dict(_mapping(raw, "code validity interval"))
        key = (_text(item, "exchange"), _text(item, "stock_code"))
        if key in validity_by_code:
            raise ListedCompanyBuildError(f"duplicate code-validity interval: {key}")
        if key not in master_by_key:
            raise ListedCompanyBuildError(
                f"code-validity interval references unknown master row: {key}"
            )
        validity_by_code[key] = item
        group_by_code[key] = _text(item, "security_id")

    security_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    intervals_by_key: dict[tuple[str, str], tuple[datetime, datetime | None]] = {}
    for key, row in sorted(master_by_key.items()):
        validity = validity_by_code.get(key)
        raw_security_id = _text(row, "security_id")
        group = group_by_code.get(key, f"{key[0]}:{raw_security_id}")
        start = _effective_start(
            _date_value(validity.get("valid_from"), "valid_from")
            if validity
            else _date_value(row.get("list_date"), "list_date")
        )
        end = _effective_end(
            _date_value(validity.get("valid_to"), "valid_to")
            if validity and validity.get("valid_to") is not None
            else (
                _date_value(row.get("delist_date"), "delist_date")
                if row.get("delist_date") is not None
                else None
            )
        )
        intervals_by_key[key] = (start, end)
        security_groups[group].append(row)

    company_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in master_rows:
        company_groups[org_map[str(row["stock_code"])]].append(row)
    company_ids = {
        org_id: _stable_id("company", "CNINFO_ORG_ID", org_id) for org_id in company_groups
    }
    security_ids = {
        group: _stable_id("security", "ECONOMIC_SECURITY", group) for group in security_groups
    }
    venue_ids = {
        exchange: _stable_id("venue", str(venue["mic_code"]))
        for exchange, venue in venue_seed.items()
    }
    master_record = by_path[master_relative]
    master_sha = _text(master_record, "sha256")
    master_revision = "security-master@" + str(master_rows[0].get("as_of"))
    org_sha = _text(by_path[org_relative], "sha256")
    seed_revision = _text(seed, "seed_id")

    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in _OWNED_TABLES}
    for org_id, members in sorted(company_groups.items()):
        starts = [
            intervals_by_key[(str(row["exchange"]), str(row["stock_code"]))][0] for row in members
        ]
        ends = [
            intervals_by_key[(str(row["exchange"]), str(row["stock_code"]))][1] for row in members
        ]
        company_end = None if any(end is None for end in ends) else max(end for end in ends if end)
        display = max(
            members,
            key=lambda row: (
                str(row.get("status")) == "listed",
                _date_value(row.get("list_date"), "list_date"),
                str(row.get("stock_code")),
            ),
        )
        exchanges = {str(row["exchange"]) for row in members}
        scope = (
            "CNINFO_ORG_ID:BSE_PARTIAL_NONBLOCKING"
            if exchanges == {"BSE"}
            else "CNINFO_ORG_ID:PRIMARY"
        )
        common = {
            "record_source": "cninfo_orgid_map+ref_security_master",
            "source_record_id": f"{org_relative}#org_id={org_id}",
            "source_revision": f"{seed_revision}:ods-base-v1.1",
            "source_artifact_sha256": org_sha,
            "knowledge_from": knowledge_from,
            "knowledge_to": None,
            "effective_from": min(starts),
            "effective_to": company_end,
        }
        rows["dwd_listed_company"].append(
            {
                **common,
                "company_id": company_ids[org_id],
                "display_name": str(display["name"]).strip(),
                "company_type": "provider_scoped_listed_company",
                "resolution_scope": scope,
            }
        )
        rows["dwd_company_identifier"].append(
            {
                **common,
                "company_id": company_ids[org_id],
                "identifier_namespace": "CNINFO_ORG_ID",
                "identifier_value": org_id,
                "identifier_authority": "CNINFO",
            }
        )

    for group, members in sorted(security_groups.items()):
        member_keys = [(str(row["exchange"]), str(row["stock_code"])) for row in members]
        org_ids = {org_map[key[1]] for key in member_keys}
        if len(org_ids) != 1:
            raise ListedCompanyBuildError(
                f"one economic security resolves to multiple companies: {group}"
            )
        starts = [intervals_by_key[key][0] for key in member_keys]
        ends = [intervals_by_key[key][1] for key in member_keys]
        security_end = None if any(end is None for end in ends) else max(end for end in ends if end)
        source_row = min(members, key=lambda row: (str(row["exchange"]), str(row["stock_code"])))
        rows["dwd_security"].append(
            {
                "record_source": "ref_security_master+security_code_validity",
                "source_record_id": (
                    f"{master_relative}#exchange={source_row['exchange']}&stock_code={source_row['stock_code']}"
                ),
                "source_revision": master_revision,
                "source_artifact_sha256": master_sha,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "effective_from": min(starts),
                "effective_to": security_end,
                "security_id": security_ids[group],
                "company_id": company_ids[next(iter(org_ids))],
                "instrument_type": "common_equity",
                "instrument_currency": "CNY",
            }
        )
        for row in sorted(
            members, key=lambda item: (str(item["exchange"]), str(item["stock_code"]))
        ):
            key = (str(row["exchange"]), str(row["stock_code"]))
            start, end = intervals_by_key[key]
            venue_id = venue_ids[key[0]]
            validity = validity_by_code.get(key)
            source_sha = code_validity_sha if validity else master_sha
            revision = _text(code_validity, "mapping_version") if validity else master_revision
            source_record_id = (
                f"{code_validity_path.name}#exchange={key[0]}&stock_code={key[1]}"
                if validity
                else f"{master_relative}#exchange={key[0]}&stock_code={key[1]}"
            )
            lineage = {
                "record_source": "security_code_validity" if validity else "ref_security_master",
                "source_record_id": source_record_id,
                "source_revision": revision,
                "source_artifact_sha256": source_sha,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "effective_from": start,
                "effective_to": end,
            }
            rows["dwd_listing"].append(
                {
                    **lineage,
                    "listing_id": _stable_id(
                        "listing", security_ids[group], venue_id, key[1], start.isoformat()
                    ),
                    "security_id": security_ids[group],
                    "venue_id": venue_id,
                    "listing_code": key[1],
                    "board_code": _board(seed, key[0], key[1]),
                    "listing_currency": "CNY",
                    "lot_size": 100,
                }
            )
            identifiers = [
                (f"CN_STOCK_CODE:{key[0]}", key[1]),
                (
                    "AKSHARE_BSE_SECURITY_ID" if key[0] == "BSE" else "BAOSTOCK_SECURITY_ID",
                    str(row["security_id"]),
                ),
            ]
            if row.get("bs_code"):
                identifiers.append(("BAOSTOCK_CODE", str(row["bs_code"])))
            for namespace, value in identifiers:
                rows["dwd_security_identifier"].append(
                    {
                        **lineage,
                        "security_id": security_ids[group],
                        "identifier_namespace": namespace,
                        "identifier_value": value,
                        "venue_id": venue_id,
                    }
                )

    for exchange, venue in sorted(venue_seed.items()):
        exchange_rows = [row for row in master_rows if row["exchange"] == exchange]
        if not exchange_rows:
            raise ListedCompanyBuildError(
                f"security master has no rows for seeded venue {exchange}"
            )
        rows["dwd_trading_venue"].append(
            {
                "record_source": "ISO_10383_reference_seed",
                "source_record_id": f"{reference_seed_path.name}#exchange_code={exchange}",
                "source_revision": seed_revision,
                "source_artifact_sha256": seed_sha,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "effective_from": min(
                    _effective_start(_date_value(row["list_date"], "list_date"))
                    for row in exchange_rows
                ),
                "effective_to": None,
                "venue_id": venue_ids[exchange],
                "mic_code": str(venue["mic_code"]),
                "venue_name": str(venue["venue_name"]),
                "country_code": str(venue["country_code"]),
                "timezone": str(venue["timezone"]),
            }
        )

    for table, table_rows in rows.items():
        table_rows.sort(key=lambda row: _row_key(table, row))
    _validate_rows(rows)
    listed_counts = {
        exchange: sum(
            row["exchange"] == exchange and row["status"] == "listed" for row in master_rows
        )
        for exchange in ("SSE", "SZSE", "BSE")
    }
    expected_primary = int(
        _mapping(semantic_plan.get("source_precedence"), "source precedence").get(
            "expected_sse_szse_listed_scope_after_overlay", -1
        )
    )
    primary_count = listed_counts["SSE"] + listed_counts["SZSE"]
    if primary_count != expected_primary:
        raise ListedCompanyBuildError(
            f"SSE/SZSE listed coverage differs from the plan: {primary_count} != {expected_primary}"
        )
    coverage_policy = _mapping(seed.get("coverage_policy"), "coverage policy")
    if coverage_policy.get("BSE") != "BSE_PARTIAL_NONBLOCKING":
        raise ListedCompanyBuildError("BSE partial coverage policy is not explicit")

    table_digests = {table: _canonical_digest(values) for table, values in rows.items()}
    row_counts = {table: len(values) for table, values in rows.items()}
    semantic_digest = _canonical_digest(
        {
            "input_plan_digest": plan["plan_digest"],
            "model_sha256": model_sha,
            "reference_seed_sha256": seed_sha,
            "code_validity_sha256": code_validity_sha,
            "table_digests": table_digests,
        }
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        _verify_existing_output(output, run_id, semantic_digest)
        return ListedCompanyBuildResult(
            root=output,
            semantic_digest=semantic_digest,
            row_counts=row_counts,
            primary_listed_count=primary_count,
            bse_listed_count=listed_counts["BSE"],
        )

    work = output.parent / f".{output.name}.work"
    checkpoint_identity = {
        "schema_version": 1,
        "run_id": run_id,
        "semantic_digest": semantic_digest,
        "completed_tables": {},
    }
    checkpoint_path = work / "checkpoint.json"
    if work.exists():
        checkpoint = _object(checkpoint_path)
        if any(
            checkpoint.get(key) != checkpoint_identity[key] for key in ("run_id", "semantic_digest")
        ):
            raise ListedCompanyBuildError(f"checkpoint belongs to another build: {work}")
    else:
        work.mkdir(mode=0o750)
        (work / "payload" / "listed_company").mkdir(parents=True)
        (work / "receipts").mkdir()
        (work / "reconciliation").mkdir()
        _write_json_atomic(checkpoint_path, checkpoint_identity)

    checkpoint = _object(checkpoint_path)
    completed = dict(_mapping(checkpoint.get("completed_tables"), "completed tables"))
    schemas = _table_schemas()
    for table in _OWNED_TABLES:
        relative = f"payload/listed_company/{table}.parquet"
        target = work / relative
        expected = {
            "row_count": row_counts[table],
            "schema_digest": _canonical_digest(str(schemas[table])),
            "semantic_digest": table_digests[table],
        }
        prior = completed.get(table)
        if prior is not None:
            prior_mapping = _mapping(prior, f"checkpoint {table}")
            if any(prior_mapping.get(key) != value for key, value in expected.items()):
                raise ListedCompanyBuildError(f"checkpoint table metadata differs: {table}")
            if not target.is_file() or _sha256(target) != prior_mapping.get("sha256"):
                raise ListedCompanyBuildError(f"checkpoint table file failed verification: {table}")
            continue
        table_value = pa.Table.from_pylist(rows[table], schema=schemas[table])
        temporary = target.with_suffix(".parquet.partial")
        pq.write_table(
            table_value,
            temporary,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
        )
        os.replace(temporary, target)
        completed[table] = {**expected, "path": relative, "sha256": _sha256(target)}
        checkpoint["completed_tables"] = completed
        _write_json_atomic(checkpoint_path, checkpoint)

    reconciliation_value = {
        "schema_version": 1,
        "input_plan_digest": plan["plan_digest"],
        "sources": reconciliation,
        "summary": {
            "source_file_count": len(reconciliation),
            "source_row_count": sum(int(item["source_rows"]) for item in reconciliation),
            "unmatched_rows": sum(int(item.get("unmatched_rows", 0)) for item in reconciliation),
        },
    }
    _write_json_atomic(
        work / "reconciliation" / "source-row-reconciliation.json", reconciliation_value
    )
    receipt = {
        "schema_version": 1,
        "kind": "dwd_listed_company_build_receipt",
        "run_id": run_id,
        "status": "built",
        "started_at": started_at.isoformat(),
        "elapsed_ms": max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        "input_plan_digest": plan["plan_digest"],
        "model_sha256": model_sha,
        "reference_seed_sha256": seed_sha,
        "code_validity_sha256": code_validity_sha,
        "semantic_digest": semantic_digest,
        "table_digests": table_digests,
        "row_counts": row_counts,
        "coverage": {
            "listed_by_exchange": listed_counts,
            "primary_sse_szse_listed": primary_count,
            "bse_policy": coverage_policy["BSE"],
        },
        "quality_exceptions": [],
        "resource_use": {
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        },
    }
    _write_json_atomic(work / "receipts" / "listed-company-build.json", receipt)
    files = []
    for path in sorted(item for item in work.rglob("*") if item.is_file()):
        relative = path.relative_to(work).as_posix()
        if relative in {"manifest.json", "READY"}:
            continue
        files.append({"path": relative, "sha256": _sha256(path), "size_bytes": path.stat().st_size})
    manifest = {
        "schema_version": 1,
        "kind": "dwd_listed_company_candidate",
        "run_id": run_id,
        "input_plan_digest": plan["plan_digest"],
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "row_counts": row_counts,
        "files": files,
    }
    _write_json_atomic(work / "manifest.json", manifest)
    with (work / "READY").open("x", encoding="ascii") as stream:
        os.fchmod(stream.fileno(), 0o640)
        stream.write(f"{_sha256(work / 'manifest.json')}\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(work, output)
    except FileExistsError:
        _verify_existing_output(output, run_id, semantic_digest)

    return ListedCompanyBuildResult(
        root=output,
        semantic_digest=semantic_digest,
        row_counts=row_counts,
        primary_listed_count=primary_count,
        bse_listed_count=listed_counts["BSE"],
    )
