"""Derive a sealed DWD Base with corrected financial concept effective dates."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quanthus.pipelines.securities_warehouse.domain_base import (
    _digest,
    _fsync_directory,
    _link,
    _sha256,
    _write_json,
    verify_domain_base,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import _period_coordinate


class DwdFinancialConceptRepairError(RuntimeError):
    """Raised when a repair cannot preserve the source package contract."""


@dataclass(frozen=True)
class DwdFinancialConceptRepairResult:
    root: Path
    manifest_sha256: str
    content_digest: str
    source_manifest_sha256: str
    source_run_id: str
    repair_id: str
    fact_rows: int
    concept_rows: int
    changed_rows: int


_CONCEPT_PATH = "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
_FACT_PREFIX = "payload/financial_reporting/dwd_financial_fact/"
_RECEIPT_PATH = "evidence/repairs/financial-concept-effective-from.json"
_MEMORY_LIMIT = re.compile(r"^[1-9][0-9]*(?:MB|GB)$")


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdFinancialConceptRepairError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdFinancialConceptRepairError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _verify_source_seal(
    source_root: Path, expected_manifest_sha256: str
) -> tuple[dict[str, Any], str]:
    manifest_path = source_root / "manifest.json"
    manifest = _object(manifest_path, "source DWD manifest")
    manifest_sha256 = _sha256(manifest_path)
    try:
        ready = (source_root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DwdFinancialConceptRepairError("cannot read source DWD READY") from exc
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_domain_base_package"
        or ready != manifest_sha256
        or manifest_sha256 != expected_manifest_sha256
    ):
        raise DwdFinancialConceptRepairError("source seal differs from the expected R13 package")
    return manifest, manifest_sha256


def _declared_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise DwdFinancialConceptRepairError(f"unsafe source member path: {relative}")
    path = root.joinpath(*pure.parts)
    if path.is_symlink() or not path.is_file():
        raise DwdFinancialConceptRepairError(f"source member is not a physical file: {relative}")
    return path


def _source_inventory(
    source_root: Path, manifest: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], Path, list[Path]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DwdFinancialConceptRepairError("source manifest files are invalid")
    files: list[dict[str, Any]] = []
    concept_path: Path | None = None
    fact_paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("path"), str):
            raise DwdFinancialConceptRepairError("source manifest file row is invalid")
        row = dict(raw)
        relative = str(row["path"])
        if relative in seen:
            raise DwdFinancialConceptRepairError(f"duplicate source member: {relative}")
        seen.add(relative)
        path = _declared_file(source_root, relative)
        files.append(row)
        if relative == _CONCEPT_PATH:
            concept_path = path
        elif relative.startswith(_FACT_PREFIX) and relative.endswith(".parquet"):
            fact_paths.append(path)
    if concept_path is None or not fact_paths:
        raise DwdFinancialConceptRepairError("financial concept or fact payload is missing")
    return files, concept_path, sorted(fact_paths)


def _fact_coordinates(
    fact_paths: list[Path], *, memory_limit: str, threads: int
) -> tuple[dict[str, date], int]:
    if not _MEMORY_LIMIT.fullmatch(memory_limit) or threads not in {1, 2, 3, 4}:
        raise DwdFinancialConceptRepairError("invalid DuckDB resource limit")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        rows = connection.execute(
            """
            SELECT financial_concept_id, MIN(period_end), COUNT(*)
            FROM read_parquet(?)
            GROUP BY financial_concept_id
            ORDER BY financial_concept_id
            """,
            [[str(path) for path in fact_paths]],
        ).fetchall()
    except duckdb.Error as exc:
        raise DwdFinancialConceptRepairError("cannot scan financial fact coordinates") from exc
    finally:
        connection.close()
    coordinates: dict[str, date] = {}
    fact_rows = 0
    for raw_concept_id, raw_period_end, raw_count in rows:
        if not isinstance(raw_period_end, date):
            raise DwdFinancialConceptRepairError("financial fact period_end is not a date")
        concept_id = str(raw_concept_id)
        if concept_id in coordinates:
            raise DwdFinancialConceptRepairError("financial fact concept aggregation is duplicated")
        coordinates[concept_id] = raw_period_end
        fact_rows += int(raw_count)
    return coordinates, fact_rows


def _correct_concepts(
    concept_path: Path, coordinates: Mapping[str, date]
) -> tuple[pa.Table, int, datetime | None, datetime | None]:
    table = pq.read_table(concept_path)
    required = {"financial_concept_id", "effective_from"}
    if not required.issubset(table.column_names):
        raise DwdFinancialConceptRepairError("financial concept schema is incomplete")
    concept_ids = table.column("financial_concept_id").to_pylist()
    effective_from = table.column("effective_from").to_pylist()
    if len(set(map(str, concept_ids))) != table.num_rows:
        raise DwdFinancialConceptRepairError("financial concept identifiers are not unique")
    unknown = set(coordinates).difference(map(str, concept_ids))
    if unknown:
        raise DwdFinancialConceptRepairError(
            f"financial facts reference {len(unknown)} unknown concepts"
        )
    corrected: list[datetime] = []
    changed = 0
    old_min: datetime | None = None
    new_min: datetime | None = None
    for raw_id, raw_effective_from in zip(concept_ids, effective_from, strict=True):
        if not isinstance(raw_effective_from, datetime):
            raise DwdFinancialConceptRepairError("financial concept effective_from is invalid")
        concept_id = str(raw_id)
        coordinate = coordinates.get(concept_id)
        value = (
            min(raw_effective_from, _period_coordinate(coordinate))
            if coordinate is not None
            else raw_effective_from
        )
        changed += int(value != raw_effective_from)
        corrected.append(value)
        old_min = raw_effective_from if old_min is None else min(old_min, raw_effective_from)
        new_min = value if new_min is None else min(new_min, value)
    index = table.schema.get_field_index("effective_from")
    field = table.schema.field(index)
    repaired = table.set_column(index, field, pa.array(corrected, type=field.type))
    if not repaired.schema.equals(table.schema, check_metadata=True):
        raise DwdFinancialConceptRepairError("financial concept schema changed during repair")
    return repaired, changed, old_min, new_min


def _file_record(path: Path, relative: str, *, candidate_domain: str) -> dict[str, Any]:
    return {
        "path": relative,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "candidate_domain": candidate_domain,
    }


def repair_financial_concept_effective_from(
    *,
    source_root: Path,
    output: Path,
    repair_id: str,
    code_revision: str,
    expected_source_manifest_sha256: str,
    expected_fact_rows: int,
    expected_concept_rows: int,
    expected_changed_rows: int,
    memory_limit: str = "2GB",
    threads: int = 2,
) -> DwdFinancialConceptRepairResult:
    """Hard-link an immutable source Base and replace only its concept parent table."""

    source_root = source_root.resolve()
    output = output.resolve()
    if output == source_root or output.is_relative_to(source_root):
        raise DwdFinancialConceptRepairError("repair output must be outside the source package")
    if not repair_id or not code_revision or len(code_revision) != 40:
        raise DwdFinancialConceptRepairError("repair identity is incomplete")
    manifest, source_manifest_sha256 = _verify_source_seal(
        source_root, expected_source_manifest_sha256
    )
    source_run_id = manifest.get("run_id")
    if not isinstance(source_run_id, str) or not source_run_id:
        raise DwdFinancialConceptRepairError("source package run identity is incomplete")
    if source_run_id == repair_id:
        raise DwdFinancialConceptRepairError("repair run must differ from its source run")
    files, concept_path, fact_paths = _source_inventory(source_root, manifest)
    coordinates, fact_rows = _fact_coordinates(
        fact_paths, memory_limit=memory_limit, threads=threads
    )
    repaired, changed_rows, old_min, new_min = _correct_concepts(concept_path, coordinates)
    if fact_rows != expected_fact_rows:
        raise DwdFinancialConceptRepairError(
            f"financial fact rows differ: expected {expected_fact_rows}, observed {fact_rows}"
        )
    if repaired.num_rows != expected_concept_rows:
        raise DwdFinancialConceptRepairError(
            "financial concept rows differ: "
            f"expected {expected_concept_rows}, observed {repaired.num_rows}"
        )
    if changed_rows != expected_changed_rows:
        raise DwdFinancialConceptRepairError(
            "financial concept changes differ: "
            f"expected {expected_changed_rows}, observed {changed_rows}"
        )
    if output.exists() or output.is_symlink():
        raise DwdFinancialConceptRepairError("repair output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.financial-concept-repair-staging")
    if staging.exists() or staging.is_symlink():
        raise DwdFinancialConceptRepairError("repair staging path already exists")
    try:
        target_files: list[dict[str, Any]] = []
        for raw in files:
            relative = str(raw["path"])
            if relative == _CONCEPT_PATH:
                continue
            _link(source_root / relative, staging / relative)
            target_files.append(dict(raw))
        repaired_path = staging / _CONCEPT_PATH
        repaired_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(repaired, repaired_path, compression="zstd", write_statistics=True)
        with repaired_path.open("rb") as stream:
            os.fsync(stream.fileno())
        target_files.append(
            _file_record(repaired_path, _CONCEPT_PATH, candidate_domain="financial_reporting")
        )
        receipt = {
            "schema_version": 1,
            "kind": "dwd_financial_concept_effective_from_repair",
            "status": "repaired",
            "algorithm": "min(existing_effective_from, earliest_referenced_period_end_shanghai)",
            "source": {
                "root": str(source_root),
                "package_id": manifest.get("package_id"),
                "run_id": manifest.get("run_id"),
                "manifest_sha256": source_manifest_sha256,
                "content_digest": manifest.get("content_digest"),
            },
            "target": {
                "root": str(output),
                "repair_id": repair_id,
                "source_run_id": source_run_id,
                "code_revision": code_revision,
            },
            "fact_rows": fact_rows,
            "referenced_concepts": len(coordinates),
            "concept_rows": repaired.num_rows,
            "changed_rows": changed_rows,
            "old_min_effective_from": old_min.isoformat() if old_min is not None else None,
            "new_min_effective_from": new_min.isoformat() if new_min is not None else None,
            "source_concept_sha256": _sha256(concept_path),
            "repaired_concept_sha256": _sha256(repaired_path),
        }
        receipt["canonical_digest"] = _digest(receipt)
        receipt_path = staging / _RECEIPT_PATH
        _write_json(receipt_path, receipt)
        target_files.append(
            _file_record(receipt_path, _RECEIPT_PATH, candidate_domain="financial_reporting")
        )
        target_files.sort(key=lambda row: str(row["path"]))
        row_counts = manifest.get("row_counts")
        upstream = manifest.get("upstream_candidates")
        if not isinstance(row_counts, Mapping) or not isinstance(upstream, Mapping):
            raise DwdFinancialConceptRepairError("source package identity is incomplete")
        identity = {
            "package_id": manifest.get("package_id"),
            "run_id": source_run_id,
            "through_date": manifest.get("through_date"),
            "input_plan_digest": manifest.get("input_plan_digest"),
            "ods_base": manifest.get("ods_base"),
            "model_sha256": manifest.get("model_sha256"),
            "model_extension_sha256": manifest.get("model_extension_sha256"),
            "code_revision": code_revision,
            "upstream_candidates": dict(upstream),
            "repair_provenance": {
                "kind": receipt["kind"],
                "receipt_path": _RECEIPT_PATH,
                "canonical_digest": receipt["canonical_digest"],
                "source_manifest_sha256": source_manifest_sha256,
            },
            "row_counts": dict(sorted((str(key), int(value)) for key, value in row_counts.items())),
        }
        payload = [row for row in target_files if str(row["path"]).startswith("payload/")]
        target_manifest = {
            "schema_version": 1,
            "kind": "dwd_domain_base_package",
            **identity,
            "content_digest": _digest({**identity, "files": target_files}),
            "table_count": len(row_counts),
            "total_rows": sum(int(value) for value in row_counts.values()),
            "payload_file_count": len(payload),
            "payload_bytes": sum(int(row["size_bytes"]) for row in payload),
            "files": target_files,
        }
        _write_json(staging / "manifest.json", target_manifest)
        ready = staging / "READY"
        ready.write_text(_sha256(staging / "manifest.json") + "\n", encoding="ascii")
        with ready.open("rb") as stream:
            os.fsync(stream.fileno())
        for directory in sorted(
            (path for path in staging.rglob("*") if path.is_dir()), reverse=True
        ):
            directory.chmod(0o750)
        staging.chmod(0o750)
        _fsync_directory(staging)
        os.replace(staging, output)
        _fsync_directory(output.parent)
    except BaseException:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
    verified = verify_domain_base(output)
    return DwdFinancialConceptRepairResult(
        root=verified.root,
        manifest_sha256=verified.manifest_sha256,
        content_digest=verified.content_digest,
        source_manifest_sha256=source_manifest_sha256,
        source_run_id=source_run_id,
        repair_id=repair_id,
        fact_rows=fact_rows,
        concept_rows=repaired.num_rows,
        changed_rows=changed_rows,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repair-id", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--expected-source-manifest-sha256", required=True)
    parser.add_argument("--expected-fact-rows", type=int, required=True)
    parser.add_argument("--expected-concept-rows", type=int, required=True)
    parser.add_argument("--expected-changed-rows", type=int, required=True)
    parser.add_argument("--memory-limit", default="2GB")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        result = repair_financial_concept_effective_from(
            source_root=args.source_root,
            output=args.output,
            repair_id=args.repair_id,
            code_revision=args.code_revision,
            expected_source_manifest_sha256=args.expected_source_manifest_sha256,
            expected_fact_rows=args.expected_fact_rows,
            expected_concept_rows=args.expected_concept_rows,
            expected_changed_rows=args.expected_changed_rows,
            memory_limit=args.memory_limit,
            threads=args.threads,
        )
    except (DwdFinancialConceptRepairError, OSError) as exc:
        print(f"DWD financial concept repair failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "repaired",
                "root": str(result.root),
                "manifest_sha256": result.manifest_sha256,
                "content_digest": result.content_digest,
                "source_manifest_sha256": result.source_manifest_sha256,
                "source_run_id": result.source_run_id,
                "repair_id": result.repair_id,
                "fact_rows": result.fact_rows,
                "concept_rows": result.concept_rows,
                "changed_rows": result.changed_rows,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
