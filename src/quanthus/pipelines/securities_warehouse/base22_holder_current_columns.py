"""Restore literal columns from the exact raw underlying a current native row.

This is a new governance revision, not a priority over an older/later source.
Only unique unsplit groups with unchanged scalar and time atoms qualify.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_financial import _clock
from .base22_holder_chronology import _native_origin
from .base22_holder_column_revision import _KEYS, _META, TABLE, _key
from .base22_holder_components_v2 import load_component_contract, project_holder_components
from .base22_holder_components_v2_replay import REPLAY_MODULES as COMPONENT_MODULES
from .base22_sources import _tree
from .financial_reporting import (
    FinancialCompanyResolver,
    financial_company_resolver_from_listed_company_rows,
)
from .ownership_capital import ownership_capital_schemas

CONTRACT_ID = "p10-base22-exact-current-raw-holder-column-restoration-v1"
PURPOSE = "EXACT_CURRENT_RAW_LITERAL_COLUMNS_NOT_COMPLETE_WEEKLY_OR_BASE22"
REPLAY_MODULES = (*COMPONENT_MODULES, "base22_holder_chronology.py", Path(__file__).name)


def qualify_current_columns(
    current: list[dict[str, Any]],
    *,
    original_raw: dict[str, Any],
    original_member_sha256: str,
    resolver: FinancialCompanyResolver,
    component_contract: dict[str, Any],
    rule: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Parse once per stock; retain later literal quantity/percentage tokens."""
    import pyarrow as pa  # type: ignore[import-untyped]

    observed = _clock(observed_at)
    if (
        not 0 < len(current) <= 80
        or rule.get("business_key") != _KEYS
        or rule.get("version_key") != _KEYS + ["knowledge_from"]
        or len({_key(r) for r in current}) != len(current)
        or any(r.get("knowledge_to") is not None for r in current)
    ):
        raise BoundedError("HOLDER_CURRENT_COLUMNS_SELECTED_MODEL_OR_BOUND_INVALID")
    projection, raw_proof = project_holder_components(
        original_raw,
        source_sha256=original_member_sha256,
        resolver=resolver,
        contract=component_contract,
    )
    legacy = _native_origin(original_raw, original_member_sha256, resolver)
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection[TABLE]:
        by_key[_key(row)].append(row)
    closure = {
        "contract_id": CONTRACT_ID,
        "observed_at": observed,
        "original_member_sha256": original_member_sha256,
        "raw_row_sha256": digest(original_raw),
        "original_content_sha256": original_raw["content_sha256"],
        "actual_acquisition": raw_proof["actual_acquisition"],
        "component_projection_identity": raw_proof["identity"],
        "component_contract_sha256": digest(component_contract),
        "native_current_row_census": dict(Counter(digest(r) for r in current)),
        "rule_sha256": digest(rule),
        "evaluator_sha256": file_sha(Path(__file__)),
    }
    identity = digest(closure)
    revisions, held = {}, {}
    for before in current:
        key = _key(before)
        try:
            day = before["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date()
            frames = [
                f
                for f in raw_proof["frames"]
                if f.get("business_date") == day.isoformat()
                and f.get("holding_type") == before["holding_type"]
            ]
            candidates = by_key.get(key, [])
            if (
                legacy.get(key) != before
                or len(candidates) != 1
                or len(frames) != 1
                or frames[0]["status"] != "QUALIFIED_COMPLETE_MATRIX"
                or observed <= _clock(before["knowledge_from"])
                or observed <= raw_proof["actual_acquisition"]
            ):
                raise BoundedError("HOLDER_CURRENT_COLUMNS_EXACT_RAW_CURRENT_FRAME_UNPROVED")
            after = candidates[0]
            changed = {
                k for k in before if k not in _META | {"parser_version"} and before[k] != after[k]
            }
            if not changed or not changed <= {"share_nature", "holder_category"}:
                raise BoundedError("HOLDER_CURRENT_COLUMNS_NON_LITERAL_COLUMN_CHANGE_FORBIDDEN")
            # Complete same raw, same key and exact scalar/time atoms. No
            # selection of a previous snapshot, rounding or basis conversion.
            new = {
                **after,
                "knowledge_from": observed,
                "source_revision": CONTRACT_ID + ":" + identity.removeprefix("sha256:"),
                "source_artifact_sha256": identity,
            }
            output = [{**before, "knowledge_to": observed}, new]
            if (
                pa.Table.from_pylist(output, schema=ownership_capital_schemas()[TABLE]).to_pylist()
                != output
            ):
                raise BoundedError("HOLDER_CURRENT_COLUMNS_SERIALIZED_ATOM_CHANGED")
            item = {
                "contract_id": CONTRACT_ID,
                "input_identity": identity,
                "business_key_sha256": key,
                "before_row_sha256": digest(before),
                "exact_raw_projected_row_sha256": digest(after),
                "changed_fields": sorted(changed),
                "selected_frame_sha256": frames[0]["source_block_sha256"],
                "output": output,
            }
            item["identity"] = digest(item)
            revisions[key] = item
        except BoundedError as error:
            held[key] = str(error)
    result = {
        **closure,
        "input_identity": identity,
        "revisions": revisions,
        "held": held,
        "provider_priority_granted": False,
        "numeric_or_percentage_token_changes_granted": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result


def audit_current_columns(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Replay all selected raw/current atoms using only image-local evidence."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False for k in ("READY", "complete_Base22"))
        or set(body.get("code_closure", {})) != set(REPLAY_MODULES)
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("HOLDER_CURRENT_COLUMNS_DESCRIPTOR_OR_PHYSICAL_SET_CHANGED")
    for name, sha in body["code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("HOLDER_CURRENT_COLUMNS_CODE_CLOSURE_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != member["sha256"] or path.stat().st_size != member["size_bytes"]:
            raise BoundedError("HOLDER_CURRENT_COLUMNS_MEMBER_CHANGED")
        budget.check()
    source = json.loads((root / "evidence/component-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/component-source-cold.json").read_bytes())
    if (
        source["identity"] != body["source_identity"]
        or source["identity"] != digest({k: v for k, v in source.items() if k != "identity"})
        or cold["scope_identity"] != source["identity"]
        or not cold["source_unchanged"]
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
        or body["parents"] != source["parents"]
        or not 0 < len(body["stock_outputs"]) <= 8
    ):
        raise BoundedError("HOLDER_CURRENT_COLUMNS_PINNED_RAW_QUALIFICATION_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    parent_rows: dict[str, list[dict[str, Any]]] = {}
    for name in ("dwd_security", "dwd_security_identifier"):
        members = body["parents"][name]
        if sum(i["rows"] for i in members) > 32768:
            raise BoundedError("HOLDER_CURRENT_COLUMNS_PARENT_BOUND_CHANGED")
        parent_rows[name] = []
        for item in members:
            path = _physical(root, item["path"], independent=True)
            pf = pq.ParquetFile(path)
            if (
                not pf.schema_arrow.equals(ss[name], check_metadata=False)
                or pf.metadata.num_rows != item["rows"]
                or file_sha(path) != source["physical_members"][item["path"]]["sha256"]
            ):
                raise BoundedError("HOLDER_CURRENT_COLUMNS_PARENT_SCHEMA_OR_ATOMS_CHANGED")
            parent_rows[name].extend(pf.read().to_pylist())
    resolver = financial_company_resolver_from_listed_company_rows(
        parent_rows["dwd_security"], parent_rows["dwd_security_identifier"]
    )
    component_contract = load_component_contract(root / "contract")
    source_bindings = {}
    wanted_codes = set(body["stock_outputs"])
    for original_path, copied_path in body["component_raw_proof_members"].items():
        path = _physical(root, copied_path, independent=True)
        if file_sha(path) != source["physical_members"][original_path]["sha256"]:
            raise BoundedError("HOLDER_CURRENT_COLUMNS_ORIGINAL_PROOF_MEMBER_CHANGED")
        with path.open("rb") as stream:
            for line in stream:
                if len(line) > 4 * 1024**2:
                    raise BoundedError("HOLDER_CURRENT_COLUMNS_RAW_PROOF_BOUND_EXCEEDED")
                budget.check()
                entry = json.loads(line)
                if entry["stock_code"] in wanted_codes:
                    binding_key = (original_path, entry["stock_code"])
                    if binding_key in source_bindings:
                        raise BoundedError("HOLDER_CURRENT_COLUMNS_DUPLICATE_RAW_PROOF")
                    source_bindings[binding_key] = entry
    total = 0
    keys: set[str] = set()
    lineage: set[tuple[str, str, str]] = set()
    paths = []
    for code, item in body["stock_outputs"].items():
        raw = pq.ParquetFile(root / item["raw_path"]).read().to_pylist()
        if len(raw) != 1 or raw[0]["stock_code"] != code:
            raise BoundedError("HOLDER_CURRENT_COLUMNS_RAW_SUBSET_CHANGED")
        binding = json.loads((root / item["source_raw_proof_path"]).read_bytes())
        if (
            source_bindings.get((item["original_proof_member"], code)) != binding
            or binding["proof"]["raw_row_sha256"] != digest(raw[0])
            or binding["stock_code"] != code
            or binding["proof"]["source_member_sha256"] != item["original_member_sha256"]
        ):
            raise BoundedError("HOLDER_CURRENT_COLUMNS_RAW_QUALIFIED_BINDING_CHANGED")
        current_pf = pq.ParquetFile(root / item["current_path"])
        result_pf = pq.ParquetFile(root / item["path"])
        if (
            not current_pf.schema_arrow.equals(ss[TABLE], check_metadata=False)
            or not result_pf.schema_arrow.equals(ss[TABLE], check_metadata=False)
            or not 0 < current_pf.metadata.num_rows <= 80
            or result_pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_CURRENT_COLUMNS_TYPED_SCHEMA_OR_CENSUS_CHANGED")
        result = qualify_current_columns(
            current_pf.read().to_pylist(),
            original_raw=raw[0],
            original_member_sha256=item["original_member_sha256"],
            resolver=resolver,
            component_contract=component_contract,
            rule=contract["tables"][TABLE],
            observed_at=_clock(body["observed_at"]),
        )
        if (
            result["identity"] != item["qualification_identity"]
            or result["held"]
            or json.loads((root / item["proof_path"]).read_bytes())["identity"]
            != result["identity"]
        ):
            raise BoundedError("HOLDER_CURRENT_COLUMNS_REPLAYED_QUALIFICATION_CHANGED")
        expected = [r for i in result["revisions"].values() for r in i["output"]]
        actual = result_pf.read().to_pylist()
        if Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected):
            raise BoundedError("HOLDER_CURRENT_COLUMNS_RAW_TO_OUTPUT_CHANGED")
        keys.update(result["revisions"])
        total += len(actual)
        paths.append(root / item["path"])
        lineage.update(
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"]) for r in actual
        )
    if len(keys) != body["qualified_keys"] or total != body["output_rows"]:
        raise BoundedError("HOLDER_CURRENT_COLUMNS_GLOBAL_ALLOWED_CENSUS_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add(TABLE, paths)
        rule = contract["tables"][TABLE]
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        scan.row_rules(TABLE, ss[TABLE], rule)
        scan.lineage(TABLE, list(lineage))
    receipt = {
        "status": "EXACT_CURRENT_RAW_LITERAL_COLUMNS_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22",
        "scope_identity": body["identity"],
        "qualified_keys": len(keys),
        "output_rows": total,
        "numeric_or_percentage_token_changes": 0,
        "original_times_preserved": True,
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
