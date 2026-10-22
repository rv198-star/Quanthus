"""Job-local, physical P3 input for an explicitly weaker retrospective purpose.

This artifact cannot be opened by the strict NativeView admission entrypoint.
Two clocks remain distinct: source-snapshot information time, and historical
business scope. No historical known_at is manufactured to make a replay pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.pipelines.securities_warehouse import p3_purpose as purpose

SCHEMA = "quanthus.p3-retrospective-input.v1"
MAX_OUTPUT = 2 * 1024**3


def _physical_files() -> set[str]:
    return {"plan.json", "source-assessment.json"} | {
        f"{prefix}/{table}.parquet" for prefix in ("native", "unavailable") for table in native.KEYS
    }


def _statement(root: Path, table: str) -> str:
    return f"read_parquet({native._q(native._member(root, f'native/{table}.parquet'))})"


def verify(root: Path, *, manifest_sha256: str, ready: bool = True) -> dict[str, Any]:
    """Full offline input verification; never dereference archived source paths."""
    root = native.physical(root)
    if native._sha(root / "manifest.json") != manifest_sha256:
        raise native.InputError("RETRO_MANIFEST_IDENTITY")
    if ready and native._member(root, "READY").read_text().strip() != manifest_sha256:
        raise native.InputError("RETRO_READY_IDENTITY")
    m = native._json(root / "manifest.json")
    if (
        m.get("schema_version") != SCHEMA
        or m.get("purpose_id") != "p3-base24-retrospective-input-v1"
        or m.get("input_grade") != purpose.GRADE
        or m.get("historical_visibility_proven") is not False
        or m.get("original_B_accepted") is not False
        or m.get("D_activated") is not False
        or m.get("files", {}).keys() != _physical_files()
    ):
        raise native.InputError("RETRO_PURPOSE_OR_FILE_SET")
    expected = _physical_files() | {"manifest.json"} | ({"READY"} if ready else set())
    if native._tree(root) != expected:
        raise native.InputError("RETRO_PHYSICAL_FILE_SET")
    for name, entry in sorted(m["files"].items()):
        native._check_member(native._member(root, name), entry)
    plan = native._json(root / "plan.json")
    assessment = native._json(root / "source-assessment.json")
    if (
        digest_value(plan) != m.get("identity")
        or plan.get("source_manifest_sha256") != assessment.get("source_manifest_sha256")
        or plan.get("policy_digest") != assessment.get("policy_digest")
        or plan.get("scope") != native.SCOPE
        or plan.get("information_cutoff") != assessment.get("information_cutoff")
        or assessment.get("status") != "RETROSPECTIVE_SOURCE_CENSUS_PASS_WITH_LIMITATIONS"
        or assessment.get("critical_failures") != []
        or assessment.get("input_grade") != purpose.GRADE
        or assessment.get("historical_visibility_proven") is not False
        or assessment.get("original_B_accepted") is not False
        or assessment.get("D_activated") is not False
        or assessment.get("no_strategy_or_economic_results") is not True
    ):
        raise native.InputError("RETRO_ASSESSMENT_BINDING")
    cutoff = datetime.fromisoformat(plan["information_cutoff"])
    dates = [s["trade_date"] for s in native._warmup_binding(plan["warmup_calendar"])]
    summary = {}
    with tempfile.TemporaryDirectory(prefix="p3-retro-cold-") as tmp:
        c = native._connect(root, Path(tmp))
        timer = threading.Timer(600, c.interrupt)
        timer.daemon = True
        timer.start()
        try:
            schemas = native._schema(c)
            for table in native.KEYS:
                query = purpose.snapshot_query(table, cutoff)
                native._unambiguous(c, query, table, "RETRO_AMBIGUOUS_VERSION")
                c.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM raw_{table}")
                if any(purpose.key_lineage_counts(c, table)):
                    raise native.InputError("RETRO_KEY_OR_LINEAGE")
                native._zero(
                    c,
                    f"SELECT count(*) FROM raw_{table} WHERE NOT ("
                    + native._predicate(table, dates)
                    + ")",
                    "RETRO_BUSINESS_SCOPE",
                )
                selected = c.execute(f"SELECT count(*) FROM raw_{table}").fetchone()[0]
                visible = c.execute(f"SELECT count(*) FROM ({query})").fetchone()[0]
                unknown_path = native._member(root, f"unavailable/{table}.parquet")
                unknown = c.execute(
                    f"SELECT count(*),count(*) FILTER (WHERE "
                    f"knowledge_from IS NOT NULL) FROM read_parquet({native._q(unknown_path)})"
                ).fetchone()
                original = assessment["tables"][table]
                unknown_schema = {
                    r[0]: r[1]
                    for r in c.execute(
                        f"DESCRIBE SELECT * FROM read_parquet({native._q(unknown_path)})"
                    ).fetchall()
                }
                if (
                    digest_value(schemas[table]) != original["schema_digest"]
                    or unknown_schema != schemas[table]
                ):
                    raise native.InputError("RETRO_SCHEMA_DIFFERS")
                if (
                    selected != visible
                    or selected != original["snapshot_rows"]
                    or (unknown[0] != original["unknown_knowledge"] or unknown[1] != 0)
                ):
                    raise native.InputError("RETRO_ROW_OR_CLOCK_CENSUS")
                summary[table] = {
                    "snapshot_rows": selected,
                    "unknown_rows_retained_unavailable": unknown[0],
                }
            if any(purpose.calendar_identity_counts(c, plan["warmup_calendar"]).values()):
                raise native.InputError("RETRO_DATE_VENUE_OR_WARMUP")
            if purpose.reference_counts(c) != assessment["tables"]["reference_defects"]:
                raise native.InputError("RETRO_REFERENCE_CENSUS_DIFFERS")
        finally:
            timer.cancel()
            timer.join()
            c.close()
    return {
        "status": "RETROSPECTIVE_INPUT_VERIFIED",
        "manifest_sha256": manifest_sha256,
        "identity": m["identity"],
        "tables": summary,
        "source_paths_opened": False,
        "historical_visibility_proven": False,
        "original_B_accepted": False,
        "D_activated": False,
    }


def prepare(
    root: Path,
    output: Path,
    *,
    assessment: dict[str, Any],
    warmup: dict[str, Any],
    repository: Path,
) -> dict[str, Any]:
    """Materialize each scoped source once. Preserve failed staging; READY last."""
    root, output = native.physical(root), native.physical(output)
    staging = native.physical(output.with_name("." + output.name + ".job"))
    native._isolate(root, output, staging)
    if output.exists() or staging.exists():
        raise native.InputError("RETRO_OUTPUT_OR_STAGING_EXISTS")
    policy = purpose.load_policy(repository)
    if (
        assessment.get("status") != "RETROSPECTIVE_SOURCE_CENSUS_PASS_WITH_LIMITATIONS"
        or assessment.get("critical_failures") != []
        or assessment.get("implementation_sha256") != native._sha(Path(purpose.__file__))
        or assessment.get("native_implementation_sha256") != native._sha(Path(native.__file__))
        or assessment.get("policy_digest") != digest_value(policy)
        or assessment.get("warmup_digest") != digest_value(warmup)
        or assessment.get("historical_visibility_proven") is not False
        or assessment.get("input_grade") != purpose.GRADE
        or assessment.get("original_B_accepted") is not False
        or assessment.get("D_activated") is not False
        or assessment.get("no_strategy_or_economic_results") is not True
    ):
        raise native.InputError("RETRO_QUALIFICATION_REQUIRED")
    dates = [s["trade_date"] for s in native._warmup_binding(warmup)]
    cutoff = datetime.fromisoformat(assessment["information_cutoff"])
    metadata = native.source_metadata(root, assessment["source_manifest_sha256"])
    if datetime.fromisoformat(metadata["source_manifest"]["knowledge_cutoff"]) != cutoff:
        raise native.InputError("RETRO_SNAPSHOT_CLOCK_DIFFERS")
    if shutil.disk_usage(output.parent).free < policy["budgets"]["minimum_free_bytes"]:
        raise native.InputError("RETRO_SPACE_REQUIRED")
    began = time.monotonic()
    deadline = began + policy["budgets"]["wall_seconds"]
    files = metadata["native_closure"]["files"]
    watched = sorted(set(files) | {"manifest.json", "READY", "native-closure.json"})
    before = {n: native._fingerprint(native._member(root, n)) for n in watched}
    for name, entry in sorted(files.items()):
        native._check_member(native._member(root, name), entry, deadline=deadline)
    plan = {
        "purpose_id": policy["purpose_id"],
        "policy_digest": digest_value(policy),
        "source_manifest_sha256": metadata["source_manifest_sha256"],
        "source_members": files,
        "scope": native.SCOPE,
        "information_cutoff": cutoff.isoformat(),
        "warmup_calendar": warmup,
        "code_revision": os.environ.get("QUANTHUS_CODE_REVISION", "uncommitted-local"),
        "implementation_sha256": native._sha(Path(__file__)),
        "purpose_implementation_sha256": native._sha(Path(purpose.__file__)),
        "native_implementation_sha256": native._sha(Path(native.__file__)),
        "runtime": {
            "python": sys.version.split()[0],
            "duckdb": version("duckdb"),
            "pyarrow": version("pyarrow"),
        },
        "max_output_bytes": MAX_OUTPUT,
        "wall_seconds": policy["budgets"]["wall_seconds"],
    }
    staging.mkdir()
    (staging / "native").mkdir()
    (staging / "unavailable").mkdir()
    native._write(staging / "plan.json", plan)
    native._write(staging / "source-assessment.json", assessment)
    stages = []
    with tempfile.TemporaryDirectory(prefix="p3-retro-build-") as tmp:
        c = native._connect(root, Path(tmp))
        timer = threading.Timer(max(0, deadline - time.monotonic()), c.interrupt)
        timer.daemon = True
        timer.start()
        try:
            for table in native.KEYS:
                table_began = time.monotonic()
                native._deadline(deadline)
                predicate = native._predicate(table, dates)
                if table == "dwd_security_bar":
                    predicate += " AND interval_size='1d' AND price_basis IN ('none','UNADJUSTED')"
                clock = "TIMESTAMPTZ " + native._q(cutoff.isoformat())
                conditions = {
                    "native": f"knowledge_from<={clock} AND "
                    f"(knowledge_to IS NULL OR knowledge_to>{clock})",
                    "unavailable": "knowledge_from IS NULL",
                }
                for prefix, condition in conditions.items():
                    target = staging / prefix / f"{table}.parquet"
                    c.execute(
                        f"COPY (SELECT DISTINCT * FROM {_statement(root, table)} "
                        f"WHERE ({predicate}) AND ({condition})) TO {native._q(target)} "
                        "(FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                live_bytes = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
                if live_bytes > MAX_OUTPUT:
                    raise native.InputError("RETRO_OUTPUT_BUDGET_EXCEEDED")
                stages.append(
                    {
                        "table": table,
                        "elapsed_seconds": round(time.monotonic() - table_began, 3),
                        "disposition": "built_once",
                        "live_output_bytes": live_bytes,
                    }
                )
        finally:
            timer.cancel()
            timer.join()
            c.close()
    if before != {n: native._fingerprint(native._member(root, n)) for n in watched}:
        raise native.InputError("RETRO_SOURCE_CHANGED")
    manifest = {
        "schema_version": SCHEMA,
        "purpose_id": policy["purpose_id"],
        "input_grade": purpose.GRADE,
        "identity": digest_value(plan),
        "historical_visibility_proven": False,
        "original_B_accepted": False,
        "D_activated": False,
        "files": {name: native._entry(staging / name) for name in sorted(_physical_files())},
    }
    native._write(staging / "manifest.json", manifest)
    sha = native._sha(staging / "manifest.json")
    result = verify(staging, manifest_sha256=sha, ready=False)
    for name in sorted(_physical_files() | {"manifest.json"}):
        with (staging / name).open("rb") as f:
            os.fsync(f.fileno())
    for directory in (staging / "native", staging / "unavailable", staging):
        native._sync_directory(directory)
    with (staging / "READY").open("x", encoding="utf-8") as f:
        f.write(sha + "\n")
        f.flush()
        os.fsync(f.fileno())
    native._sync_directory(staging)
    if output.exists():
        raise native.InputError("RETRO_OUTPUT_EXISTS_DURING_BUILD")
    staging.rename(output)
    native._sync_directory(output.parent)
    return {
        **result,
        "output": str(output),
        "stages": stages,
        "source_fingerprints_unchanged": True,
        "elapsed_seconds": round(time.monotonic() - began, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assessment", type=Path)
    parser.add_argument("--warmup", type=Path)
    parser.add_argument("--repository", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify":
        result = verify(args.root, manifest_sha256=args.manifest_sha256)
    else:
        result = prepare(
            args.root,
            args.output,
            assessment=native._json(args.assessment),
            warmup=native._json(args.warmup),
            repository=args.repository,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
