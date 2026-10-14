"""Add compatible source observations without declaring a legacy-union matrix.

The complete current source matrix owns its explicit membership. Legacy
observations absent from that matrix remain intact, never inferred exits.
The flat union is not a complete ranking, denominator or net-change view.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, GlobalScan
from .base22_holder_components_v2 import COMPONENT_KEYS, load_component_contract
from .base22_sources import _tree, physical_path
from .base231_domain_pack import schemas
from .base231_holder_recovery import CONTRACT_ID as SOURCE_CONTRACT
from .base231_holder_recovery import TABLE, recover_frame
from .base231_native_delta import FRAME, META
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base231-qualified-holder-source-matrix-membership-v1"


@dataclass(frozen=True)
class QualifiedSourceMatrix:
    identity: str
    rows: int
    frames: int


def replay_qualified_source(root: Path) -> QualifiedSourceMatrix:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != SOURCE_CONTRACT
        or plan.get("through_date") != "2026-09-30"
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("source_qualification") != "NEW_GOVERNED_EXACT_RAW_CHILD_SELECTION"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("HOLDER_MATRIX_SCOPE_EXACT_QUALIFICATION_REQUIRED")
    if _tree(root) != set(plan["members"]) | {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"}:
        raise BoundedError("HOLDER_MATRIX_SCOPE_PHYSICAL_MEMBERS_CHANGED")
    ready = json.loads((root / "READY").read_bytes())
    if ready["input_plan_sha256"] != file_sha(root / "INPUTS.json") or ready[
        "seal_sha256"
    ] != file_sha(root / "DOMAIN-REPLAY.json"):
        raise BoundedError("HOLDER_MATRIX_SCOPE_SEAL_CHANGED")
    for name, member in plan["members"].items():
        p = physical_path(root / name)
        if (
            root not in p.parents
            or p.stat().st_nlink != 1
            or p.stat().st_size != member["bytes"]
            or file_sha(p) != member["sha256"]
        ):
            raise BoundedError("HOLDER_MATRIX_SCOPE_MEMBER_CHANGED")
    raws = pq.ParquetFile(root / "inputs/raw-selected.parquet").read().to_pylist()
    resolver = financial_company_resolver_from_listed_company_rows(
        pq.ParquetFile(root / "inputs/dwd_security.parquet").read().to_pylist(),
        pq.ParquetFile(root / "inputs/dwd_security_identifier.parquet").read().to_pylist(),
    )
    contract = load_component_contract(root / "config")
    values: list[dict[str, Any]] = []
    proofs = []
    frames = set()
    for spec in plan["selection"]:
        coordinate = (spec["stock_code"], spec["business_date"], spec["holding_type"])
        if coordinate in frames or spec["business_variants"] != 1:
            raise BoundedError("HOLDER_MATRIX_SCOPE_NONCONSENSUS_OR_DUPLICATE_FRAME")
        frames.add(coordinate)
        raw = raws[spec["raw_index"]]
        if raw["stock_code"] != spec["stock_code"]:
            raise BoundedError("HOLDER_MATRIX_SCOPE_RAW_CODE_CHANGED")
        rows, proof = recover_frame(
            raw,
            source_sha256=spec["member_sha256"],
            source_revision=spec["source_revision"],
            raw_row_sha256=spec["raw_row_sha256"],
            business_date=date.fromisoformat(spec["business_date"]),
            holding_type=spec["holding_type"],
            expected_business_sha256=spec["business_sha256"],
            resolver=resolver,
            component_contract=contract,
            observed_at=datetime.fromisoformat(plan["observed_at"]),
        )
        values.extend(rows)
        proofs.append(proof)
    actual = pq.ParquetFile(root / ("outputs/" + TABLE + ".parquet")).read()
    if (
        not actual.schema.equals(schemas()[TABLE], check_metadata=False)
        or actual.to_pylist() != values
        or digest(values) != plan["semantic_sha256"]
        or digest(proofs) != plan["proof_sha256"]
    ):
        raise BoundedError("HOLDER_MATRIX_SCOPE_NATIVE_RAW_REPLAY_CHANGED")
    return QualifiedSourceMatrix(plan["identity"], len(values), len(frames))


def source_scope_views(scan: GlobalScan, scope: QualifiedSourceMatrix) -> None:
    c = scan.connection
    census = c.execute("SELECT count(*) FROM new_holding").fetchone()
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", scope.identity)
        or scope.rows <= 0
        or scope.frames <= 0
        or census is None
        or census[0] != scope.rows
    ):
        raise BoundedError("HOLDER_MATRIX_SCOPE_CENSUS_CHANGED")
    keys = ",".join(COMPONENT_KEYS)
    frames = c.execute(
        "SELECT count(*) FROM (SELECT " + ",".join(FRAME) + " FROM new_holding GROUP BY ALL)"
    ).fetchone()
    unique = c.execute(
        "SELECT count(*) FROM (SELECT " + keys + " FROM new_holding GROUP BY ALL)"
    ).fetchone()
    if frames is None or frames[0] != scope.frames or unique is None or unique[0] != scope.rows:
        raise BoundedError("HOLDER_MATRIX_SCOPE_FRAME_OR_KEY_CHANGED")
    join = " AND ".join("o." + k + " IS NOT DISTINCT FROM n." + k for k in COMPONENT_KEYS)
    fields = [f for f in schemas()[TABLE].names if f not in META]
    different = " OR ".join("o." + f + " IS DISTINCT FROM n." + f for f in fields)
    c.execute(
        "CREATE TEMP VIEW prior_holding AS SELECT *,row_number() OVER(PARTITION BY "
        + keys
        + " ORDER BY knowledge_from DESC) AS __n FROM old_holding"
    )
    frame = ",".join("n." + k for k in FRAME)
    c.execute(
        "CREATE TEMP VIEW held_frames AS SELECT DISTINCT "
        + frame
        + " FROM new_holding n JOIN prior_holding o ON "
        + join
        + " AND o.__n=1 WHERE "
        + different
    )
    frame_join = " AND ".join("f." + k + " IS NOT DISTINCT FROM n." + k for k in FRAME)
    columns = ",".join("n." + f for f in schemas()[TABLE].names)
    c.execute(
        "CREATE TEMP VIEW holder_additions AS SELECT "
        + columns
        + " FROM new_holding n WHERE NOT EXISTS(SELECT 1 FROM prior_holding o WHERE "
        + join
        + " AND o.__n=1) AND NOT EXISTS(SELECT 1 FROM held_frames f WHERE "
        + frame_join
        + ")"
    )
    # Membership is the original qualified new matrix, not the flat union.
    c.execute(
        "CREATE TEMP VIEW qualified_matrix_members AS SELECT "
        + columns
        + " FROM new_holding n WHERE NOT EXISTS(SELECT 1 FROM held_frames f WHERE "
        + frame_join
        + ")"
    )


def write_source_view(scan: GlobalScan, view: str, destination: Path) -> dict[str, Any]:
    """Stream an exact native schema; DuckDB COPY loses required nullability."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if view not in {"holder_additions", "qualified_matrix_members", "integrated_holding"}:
        raise BoundedError("HOLDER_MATRIX_SCOPE_OUTPUT_VIEW_INVALID")
    destination = scan.budget.destination(destination)
    if destination.exists():
        raise BoundedError("HOLDER_MATRIX_SCOPE_OUTPUT_ALREADY_EXISTS")
    schema = schemas()[TABLE]
    columns = ",".join(schema.names)
    keys = ",".join(COMPONENT_KEYS + ["knowledge_from"])
    reader = scan.connection.execute(
        "SELECT " + columns + " FROM " + view + " ORDER BY " + keys
    ).to_arrow_reader(scan.budget.limits.batch_rows)
    count = 0
    semantic = hashlib.sha256()
    with pq.ParquetWriter(destination, schema, compression="zstd") as writer:
        for batch in reader:
            scan.budget.check(reserve=batch.nbytes * 2)
            typed = pa.Table.from_batches([batch]).cast(schema, safe=True)
            if any(not f.nullable and typed[f.name].null_count for f in schema):
                raise BoundedError("HOLDER_MATRIX_SCOPE_REQUIRED_VALUE_NULL")
            writer.write_table(typed)
            for row in typed.to_pylist():
                semantic.update(canonical(row) + b"\n")
            count += typed.num_rows
    scan.budget.check()
    return dict(rows=count, semantic_sha256="sha256:" + semantic.hexdigest())
