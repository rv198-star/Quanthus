"""Opt-in additive holder observations; neither legacy nor complete-image admission."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import THROUGH, _check_row, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError
from .base22_outputs import acquisition
from .base22_precision import decimal_profile
from .base22_sources import _tree, physical_path
from .ownership_capital import materialize_shareholder_count_row

CONTRACT_ID = "p10-base22-holder-exact-v2"
TABLE = "dwd_shareholder_count_observation"
PARSER = "quanthus-structured-shareholder-count-exact-v2"
CONTRACT = {
    "schema_version": 2,
    "contract_id": CONTRACT_ID,
    "purpose": "isolated_governance_holder_observations",
    "parent_contract_sha256": "sha256:"
    "a57938ee94a91510133cbfbe78373c32c38673a5a3780dd759d250d8ddefe97c",
    "approved_draft_sha256": "sha256:"
    "a9329f73c81e6ba34fbf287ca5a5922faef1d5605e53429d613b074c89a41059",
    "table": TABLE,
    "field": {"name": "average_holding_exact", "precision": 38, "scale": 11, "nullable": True},
    "old_field": {"name": "average_holding", "precision": 38, "scale": 10, "nullable": True},
    "raw_field": "raw_average_holding",
    "column_rule": "exact_when_representable_else_null_with_per_row_evidence",
    "unsupported_both_columns": "fail_closed",
    "maximum_scope_rows": 32768,
    "through_date": "2026-08-31",
    "business_timezone": "Asia/Shanghai",
    "sealed_legacy_data": "unchanged",
    "source_admission": "NOT_GRANTED_BY_SCHEMA",
    "consumer_admission": "NOT_GRANTED",
}


def load_holder_contract(config: Path) -> tuple[dict[str, Any], Any]:
    """Validate the explicit version plus frozen parent before deriving its schema."""
    import pyarrow as pa  # type: ignore[import-untyped]

    try:
        contract = json.loads((config / "base22-holder-exact.v2.json").read_bytes())
        if (
            contract != CONTRACT
            or file_sha(config / "base22-independent-image.v1.json")
            != (CONTRACT["parent_contract_sha256"])
        ):
            raise BoundedError("HOLDER_EXACT_CONTRACT_INVALID")
        _, model, extension = load_contract(config)
    except (OSError, ValueError) as exc:
        raise BoundedError("HOLDER_EXACT_CONTRACT_UNREADABLE") from exc
    legacy = schemas(model, extension)[TABLE]
    return contract, legacy.append(pa.field("average_holding_exact", pa.decimal128(38, 11)))


def _columns(raw: Any) -> tuple[Decimal | None, Decimal | None, dict[str, Any], dict[str, Any]]:
    import pyarrow as pa

    old = decimal_profile(raw, precision=38, scale=10)
    new = decimal_profile(raw, precision=38, scale=11)
    if old["classification"] == "NULL":
        return None, None, old, new
    if not old["fits"] and not new["fits"]:
        raise BoundedError("HOLDER_EXACT_UNSUPPORTED_VALUE")
    value = Decimal(str(raw))
    # Bind row evidence to the declared physical scale, before hashing. Arrow's
    # exact rescale rejects loss, ignores Decimal context and matches cold readback;
    # the original token/precision/sign still lives unchanged in the raw string.
    return (
        pa.scalar(value, type=pa.decimal128(38, 10)).as_py() if old["fits"] else None,
        pa.scalar(value, type=pa.decimal128(38, 11)).as_py() if new["fits"] else None,
        old,
        new,
    )


def holder_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Hash-bound per-row unavailable reasons, without exposing financial scalars."""
    _, _, old, new = _columns(row["raw_average_holding"])
    return {
        "row_sha256": digest(row),
        "old_column": old,
        "exact_column": new,
        "old_unavailable_reason": None if old["fits"] else old["classification"],
        "exact_unavailable_reason": None if new["fits"] else new["classification"],
    }


def validate_holder_row(row: dict[str, Any]) -> dict[str, Any]:
    """Same lossless numeric semantics before serialization and in offline scans."""
    old, new, _, _ = _columns(row["raw_average_holding"])
    count = decimal_profile(row["raw_holder_count"], precision=19, scale=0)
    if (
        row["average_holding"] != old
        or row["average_holding_exact"] != new
        or row["parser_version"] != PARSER
        or row["published_at"] is not None
        or row["period_end"] > THROUGH
        or not count["fits"]
        or Decimal(row["raw_holder_count"]) != row["holder_count"]
        or not -(2**63) <= row["holder_count"] < 2**63
    ):
        raise BoundedError("HOLDER_EXACT_ROW_SEMANTICS_INVALID")
    return holder_evidence(row)


def exact_holder_observation(
    row: dict[str, Any],
    *,
    company_id: str,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
    contract_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Preserve real clocks/old identities; avoid the legacy quantization entirely."""
    if contract_id != CONTRACT_ID:
        raise BoundedError("HOLDER_EXACT_CONTRACT_ID_INVALID")
    raw = row.get("avg_hold_num")
    old, new, _, _ = _columns(raw)
    knowledge = acquisition(row, parent_knowledge)
    # Reuse only identity/count/metadata construction, with no numeric input to its
    # legacy average quantizer. The explicit v2 route then installs exact scalars.
    output = materialize_shareholder_count_row(
        dict(row, avg_hold_num=None, knowledge_cutoff=knowledge),
        company_id=company_id,
        security_id=security_id,
        source_sha256=source_sha256,
        parent_knowledge_from=parent_knowledge,
    )
    if output["period_end"] > THROUGH:
        return {}
    output.update(
        raw_average_holding=None if raw is None else str(raw),
        average_holding=old,
        average_holding_exact=new,
        knowledge_from=knowledge,
        published_at=None,
        source_revision=revision,
        parser_version=PARSER,
    )
    validate_holder_row(output)
    return {TABLE: [output]}


def audit_holder_exact_scope(
    root: Path, descriptor: dict[str, Any], config: Path
) -> dict[str, Any]:
    """Bounded cold audit: local version, all values, row evidence, lineage and keys.

    This is a holder observation scope, not whole-model/source/PIT admission. The
    complete-image global verifier must still check the native union and parents.
    No archived source_root string is dereferenced by this verifier.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22_output_audit import _audit_observation_stage

    root, config = physical_path(root), physical_path(config)
    if config != root / "observations" / descriptor["label"] / "contract":
        raise BoundedError("HOLDER_EXACT_LOCAL_CONTRACT_REQUIRED")
    required = {
        "base22-holder-exact.v2.json",
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    }
    if set(descriptor.get("contract_members", {})) != required or set(_tree(config)) != required:
        raise BoundedError("HOLDER_EXACT_CONTRACT_MEMBERS_INVALID")
    for name, sha in descriptor["contract_members"].items():
        path = physical_path(config / name)
        if path.stat().st_nlink != 1 or file_sha(path) != sha:
            raise BoundedError("HOLDER_EXACT_CONTRACT_HASH_MISMATCH")
    contract, schema = load_holder_contract(config)
    if (
        descriptor.get("schema_contract_id") != CONTRACT_ID
        or descriptor.get("schema_contract_sha256")
        != file_sha(config / "base22-holder-exact.v2.json")
        or set(descriptor["output_rows"]) != {TABLE}
        or not 0 < descriptor["output_rows"][TABLE] <= contract["maximum_scope_rows"]
        or descriptor.get("input_kind") not in {"synthetic", "retained_raw_governance"}
        or descriptor.get("input_kind") == "retained_raw_governance"
        and "raw_origin_bindings" not in descriptor
    ):
        raise BoundedError("HOLDER_EXACT_SCOPE_INVALID")
    receipt = _audit_observation_stage(root, descriptor, config)
    legacy, _, _ = load_contract(config)
    rule = legacy["tables"][TABLE]
    admitted = {
        (item["record_source"], item["revision"], item["sha256"]) for item in descriptor["inputs"]
    }
    evidence = descriptor["precision_evidence"]
    if len(evidence) != descriptor["output_rows"][TABLE]:
        raise BoundedError("HOLDER_EXACT_EVIDENCE_CENSUS_INVALID")
    expected = {item["row_sha256"]: item for item in evidence}
    if len(expected) != len(evidence):
        raise BoundedError("HOLDER_EXACT_EVIDENCE_DUPLICATE")
    versions: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    origin_rows = {}
    if "raw_origin_bindings" in descriptor:
        bindings = descriptor["raw_origin_bindings"]
        if len(bindings) != len(evidence):
            raise BoundedError("HOLDER_EXACT_RAW_BINDING_CENSUS_INVALID")
        origins = {b["row_sha256"]: b for b in bindings}
        if len(origins) != len(bindings):
            raise BoundedError("HOLDER_EXACT_RAW_BINDING_DUPLICATE")
        archives = {a["path"]: a for a in descriptor["archived_true_source_observations"]}
        for path, archive in archives.items():
            index = 0
            for batch in pq.ParquetFile(root / path).iter_batches(batch_size=1024):
                for raw in batch.to_pylist():
                    origin_rows[path, index] = raw
                    index += 1
                    if len(origin_rows) > contract["maximum_scope_rows"]:
                        raise BoundedError("HOLDER_EXACT_RAW_SCOPE_LIMIT")
            if index != archive["rows"]:
                raise BoundedError("HOLDER_EXACT_RAW_ARCHIVE_CENSUS_INVALID")
    for item in descriptor["outputs"]:
        file_keys = []
        for batch in pq.ParquetFile(root / item["path"]).iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                actual = validate_holder_row(row)
                _check_row(row, rule, schema, admitted)
                if partition(row, rule) != item["partition"]:
                    raise BoundedError("HOLDER_EXACT_PARTITION_MISMATCH")
                if expected.pop(actual["row_sha256"], None) != actual:
                    raise BoundedError("HOLDER_EXACT_PRECISION_EVIDENCE_MISMATCH")
                if "raw_origin_bindings" in descriptor:
                    origin = origins.pop(actual["row_sha256"], None)
                    if origin is None:
                        raise BoundedError("HOLDER_EXACT_RAW_ORIGIN_MISSING")
                    raw = origin_rows.get((origin["archive_path"], origin["raw_row_index"]))
                    if raw is None or digest(raw) != origin["raw_row_sha256"]:
                        raise BoundedError("HOLDER_EXACT_RAW_ORIGIN_HASH_MISMATCH")
                    rebuilt = exact_holder_observation(
                        raw,
                        company_id=row["company_id"],
                        security_id=row["security_id"],
                        parent_knowledge=datetime.fromisoformat(origin["parent_knowledge_from"]),
                        source_sha256=archives[origin["archive_path"]]["sha256"],
                        revision=row["source_revision"],
                        contract_id=CONTRACT_ID,
                    )[TABLE][0]
                    rebuilt["knowledge_to"] = row["knowledge_to"]
                    if rebuilt != row:
                        raise BoundedError("HOLDER_EXACT_RAW_TO_OUTPUT_MISMATCH")
                key = digest([row[k] for k in rule["version_key"]])
                if key in versions:
                    raise BoundedError("HOLDER_EXACT_DUPLICATE_VERSION")
                versions.add(key)
                file_keys.append(key)
                business = digest([row[k] for k in rule["business_key"]])
                groups.setdefault(business, []).append(row)
        if digest(sorted(file_keys)) != item["version_key_census_sha256"]:
            raise BoundedError("HOLDER_EXACT_KEY_CENSUS_MISMATCH")
    if expected:
        raise BoundedError("HOLDER_EXACT_EVIDENCE_UNUSED")
    if "raw_origin_bindings" in descriptor and origins:
        raise BoundedError("HOLDER_EXACT_RAW_ORIGIN_UNUSED")
    for rows in groups.values():
        rows.sort(key=lambda r: r["knowledge_from"])
        if any(
            a["knowledge_to"] is None or a["knowledge_to"] > b["knowledge_from"]
            for a, b in zip(rows, rows[1:], strict=False)
        ):
            raise BoundedError("HOLDER_EXACT_KNOWLEDGE_VERSION_OVERLAP")
    return {
        **receipt,
        "exact_column_semantics": "PASS",
        "version_count": len(versions),
        "local_raw_reproduction": "PASS" if "raw_origin_bindings" in descriptor else "SYNTHETIC",
    }
