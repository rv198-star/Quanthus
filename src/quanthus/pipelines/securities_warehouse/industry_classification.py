"""Build the governed first industry-classification snapshot for Issue #62."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    _mapping,
    _object,
    _sequence,
    _verify_ods_binding,
    _verify_plan_package,
    _verify_source,
)


class IndustryClassificationBuildError(RuntimeError):
    """Raised when the governed industry snapshot is ambiguous or incomplete."""


@dataclass(frozen=True)
class IndustryClassificationBuildResult:
    """A sealed, non-published three-object industry candidate."""

    root: Path
    semantic_digest: str
    row_counts: Mapping[str, int]


@dataclass(frozen=True)
class _IndustrySource:
    label: str
    record: Mapping[str, Any]
    path: Path
    precedence: str


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_OWNED_TABLES = (
    "dwd_industry_scheme",
    "dwd_industry_node",
    "dwd_company_industry_membership",
)
_PARENT_SOURCE = "industry_membership"
_HOTFIX_SOURCE = "fundamentals_industry_membership_parquet"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _digest(value: object) -> str:
    def normalize(item: object) -> object:
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, Mapping):
            return {str(key): normalize(value) for key, value in sorted(item.items())}
        if isinstance(item, list | tuple):
            return [normalize(value) for value in item]
        return item

    payload = json.dumps(
        normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IndustryClassificationBuildError(
                f"{field} must be an ISO-8601 timestamp"
            ) from exc
    else:
        raise IndustryClassificationBuildError(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IndustryClassificationBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise IndustryClassificationBuildError(f"{field} must be YYYY-MM-DD") from exc
    raise IndustryClassificationBuildError(f"{field} must be YYYY-MM-DD")


def _effective_start(value: date) -> datetime:
    return datetime.combine(value, time.min, _SHANGHAI).astimezone(timezone.utc)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndustryClassificationBuildError(f"{field} must be a non-empty string")
    return value.strip()


def _read_reference(path: Path) -> tuple[dict[str, Any], str]:
    reference = _object(path)
    source = _mapping(reference.get("source"), "industry reference source")
    parents = _mapping(reference.get("parent_nodes"), "industry reference parents")
    leaves = _mapping(reference.get("leaf_nodes"), "industry reference leaves")
    if (
        reference.get("schema_version") != 1
        or reference.get("kind") != "csrc_industry_hierarchy_reference"
        or not isinstance(reference.get("mapping_version"), str)
        or not reference["mapping_version"]
        or set(source) != {"authority", "title", "version", "source_locator"}
        or any(not isinstance(code, str) or len(code) != 1 for code in parents)
        or any(not isinstance(name, str) or not name for name in parents.values())
        or any(
            not isinstance(code, str)
            or len(code) != 3
            or code[0] not in parents
            or not code[1:].isdigit()
            for code in leaves
        )
        or any(not isinstance(name, str) or not name for name in leaves.values())
    ):
        raise IndustryClassificationBuildError(
            "industry hierarchy reference has an invalid contract"
        )
    return reference, _sha256(path)


def _schemas() -> dict[str, Any]:
    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise IndustryClassificationBuildError("pyarrow is required for industry DWD rows") from exc
    lineage = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    interval = [
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    return {
        "dwd_industry_scheme": pa.schema(
            lineage
            + interval
            + [
                pa.field("industry_scheme_id", pa.string(), nullable=False),
                pa.field("scheme_version", pa.string(), nullable=False),
                pa.field("scheme_name", pa.string(), nullable=False),
                pa.field("scheme_owner", pa.string(), nullable=False),
            ]
        ),
        "dwd_industry_node": pa.schema(
            lineage
            + interval
            + [
                pa.field("industry_node_id", pa.string(), nullable=False),
                pa.field("industry_scheme_id", pa.string(), nullable=False),
                pa.field("parent_node_id", pa.string(), nullable=True),
                pa.field("node_code", pa.string(), nullable=False),
                pa.field("node_name", pa.string(), nullable=False),
                pa.field("node_level", pa.int32(), nullable=False),
            ]
        ),
        "dwd_company_industry_membership": pa.schema(
            lineage
            + interval
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("industry_node_id", pa.string(), nullable=False),
                pa.field("membership_type", pa.string(), nullable=False),
                pa.field("confidence", pa.decimal128(9, 6), nullable=True),
                pa.field("source_declared_as_of", pa.date32(), nullable=False),
            ]
        ),
    }


def _verify_model(model: Mapping[str, Any]) -> None:
    tables = {
        raw.get("name"): raw
        for raw in model.get("tables", [])
        if isinstance(raw, Mapping) and raw.get("name") in _OWNED_TABLES
    }
    if set(tables) != set(_OWNED_TABLES):
        raise IndustryClassificationBuildError("model does not define the three industry objects")
    expected = {
        "dwd_industry_scheme": "effective_interval",
        "dwd_industry_node": "effective_interval",
        "dwd_company_industry_membership": "effective_interval",
    }
    if any(tables[table].get("time_profile") != profile for table, profile in expected.items()):
        raise IndustryClassificationBuildError("industry model time profile differs")
    if any(tables[table].get("partition_by") != [] for table in _OWNED_TABLES):
        raise IndustryClassificationBuildError("industry model partition contract differs")


def _select_sources(
    records: Sequence[Mapping[str, Any]], ods_package: Path
) -> tuple[_IndustrySource, _IndustrySource]:
    selected = {
        str(record.get("mapping_identity")): record
        for record in records
        if record.get("record_type") == "file"
        and record.get("disposition") == "modeled"
        and record.get("mapping_identity") in {_PARENT_SOURCE, _HOTFIX_SOURCE}
        and set(record.get("target_tables", [])).intersection(_OWNED_TABLES)
    }
    if set(selected) != {_PARENT_SOURCE, _HOTFIX_SOURCE}:
        raise IndustryClassificationBuildError(
            "input plan lacks the full and Hotfix industry sources"
        )
    result: list[_IndustrySource] = []
    for label, precedence in ((_PARENT_SOURCE, "parent"), (_HOTFIX_SOURCE, "hotfix")):
        record = selected[label]
        try:
            path = _verify_source(record, ods_package)
        except TradingCalendarBuildError as exc:
            raise IndustryClassificationBuildError(
                f"industry source verification failed: {label}"
            ) from exc
        result.append(_IndustrySource(label, record, path, precedence))
    return result[0], result[1]


def _resolve_company(
    raw_identifiers: Sequence[Mapping[str, Any]],
    security_company: Mapping[str, str],
    *,
    stock_code: str,
    effective_at: datetime,
) -> tuple[str, datetime]:
    """Resolve one stock code at the industry snapshot time without current-pointer use."""

    matches: list[tuple[str, datetime]] = []
    for row in raw_identifiers:
        namespace = row.get("identifier_namespace")
        if (
            not isinstance(namespace, str)
            or not namespace.startswith("CN_STOCK_CODE:")
            or row.get("identifier_value") != stock_code
        ):
            continue
        start = _timestamp(row.get("effective_from"), "identifier effective_from")
        end = (
            _timestamp(row["effective_to"], "identifier effective_to")
            if row.get("effective_to") is not None
            else None
        )
        if start <= effective_at and (end is None or effective_at < end):
            security_id = _text(row.get("security_id"), "listed-company identifier security_id")
            company_id = security_company.get(security_id)
            if company_id is None:
                raise IndustryClassificationBuildError("industry identifier has no company")
            matches.append(
                (company_id, _timestamp(row.get("knowledge_from"), "identifier knowledge"))
            )
    if len(matches) != 1:
        raise IndustryClassificationBuildError(
            f"industry membership cannot resolve one company: {stock_code}, matches={len(matches)}"
        )
    return matches[0]


def _load_identity_rows(candidate: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise IndustryClassificationBuildError("pyarrow is required for industry DWD rows") from exc
    security_rows = pq.read_table(
        candidate / "payload/listed_company/dwd_security.parquet"
    ).to_pylist()
    security_company: dict[str, str] = {}
    for row in security_rows:
        security_id = _text(row.get("security_id"), "listed-company security_id")
        company_id = _text(row.get("company_id"), "listed-company company_id")
        prior = security_company.setdefault(security_id, company_id)
        if prior != company_id:
            raise IndustryClassificationBuildError("security resolves to multiple companies")
    identifiers = pq.read_table(
        candidate / "payload/listed_company/dwd_security_identifier.parquet"
    ).to_pylist()
    return [dict(row) for row in identifiers], security_company


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_parquet_atomic(path: Path, table: Any) -> None:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise IndustryClassificationBuildError("pyarrow is required for industry DWD rows") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.unlink(missing_ok=True)
    try:
        pq.write_table(table, temporary, compression="zstd", write_statistics=True)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _file_record(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _verify_existing(
    output: Path, identity: Mapping[str, Any]
) -> IndustryClassificationBuildResult:
    if output.is_symlink() or not output.is_dir():
        raise IndustryClassificationBuildError("industry candidate must be a physical directory")
    manifest_path = output / "manifest.json"
    manifest = _object(manifest_path)
    if (output / "READY").read_text(encoding="ascii").strip() != _sha256(manifest_path):
        raise IndustryClassificationBuildError("industry candidate READY does not match manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_industry_classification_candidate"
        or manifest.get("run_id") != identity.get("run_id")
        or manifest.get("input_plan_digest") != identity.get("input_plan_digest")
        or manifest.get("model_sha256") != identity.get("model_sha256")
        or manifest.get("hierarchy_reference_sha256")
        != identity.get("hierarchy_reference_sha256")
        or manifest.get("listed_company_manifest_sha256")
        != identity.get("listed_company_manifest_sha256")
        or manifest.get("listed_company_semantic_digest")
        != identity.get("listed_company_semantic_digest")
        or manifest.get("source_sha256") != identity.get("source_sha256")
        or manifest.get("owned_tables") != list(_OWNED_TABLES)
    ):
        raise IndustryClassificationBuildError("industry candidate manifest identity differs")
    declared: set[str] = set()
    for raw in _sequence(manifest.get("files"), "industry candidate manifest files"):
        item = _mapping(raw, "industry candidate manifest file")
        relative = _text(item.get("path"), "industry candidate file path")
        if (
            relative in declared
            or relative.startswith("/")
            or ".." in PurePosixPath(relative).parts
        ):
            raise IndustryClassificationBuildError("industry candidate manifest has unsafe files")
        declared.add(relative)
        path = output / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise IndustryClassificationBuildError("industry candidate file verification failed")
    physical = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != declared | {"manifest.json", "READY"} or any(
        path.is_symlink() for path in output.rglob("*")
    ):
        raise IndustryClassificationBuildError("industry candidate physical inventory differs")
    row_counts = manifest.get("row_counts")
    if not isinstance(row_counts, Mapping):
        raise IndustryClassificationBuildError("industry candidate row counts are absent")
    return IndustryClassificationBuildResult(
        root=output.resolve(),
        semantic_digest=_text(manifest.get("semantic_digest"), "industry semantic digest"),
        row_counts={str(key): int(value) for key, value in row_counts.items()},
    )


def _build_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    hierarchy_reference: Path,
    output: Path,
    run_id: str,
) -> IndustryClassificationBuildResult:
    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise IndustryClassificationBuildError("industry input-plan verification failed") from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise IndustryClassificationBuildError("industry build must match one Base run")
    _verify_model(model)
    input_plan_digest = _text(plan.get("plan_digest"), "industry input plan digest")
    model_sha = _sha256(model_path)
    if _mapping(semantic_plan.get("model"), "input plan model").get("sha256") != model_sha:
        raise IndustryClassificationBuildError("industry model differs from input plan")
    reference, reference_sha = _read_reference(hierarchy_reference)
    try:
        verified = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            model_sha256=model_sha,
        )
    except ListedCompanyBuildError as exc:
        raise IndustryClassificationBuildError(
            "listed-company predecessor failed verification"
        ) from exc
    identifiers, security_company = _load_identity_rows(listed_company_candidate)
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    sources = _select_sources(records, ods_package)
    identity = {
        "schema_version": 1,
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "model_sha256": model_sha,
        "hierarchy_reference_sha256": reference_sha,
        "listed_company_manifest_sha256": verified.manifest_sha256,
        "listed_company_semantic_digest": verified.semantic_digest,
        "source_sha256": {source.label: source.record["sha256"] for source in sources},
    }
    if output.exists():
        return _verify_existing(output, identity)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise IndustryClassificationBuildError("pyarrow is required for industry DWD rows") from exc
    parent_rows: dict[str, tuple[dict[str, Any], _IndustrySource]] = {}
    hotfix_rows: set[str] = set()
    selected: list[tuple[dict[str, Any], _IndustrySource]] = []
    required_columns = {
        "stock_code",
        "industry_code",
        "industry_name",
        "classification",
        "provider_update_date",
        "market_date",
        "source",
        "source_revision",
        "acquired_at",
    }
    for source in sources:
        table = pq.read_table(source.path)
        if not required_columns.issubset(table.column_names):
            raise IndustryClassificationBuildError("industry source schema is incomplete")
        for raw in table.to_pylist():
            row = dict(raw)
            stock_code = _text(row.get("stock_code"), "industry stock code")
            if row.get("classification") != "证监会行业分类":
                raise IndustryClassificationBuildError(
                    "industry source classification is unsupported"
                )
            if source.precedence == "parent":
                if stock_code in parent_rows:
                    raise IndustryClassificationBuildError(
                        "industry parent has duplicate stock code"
                    )
                parent_rows[stock_code] = (row, source)
                selected.append((row, source))
                continue
            if stock_code in hotfix_rows:
                raise IndustryClassificationBuildError("industry Hotfix has duplicate stock code")
            hotfix_rows.add(stock_code)
            parent = parent_rows.get(stock_code)
            if parent is not None:
                if parent[0] != row:
                    raise IndustryClassificationBuildError("industry parent/Hotfix overlap differs")
                continue
            selected.append((row, source))
    if not selected:
        raise IndustryClassificationBuildError("industry source selection is empty")
    selected.sort(key=lambda value: _text(value[0].get("stock_code"), "industry stock code"))
    cutoff = _timestamp(semantic_plan.get("knowledge_cutoff"), "industry knowledge cutoff")
    scheme_effective_from = min(
        _effective_start(_date(row["market_date"], "market_date")) for row, _ in selected
    )
    scheme_id = _stable_id(
        "industry_scheme", _text(reference["mapping_version"], "industry mapping version")
    )
    parent_nodes = _mapping(reference["parent_nodes"], "industry parent nodes")
    leaf_nodes = _mapping(reference["leaf_nodes"], "industry leaf nodes")
    observed_leaves = {
        _text(row.get("industry_code"), "industry code") for row, _source in selected
    }
    unknown_leaves = sorted(observed_leaves.difference(leaf_nodes))
    if unknown_leaves:
        raise IndustryClassificationBuildError(
            f"industry reference lacks observed leaf nodes: {unknown_leaves[:10]}"
        )
    node_rows: list[dict[str, Any]] = []
    node_ids: dict[str, str] = {}
    reference_common = {
        "record_source": "csrc_industry_hierarchy_reference",
        "source_revision": _text(reference["mapping_version"], "industry mapping version"),
        "source_artifact_sha256": reference_sha,
        "knowledge_from": cutoff,
        "knowledge_to": None,
        "effective_from": scheme_effective_from,
        "effective_to": None,
    }
    for code in sorted({leaf[0] for leaf in observed_leaves}):
        node_id = _stable_id("industry_node", scheme_id, code)
        node_ids[code] = node_id
        node_rows.append(
            {
                **reference_common,
                "source_record_id": f"{hierarchy_reference.name}#node={code}",
                "industry_node_id": node_id,
                "industry_scheme_id": scheme_id,
                "parent_node_id": None,
                "node_code": code,
                "node_name": _text(parent_nodes[code], "industry parent name"),
                "node_level": 1,
            }
        )
    for code in sorted(observed_leaves):
        node_id = _stable_id("industry_node", scheme_id, code)
        node_ids[code] = node_id
        node_rows.append(
            {
                **reference_common,
                "source_record_id": f"{hierarchy_reference.name}#node={code}",
                "industry_node_id": node_id,
                "industry_scheme_id": scheme_id,
                "parent_node_id": node_ids[code[0]],
                "node_code": code,
                "node_name": _text(leaf_nodes[code], "industry leaf name"),
                "node_level": 2,
            }
        )
    scheme_row = {
        **reference_common,
        "source_record_id": f"{hierarchy_reference.name}#scheme",
        "industry_scheme_id": scheme_id,
        "scheme_version": _text(reference["mapping_version"], "industry mapping version"),
        "scheme_name": _text(
            _mapping(reference["source"], "industry source")["title"], "scheme name"
        ),
        "scheme_owner": _text(
            _mapping(reference["source"], "industry source")["authority"], "scheme owner"
        ),
    }
    membership_rows: list[dict[str, Any]] = []
    for row, source in selected:
        code = _text(row.get("industry_code"), "industry code")
        name = _text(row.get("industry_name"), "industry name")
        if leaf_nodes[code] != name:
            raise IndustryClassificationBuildError(
                "industry source leaf name differs from reference"
            )
        market_date = _date(row.get("market_date"), "industry market_date")
        effective_from = _effective_start(market_date)
        company_id, identifier_knowledge = _resolve_company(
            identifiers,
            security_company,
            stock_code=_text(row.get("stock_code"), "industry stock code"),
            effective_at=effective_from,
        )
        acquired_at = _timestamp(row.get("acquired_at"), "industry acquired_at")
        membership_rows.append(
            {
                "record_source": _text(row.get("source"), "industry source"),
                "source_record_id": (
                    f"{source.record['path']}#stock_code={row['stock_code']}&industry_code={code}"
                ),
                "source_revision": _text(row.get("source_revision"), "industry source revision"),
                "source_artifact_sha256": _text(source.record.get("sha256"), "industry source sha"),
                "knowledge_from": max(cutoff, acquired_at, identifier_knowledge),
                "knowledge_to": None,
                "effective_from": effective_from,
                "effective_to": None,
                "company_id": company_id,
                "industry_node_id": node_ids[code],
                "membership_type": "PRIMARY_CLASSIFICATION",
                "confidence": None,
                "source_declared_as_of": _date(
                    row.get("provider_update_date"), "industry provider_update_date"
                ),
            }
        )
    membership_keys = [
        (row["company_id"], row["industry_node_id"], row["effective_from"], row["knowledge_from"])
        for row in membership_rows
    ]
    if len(membership_keys) != len(set(membership_keys)):
        raise IndustryClassificationBuildError("industry membership output has duplicate row keys")
    by_company: dict[str, int] = {}
    for row in membership_rows:
        by_company[row["company_id"]] = by_company.get(row["company_id"], 0) + 1
    if any(count != 1 for count in by_company.values()):
        raise IndustryClassificationBuildError("industry snapshot has ambiguous company membership")
    output_rows = {
        "dwd_industry_scheme": [scheme_row],
        "dwd_industry_node": sorted(node_rows, key=lambda row: row["node_code"]),
        "dwd_company_industry_membership": membership_rows,
    }
    schemas = _schemas()
    work = output.parent / f".{output.name}.work"
    if work.exists() and (work.is_symlink() or not work.is_dir()):
        raise IndustryClassificationBuildError("industry work directory must be physical")
    work.mkdir(parents=True, exist_ok=True)
    checkpoint_path = work / "checkpoint.json"
    checkpoint = (
        _object(checkpoint_path)
        if checkpoint_path.exists()
        else {"schema_version": 1, "identity": identity, "completed_tables": {}}
    )
    if checkpoint.get("identity") != identity:
        raise IndustryClassificationBuildError("industry work identity differs")
    table_files: dict[str, str] = {}
    for table in _OWNED_TABLES:
        relative = f"payload/industry_classification/{table}.parquet"
        path = work / relative
        state = _mapping(checkpoint["completed_tables"].get(table, {}), "industry checkpoint")
        if path.exists() and state:
            if _sha256(path) != state.get("sha256"):
                raise IndustryClassificationBuildError("industry checkpoint output differs")
        else:
            _write_parquet_atomic(
                path, pa.Table.from_pylist(output_rows[table], schema=schemas[table])
            )
            checkpoint["completed_tables"][table] = _file_record(work, path)
            _write_json_atomic(checkpoint_path, checkpoint)
        table_files[table] = relative
    row_counts = {table: len(output_rows[table]) for table in _OWNED_TABLES}
    node_by_id = {row["industry_node_id"]: row for row in node_rows}
    if any(
        row["parent_node_id"] is not None and row["parent_node_id"] not in node_by_id
        for row in node_rows
    ):
        raise IndustryClassificationBuildError("industry node parent foreign key is unresolved")
    if any(row["industry_node_id"] not in node_by_id for row in membership_rows):
        raise IndustryClassificationBuildError("industry membership node foreign key is unresolved")
    source_reconciliation = {
        "schema_version": 1,
        "kind": "dwd_industry_classification_reconciliation",
        "source_rows": {
            _PARENT_SOURCE: len(parent_rows),
            _HOTFIX_SOURCE: len(hotfix_rows),
        },
        "overlay": {
            "policy": "parent_base_wins_on_overlapping_stock_code_hotfix_fills_absent_only",
            "parent_overlap_rows": len(set(parent_rows).intersection(hotfix_rows)),
            "hotfix_fill_rows": len(hotfix_rows.difference(parent_rows)),
            "selected_memberships": len(membership_rows),
        },
        "row_counts": row_counts,
        "unresolved_companies": 0,
        "ambiguous_companies": 0,
        "historical_backfill": False,
        "scope": "first_defensible_industry_snapshot_only",
    }
    _write_json_atomic(
        work / "reconciliation/source-row-reconciliation.json", source_reconciliation
    )
    table_digests = {
        table: _digest(_file_record(work, work / relative))
        for table, relative in table_files.items()
    }
    semantic_digest = _digest({**identity, "table_digests": table_digests})
    receipt = {
        "schema_version": 1,
        "kind": "dwd_industry_classification_build_receipt",
        "status": "built",
        **identity,
        "semantic_digest": semantic_digest,
        "row_counts": row_counts,
        "table_digests": table_digests,
        "quality_exceptions": [],
    }
    _write_json_atomic(work / "receipts/industry-classification-build.json", receipt)
    checkpoint["semantic_digest"] = semantic_digest
    checkpoint["completed"] = True
    _write_json_atomic(checkpoint_path, checkpoint)
    files = sorted(
        path
        for path in work.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name not in {"manifest.json", "READY"}
    )
    manifest = {
        "schema_version": 1,
        "kind": "dwd_industry_classification_candidate",
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "model_sha256": model_sha,
        "hierarchy_reference_sha256": reference_sha,
        "listed_company_manifest_sha256": verified.manifest_sha256,
        "listed_company_semantic_digest": verified.semantic_digest,
        "source_sha256": identity["source_sha256"],
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "partition_by": {table: [] for table in _OWNED_TABLES},
        "row_counts": row_counts,
        "files": [_file_record(work, path) for path in files],
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", _sha256(work / "manifest.json") + "\n")
    _fsync_directory(work)
    _verify_existing(work, identity)
    os.replace(work, output)
    _fsync_directory(output.parent)
    return _verify_existing(output, identity)


def build_industry_classification(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    hierarchy_reference: Path,
    output: Path,
    run_id: str,
) -> IndustryClassificationBuildResult:
    """Build the Issue #62 candidate from the exact ODS overlay and #57 identity."""

    if output.is_symlink():
        raise IndustryClassificationBuildError(
            "industry candidate output must not be a symbolic link"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = output.resolve()
    lock_path = normalized.parent / f".{normalized.name}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        raise IndustryClassificationBuildError("cannot open industry build lock") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise IndustryClassificationBuildError("industry build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            listed_company_candidate=listed_company_candidate,
            hierarchy_reference=hierarchy_reference,
            output=normalized,
            run_id=run_id,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
