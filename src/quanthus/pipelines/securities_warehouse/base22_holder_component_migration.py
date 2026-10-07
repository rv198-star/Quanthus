"""One immutable component projection feeds every selected native-group repair.

The caller binds the qualified typed source descriptor and cold receipt. Formal
offline reproduction rebuilds each raw observation once before this consumer.
No full-category admission, exit inference or fabricated historical acceptance.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget
from .base22_financial import _clock
from .base22_holder_chronology import _native_origin
from .base22_holder_column_revision import _KEYS, TABLE
from .base22_holder_components import (
    CONTRACT_ID,
    component_key,
    load_component_contract,
    project_holder_components,
)
from .base22_sources import _tree
from .financial_reporting import (
    FinancialCompanyResolver,
    financial_company_resolver_from_listed_company_rows,
)
from .ownership_capital import ownership_capital_schemas

MIGRATION_ID = "p10-base22-qualified-holder-component-group-migration-v1"
REPLAY_MODULES = (
    "base22.py",
    "base22_bounded.py",
    "base22_sources.py",
    "base22_precision.py",
    "base22_financial.py",
    "base22_f10_ownership.py",
    "base22_holder_literals.py",
    "base22_holder_column_revision.py",
    "base22_holder_column_continuation.py",
    "base22_holder_chronology.py",
    "base22_holder_components.py",
    "base22_holder_component_migration.py",
    "financial_reporting.py",
    "ownership_capital.py",
)


def prepare_retained_components(
    later_raw: list[tuple[str, dict[str, Any]]],
    *,
    resolver: FinancialCompanyResolver,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Formal reproduction only; live consumers use the shared qualified TMP."""
    if not 0 < len(later_raw) <= 4:
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_WEEKLY_BOUND_INVALID")
    return [
        {"raw": raw, "member_sha256": sha, "rows": result[TABLE], "proof": proof}
        for sha, raw in later_raw
        for result, proof in [
            project_holder_components(
                raw,
                source_sha256=sha,
                resolver=resolver,
                contract=contract,
            )
        ]
    ]


def qualify_component_migrations(
    native: list[dict[str, Any]],
    *,
    original_raw: dict[str, Any],
    original_member_sha256: str,
    retained_components: list[dict[str, Any]],
    source_scope_identity: str,
    resolver: FinancialCompanyResolver,
    contract: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Preserve each exact original row, and close/open one literal holder group.

    `retained_components` belongs to a closed, independently replayed scope. Its
    complete row/proof/raw bindings are checked here; cold verification replays
    originals, including all frames and held dispositions, before calling again.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    if (
        not 0 < len(native) <= 80
        or not 0 < len(retained_components) <= 4
        or contract.get("contract_id") != CONTRACT_ID
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_scope_identity) is None
        or original_raw.get("category") != "股东研究"
        or not isinstance(original_raw.get("content"), str)
        or original_raw.get("content_sha256")
        != "sha256:" + hashlib.sha256(original_raw["content"].encode()).hexdigest()
        or re.fullmatch(r"sha256:[0-9a-f]{64}", original_member_sha256) is None
    ):
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_INPUT_BOUND_OR_CONTRACT_INVALID")
    observed, original_clock = _clock(observed_at), _clock(original_raw["acquired_at"])
    original = _native_origin(original_raw, original_member_sha256, resolver)
    clocks: dict[datetime, str] = {}
    ordered = []
    for item in retained_components:
        raw, rows, proof = item["raw"], item["rows"], item["proof"]
        actual = _clock(raw["acquired_at"])
        if (
            proof.get("contract_id") != CONTRACT_ID
            or proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
            or proof.get("raw_row_sha256") != digest(raw)
            or proof.get("typed_rows_sha256") != digest(rows)
            or proof.get("contract_sha256") != digest(contract)
            or proof.get("source_member_sha256") != item["member_sha256"]
            or _clock(proof["actual_acquisition"]) != actual
            or raw["stock_code"] != original_raw["stock_code"]
            or raw["source"] != original_raw["source"]
            or not original_clock < actual < observed
            or any(_clock(r["knowledge_from"]) >= observed for r in rows)
        ):
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_SHARED_RAW_PROOF_OR_CHRONOLOGY_CHANGED")
        if actual in clocks and clocks[actual] != digest(raw):
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_EQUAL_CLOCK_CONFLICT")
        clocks[actual] = digest(raw)
        ordered.append((actual, digest(raw), item))
    ordered.sort(key=lambda x: (x[0], x[1]))
    latest = ordered[-1][2]
    base = {
        "contract_id": MIGRATION_ID,
        "component_contract_sha256": digest(contract),
        "source_scope_identity": source_scope_identity,
        "observed_at": observed,
        "original_raw_sha256": digest(original_raw),
        "original_member_sha256": original_member_sha256,
        "native_rows_sha256": digest(native),
        "all_retained_proof_identities": sorted(
            i["proof"]["identity"] for i in retained_components
        ),
        "evaluator_sha256": file_sha(Path(__file__)),
    }
    base_identity = digest(base)
    revisions, held = {}, {}
    seen = set()
    schema = ownership_capital_schemas()[TABLE]
    for before in native:
        key = digest([before[k] for k in _KEYS])
        if key in seen:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_DUPLICATE_NATIVE_GROUP")
        seen.add(key)
        try:
            if (
                original.get(key) != before
                or before["knowledge_to"] is not None
                or _clock(before["knowledge_from"]) >= observed
            ):
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_EXACT_NATIVE_BINDING_FAILED")
            day = (
                before["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            )
            for _, _, item in ordered:
                frames = [
                    f
                    for f in item["proof"]["frames"]
                    if f.get("business_date") == day
                    and f.get("holding_type") == before["holding_type"]
                ]
                if len(frames) != 1 or frames[0]["status"] != "QUALIFIED_COMPLETE_MATRIX":
                    raise BoundedError(
                        "HOLDER_COMPONENT_MIGRATION_TARGET_FRAME_MISSING_OR_UNQUALIFIED"
                    )
                group = [r for r in item["rows"] if digest([r[k] for k in _KEYS]) == key]
                if not group:
                    raise BoundedError("HOLDER_COMPONENT_MIGRATION_LATER_OMISSION_NOT_EXIT_OR_ZERO")
            group = [r for r in latest["rows"] if digest([r[k] for k in _KEYS]) == key]
            if len({component_key(r) for r in group}) != len(group) or len(group) > 6:
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_LITERAL_COMPONENT_SET_INVALID")
            item_proof = {
                "input_identity": base_identity,
                "native_key_sha256": key,
                "old_row_sha256": digest(before),
                "latest_literal_rows_sha256": digest(group),
                "latest_actual_acquisition": ordered[-1][0],
                "new_governance_clock": observed,
            }
            identity = digest(item_proof)
            output = [{**before, "knowledge_to": observed}] + [
                {
                    **r,
                    "knowledge_from": observed,
                    "knowledge_to": None,
                    "source_artifact_sha256": identity,
                    "source_revision": MIGRATION_ID + ":" + identity[7:],
                }
                for r in group
            ]
            if pa.Table.from_pylist(output, schema=schema).to_pylist() != output:
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_SERIALIZED_ATOM_CHANGED")
            revisions[key] = {
                **item_proof,
                "identity": identity,
                "output": output,
                "old_atom_preserved_except_knowledge_to": True,
                "PIT_admission": False,
            }
        except BoundedError as error:
            held[key] = str(error)
    result = {
        "input_closure": base,
        "input_identity": base_identity,
        "revisions": revisions,
        "held": held,
        "original_parses_built": 1,
        "shared_weekly_projection": "REUSED",
        "classification_key_move": "CLOSE_OLD_GROUP_OPEN_LITERAL_NEW_COMPONENTS",
        "consumer_admission": "NOT_GRANTED",
        "whole_category_or_union": "SEPARATE_GATE",
    }
    result["identity"] = digest(result)
    return result


def replay_migration_inputs(
    root: Path,
    body: dict[str, Any],
    budget: Budget,
    consume: Callable[..., None],
) -> dict[str, Any]:
    """Rebuild the bounded selected source TMP once per stock in a new process."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    contract = load_component_contract(root / "contract")
    _, model, extension = load_contract(root / "contract")
    physical = schemas(model, extension)
    parents = {}
    for table in ("dwd_security", "dwd_security_identifier"):
        rows = []
        if sum(i["rows"] for i in body["parents"][table]) > 32768:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_PARENT_BOUND_INVALID")
        for item in body["parents"][table]:
            pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
            if pf.metadata.num_rows != item["rows"] or not pf.schema_arrow.equals(
                physical[table], check_metadata=False
            ):
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_PARENT_SCHEMA_CHANGED")
            rows.extend(pf.read().to_pylist())
        parents[table] = rows
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    schema = physical[TABLE]
    native = pq.ParquetFile(_physical(root, body["native"]["path"], independent=True))
    if (
        native.metadata.num_rows != body["native"]["rows"]
        or not 0 < native.metadata.num_rows <= 10000
        or not native.schema_arrow.equals(schema, check_metadata=False)
    ):
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_NATIVE_SCHEMA_OR_BOUND_INVALID")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in native.read().to_pylist():
        code = row["source_record_id"].split("&", 1)[0].removeprefix("stock_code=")
        grouped[code].append(row)
    if set(grouped) != set(body["selected_codes"]):
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_EXACT_SELECTED_CODES_CHANGED")
    original, later = {}, defaultdict(list)
    selected = set()
    for item in body["raw_inputs"]:
        pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if pf.metadata.num_rows != item["rows"] or pf.metadata.num_rows != len(grouped):
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_RAW_CENSUS_CHANGED")
        for raw in pf.read().to_pylist():
            code = raw["stock_code"]
            if (
                code not in grouped
                or (item["day"], code) in selected
                or raw["category"] != "股东研究"
            ):
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_RAW_SELECTION_CHANGED")
            selected.add((item["day"], code))
            if digest(raw) != item["raw_row_sha256s"][code]:
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_EXACT_RAW_ROW_CHANGED")
            if item["original"]:
                original[code] = (item["original_member_sha256"], raw)
            else:
                later[code].append((item["original_member_sha256"], raw))
    summary: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for code, before in sorted(grouped.items()):
        budget.check()
        sha, raw = original[code]
        prepared = prepare_retained_components(later[code], resolver=resolver, contract=contract)
        proof = qualify_component_migrations(
            before,
            original_raw=raw,
            original_member_sha256=sha,
            retained_components=prepared,
            source_scope_identity=body["source_scope_identity"],
            resolver=resolver,
            contract=contract,
            observed_at=_clock(body["observed_at"]),
        )
        consume(code, proof)
        summary.update(
            {
                "selected_stocks": 1,
                "selected_keys": len(before),
                "qualified_keys": len(proof["revisions"]),
                "held_keys": len(proof["held"]),
                "output_rows": sum(len(i["output"]) for i in proof["revisions"].values()),
            }
        )
        reasons.update(proof["held"].values())
    return {
        **dict(summary),
        "held_reasons": dict(reasons),
        "complete_native_union": "NOT_QUALIFIED",
    }


def audit_component_migrations(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Exact original/raw/schema/Decimal/time/old-new group reproduction offline."""
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != MIGRATION_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
        or set(body["code_closure"]) != set(REPLAY_MODULES)
    ):
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_DESCRIPTOR_CHANGED")
    for name, sha in body["code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_CODE_CHANGED")
    for relative, member in body["physical_members"].items():
        p = _physical(root, relative, independent=True)
        if file_sha(p) != member["sha256"] or p.stat().st_size != member["size_bytes"]:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_FILE_CHANGED")
        budget.check()
    _, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]
    checked = set()

    def compare(code: str, proof: dict[str, Any]) -> None:
        expected = [row for _, item in sorted(proof["revisions"].items()) for row in item["output"]]
        item = body["stock_outputs"].get(code)
        if item is None or item["proof_identity"] != proof["identity"]:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_REQUALIFICATION_CHANGED")
        if expected:
            pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
            if (
                not pf.schema_arrow.equals(schema, check_metadata=False)
                or pf.read().to_pylist() != expected
            ):
                raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_SERIALIZED_OUTPUT_CHANGED")
        elif item["path"] is not None:
            raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_UNQUALIFIED_OUTPUT_PRESENT")
        checked.add(code)

    result = replay_migration_inputs(root, body, budget, compare)
    if checked != set(body["stock_outputs"]) or result != body["replay_summary"]:
        raise BoundedError("HOLDER_COMPONENT_MIGRATION_OFFLINE_EXACT_KEY_CENSUS_CHANGED")
    return {
        "status": "LOCAL_FULL_SELECTED_RAW_AND_COMPONENT_GROUP_REPLAY_PASS_NOT_COMPLETE_UNION",
        **result,
    }
