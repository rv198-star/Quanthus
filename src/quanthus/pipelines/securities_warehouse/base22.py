"""Pointerless Base 2.2 image construction and source-independent verification.

The first contract is deliberately synthetic-only. It implements the construction
boundary; it does not grant real-row access or invent missing domain projectors.
Inputs are exact, admitted, typed domain projections, never legacy Daily packages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse import daily_price_basis

CONTRACT_ID = "p10-base22-independent-image-v1"
KIND = "dwd_base22_independent_candidate"
MODEL_SHA = "sha256:7ccfbebbe8f06622363228cfa85abca23adb1b4ff8f5fff8c6c07cbc8d633dd7"
EXTENSION_SHA = "sha256:b34a9b60fec803cbc8f14fda12694dbe2a4f28972f8a9784f9cf12bce1159cb1"
CONTRACT_SHA = "sha256:a57938ee94a91510133cbfbe78373c32c38673a5a3780dd759d250d8ddefe97c"
THROUGH = date(2026, 8, 31)
HISTORY_THROUGH = date(2026, 7, 31)
SHANGHAI = ZoneInfo("Asia/Shanghai")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.=-]+$")


class Base22Error(RuntimeError):
    """A closed input, image or stage contract was not satisfied."""


def runtime_info() -> dict[str, str]:
    """Dependencies affecting the stage evaluator belong to its reuse identity."""
    import pyarrow  # type: ignore[import-untyped]

    return {"python": sys.version, "sqlite": sqlite3.sqlite_version, "pyarrow": pyarrow.__version__}


def canonical(value: Any) -> bytes:
    """Typed values are serialized without float or local-time conversions."""

    def encode(item: Any) -> str:
        if isinstance(item, datetime):
            if item.tzinfo is None:
                raise Base22Error("TIMEZONE_REQUIRED")
            return item.astimezone(timezone.utc).isoformat(timespec="microseconds")
        if isinstance(item, (date, Decimal)):
            return str(item)
        raise Base22Error("CANONICAL_TYPE_UNKNOWN")

    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=encode,
        allow_nan=False,
    ).encode("ascii")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return "sha256:" + result.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as exc:
        raise Base22Error("JSON_UNREADABLE") from exc


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _fsync(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _physical(root: Path, relative: str, *, independent: bool = False) -> Path:
    parts = PurePosixPath(relative)
    if (
        not relative
        or parts.is_absolute()
        or ".." in parts.parts
        or parts.as_posix() != relative
        or relative == "."
    ):
        raise Base22Error("UNSAFE_PATH")
    path = root
    if root.is_symlink() or not root.is_dir() or root.absolute() != root.resolve():
        raise Base22Error("PHYSICAL_ROOT_REQUIRED")
    for component in parts.parts:
        path = path / component
        if path.is_symlink():
            raise Base22Error("SYMLINK_FORBIDDEN")
    if not path.is_file() or (independent and path.stat().st_nlink != 1):
        raise Base22Error("INDEPENDENT_FILE_REQUIRED")
    return path


def _inventory(root: Path) -> set[str]:
    found = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Base22Error("SYMLINK_FORBIDDEN")
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise Base22Error("SPECIAL_FILE_FORBIDDEN")
    return found


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": file_sha(path),
        "size_bytes": path.stat().st_size,
    }


def _verify_files(root: Path, files: list[dict[str, Any]], *, independent: bool) -> set[str]:
    seen: set[str] = set()
    for item in files:
        relative = item["path"]
        if relative in seen:
            raise Base22Error("DUPLICATE_FILE")
        path = _physical(root, relative, independent=independent)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise Base22Error("FILE_HASH_OR_SIZE_MISMATCH")
        seen.add(relative)
    return seen


def load_contract(config_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if file_sha(config_root / "base22-independent-image.v1.json") != CONTRACT_SHA:
        raise Base22Error("FROZEN_BASE22_CONTRACT_CHANGED")
    contract = _object(config_root / "base22-independent-image.v1.json")
    model_path = config_root / "dwd-domain-model.v1.json"
    extension_path = config_root / "dwd-domain-model.v1.1-extension.json"
    if file_sha(model_path) != MODEL_SHA or file_sha(extension_path) != EXTENSION_SHA:
        raise Base22Error("FROZEN_MODEL_CHANGED")
    model, extension = _object(model_path), _object(extension_path)
    _validate_contract(contract, model, extension)
    return contract, model, extension


def _validate_contract(
    contract: dict[str, Any], model: dict[str, Any], extension: dict[str, Any]
) -> None:
    tables = {table["name"]: table for table in model["tables"]}
    if (
        contract.get("schema_version") != 1
        or contract.get("contract_id") != CONTRACT_ID
        or contract.get("candidate_kind") != KIND
        or contract.get("through_date") != THROUGH.isoformat()
        or contract.get("history_through_date") != HISTORY_THROUGH.isoformat()
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("model_sha256") != MODEL_SHA
        or contract.get("extension_sha256") != EXTENSION_SHA
        or contract.get("execution_scope") != "synthetic_only_pending_real_input_authority"
        or set(contract["tables"]) != set(tables)
        or len(tables) != 33
    ):
        raise Base22Error("CONTRACT_OR_EXACT_TABLE_SET_MISMATCH")
    for name, table in tables.items():
        rule = contract["tables"][name]
        key = table["business_key"] + extension["table_extensions"].get(name, {}).get(
            "business_key_append", []
        )
        if (
            rule["business_key"] != key
            or rule["version_key"] != key + ["knowledge_from"]
            or rule["partition_by"] != table["partition_by"]
        ):
            raise Base22Error("TABLE_RULE_MISMATCH")


def schemas(model: dict[str, Any], extension: dict[str, Any]) -> dict[str, Any]:
    """Derive all 33 physical schemas from the pinned model and additive extension."""
    import pyarrow as pa

    types = {
        "string": pa.string(),
        "bool": pa.bool_(),
        "int32": pa.int32(),
        "int64": pa.int64(),
        "date32": pa.date32(),
        "timestamp_us_utc": pa.timestamp("us", tz="UTC"),
    }
    result = {}
    for table in model["tables"]:
        columns: dict[str, str] = {}
        for inherited in table["inherits"]:
            columns.update(model["column_sets"][inherited])
        columns.update(model["time_profiles"][table["time_profile"]])
        columns.update(table["columns"])
        columns.update(extension["table_extensions"].get(table["name"], {}).get("columns", {}))
        fields = []
        for name, raw_type in columns.items():
            kind = raw_type.rstrip("?")
            if kind.startswith("decimal_"):
                _, precision, scale = kind.split("_")
                dtype = pa.decimal128(int(precision), int(scale))
            else:
                dtype = types[kind]
            fields.append(pa.field(name, dtype, nullable=raw_type.endswith("?")))
        result[table["name"]] = pa.schema(fields)
    return result


def _decode(document: str, schema: Any) -> dict[str, Any]:
    import pyarrow as pa

    row: dict[str, Any] = json.loads(document)
    for field in schema:
        value = row[field.name]
        if value is None:
            continue
        if pa.types.is_timestamp(field.type):
            row[field.name] = datetime.fromisoformat(value)
        elif pa.types.is_date32(field.type):
            row[field.name] = date.fromisoformat(value)
        elif pa.types.is_decimal(field.type):
            row[field.name] = Decimal(value)
    return row


def business_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise Base22Error("TIMEZONE_REQUIRED")
        return value.astimezone(SHANGHAI).date()
    return value


def partition(row: Mapping[str, Any], rule: Mapping[str, Any]) -> str:
    values = []
    for field in rule["partition_by"]:
        if field.endswith(("_year", "_month")):
            source, part = field.rsplit("_", 1)
            coordinate = business_date(row[source])
            value = str(coordinate.year if part == "year" else coordinate.month)
        else:
            value = str(row[field])
        if not _SAFE.fullmatch(value):
            raise Base22Error("PARTITION_VALUE_INVALID")
        values.append(field + "=" + value)
    return "/".join(values) or "all"


def _db(path: Path | str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE rows (t TEXT, k TEXT, p TEXT, doc TEXT, origin TEXT, PRIMARY KEY(t,k))"
    )
    db.execute("CREATE INDEX partitions ON rows(t,p,k)")
    return db


def _rows(path: Path, schema: Any) -> Iterator[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    parquet = pq.ParquetFile(path)
    # Input ordering is not business identity. Types, nullability and field set are.
    if set(parquet.schema_arrow.names) != set(schema.names) or any(
        parquet.schema_arrow.field(field.name) != field for field in schema
    ):
        raise Base22Error("SCHEMA_MISMATCH")
    for batch in parquet.iter_batches(batch_size=65536):
        yield from batch.to_pylist()


def _check_row(
    row: dict[str, Any], rule: dict[str, Any], schema: Any, admitted: set[tuple[str, str, str]]
) -> None:
    if any(row[field.name] is None for field in schema if not field.nullable):
        raise Base22Error("REQUIRED_VALUE_OR_KNOWLEDGE_UNKNOWN")
    if (
        not row["source_record_id"]
        or (row["record_source"], row["source_revision"], row["source_artifact_sha256"])
        not in admitted
    ):
        raise Base22Error("UNKNOWN_OR_CONFLICTING_SOURCE")
    if business_date(row[rule["cutoff_field"]]) > THROUGH:
        raise Base22Error("BUSINESS_DATE_AFTER_CUTOFF")
    for start, end in (
        ("effective_from", "effective_to"),
        ("knowledge_from", "knowledge_to"),
        ("observation_start", "observation_end"),
        ("period_start", "period_end"),
    ):
        if row.get(start) is not None and row.get(end) is not None and row[end] <= row[start]:
            raise Base22Error("INVALID_TIME_INTERVAL")
    # Endpoints, acquired/publication and knowledge times are NEVER clipped to THROUGH.


def _summary(db: sqlite3.Connection, table: str) -> dict[str, Any]:
    partitions = {}
    for (name,) in db.execute("SELECT DISTINCT p FROM rows WHERE t=? ORDER BY p", (table,)):
        content, keys = hashlib.sha256(), hashlib.sha256()
        count = 0
        for key, document in db.execute(
            "SELECT k,doc FROM rows WHERE t=? AND p=? ORDER BY k", (table, name)
        ):
            content.update(document.encode("ascii") + b"\n")
            keys.update(key.encode("ascii") + b"\n")
            count += 1
        partitions[name] = {
            "rows": count,
            "semantic_digest": "sha256:" + content.hexdigest(),
            "key_digest": "sha256:" + keys.hexdigest(),
        }
    return {"rows": sum(item["rows"] for item in partitions.values()), "partitions": partitions}


def _admitted(sources: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (member["record_source"], member["source_revision"], member["sha256"])
        for source in sources.values()
        for member in source["raw_members"]
    }


def _verify_source(binding: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(binding["root"])
    manifest_path = _physical(root, "manifest.json")
    manifest = _object(manifest_path)
    if (
        manifest.get("schema_version") != 1
        or file_sha(manifest_path) != binding["manifest_sha256"]
        or _physical(root, "READY").read_text().strip() != binding["manifest_sha256"]
        or manifest.get("kind") != "base22_typed_projection_input"
        or manifest.get("purpose") != "synthetic_contract"
        or manifest.get("role") != binding["role"]
        or not binding.get("authority")
        or not manifest.get("raw_members")
    ):
        raise Base22Error("SOURCE_ADMISSION_MISMATCH")
    files = manifest["files"] + manifest["raw_members"]
    expected = _verify_files(root, files, independent=False)
    if _inventory(root) != expected | {"manifest.json", "READY"}:
        raise Base22Error("SOURCE_PHYSICAL_SET_MISMATCH")
    return manifest


def _freeze(plan: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if (
        plan.get("schema_version") != 1
        or plan.get("contract_id") != CONTRACT_ID
        or plan.get("purpose") != "synthetic_contract"
        or plan.get("through_date") != THROUGH.isoformat()
        or not re.fullmatch(r"[0-9a-f]{40}", plan.get("code_revision", ""))
        or set(plan.get("tables", {})) != set(contract["tables"])
        or set(plan.get("quality_gates", {})) != {str(i) for i in range(470, 475)}
    ):
        raise Base22Error("PLAN_SCOPE_OR_TABLE_SET_MISMATCH")
    sources = {name: _verify_source(binding) for name, binding in plan["sources"].items()}
    if not sources or {binding["role"] for binding in plan["sources"].values()} != {
        "history",
        "repair",
        "august",
    }:
        raise Base22Error("COMPOSITE_INPUT_ROLES_REQUIRED")
    for gate in plan["quality_gates"].values():
        if gate.get("status") != "synthetic_case" or not gate.get("reason"):
            raise Base22Error("REAL_QUALITY_GATE_NOT_AUTHORIZED")
    calendar_binding = plan.get("calendar_evidence", {})
    calendar_source = calendar_binding.get("source_id")
    calendar_path = calendar_binding.get("path")
    if calendar_source not in sources or calendar_path not in {
        member["path"] for member in sources[calendar_source]["raw_members"]
    }:
        raise Base22Error("AUTHORITATIVE_CALENDAR_EVIDENCE_REQUIRED")
    calendar = _object(_physical(Path(plan["sources"][calendar_source]["root"]), calendar_path))
    if (
        calendar.get("kind") != "base22_authoritative_calendar_census"
        or calendar.get("through_date") != THROUGH.isoformat()
        or calendar.get("open_dates") != plan["august_open_dates"]
        or plan["august_open_dates"] != sorted(set(plan["august_open_dates"]))
        or any(
            not HISTORY_THROUGH < date.fromisoformat(value) <= THROUGH
            for value in plan["august_open_dates"]
        )
    ):
        raise Base22Error("AUTHORITATIVE_CALENDAR_CENSUS_MISMATCH")
    for source in sources.values():
        expected = HISTORY_THROUGH if source["role"] in {"history", "repair"} else THROUGH
        if source["through_date"] != expected.isoformat():
            raise Base22Error("SOURCE_DATE_DECLARATION_MISMATCH")
        for artifact in source.get("artifact_census", []):
            if artifact["row_count"] > 0 and artifact.get("handler") != "typed_projection_v1":
                raise Base22Error("NONEMPTY_ARTIFACT_WITHOUT_HANDLER")
    for table, entry in plan["tables"].items():
        if not entry.get("reason") or not entry.get("required_partitions"):
            raise Base22Error("PARTITION_CENSUS_OR_REASON_REQUIRED")
        identifiers = entry.get("source_ids", [])
        if not identifiers or len(set(identifiers)) != len(identifiers):
            raise Base22Error("EXACT_TABLE_SOURCES_REQUIRED")
        if any(name not in sources for name in identifiers):
            raise Base22Error("UNKNOWN_SOURCE_ID")
        disposition = entry.get("august_disposition", {})
        if disposition.get("mode") not in {"project", "no_change"}:
            raise Base22Error("AUGUST_DOMAIN_DISPOSITION_REQUIRED")
        source_id, relative = disposition["evidence_source"], disposition["evidence_path"]
        if source_id not in sources or relative not in {
            member["path"] for member in sources[source_id]["raw_members"]
        }:
            raise Base22Error("AUGUST_EVIDENCE_NOT_IN_SOURCE_CLOSURE")
        evidence = _object(_physical(Path(plan["sources"][source_id]["root"]), relative))
        if (
            evidence.get("table") != table
            or evidence.get("through_date") != THROUGH.isoformat()
            or evidence.get("mode") != disposition["mode"]
            or not evidence.get("census_digest")
            or (
                disposition["mode"] == "no_change"
                and evidence.get("no_required_changes") is not True
            )
        ):
            raise Base22Error("AUGUST_CHANGE_CENSUS_UNPROVEN")
        projected_rows = sum(
            file["row_count"]
            for name in identifiers
            if sources[name]["role"] == "august"
            for file in sources[name]["files"]
            if file["table"] == table
        )
        if (
            disposition["mode"] == "no_change"
            and projected_rows
            or disposition["mode"] == "project"
            and not projected_rows
        ):
            raise Base22Error("AUGUST_DISPOSITION_OUTPUT_MISMATCH")
    return sources


def _normalize_basis(row: dict[str, Any], source: dict[str, Any], stats: dict[str, int]) -> None:
    if row["interval_size"] != "1d":
        if row["price_basis"] != "UNADJUSTED":
            raise Base22Error("CANONICAL_PRICE_BASIS_UNKNOWN")
        return
    original = dict(row)
    if row["price_basis"] == "none":
        if source.get("basis_policy") != daily_price_basis.POLICY:
            raise Base22Error("PRICE_BASIS_ALIAS_NOT_AUTHORIZED")
        daily_price_basis.contract(source["basis_policy"])
        row["price_basis"] = "UNADJUSTED"
        stats["basis_normalized"] += 1
    if row["price_basis"] != "UNADJUSTED":
        raise Base22Error("CANONICAL_PRICE_BASIS_UNKNOWN")
    if any(row[key] != value for key, value in original.items() if key != "price_basis"):
        raise Base22Error("PRICE_OR_TIME_PRESERVATION_FAILED")
    for field in ("open", "high", "low", "close", "volume", "turnover"):
        stats["null_values"] += int(row[field] is None)
        stats["zero_values"] += int(row[field] == 0)
    stats["late_knowledge_rows"] += int(
        business_date(row["knowledge_from"]) > business_date(row["observation_start"])
    )
    stats["unavailable_rows"] += int(
        any(row[field] is None or row[field] <= 0 for field in ("open", "high", "low", "close"))
    )


def _ingest(
    db: sqlite3.Connection,
    plan: dict[str, Any],
    sources: dict[str, Any],
    contract: dict[str, Any],
    physical_schemas: dict[str, Any],
    roles: set[str],
    stats: dict[str, int],
) -> None:
    replacements = {digest(item["key"]): item for item in plan.get("replacements", [])}
    if len(replacements) != len(plan.get("replacements", [])):
        raise Base22Error("DUPLICATE_REPAIR_AUTHORITY")
    used_replacements: set[str] = set()
    # Fixed history -> repair -> August precedence; conflicts never select a source implicitly.
    for role in ("history", "repair", "august"):
        if role not in roles:
            continue
        for source_id, source in sorted(sources.items()):
            if source["role"] != role:
                continue
            seen: set[tuple[str, str]] = set()
            for file in source["files"]:
                table = file["table"]
                if (
                    table not in plan["tables"]
                    or source_id not in plan["tables"][table]["source_ids"]
                ):
                    raise Base22Error("UNDECLARED_TABLE_SOURCE")
                rule, schema = contract["tables"][table], physical_schemas[table]
                count = 0
                for row in _rows(
                    _physical(Path(plan["sources"][source_id]["root"]), file["path"]), schema
                ):
                    _check_row(row, rule, schema, _admitted({source_id: source}))
                    coordinate = business_date(row[rule["cutoff_field"]])
                    if (
                        role != "august"
                        and coordinate > HISTORY_THROUGH
                        or role == "august"
                        and coordinate <= HISTORY_THROUGH
                    ):
                        raise Base22Error("INPUT_ROLE_DATE_MISMATCH")
                    if table == "dwd_security_bar":
                        _normalize_basis(row, source, stats)
                    key = canonical([row[field] for field in rule["version_key"]]).decode("ascii")
                    if (table, key) in seen:
                        raise Base22Error("DUPLICATE_KEY_IN_SOURCE")
                    seen.add((table, key))
                    part = partition(row, rule)
                    if part != file["partition"]:
                        raise Base22Error("SOURCE_PARTITION_COORDINATE_MISMATCH")
                    document = canonical(row).decode("ascii")
                    previous = db.execute(
                        "SELECT doc FROM rows WHERE t=? AND k=?", (table, key)
                    ).fetchone()
                    if previous and previous[0] != document:
                        replacement_key = digest([table, json.loads(key)])
                        replacement = replacements.get(replacement_key, {})
                        if (
                            role != "repair"
                            or replacement.get("source_id") != source_id
                            or not replacement.get("authority")
                            or replacement.get("before") != digest(json.loads(previous[0]))
                            or replacement.get("after") != digest(row)
                        ):
                            raise Base22Error("CONFLICT_REQUIRES_EXACT_REPAIR")
                        used_replacements.add(replacement_key)
                        db.execute(
                            "UPDATE rows SET doc=?,origin=? WHERE t=? AND k=?",
                            (document, source_id, table, key),
                        )
                    elif previous is None:
                        db.execute(
                            "INSERT INTO rows VALUES (?,?,?,?,?)",
                            (table, key, part, document, source_id),
                        )
                    else:
                        stats["identical_cross_source_rows"] += 1
                    count += 1
                if count != file["row_count"]:
                    raise Base22Error("SOURCE_ROW_COUNT_MISMATCH")
    expected_repairs = {
        key
        for key, item in replacements.items()
        if plan["sources"][item["source_id"]]["role"] in roles
    }
    if used_replacements != expected_repairs:
        raise Base22Error("UNUSED_OR_DUPLICATE_REPAIR")
    db.commit()


def freeze_base22_plan(plan: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Validate the composite contract; July source identities remain unchanged."""
    try:
        return _freeze(plan, contract)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Base22Error("INPUT_PLAN_UNREADABLE_OR_INVALID") from exc


def _scan_checks(
    db: sqlite3.Connection,
    plan: dict[str, Any],
    contract: dict[str, Any],
    model: dict[str, Any],
    physical_schemas: dict[str, Any],
    *,
    census: bool,
) -> dict[str, Any]:
    tables = {table["name"]: table for table in model["tables"]}
    # Job-local disk index keeps FK verification bounded; no full-history Python maps.
    db.execute("CREATE TABLE IF NOT EXISTS refs (t TEXT,c TEXT,v TEXT,doc TEXT)")
    db.execute("DELETE FROM refs")
    columns: set[tuple[str, str]] = {
        (fk["table"], fk["reference_column"])
        for table in tables.values()
        for fk in table["foreign_keys"]
    }
    for table, column in sorted(columns):
        for (document,) in db.execute("SELECT doc FROM rows WHERE t=?", (table,)):
            row: dict[str, Any] = json.loads(document)
            db.execute("INSERT INTO refs VALUES (?,?,?,?)", (table, column, row[column], document))
    db.execute("CREATE INDEX IF NOT EXISTS reference_lookup ON refs(t,c,v)")
    summaries = {}
    db.execute("CREATE TABLE IF NOT EXISTS versions (t TEXT,b TEXT,kf TEXT,kt TEXT)")
    db.execute("DELETE FROM versions")
    for name, table in sorted(tables.items()):
        rule = contract["tables"][name]
        summary = _summary(db, name)
        if census:
            expected = plan["tables"][name]["required_partitions"]
            actual = {
                key: {"rows": value["rows"], "key_digest": value["key_digest"]}
                for key, value in summary["partitions"].items()
            }
            # Empty tables have an explicit, typed zero-row partition, never an omitted file.
            if not actual:
                actual = {
                    "empty": {"rows": 0, "key_digest": "sha256:" + hashlib.sha256().hexdigest()}
                }
            if actual != expected:
                raise Base22Error("PARTITION_COVERAGE_OR_KEY_CENSUS_MISMATCH:" + name)
        for (document,) in db.execute("SELECT doc FROM rows WHERE t=? ORDER BY k", (name,)):
            row = _decode(document, physical_schemas[name])
            business_key = canonical([row[field] for field in rule["business_key"]]).decode()
            db.execute(
                "INSERT INTO versions VALUES (?,?,?,?)",
                (
                    name,
                    business_key,
                    row["knowledge_from"].isoformat(),
                    row["knowledge_to"].isoformat() if row["knowledge_to"] is not None else None,
                ),
            )
            for fk in table["foreign_keys"]:
                value = row[fk["column"]]
                if value is None and fk["nullable"]:
                    continue
                matched = False
                for (reference,) in db.execute(
                    "SELECT doc FROM refs WHERE t=? AND c=? AND v=?",
                    (fk["table"], fk["reference_column"], value),
                ):
                    target = _decode(reference, physical_schemas[fk["table"]])
                    knowledge = row["knowledge_from"]
                    coordinate = row[rule["cutoff_field"]]
                    if isinstance(coordinate, date) and not isinstance(coordinate, datetime):
                        coordinate = datetime.combine(coordinate, datetime.min.time(), SHANGHAI)
                    if (
                        target["knowledge_from"] <= knowledge
                        and (target["knowledge_to"] is None or knowledge < target["knowledge_to"])
                        and (
                            "effective_from" not in target
                            or target["effective_from"] <= coordinate
                            and (
                                target["effective_to"] is None
                                or coordinate < target["effective_to"]
                            )
                        )
                    ):
                        matched = True
                        break
                if not matched:
                    raise Base22Error("FOREIGN_KEY_OR_TEMPORAL_CLOSURE_FAILED:" + name)
        summaries[name] = summary
    previous_key, previous_end = None, None
    for table_name, business_key, start, end in db.execute(
        "SELECT t,b,kf,kt FROM versions ORDER BY t,b,kf"
    ):
        current_key = (table_name, business_key)
        if current_key == previous_key and (previous_end is None or previous_end > start):
            raise Base22Error("OVERLAPPING_KNOWLEDGE_VERSIONS")
        previous_key, previous_end = current_key, end
    session_dates = sorted(
        {
            json.loads(doc)["trade_date"]
            for (doc,) in db.execute("SELECT doc FROM rows WHERE t='dwd_trading_calendar'")
            if json.loads(doc)["is_open"]
            and HISTORY_THROUGH.isoformat() < json.loads(doc)["trade_date"] <= THROUGH.isoformat()
        }
    )
    if session_dates != plan["august_open_dates"]:
        raise Base22Error("AUTHORITATIVE_CALENDAR_CENSUS_MISMATCH")
    return summaries


def _materialize(
    db: sqlite3.Connection, root: Path, physical_schemas: dict[str, Any]
) -> list[dict[str, Any]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    files = []
    for table, schema in sorted(physical_schemas.items()):
        parts = [
            part
            for (part,) in db.execute("SELECT DISTINCT p FROM rows WHERE t=? ORDER BY p", (table,))
        ] or ["empty"]
        for part in parts:
            path = root / "payload" / table / part / "part-00000.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with pq.ParquetWriter(path, schema, compression="zstd") as writer:
                batch = []
                for (document,) in db.execute(
                    "SELECT doc FROM rows WHERE t=? AND p=? ORDER BY k", (table, part)
                ):
                    batch.append(_decode(document, schema))
                    count += 1
                    if len(batch) == 65536:
                        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                        batch.clear()
                if batch or not count:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            files.append(
                {**_file_record(root, path), "table": table, "partition": part, "row_count": count}
            )
    return files


def _scan_image(
    root: Path,
    manifest: dict[str, Any],
    plan: dict[str, Any],
    contract: dict[str, Any],
    model: dict[str, Any],
    extension: dict[str, Any],
    scratch: Path | None = None,
) -> dict[str, Any]:
    import tempfile

    physical_schemas = schemas(model, extension)
    sources = manifest["source_manifests"]  # archival objects, never dereference their roots
    admitted = _admitted(sources)
    with tempfile.TemporaryDirectory(prefix="base22-verify-", dir=scratch) as tmp:
        db = _db(Path(tmp) / "scan.sqlite")
        try:
            for item in manifest["files"]:
                if "table" not in item:
                    continue
                name = item["table"]
                if name not in physical_schemas or item["path"] != (
                    "payload/" + name + "/" + item["partition"] + "/part-00000.parquet"
                ):
                    raise Base22Error("EXACT_TABLE_OR_PARTITION_PATH_MISMATCH")
                count = 0
                for row in _rows(
                    _physical(root, item["path"], independent=True), physical_schemas[name]
                ):
                    _check_row(row, contract["tables"][name], physical_schemas[name], admitted)
                    if name == "dwd_security_bar" and row["price_basis"] != "UNADJUSTED":
                        raise Base22Error("CANONICAL_PRICE_BASIS_UNKNOWN")
                    key = canonical(
                        [row[field] for field in contract["tables"][name]["version_key"]]
                    ).decode()
                    part = partition(row, contract["tables"][name])
                    if part != item["partition"]:
                        raise Base22Error("OUTPUT_PARTITION_COORDINATE_MISMATCH")
                    try:
                        db.execute(
                            "INSERT INTO rows VALUES (?,?,?,?,?)",
                            (name, key, part, canonical(row).decode(), "image"),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise Base22Error("DUPLICATE_OUTPUT_KEY") from exc
                    count += 1
                if count != item["row_count"]:
                    raise Base22Error("OUTPUT_ROW_COUNT_MISMATCH")
            if {item.get("table") for item in manifest["files"] if "table" in item} != set(
                physical_schemas
            ):
                raise Base22Error("EXACT_TABLE_SET_MISMATCH")
            return _scan_checks(db, plan, contract, model, physical_schemas, census=True)
        finally:
            db.close()


def verify_base22(root: Path, *, require_ready: bool = True) -> dict[str, Any]:
    """Scan every image table using only this root, ordinary code and job-local scratch."""
    try:
        manifest = _object(_physical(root, "manifest.json", independent=True))
        if (
            manifest.get("schema_version") != 2
            or manifest.get("kind") != KIND
            or manifest.get("through_date") != THROUGH.isoformat()
            or manifest.get("consumer_admission") != "not_granted"
            or manifest.get("purpose") != "synthetic_contract"
        ):
            raise Base22Error("CANDIDATE_VERSION_OR_SCOPE_MISMATCH")
        expected = _verify_files(root, manifest["files"], independent=True)
        allowed = expected | {"manifest.json"} | ({"READY"} if require_ready else set())
        if _inventory(root) != allowed:
            raise Base22Error("IMAGE_PHYSICAL_SET_MISMATCH")
        if require_ready and _physical(
            root, "READY", independent=True
        ).read_text().strip() != file_sha(root / "manifest.json"):
            raise Base22Error("READY_MISMATCH")
        config_root = root / "contract"
        contract, model, extension = load_contract(config_root)
        plan = _object(root / "evidence/input-plan.json")
        if digest(plan) != manifest["input_plan_digest"]:
            raise Base22Error("PLAN_DIGEST_MISMATCH")
        if digest(manifest["source_manifests"]) != manifest["source_closure_digest"]:
            raise Base22Error("SOURCE_CLOSURE_DIGEST_MISMATCH")
        if set(manifest["source_manifests"]) != set(plan["sources"]):
            raise Base22Error("SOURCE_MANIFEST_SET_MISMATCH")
        for source_id, binding in plan["sources"].items():
            if not _SAFE.fullmatch(source_id):
                raise Base22Error("SOURCE_ID_UNSAFE")
            archived = _physical(root, "evidence/sources/" + source_id + ".json", independent=True)
            if (
                file_sha(archived) != binding["manifest_sha256"]
                or _object(archived) != manifest["source_manifests"][source_id]
            ):
                raise Base22Error("ARCHIVED_SOURCE_IDENTITY_MISMATCH")
        summary = _scan_image(root, manifest, plan, contract, model, extension)
        if summary != manifest["tables"] or digest(summary) != manifest["semantic_digest"]:
            raise Base22Error("RECOMPUTED_SEMANTICS_MISMATCH")
        return {
            "manifest_sha256": file_sha(root / "manifest.json"),
            "content_digest": digest(manifest["files"]),
            "semantic_digest": digest(summary),
            "tables": summary,
            "through_date": manifest["through_date"],
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Base22Error("IMAGE_UNREADABLE_OR_INVALID") from exc


def build_base22(*, plan: dict[str, Any], config_root: Path, output: Path) -> dict[str, Any]:
    """Construct a synthetic independent image; never set CURRENT or admit consumers.

    An interrupted job retains its immutable history stage and failed attempt. A new
    attempt reuses that stage only with the exact complete input identity. Sealed
    output is verified and returned without rewriting; changed inputs are rejected.
    """
    import tempfile
    import uuid

    from quanthus.pipelines.securities_warehouse.planner import freeze_base22_composite_plan

    freeze_started = time.monotonic()
    contract, model, extension = load_contract(config_root)
    # Freeze a private copy before any stage; callers cannot mutate the plan in-flight.
    envelope = freeze_base22_composite_plan(plan=plan, config_root=config_root)
    plan = envelope["plan"]
    sources = envelope["source_manifests"]
    if not output.is_absolute() or output != output.resolve():
        raise Base22Error("OUTPUT_REQUIRES_PHYSICAL_ABSOLUTE_PATH")
    identity = digest(
        {
            "plan": plan,
            "contract": contract,
            "model": MODEL_SHA,
            "extension": EXTENSION_SHA,
            "runtime": runtime_info(),
            "evaluators": {
                name: file_sha(Path(__file__).with_name(name))
                for name in ("base22.py", "planner.py", "daily_price_basis.py")
            },
        }
    )
    if output.is_symlink() or any(
        output == Path(item["root"])
        or output.is_relative_to(Path(item["root"]))
        or Path(item["root"]).is_relative_to(output)
        for item in plan["sources"].values()
    ):
        raise Base22Error("OUTPUT_OVERLAPS_SOURCE")
    if output.exists():
        result = verify_base22(output)
        if _object(output / "manifest.json")["job_identity"] != identity:
            raise Base22Error("SEALED_OUTPUT_INPUT_CHANGED")
        return result
    output.parent.mkdir(parents=True, exist_ok=True)
    job = output.with_name("." + output.name + ".job")
    job.mkdir(exist_ok=True)
    lock = job / "LOCK"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Base22Error("JOB_SINGLE_WRITER_LOCKED") from exc
    os.close(descriptor)
    attempt = job / ("attempt-" + uuid.uuid4().hex)
    attempt.mkdir()
    stats = dict.fromkeys(
        (
            "basis_normalized",
            "zero_values",
            "null_values",
            "late_knowledge_rows",
            "unavailable_rows",
            "identical_cross_source_rows",
        ),
        0,
    )
    db: sqlite3.Connection | None = None
    stages: list[dict[str, Any]] = [
        {
            "stage": "freeze",
            "elapsed_seconds": time.monotonic() - freeze_started,
            "rows": sum(
                item["row_count"] for source in sources.values() for item in source["files"]
            ),
            "bytes": sum(
                item["size_bytes"]
                for source in sources.values()
                for item in source["files"] + source["raw_members"]
            ),
            "disposition": "verify",
            "lifecycle": "TMP",
        }
    ]
    try:
        if (job / "identity.json").exists():
            if _object(job / "identity.json")["identity"] != identity:
                raise Base22Error("INTERRUPTED_JOB_INPUT_CHANGED")
        else:
            _write(job / "identity.json", {"identity": identity, "lifecycle": "TMP"})
        physical_schemas = schemas(model, extension)
        history = job / "history"
        started = time.monotonic()
        if history.exists():
            checkpoint = _object(history / "checkpoint.json")
            if checkpoint["identity"] != identity:
                raise Base22Error("HISTORY_CHECKPOINT_IDENTITY_CHANGED")
            _verify_files(history, checkpoint["files"], independent=True)
            if _inventory(history) != {item["path"] for item in checkpoint["files"]} | {
                "checkpoint.json"
            }:
                raise Base22Error("HISTORY_CHECKPOINT_FILE_SET_CHANGED")
            stats.update(checkpoint["statistics"])
            disposition = "reuse"
        else:
            history_tmp = Path(tempfile.mkdtemp(prefix="history-building-", dir=job))
            db = _db(history_tmp / "rows.sqlite")
            _ingest(db, plan, sources, contract, physical_schemas, {"history", "repair"}, stats)
            # The single immutable intermediate supplies ALL August dependencies and merge.
            db.close()
            db = None
            files = [_file_record(history_tmp, history_tmp / "rows.sqlite")]
            _write(
                history_tmp / "checkpoint.json",
                {"identity": identity, "files": files, "statistics": stats},
            )
            os.rename(history_tmp, history)
            _fsync(job)
            disposition = "build"
        stages.append(
            {
                "stage": "repaired_history",
                "elapsed_seconds": time.monotonic() - started,
                "disposition": disposition,
                "bytes": (history / "rows.sqlite").stat().st_size,
                "lifecycle": "TMP",
            }
        )
        # SQLite backup is a physical private copy, never a hard link or external view.
        started = time.monotonic()
        db = sqlite3.connect(attempt / "merge.sqlite")
        with sqlite3.connect(f"file:{history / 'rows.sqlite'}?mode=ro", uri=True) as prior:
            prior.backup(db)
        history_summary = {name: _summary(db, name) for name in physical_schemas}
        history_digest = digest(history_summary)
        for source in sources.values():
            if (
                source["role"] == "august"
                and source.get("repaired_history_digest") != history_digest
            ):
                raise Base22Error("AUGUST_REPAIRED_HISTORY_BINDING_MISMATCH")
        _ingest(db, plan, sources, contract, physical_schemas, {"august"}, stats)
        tables = _scan_checks(db, plan, contract, model, physical_schemas, census=True)
        stages[-1]["rows"] = sum(value["rows"] for value in history_summary.values())
        stages.append(
            {
                "stage": "august_projection_merge",
                "elapsed_seconds": time.monotonic() - started,
                "disposition": "build",
                "rows": sum(value["rows"] for value in tables.values()),
                "bytes": (attempt / "merge.sqlite").stat().st_size,
                "lifecycle": "TMP",
                "history_digest": history_digest,
            }
        )
        image = attempt / "image"
        image.mkdir()
        started = time.monotonic()
        files = _materialize(db, image, physical_schemas)
        for name in (
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ):
            target = image / "contract" / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(config_root / name, target)
            files.append(_file_record(image, target))
        # Retain exact source manifest bytes, not merely reconstructed JSON identities.
        for source_id, binding in sorted(plan["sources"].items()):
            if not _SAFE.fullmatch(source_id):
                raise Base22Error("SOURCE_ID_UNSAFE")
            target = image / "evidence/sources" / (source_id + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_physical(Path(binding["root"]), "manifest.json"), target)
            if file_sha(target) != binding["manifest_sha256"]:
                raise Base22Error("SOURCE_MANIFEST_CHANGED_DURING_COPY")
            files.append(_file_record(image, target))
        _write(image / "evidence/input-plan.json", plan)
        files.append(_file_record(image, image / "evidence/input-plan.json"))
        stages.append(
            {
                "stage": "seal",
                "elapsed_seconds": time.monotonic() - started,
                "rows": sum(value["rows"] for value in tables.values()),
                "bytes": sum(item["size_bytes"] for item in files),
                "disposition": "build",
                "lifecycle": "candidate",
            }
        )
        _write(image / "evidence/stages.json", {"stages": stages, "statistics": stats})
        files.append(_file_record(image, image / "evidence/stages.json"))
        # Detect input changes after projection/seal as well as before first row access.
        if freeze_base22_plan(plan, contract) != sources:
            raise Base22Error("INPUT_CHANGED_DURING_BUILD")
        manifest = {
            "schema_version": 2,
            "kind": KIND,
            "through_date": THROUGH.isoformat(),
            "business_timezone": "Asia/Shanghai",
            "consumer_admission": "not_granted",
            "purpose": "synthetic_contract",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": plan["code_revision"],
            "runtime": runtime_info(),
            "job_identity": identity,
            "input_plan_digest": digest(plan),
            "source_manifests": sources,
            "source_closure_digest": digest(sources),
            "tables": tables,
            "semantic_digest": digest(tables),
            "files": sorted(files, key=lambda item: item["path"]),
        }
        _write(image / "manifest.json", manifest)
        started = time.monotonic()
        verified = verify_base22(image, require_ready=False)
        _write(
            attempt / "independent-verification.json",
            {
                "stage": "independent_verification",
                "elapsed_seconds": time.monotonic() - started,
                "rows": sum(value["rows"] for value in tables.values()),
                "bytes": sum(item["size_bytes"] for item in files),
                "disposition": "verify",
                "lifecycle": "TMP",
                "job_identity": identity,
                "result": verified,
            },
        )
        # READY is the last image write. Failed verification can never mark a package complete.
        with (image / "READY").open("x") as stream:
            stream.write(file_sha(image / "manifest.json") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync(image)
        if output.exists():
            raise Base22Error("OUTPUT_ALREADY_EXISTS")
        os.rename(image, output)
        _fsync(output.parent)
        # Relocation cannot change the verified bytes. Do not repeat the full scan inside this Job.
        return verified
    except BaseException as exc:
        _write(
            attempt / "FAILED.json",
            {
                "kind": "base22_failed_attempt",
                "identity": identity,
                "reason": str(exc),
                "ready_published": output.exists(),
            },
        )
        raise
    finally:
        if db is not None:
            db.close()
        lock.unlink()
