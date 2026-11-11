"""Additive native integration TMP from source-qualified patch observations.

Identical components reuse existing history. A differing component holds its
whole dated frame. Unproven transaction atoms remain isolated. This output does
not stand in for a full independently accepted 33-table Base mirror.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_holder_components_v2 import COMPONENT_KEYS
from .base231_domain_pack import _local, schemas
from .base231_source_qualification import DWD, qualify_retained_inputs
from .ownership_capital import _effective_start

HOLDING = "dwd_shareholding_observation"
TABLES = ("dwd_financial_report", "dwd_financial_concept", "dwd_financial_fact", HOLDING)
FRAME = ["company_id", "security_id", "holding_type", "observation_start"]
META = {"record_source", "source_record_id", "source_revision", "source_artifact_sha256",
        "knowledge_from", "knowledge_to", "parser_version"}


def future_claim_frames(entries: Any) -> list[dict[str, Any]]:
    future = []
    for entry in entries:
        if entry["category"] != "股东研究" or "proof" not in entry:
            continue
        proof = entry["proof"]
        literal = proof["literal_identity"]
        acquired = datetime.fromisoformat(literal["actual_acquisition_instant"])
        captured_day = acquired.astimezone(ZoneInfo("Asia/Shanghai")).date()
        source_id = (f"stock_code={literal['identity_key'][1]}&category=股东研究"
                     f"&content_sha256={literal['content_sha256']}")
        for frame in proof["frames"]:
            if "business_date" in frame and date.fromisoformat(
                frame["business_date"],
            ) > captured_day:
                future.append({"source_record_id": source_id,
                               "source_artifact_sha256": proof["source_member_sha256"],
                               "observation_start": _effective_start(
                                   date.fromisoformat(frame["business_date"]))})
    return future


def holder_views(scan: GlobalScan) -> None:
    fields = [f for f in schemas()[HOLDING].names if f not in META]
    atom = "sha256(to_json(struct_pack(" + ",".join(f + ":=" + f for f in fields) + ")))"
    keys = ",".join(COMPONENT_KEYS)
    join = " AND ".join("o." + k + " IS NOT DISTINCT FROM n." + k for k in COMPONENT_KEYS)
    frame = ",".join("n." + k for k in FRAME)
    frame_join = " AND ".join("f." + k + " IS NOT DISTINCT FROM n." + k for k in FRAME)
    scan.connection.execute(
        "CREATE TEMP VIEW prior_holding AS SELECT *," + atom + " AS __atom,"
        "row_number() OVER(PARTITION BY " + keys + " ORDER BY knowledge_from DESC) AS __n "
        "FROM old_holding")
    scan.connection.execute("CREATE TEMP VIEW next_holding AS SELECT *," + atom
                            + " AS __atom FROM new_holding")
    scan.connection.execute("CREATE TEMP VIEW conflicting_frames AS SELECT DISTINCT " + frame
                            + " FROM next_holding n JOIN prior_holding o ON " + join
                            + " AND o.__n=1 WHERE n.__atom<>o.__atom")
    old_frame = ",".join("o." + k for k in FRAME)
    frame_match = " AND ".join("o." + k + " IS NOT DISTINCT FROM f." + k for k in FRAME)
    scan.connection.execute("CREATE TEMP VIEW held_frames AS SELECT * FROM conflicting_frames "
                            "UNION SELECT DISTINCT " + old_frame + " FROM prior_holding o JOIN "
                            "(SELECT DISTINCT " + ",".join(FRAME) + " FROM next_holding) f ON "
                            + frame_match + " WHERE o.__n=1 AND NOT EXISTS(SELECT 1 FROM "
                            "next_holding n WHERE " + join + ")")
    columns = ",".join("n." + name for name in schemas()[HOLDING].names)
    scan.connection.execute("CREATE TEMP VIEW holder_additions AS SELECT " + columns
                            + " FROM next_holding n WHERE NOT EXISTS(SELECT 1 FROM prior_holding o"
                            + " WHERE " + join + " AND o.__n=1) AND NOT EXISTS(SELECT 1 FROM "
                            "held_frames f WHERE " + frame_join + ")")


def prepare_native_delta(
    *, base: Path, bundle: Path, output: Path, budget: Budget,
) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if output != budget.root or file_sha(base / "manifest.json") != DWD:
        raise BoundedError("BASE231_NATIVE_DELTA_PARENT_OR_ROOT_CHANGED")
    qualification = qualify_retained_inputs(bundle)
    base_manifest = json.loads((base / "manifest.json").read_bytes())
    descriptor = base / "MATERIALIZATION.json"
    if file_sha(descriptor) != base_manifest["materialization_sha256"]:
        raise BoundedError("BASE231_NATIVE_DELTA_BASE_DESCRIPTOR_CHANGED")
    layout = json.loads(descriptor.read_bytes())["layout"]
    # Metadata/source hashing is bounded and no market-bar row is opened.
    old_paths: list[Path] = []
    for item in layout[HOLDING]["files"]:
        source = _local(base, item["path"])
        if file_sha(source) != item["sha256"] or source.stat().st_size != item["size_bytes"]:
            raise BoundedError("BASE231_NATIVE_DELTA_OLD_HOLDER_SOURCE_CHANGED")
        destination = output / "TMP-old-keys" / str(len(old_paths))
        destination.parent.mkdir(exist_ok=True)
        # Job-local typed key/value dependency only, released under this job's
        # explicit retention policy. Never a source hardlink or cache fallback.
        shutil.copyfile(source, destination)
        if file_sha(destination) != item["sha256"] or destination.stat().st_nlink != 1:
            raise BoundedError("BASE231_NATIVE_DELTA_OLD_DEPENDENCY_COPY_CHANGED")
        old_paths.append(destination)
        budget.check()
    projection = bundle / "domain/projection"
    source_schema = schemas()
    for table in TABLES:
        if not pq.ParquetFile(projection / (table + ".parquet")).schema_arrow.equals(
            source_schema[table], check_metadata=True,
        ):
            raise BoundedError("BASE231_NATIVE_DELTA_SCHEMA_CHANGED")
    local_holding = output / "TMP-new-holding.parquet"
    shutil.copyfile(projection / (HOLDING + ".parquet"), local_holding)
    with (projection / "frame-proofs.jsonl").open() as proofs:
        future = future_claim_frames(json.loads(line) for line in proofs)
    future_path = output / "TMP-future-claims.parquet"
    pq.write_table(pa.Table.from_pylist(future, schema=pa.schema([
        source_schema[HOLDING].field(name) for name in (
            "source_record_id", "source_artifact_sha256", "observation_start",
        )])), future_path)
    with GlobalScan(budget) as scan:
        scan.add("old_holding", old_paths)
        scan.add("new_holding_all", [local_holding])
        scan.add("future_claims", [future_path])
        scan.connection.execute("CREATE VIEW new_holding AS SELECT n.* FROM new_holding_all n "
                                "WHERE NOT EXISTS(SELECT 1 FROM future_claims f WHERE "
                                "n.source_record_id=f.source_record_id AND "
                                "n.source_artifact_sha256=f.source_artifact_sha256 AND "
                                "n.observation_start=f.observation_start)")
        holder_views(scan)
        frame_result = scan.connection.execute("SELECT count(*) FROM held_frames").fetchone()
        assert frame_result is not None
        frame_count = frame_result[0]
        table_path = output / (HOLDING + ".parquet")
        keys = ",".join(COMPONENT_KEYS)
        reader = scan.connection.execute("SELECT * FROM holder_additions ORDER BY " + keys
                                         ).to_arrow_reader(16384)
        selected = 0
        with pq.ParquetWriter(table_path, source_schema[HOLDING], compression="zstd") as writer:
            for batch in reader:
                budget.check(reserve=batch.nbytes * 2)
                typed = pa.Table.from_batches([batch]).cast(source_schema[HOLDING], safe=True)
                writer.write_table(typed)
                selected += typed.num_rows
    members = []
    for table in TABLES:
        path = output / (table + ".parquet")
        if table != HOLDING:
            shutil.copyfile(projection / path.name, path)
        budget.check()
        members.append({"table": table, "path": path.name, "rows": pq.ParquetFile(
            path).metadata.num_rows, "bytes": path.stat().st_size, "sha256": file_sha(path)})
    body = {"kind": "p10_base231_additive_native_integration_TMP_v1", "lifecycle": "TMP",
            "business_through": "2026-09-30", "source_qualification": qualification,
            "source_bundle_manifest_sha256": file_sha(bundle / "manifest.json"),
            "base_manifest_sha256": DWD, "members": members,
            "new_holder_components": selected, "non_additive_frames_preserve_old": frame_count,
            "source_future_claim_frames_not_promoted": len(future),
            "old_value_replacements": 0, "old_history_deleted": 0,
            "unproven_change_atoms_merged": 0, "consumer_admission": "NOT_GRANTED",
            "whole_mirror_acceptance": False, "evaluator_sha256": file_sha(Path(__file__))}
    (output / "integration-TMP.json").open("xb").write(canonical(body))
    return body
