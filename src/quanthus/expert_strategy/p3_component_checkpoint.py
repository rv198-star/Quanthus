"""Exact own-Job index recovery for the fixed P3 component comparison only.

An admitted receipt binds all bytes, producer proof, role and semantic inputs.
This is not a cross-Job cache. Real admission still requires the versioned registry.
No data repair or price transformation occurs: only one exact snapshot ID changes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as n
from quanthus.expert_strategy import p3_retrospective_preparation as prep


def input_identity(root: Path, plan: Mapping[str, Any]) -> str:
    semantic_plan = {
        k: v
        for k, v in plan.items()
        if k not in {"code_config_members", "component_successor_rebind"}
    }
    manifest = n._json(root / "manifest.json")
    return digest_value(
        {
            "plan": semantic_plan,
            "members": {
                k: v
                for k, v in manifest["files"].items()
                if k not in {"plan.json", "variants.json"}
            },
        }
    )


def _port_clocks(
    root: Path,
    plan: Mapping[str, Any],
    guard: Callable[[], None],
) -> tuple[dict[date, tuple[datetime, datetime]], int]:
    """Read actual clocks, including the first decision day omitted by the index."""
    clocks: dict[date, tuple[datetime, datetime]] = {}
    days = set(map(date.fromisoformat, plan["trading_dates"]))
    cutoff = datetime.fromisoformat(plan["information_cutoff"])
    previous = ("", date.min)
    rows = 0
    with gzip.open(root / "ports.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            raw = json.loads(line)
            day = date.fromisoformat(raw["business_date"]["date"])
            key = (raw["security_id"], day)
            if (
                key <= previous
                or day not in days
                or raw["information_cutoff"]["datetime"] != plan["information_cutoff"]
                or raw["source_manifest_sha256"] != plan["source_manifest_sha256"]
            ):
                raise n.InputError("COMPONENT_CHECKPOINT_PORT_BINDING")
            previous = key
            selected = raw["selected"]
            if selected is not None:
                opened = datetime.fromisoformat(selected["open_at"]["datetime"])
                closed = datetime.fromisoformat(selected["close_at"]["datetime"])
                if (
                    opened.tzinfo is None
                    or closed.tzinfo is None
                    or opened.astimezone(n.TZ).date() != day
                    or closed.astimezone(n.TZ).date() != day
                    or not opened < closed <= cutoff
                    or (day in clocks and clocks[day] != (opened, closed))
                ):
                    raise n.InputError("COMPONENT_CHECKPOINT_CLOCK")
                clocks[day] = (opened, closed)
            rows += 1
            if rows % 4096 == 0:
                guard()
    needed = {d for d in days if plan["scope"][0] <= d.isoformat() <= plan["scope"][2]}
    if not needed.issubset(clocks):
        raise n.InputError("COMPONENT_CHECKPOINT_CLOCK_COVERAGE")
    return clocks, rows


def resume_index(
    prepared: Path,
    output: Path,
    plan: Mapping[str, Any],
    new_snapshot: str,
    descriptor: Mapping[str, Any],
    role: str,
    guard: Callable[[], None],
) -> dict[date, tuple[datetime, datetime]]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    started = time.monotonic()
    source = n.physical(output.parent / "session-index-checkpoint.parquet")
    expected = {
        "schema_version",
        "source_result_role",
        "index_sha256",
        "index_rows",
        "old_snapshot_id",
        "input_identity",
        "producer_proof_sha256",
        "current_code_config_digest",
        "prior_runtime_seconds",
    }
    if (
        set(descriptor) != expected
        or descriptor["schema_version"] != "quanthus.p3-component-index-checkpoint.v1"
        or descriptor["source_result_role"] != role
        or descriptor["input_identity"] != input_identity(prepared, plan)
        or descriptor["current_code_config_digest"] != digest_value(plan["code_config_members"])
        or not isinstance(descriptor["producer_proof_sha256"], str)
        or not n._SHA.fullmatch(descriptor["producer_proof_sha256"])
        or not isinstance(descriptor["old_snapshot_id"], str)
        or not descriptor["old_snapshot_id"].startswith(prep.PURPOSE + "/")
        or type(descriptor["index_rows"]) is not int
        or descriptor["index_rows"] < 1
        or n._sha(source) != descriptor["index_sha256"]
    ):
        raise n.InputError("COMPONENT_CHECKPOINT_BINDING")
    source_fingerprint = n._fingerprint(source)
    schema = pa.schema(
        [
            ("market_date", pa.date32()),
            ("security_id", pa.string()),
            ("payload", pa.string()),
        ]
    )
    pf = pq.ParquetFile(source)
    if pf.schema_arrow != schema or pf.metadata.num_rows != descriptor["index_rows"]:
        raise n.InputError("COMPONENT_CHECKPOINT_SCHEMA_CENSUS")
    guard()
    clocks, port_rows = _port_clocks(prepared, plan, guard)
    clock_seconds = time.monotonic() - started
    target = output / "sessions.parquet"
    if target.exists() or target.is_symlink():
        raise n.InputError("COMPONENT_CHECKPOINT_NO_OVERWRITE")
    old_snapshot = descriptor["old_snapshot_id"]
    before, after = hashlib.sha256(), hashlib.sha256()
    previous = (date.min, "")
    rows = 0

    def row_bytes(row: Mapping[str, Any], payload: Any) -> bytes:
        return (
            str(row["market_date"])
            + "|"
            + str(row["security_id"])
            + "|"
            + json.dumps(payload, sort_keys=True)
            + "\n"
        ).encode()

    with pq.ParquetWriter(target, schema, compression="zstd") as writer:
        for batch in pf.iter_batches(batch_size=4096):
            rewritten = []
            for row in batch.to_pylist():
                key = (row["market_date"], row["security_id"])
                payload = json.loads(row["payload"])
                session = payload["session"]
                if key <= previous or session["snapshot_id"] != old_snapshot:
                    raise n.InputError("COMPONENT_CHECKPOINT_KEY_OR_SNAPSHOT")
                previous = key
                before.update(row_bytes(row, payload))
                session["snapshot_id"] = new_snapshot
                rewritten.append({**row, "payload": json.dumps(payload, sort_keys=True)})
                rows += 1
            writer.write_table(pa.Table.from_pylist(rewritten, schema=schema))
            guard()
    pf.close()
    restored_rows = 0
    with pq.ParquetFile(target) as restored:
        if restored.schema_arrow != schema:
            raise n.InputError("COMPONENT_CHECKPOINT_OUTPUT_SCHEMA")
        for batch in restored.iter_batches(batch_size=4096):
            for row in batch.to_pylist():
                payload = json.loads(row["payload"])
                if payload["session"]["snapshot_id"] != new_snapshot:
                    raise n.InputError("COMPONENT_CHECKPOINT_OUTPUT_SNAPSHOT")
                payload["session"]["snapshot_id"] = old_snapshot
                after.update(row_bytes(row, payload))
                restored_rows += 1
            guard()
    if (
        rows != descriptor["index_rows"]
        or restored_rows != rows
        or before.digest() != after.digest()
        or n._fingerprint(source) != source_fingerprint
    ):
        raise n.InputError("COMPONENT_CHECKPOINT_CONTENT_CHANGED")
    prep._write(
        output / "index-recovery.safe.json",
        {
            "disposition": "own_job_index_reuse_exact_snapshot_mapping",
            "descriptor_digest": digest_value(dict(descriptor)),
            "role": role,
            "index_rows": rows,
            "port_rows_read_only_for_clocks": port_rows,
            "clock_dates": len(clocks),
            "clock_recovery_seconds": clock_seconds,
            "elapsed_seconds": time.monotonic() - started,
            "before_normalized_sha256": "sha256:" + before.hexdigest(),
            "after_normalized_sha256": "sha256:" + after.hexdigest(),
            "output_sha256": n._sha(target),
            "output_bytes": target.stat().st_size,
            "source_unchanged": True,
            "producer_builds": 0,
        },
    )
    return clocks
