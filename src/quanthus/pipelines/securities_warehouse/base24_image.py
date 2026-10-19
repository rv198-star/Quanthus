"""Complete Base 2.4 image profile and exact predecessor/bar composition."""

from __future__ import annotations

import importlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .base22 import file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _member, physical_path
from .base23_delivery import HARD_CHECKS
from .base23_native_image import (
    NativeImageProfile,
    audit_native_image,
    materialize_native_image,
    seal_native_image,
)
from .base24_delivery import (
    INTERVALS,
    ONE_MINUTE_DISPOSITION,
    PURPOSE,
    assess_delivery,
    validate_contract,
)

KIND = "dwd_base24_native_1d5m_purpose_qualified_candidate_v1"
SOURCES = {"predecessor", "native5m_bar"}
BAR_TABLE = "dwd_security_bar"


def _sha_id(value: str) -> str:
    return value if value.startswith("sha256:") else "sha256:" + value


def _ready_manifest(root: Path) -> dict[str, Any]:
    root = physical_path(root)
    manifest = _member(root, "manifest.json")
    ready = _member(root, "READY")
    if _sha_id(ready.read_text(encoding="ascii").strip()) != file_sha(manifest):
        raise BoundedError("BASE24_SOURCE_READY_IDENTITY_CHANGED")
    return json.loads(manifest.read_bytes())


def _bar_lineage(root: Path, files: list[dict[str, Any]]) -> list[list[str]]:
    pq = importlib.import_module("pyarrow.parquet")
    observed: set[tuple[str, str, str]] = set()
    for item in files:
        path = _member(root, item["path"])
        for batch in pq.ParquetFile(path).iter_batches(
            columns=["record_source", "source_revision", "source_artifact_sha256"],
            batch_size=65536,
        ):
            for row in zip(*(column.to_pylist() for column in batch.columns), strict=True):
                if any(not isinstance(value, str) or not value for value in row):
                    raise BoundedError("BASE24_BAR_LINEAGE_VALUE_INVALID")
                observed.add(row)
    if not observed:
        raise BoundedError("BASE24_BAR_LINEAGE_EMPTY")
    return [list(row) for row in sorted(observed)]


def build_composition_plan(
    predecessor_root: Path, bar_root: Path, rules: Mapping[str, Any]
) -> dict[str, Any]:
    """Freeze the exact 32-table reuse plus whole-bar replacement plan."""
    predecessor_root, bar_root = physical_path(predecessor_root), physical_path(bar_root)
    predecessor_manifest = _ready_manifest(predecessor_root)
    bar_manifest = _ready_manifest(bar_root)
    predecessor_materialization_path = _member(predecessor_root, "MATERIALIZATION.json")
    predecessor_plan_path = _member(predecessor_root, "evidence/input-plan.json")
    predecessor = json.loads(predecessor_materialization_path.read_bytes())
    predecessor_plan = json.loads(predecessor_plan_path.read_bytes())
    tables = set(rules.get("tables", {}))
    if (
        len(tables) != 33
        or set(predecessor.get("layout", {})) != tables
        or set(predecessor_plan.get("tables", {})) != tables
        or predecessor_manifest.get("business_through") != "2026-09-30"
        or bar_manifest.get("business_through") != "2026-09-30"
        or bar_manifest.get("default_intervals") != list(INTERVALS)
        or bar_manifest.get("one_minute_disposition") != ONE_MINUTE_DISPOSITION
        or bar_manifest.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("BASE24_SOURCE_SCOPE_CHANGED")
    bar_files = [
        item for item in bar_manifest.get("files", []) if item["path"].endswith(".parquet")
    ]
    if not bar_files or any(
        not item["path"].startswith("payload/dwd_security_bar/")
        or not isinstance(item.get("rows"), int)
        or item["rows"] <= 0
        for item in bar_files
    ):
        raise BoundedError("BASE24_BAR_PACKAGE_FILE_SET_CHANGED")
    lineages = _bar_lineage(bar_root, bar_files)
    plan: dict[str, Any] = {
        "purpose": PURPOSE,
        "through_date": "2026-09-30",
        "timezone": "Asia/Shanghai",
        "sources": {
            "predecessor": {
                "root": str(predecessor_root),
                "descriptor": "MATERIALIZATION.json",
                "descriptor_sha256": file_sha(predecessor_materialization_path),
                "role": "retained_native",
                "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION",
            },
            "native5m_bar": {
                "root": str(bar_root),
                "descriptor": "manifest.json",
                "descriptor_sha256": file_sha(bar_root / "manifest.json"),
                "role": "qualified_domain_output",
                "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION",
            },
        },
        "tables": {},
        "predecessor_manifest_sha256": file_sha(predecessor_root / "manifest.json"),
        "bar_manifest_sha256": file_sha(bar_root / "manifest.json"),
    }
    for table in sorted(tables):
        if table == BAR_TABLE:
            files = []
            counts: Counter[str] = Counter()
            prefix = PurePosixPath("payload/dwd_security_bar")
            for item in sorted(bar_files, key=lambda value: value["path"]):
                relative = PurePosixPath(item["path"])
                partition = relative.parent.relative_to(prefix).as_posix()
                record = {
                    "source_id": "native5m_bar",
                    "path": item["path"],
                    "table": table,
                    "sha256": _sha_id(item["sha256"]),
                    "rows": item["rows"],
                    "size_bytes": item["size_bytes"],
                    "partition": partition,
                }
                files.append(record)
                counts[partition] += item["rows"]
            source_ids = ["native5m_bar"]
            admitted = lineages
        else:
            files = [
                {
                    "source_id": "predecessor",
                    "path": item["path"],
                    "table": table,
                    "sha256": _sha_id(item["sha256"]),
                    "rows": item["rows"],
                    "size_bytes": item["size_bytes"],
                    "partition": item["partition"],
                }
                for item in predecessor["layout"][table]["files"]
            ]
            partition_rows = predecessor["layout"][table]["expected_partition_rows"]
            counts = Counter({key: int(value) for key, value in partition_rows.items()})
            source_ids = ["predecessor"]
            admitted = predecessor_plan["tables"][table]["admitted_lineage"]
        plan["tables"][table] = {
            "source_ids": source_ids,
            "files": files,
            "expected_partition_rows": dict(counts),
            "rows": sum(counts.values()),
            "admitted_lineage": admitted,
            "lineage_by_source": {source_ids[0]: admitted},
        }
    validate_composition(plan, {name: {} for name in SOURCES})
    return plan


def validate_default_intervals(table: str, paths: list[Path]) -> None:
    if table != BAR_TABLE:
        return
    pq = importlib.import_module("pyarrow.parquet")
    observed: set[str] = set()
    for path in paths:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(columns=["interval_size"], batch_size=65536):
            observed.update(x.as_py() for x in batch.column(0).unique() if x.is_valid)
    if observed != set(INTERVALS):
        raise BoundedError("BASE24_DEFAULT_INTERVAL_SET_CHANGED")


PROFILE = NativeImageProfile(
    kind=KIND,
    purpose=PURPOSE,
    version="2.4",
    business_through="2026-09-30",
    contract_filename="base24-delivery.v1.json",
    hard_checks=HARD_CHECKS,
    validate_contract=validate_contract,
    assess_delivery=assess_delivery,
    validate_table=validate_default_intervals,
    manifest_fields=(
        ("default_intervals", list(INTERVALS)),
        ("one_minute_disposition", ONE_MINUTE_DISPOSITION),
    ),
)


def validate_composition(plan: Mapping[str, Any], descriptors: Mapping[str, Any]) -> None:
    """Bind all unchanged tables to the predecessor and replace only the bar table."""
    if set(plan.get("sources", {})) != SOURCES or set(descriptors) != SOURCES:
        raise BoundedError("BASE24_EXACT_SOURCE_SET_CHANGED")
    tables = plan.get("tables", {})
    if BAR_TABLE not in tables or len(tables) != 33:
        raise BoundedError("BASE24_EXACT_TABLE_SET_CHANGED")
    for table, entry in tables.items():
        expected = ["native5m_bar"] if table == BAR_TABLE else ["predecessor"]
        if entry.get("source_ids") != expected or any(
            item.get("source_id") != expected[0] for item in entry.get("files", [])
        ):
            raise BoundedError("BASE24_TABLE_SOURCE_REPLACEMENT_CHANGED")


def materialize_base24_image(**kwargs: Any) -> dict[str, Any]:
    plan = kwargs["plan"]
    descriptors = {}
    for sid, binding in plan["sources"].items():
        root = physical_path(Path(binding["root"]))
        descriptors[sid] = json.loads(_member(root, binding["descriptor"]).read_bytes())
    validate_composition(plan, descriptors)
    return materialize_native_image(profile=PROFILE, **kwargs)


def audit_base24_image(root: Path, budget: Budget) -> dict[str, Any]:
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    descriptors = {
        sid: json.loads((root / "evidence/sources" / f"{sid}.json").read_bytes())
        for sid in plan["sources"]
    }
    validate_composition(plan, descriptors)
    return audit_native_image(root, budget, profile=PROFILE, unique_business_key_shortcut=True)


def seal_base24_image(
    root: Path, receipt: Mapping[str, Any], *, code_revision: str
) -> dict[str, Any]:
    return seal_native_image(root, receipt, code_revision=code_revision, profile=PROFILE)
