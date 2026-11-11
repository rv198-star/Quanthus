"""Offline replay of two bounded additive governance supplements, not a new Base.

All replay inputs are physical local members. Archived source roots are inert.
The earlier sealed image and its consumer/knowledge qualifications are unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_sources import _tree
from .base23_calendar_authority import compare_retained_calendar, derive_date_authority
from .base23_dividend import TABLES, latest_literal_captures
from .base23_dividend_units import materialize_unit_additions
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base23-low-cost-additive-supplements-v1"
QUALIFICATION = "EXPLICIT_UNITS_AND_CURRENT_EXCHANGE_DATE_AUTHORITY_NOT_HISTORICAL_PIT"
CODE_MODULES = (
    "base22.py", "base22_bounded.py", "base22_sources.py", "base22_precision.py",
    "base22_outputs.py", "base22_financial.py", "base22_f10_dividend.py",
    "base23_sources.py", "base23_ownership_reconfirmation.py", "base23_dividend.py",
    "base23_calendar_authority.py", "base23_dividend_units.py", "base23_low_cost_audit.py",
    "daily_price_basis.py", "financial_reporting.py", "financial_concept_semantics.py",
    "disclosure_event.py", "industry_classification.py", "listed_company.py",
    "trading_calendar.py",
)


def code_closure() -> dict[str, str]:
    return {name: file_sha(Path(__file__).with_name(name)) for name in CODE_MODULES}


def runtime_identity() -> dict[str, str]:
    import duckdb
    import pyarrow  # type: ignore[import-untyped]

    return {"python": sys.version, "pyarrow": pyarrow.__version__, "duckdb": duckdb.__version__}


def _retained_scope(root: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
            or body.get("input_digest") != digest(body.get("input_closure"))
            or body.get("consumer_admission") != "NOT_GRANTED"):
        raise BoundedError("LOW_COST_RETAINED_SCOPE_IDENTITY_CHANGED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("LOW_COST_RETAINED_PHYSICAL_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("LOW_COST_RETAINED_PHYSICAL_HASH_CHANGED")
    return body


def project_unit_scope(
    retained: Path, *, observed_at: Any, check: Any,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """One immutable local dependency, latest real captures, exact keyed additions."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = _retained_scope(retained)
    contract, model, extension = load_contract(retained / "contract")
    ss = schemas(model, extension)
    vocabulary = json.loads(
        (retained / "contract/disclosure-event-vocabulary.v1.json").read_bytes())
    parents = {t: pq.ParquetFile(retained / i["path"]).read().to_pylist()
               for t, i in body["parents"].items()}
    if set(parents) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("LOW_COST_PARENT_CLOSURE_REQUIRED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"])
    observations = []
    for source, local in zip(body["input_closure"]["sources"], body["sources"], strict=True):
        manifest = json.loads((retained / local["manifest"]).read_bytes())
        if (file_sha(retained / local["manifest"]) != source["manifest_sha256"]
                or manifest["release_id"] != source["release"]
                or manifest["status"] != "ready"
                or not any(a["relative_path"] == source["path"]
                           and a["sha256"] == source["sha256"] for a in manifest["artifacts"])):
            raise BoundedError("LOW_COST_ORIGINAL_MEMBER_IDENTITY_CHANGED")
        bindings = json.loads((retained / local["bindings"]).read_bytes())
        raws = pq.ParquetFile(retained / local["raw"]).read().to_pylist()
        if len(raws) != len(bindings) or len({b["ordinal"] for b in bindings}) != len(bindings):
            raise BoundedError("LOW_COST_COMPLETE_CAPTURE_BINDINGS_CHANGED")
        for raw, binding in zip(raws, bindings, strict=True):
            if (digest(raw) != binding["complete_raw_row_sha256"]
                    or binding["member_sha256"] != source["sha256"]
                    or binding["release"] != source["release"]
                    or not 0 <= binding["ordinal"] < source["rows"]):
                raise BoundedError("LOW_COST_ORIGINAL_ROW_BINDING_CHANGED")
            observations.append((raw, binding))
    if len(observations) > 12000:
        raise BoundedError("LOW_COST_REGISTERED_CAPTURE_BOUND_EXCEEDED")
    selected = latest_literal_captures(observations)
    if digest([b for _, b in selected]) != body["selection_sha256"] or len(selected) > 6000:
        raise BoundedError("LOW_COST_LATEST_CAPTURE_SELECTION_CHANGED")
    existing = set()
    for item in body["outputs"][TABLES[0]]:
        existing.update(pq.ParquetFile(retained / item["path"])
                        .read(columns=["corporate_action_id"]).column(0).to_pylist())
    events: dict[str, dict[str, list[dict[str, Any]]]] = {}
    conflicts: set[str] = set()
    proofs = []
    classifications: Counter[str] = Counter()
    holds: Counter[str] = Counter()
    old_overlap = repeats = 0
    for raw, binding in selected:
        output, proof = materialize_unit_additions(
            raw, source_sha256=binding["member_sha256"], revision=binding["release"],
            resolver=resolver, observed_at=observed_at, vocabulary=vocabulary, schemas=ss)
        classifications.update(b["disposition"] for b in proof["blocks"])
        holds.update(h["reason"] for h in proof["holds"])
        holds["CONTRADICTORY_EVENT_ALL_HELD"] += proof["conflicting_event_keys"]
        for row in output[TABLES[0]]:
            key = row["corporate_action_id"]
            event = {TABLES[0]: [row], TABLES[1]: [r for r in output[TABLES[1]]
                                                if r["corporate_action_id"] == key]}
            if key in existing:
                old_overlap += 1  # No implicit overwrite of an earlier qualified event.
            elif key in events:
                if events[key] != event:
                    conflicts.add(key)
                else:
                    repeats += 1
            else:
                events[key] = event
        proofs.append(proof)
        if len(proofs) % 64 == 0:
            check()
    result: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    for key in sorted(set(events) - conflicts):
        for table in TABLES:
            result[table].extend(events[key][table])
    summary = {"original_scope_identity": body["identity"], "observed_at": observed_at,
               "complete_retained_captures": len(observations), "latest_scopes": len(selected),
               "selection_sha256": body["selection_sha256"],
               "projection_proofs_sha256": digest(proofs), "rows_sha256": digest(result),
               "row_counts": {t: len(r) for t, r in result.items()},
               "block_dispositions": dict(classifications), "holds": dict(holds),
               "existing_qualified_events_not_replaced": old_overlap,
               "identical_repeats": repeats, "cross_capture_conflicts_held": len(conflicts),
               "source_acquisition_changed": False, "provider_PIT_qualified": False,
               "old_normalization_restored": False, "native_image_union": "NOT_RUN",
               "consumer_admission": "NOT_GRANTED"}
    summary["identity"] = digest(summary)
    # Partition fields and all typed atoms are also tested by the offline auditor.
    for table, values in result.items():
        for row in values:
            partition(row, contract["tables"][table])
    return result, summary


def audit_low_cost_scope(
    root: Path, budget: Budget, *, require_ready: bool = True,
) -> dict[str, Any]:
    import pyarrow.parquet as pq

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (body.get("contract_id") != CONTRACT_ID or body.get("qualification") != QUALIFICATION
            or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
            or body.get("sealed_image_changed") is not False
            or body.get("consumer_admission") != "NOT_GRANTED"
            or body.get("complete_Base23") is not False
            or body.get("input_identity") != digest(body.get("input_closure"))):
        raise BoundedError("LOW_COST_SCOPE_UNQUALIFIED")
    code_dir = Path(__file__).parent
    closure = body["input_closure"]
    if (closure.get("purpose") != CONTRACT_ID or closure.get("qualification") != QUALIFICATION
            or closure.get("business_through") != "2026-09-30"
            or closure.get("timezone") != "Asia/Shanghai"
            or closure.get("runtime") != runtime_identity()
            or set(closure.get("code_closure", {})) != set(CODE_MODULES)):
        raise BoundedError("LOW_COST_INPUT_CONTRACT_OR_RUNTIME_CHANGED")
    for module, sha in closure["code_closure"].items():
        if Path(module).name != module or file_sha(code_dir / module) != sha:
            raise BoundedError("LOW_COST_CODE_CLOSURE_CHANGED")
    markers = {"SCOPE.json", "READY"} if require_ready else {"SCOPE.json"}
    if _tree(root) != {*markers, *body["physical_members"]}:
        raise BoundedError("LOW_COST_PHYSICAL_SET_CHANGED")
    if require_ready and (root / "READY").read_text().strip() != file_sha(root / "SCOPE.json"):
        raise BoundedError("LOW_COST_READY_IDENTITY_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("LOW_COST_PHYSICAL_HASH_CHANGED")
        budget.check()
    if body["kind"] == "calendar-date-authority":
        config = json.loads((root / "calendar-config.json").read_bytes())
        docs = {v: (root / f"official/{v}.html").read_bytes() for v in config["documents"]}
        authority = derive_date_authority(config, docs)
        if authority != json.loads((root / "authority.json").read_bytes()):
            raise BoundedError("LOW_COST_CALENDAR_AUTHORITY_REPLAY_CHANGED")
        rows = [r for p in body["calendar_inputs"]
                for r in pq.ParquetFile(root / p).read().to_pylist()]
        venues = pq.ParquetFile(root / body["venue_input"]).read().to_pylist()
        replay = compare_retained_calendar(authority, rows, venues)
        if replay != json.loads((root / "comparison.json").read_bytes()):
            raise BoundedError("LOW_COST_CALENDAR_COMPARISON_CHANGED")
    elif body["kind"] == "explicit-dividend-share-units":
        retained = root / "retained"
        output, replay = project_unit_scope(retained, observed_at=body["observed_at"],
                                           check=budget.check)
        if replay != json.loads((root / "projection-safe.json").read_bytes()):
            raise BoundedError("LOW_COST_DIVIDEND_REPLAY_CHANGED")
        contract, model, extension = load_contract(retained / "contract")
        ss = schemas(model, extension)
        if set(body["outputs"]) != set(TABLES):
            raise BoundedError("LOW_COST_EXACT_OUTPUT_TABLE_SET_CHANGED")
        wanted = {i["path"].removeprefix("payload/") for t in TABLES for i in body["outputs"][t]}
        if _tree(root / "payload") != wanted:
            raise BoundedError("LOW_COST_EXACT_PARTITION_SET_CHANGED")
        reader_roots = [root] if os.statvfs(root).f_flag & os.ST_RDONLY else None
        with GlobalScan(budget, read_only_roots=reader_roots) as scan:
            for table in TABLES:
                expected: dict[str, list[dict[str, Any]]] = {}
                for row in output[table]:
                    expected.setdefault(partition(row, contract["tables"][table]), []).append(row)
                if set(expected) != {i["partition"] for i in body["outputs"][table]}:
                    raise BoundedError("LOW_COST_EXACT_PARTITION_SET_CHANGED")
                paths = []
                for item in body["outputs"][table]:
                    path = root / item["path"]
                    if (item["path"] != f"payload/{table}/{item['partition']}/part-000000.parquet"
                            or pq.ParquetFile(path).read().to_pylist()
                            != sorted(expected[item["partition"]], key=digest)
                            or semantic_scan([path], ss[table], check=budget.check)
                            != item["semantic_scan"]):
                        raise BoundedError("LOW_COST_TYPED_ATOMS_OR_PARTITION_CHANGED")
                    paths.append(path)
                scan.add(table, paths)
                rule = contract["tables"][table]
                scan.keys(table, rule["business_key"], rule["version_key"])
            old = json.loads((retained / "SCOPE.json").read_bytes())
            for table in ("dwd_security", "dwd_listed_company"):
                scan.add(table, [retained / old["parents"][table]["path"]])
            subset = {*TABLES, "dwd_security", "dwd_listed_company"}
            scan.references({"tables": [t for t in model["tables"] if t["name"] in subset]},
                            {"tables": {t: contract["tables"][t] for t in subset}})
    else:
        raise BoundedError("LOW_COST_KIND_UNSUPPORTED")
    result = {"status": "LOW_COST_SCOPED_EXACT_REPLAY_PASS", "kind": body["kind"],
              "scope_identity": body["identity"], "replay_identity": replay["identity"],
              "complete_Base23": False, "sealed_image_changed": False,
              "consumer_admission": "NOT_GRANTED"}
    result["identity"] = digest(result)
    return result
