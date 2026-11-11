"""Bounded original-raw replay of a selected holder text-restoration scope.

Complete source files are local evidence. This is neither the full holder union
nor permission to discard nonempty unqualified source categories.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget
from .base22_financial import _clock
from .base22_holder_column_continuation import holder_observation
from .base22_holder_column_revision import CONTRACT_ID, TABLE, qualify_column_restorations
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows
from .ownership_capital import OwnershipCapitalBuildError

PURPOSE = "ORIGINAL_COLUMN_TEXT_RESTORATION_SELECTED_SCOPE_NOT_COMPLETE_UNION"
REPLAY_MODULES = (
    "base22.py",
    "base22_bounded.py",
    "base22_financial.py",
    "base22_sources.py",
    "base22_holder_column_continuation.py",
    "base22_holder_literals.py",
    "base22_holder_column_revision.py",
    "base22_holder_column_replay.py",
    "financial_reporting.py",
    "ownership_capital.py",
)


def replay_selected_inputs(
    root: Path, body: dict[str, Any], budget: Budget, consume: Callable[..., None]
) -> dict[str, Any]:
    """Once per selected stock, reproduce originals and every retained witness."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    contract, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]
    rule = contract["tables"][TABLE]
    selection = json.loads(_physical(root, body["selection"], independent=True).read_bytes())
    keys = {i["business_key_sha256"] for i in selection}
    codes = {}
    for item in selection:
        cc = item["stock_codes_from_original_provenance"]
        if len(cc) != 1 or not re.fullmatch(r"\d{6}", cc[0]):
            raise BoundedError("HOLDER_COLUMN_LOCAL_SELECTION_IDENTITY_INVALID")
        codes[item["business_key_sha256"]] = cc[0]
    if len(keys) != len(selection) or not 0 < len(keys) <= 10000:
        raise BoundedError("HOLDER_COLUMN_LOCAL_SELECTION_KEY_BOUND_INVALID")
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"native": [], "weekly": []}
    )
    for origin in ("native", "weekly"):
        item = body["inputs"][origin]
        path = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(path)
        if (
            pf.metadata.num_rows != item["rows"]
            or not 0 < item["rows"] <= 100000
            or file_sha(path) != item["sha256"]
            or not pf.schema_arrow.equals(schema, check_metadata=False)
        ):
            raise BoundedError("HOLDER_COLUMN_LOCAL_TYPED_INPUT_CHANGED")
        for batch in pf.iter_batches(batch_size=1024):
            budget.check()
            for row in batch.to_pylist():
                key = digest([row[k] for k in rule["business_key"]])
                code = codes.get(key)
                if code is None or not row["source_record_id"].startswith(f"stock_code={code}&"):
                    raise BoundedError("HOLDER_COLUMN_LOCAL_UNREGISTERED_KEY_OR_IDENTITY")
                grouped[code][origin].append(row)
    original, later = {}, defaultdict(list)
    raw_members = body["raw_inputs"]
    if (
        not 2 <= len(raw_members) <= 5
        or len({i["day"] for i in raw_members}) != len(raw_members)
        or sum(i["original"] is True for i in raw_members) != 1
    ):
        raise BoundedError("HOLDER_COLUMN_LOCAL_RAW_SOURCE_SET_INVALID")
    for item in raw_members:
        path = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(path)
        if (
            file_sha(path) != item["source_sha256"]
            or pf.metadata.num_rows != item["rows"]
            or not 0 < item["rows"] <= 100000
            or path.stat().st_size > 512 * 1024**2
        ):
            raise BoundedError("HOLDER_COLUMN_LOCAL_FULL_SOURCE_CHANGED")
        seen = set()
        for batch in pf.iter_batches(batch_size=32):
            budget.check()
            for raw in batch.to_pylist():
                code = raw["stock_code"]
                if raw["category"] != "股东研究" or code not in grouped:
                    continue
                if code in seen:
                    raise BoundedError("HOLDER_COLUMN_LOCAL_DUPLICATE_RAW_STOCK")
                seen.add(code)
                source = {"raw": raw, "source_sha256": item["source_sha256"]}
                if item["original"]:
                    original[code] = source
                else:
                    later[code].append(source)
    parents = {}
    for table in ("dwd_security", "dwd_security_identifier"):
        items = body["parents"][table]
        if sum(i["rows"] for i in items) > 32768:
            raise BoundedError("HOLDER_COLUMN_LOCAL_IDENTITY_PARENT_BOUND")
        parents[table] = [
            r
            for i in items
            for r in pq.ParquetFile(_physical(root, i["path"], independent=True)).read().to_pylist()
        ]
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    held: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    total: Counter[str] = Counter()
    native_keys: set[str] = set()
    for code, inputs in sorted(grouped.items()):
        budget.check()
        nn, ww = inputs["native"], inputs["weekly"]
        own = {digest([r[k] for k in rule["business_key"]]) for r in nn}
        if len(own) != len(nn) or not own or code not in original:
            raise BoundedError("HOLDER_COLUMN_LOCAL_EXACT_NATIVE_ORIGINAL_SELECTION_INVALID")
        native_keys.update(own)
        clocks = {}
        projected = []
        for item in later[code]:
            raw = item["raw"]
            try:
                rows, _ = holder_observation(
                    raw, source_sha256=item["source_sha256"], resolver=resolver
                )
            except (BoundedError, OwnershipCapitalBuildError):
                # This exact source category remains held. Its bytes are still
                # archived and cannot authorize typed witnesses or a repair.
                continue
            selected = [
                r for r in rows[TABLE] if digest([r[k] for k in rule["business_key"]]) in own
            ]
            projected.extend(selected)
            for row in selected:
                clocks[(item["source_sha256"], row["source_record_id"])] = _clock(
                    raw["acquired_at"]
                )
        if Counter(digest(r) for r in projected) != Counter(digest(r) for r in ww):
            raise BoundedError("HOLDER_COLUMN_LOCAL_EXACT_WEEKLY_RAW_REPLAY_CHANGED")
        try:
            proof = qualify_column_restorations(
                nn,
                ww,
                original_raw=original[code]["raw"],
                original_member_sha256=original[code]["source_sha256"],
                actual_weekly_acquisitions=clocks,
                resolver=resolver,
                rule=rule,
                observed_at=_clock(body["observed_at"]),
            )
        except (BoundedError, OwnershipCapitalBuildError) as exc:
            proof = {"revisions": {}, "held": {key: str(exc) for key in sorted(own)}}
            proof["identity"] = digest(proof)
        for revision in proof["revisions"].values():
            changed.update(revision["changed_fields"])
        held.update(proof["held"].values())
        total["qualified_keys"] += len(proof["revisions"])
        total["held_keys"] += len(proof["held"])
        consume(code, proof, nn, ww)
    if native_keys != keys or sum(total.values()) != len(keys):
        raise BoundedError("HOLDER_COLUMN_LOCAL_EXACT_SELECTED_KEYS_NOT_ACCOUNTED")
    return {
        "selected_keys": len(keys),
        "selected_stocks": len(grouped),
        **dict(total),
        "changed_field_counts": dict(changed),
        "held_reasons": dict(held),
        "historical_PIT_admission": False,
        "complete_Base22": False,
    }


def audit_column_restoration_scope(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Exact files, raw requalification and every serialized old/new atom offline."""
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False for k in ("READY", "complete_Base22"))
    ):
        raise BoundedError("HOLDER_COLUMN_OFFLINE_SELECTED_SCOPE_INVALID")
    if _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("HOLDER_COLUMN_OFFLINE_PHYSICAL_SET_CHANGED")
    if set(body["code_closure"]) != set(REPLAY_MODULES):
        raise BoundedError("HOLDER_COLUMN_OFFLINE_EVALUATOR_CLOSURE_INCOMPLETE")
    for name, expected in body["code_closure"].items():
        if Path(name).name != name or file_sha(Path(__file__).with_name(name)) != expected:
            raise BoundedError("HOLDER_COLUMN_OFFLINE_EVALUATOR_CLOSURE_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("HOLDER_COLUMN_OFFLINE_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    rule, schema = contract["tables"][TABLE], schemas(model, extension)[TABLE]
    outputs = body["stock_outputs"]
    checked = set()

    def compare(code: str, proof: dict[str, Any], native: Any, weekly: Any) -> None:
        from .base22_holder_column_revision import apply_column_restoration

        item = outputs.get(code)
        if item is None or code in checked or item["qualification_identity"] != proof["identity"]:
            raise BoundedError("HOLDER_COLUMN_OFFLINE_REQUALIFIED_STOCK_CHANGED")
        expected = []
        for key, revision in sorted(proof["revisions"].items()):
            group = [
                r for r in native + weekly if digest([r[k] for k in rule["business_key"]]) == key
            ]
            expected.extend(apply_column_restoration(group, rule, revision))
        if expected:
            pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
            if (
                not pf.schema_arrow.equals(schema, check_metadata=False)
                or pf.read().to_pylist() != expected
            ):
                raise BoundedError("HOLDER_COLUMN_OFFLINE_SERIALIZED_VERSION_ATOM_CHANGED")
        elif item.get("path") is not None:
            raise BoundedError("HOLDER_COLUMN_OFFLINE_UNQUALIFIED_OUTPUT_PRESENT")
        checked.add(code)

    result = replay_selected_inputs(root, body, budget, compare)
    if checked != set(outputs) or result != body["replay_summary"]:
        raise BoundedError("HOLDER_COLUMN_OFFLINE_EXACT_STOCK_ACCOUNTING_CHANGED")
    return {"status": "ALL_LOCAL_RAW_COLUMN_RESTORATIONS_REPLAY_PASS_NOT_COMPLETE_UNION", **result}
