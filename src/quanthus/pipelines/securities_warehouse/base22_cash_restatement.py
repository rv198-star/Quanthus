"""Exact shared cash admission for retained MD events outside normalization v3.

This is a bounded new source revision, never vendor priority or recovery of an
old manifest. Its caller must freeze and independently replay the complete F10
raw/receipt dependency before using the returned binding.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_canonical_action_revision import (
    IDENTITY,
    SOURCE_VERSION,
    _sha,
    revise_canonical_action,
    validate_revision_partition,
)
from .base22_normalization_sources import replay_f10_source
from .base22_sources import _tree
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-md-exact-cash-restatement-v1"


def cash_restatement_binding(
    raw: dict[str, Any],
    before: dict[str, Any],
    before_terms: list[dict[str, Any]],
    donor: dict[str, Any],
    donor_terms: list[dict[str, Any]],
    *,
    source_scope_identity: str,
    raw_replay_receipt_identity: str,
) -> dict[str, Any]:
    """Require exact raw/before/F10 cash atoms; missing or conflicting units block.

    Old zero share terms remain preserved history. The new version keeps only
    actual F10 terms, without manufacturing zero or historical publication.
    Record/payment dates may become known in the real new observation version.
    """
    try:
        raw_cash = Decimal(str(raw.get("cash_per_share")))
        raw_shares = [Decimal(str(raw.get(f))) for f in ("bonus_shares", "transfer_shares")]
    except InvalidOperation as exc:
        raise BoundedError("CASH_RESTATEMENT_ORIGINAL_SCALAR_UNKNOWN") from exc
    if (
        raw.get("source") != "md.dividend_history"
        or raw.get("progress") != "实施"
        or not raw_cash.is_finite()
        or raw_cash <= 0
        or any(not v.is_finite() or v != 0 for v in raw_shares)
        or any(donor.get(k) != before.get(k) for k in IDENTITY)
        or any(
            a.get("action_type") != "CASH_DIVIDEND" or a.get("action_status") != "IMPLEMENTED"
            for a in (before, donor)
        )
        or donor.get("record_source") != "base22_f10/dividend_implemented"
        or len(donor_terms) != 1
        or not 0 < len(before_terms) <= 8
        or not _sha(source_scope_identity)
        or not _sha(raw_replay_receipt_identity)
    ):
        raise BoundedError("CASH_RESTATEMENT_EXACT_IMPLEMENTED_CASH_SCOPE_REQUIRED")
    amounts = []
    seen = set()
    for row in before_terms:
        typ = row.get("term_type")
        if (
            not isinstance(typ, str)
            or typ in seen
            or row.get("corporate_action_id") != before["corporate_action_id"]
            or row.get("corporate_action_term_id")
            != _stable_id("corporate_action_term", before["corporate_action_id"], typ)
            or any(row.get(k) != before.get(k) for k in SOURCE_VERSION)
            or row.get("option_id") is not None
            or row.get("subscription_price") is not None
        ):
            raise BoundedError("CASH_RESTATEMENT_ORIGINAL_TERM_IDENTITY_INCOMPLETE")
        seen.add(typ)
        if typ == "CASH_AMOUNT_PER_SHARE":
            amounts.append(row["amount_per_share"])
            if row.get("currency") != "CNY" or any(
                row.get(f) is not None for f in ("ratio_numerator", "ratio_denominator")
            ):
                raise BoundedError("CASH_RESTATEMENT_ORIGINAL_CASH_UNIT_UNKNOWN")
        elif typ in {"BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}:
            if (
                row.get("ratio_numerator") != Decimal(0)
                or row.get("ratio_denominator") != Decimal(1)
                or row.get("amount_per_share") is not None
                or row.get("currency") is not None
            ):
                raise BoundedError("CASH_RESTATEMENT_NONZERO_OR_UNKNOWN_SHARE_TERM")
        else:
            raise BoundedError("CASH_RESTATEMENT_UNKNOWN_ORIGINAL_TERM")
    term = donor_terms[0]
    if (
        amounts != [raw_cash]
        or not isinstance(amounts[0], Decimal)
        or term.get("term_type") != "CASH_AMOUNT_PER_SHARE"
        or term.get("amount_per_share") != amounts[0]
        or term.get("currency") != "CNY"
        or any(
            term.get(f) is not None
            for f in ("ratio_numerator", "ratio_denominator", "subscription_price", "option_id")
        )
        or term.get("corporate_action_id") != donor["corporate_action_id"]
        or any(term.get(k) != donor.get(k) for k in SOURCE_VERSION)
    ):
        raise BoundedError("CASH_RESTATEMENT_EXACT_SHARED_TYPED_CASH_REQUIRED")
    return {
        "cash_contract_id": CONTRACT_ID,
        "source_scope_identity": source_scope_identity,
        "raw_replay_receipt_sha256": raw_replay_receipt_identity,
        "original_raw_member_sha256": before["source_artifact_sha256"],
        "original_raw_row_sha256": digest(raw),
        "qualified_action_sha256": digest(donor),
        "qualified_term_rows_sha256": sorted(digest(r) for r in donor_terms),
        "original_before_terms_sha256": sorted(digest(r) for r in before_terms),
        "disposition": "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR",
        "exact_shared_cash_atoms": True,
        "cash_source_priority_granted": False,
        "historical_PIT_admission": False,
        "old_manifest_restored": False,
    }


def audit_cash_restatement_scope(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Whole local raw replay, exact allowed diff and global selected closure."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "QUALIFIED_NEW_SHARED_CASH_SOURCE_SCOPE_NOT_COMPLETE_HISTORY"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "complete_Base22",
                "historical_PIT_admission",
                "old_manifest_restored",
                "cash_source_priority_granted",
            )
        )
    ):
        raise BoundedError("CASH_RESTATEMENT_SCOPE_INVALID")
    if set(_tree(root)) != {*body["physical_members"], "QUALIFICATION.json"}:
        raise BoundedError("CASH_RESTATEMENT_EXACT_LOCAL_FILE_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("CASH_RESTATEMENT_LOCAL_MEMBER_CHANGED")
        budget.check()
    reg = json.loads((root / "evidence/write-registration.json").read_bytes())
    if (
        reg["identity"] != body["registration_identity"]
        or reg["identity"] != digest({k: v for k, v in reg.items() if k != "identity"})
        or body["selected_original_keys"] != reg["selected_original_keys"]
        or body["observed_at"] != reg["observed_at"]
        or body["revision"] != reg["revision"]
        or body["HEAD"] != reg["HEAD"]
    ):
        raise BoundedError("CASH_RESTATEMENT_FIXED_WRITE_REGISTRATION_CHANGED")
    for name, sha in reg["configuration_SHA256"].items():
        if file_sha(root / "contract" / name) != sha:
            raise BoundedError("CASH_RESTATEMENT_FROZEN_CONFIGURATION_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    donor_root = root / "source-dependency/F10"
    donor_body = json.loads((donor_root / reg["F10"]["descriptor"]).read_bytes())
    if (
        file_sha(donor_root / reg["F10"]["descriptor"]) != reg["F10"]["descriptor_sha256"]
        or donor_body["identity"] != reg["F10"]["identity"]
        or donor_body["identity"]
        != digest({k: v for k, v in donor_body.items() if k != "identity"})
    ):
        raise BoundedError("CASH_RESTATEMENT_DONOR_SOURCE_IDENTITY_CHANGED")
    receipt, actions, terms = replay_f10_source(donor_root, donor_body, budget)
    if receipt != body["complete_F10_raw_replay"]:
        raise BoundedError("CASH_RESTATEMENT_COMPLETE_DONOR_RAW_REPLAY_CHANGED")

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        total = []
        p = _physical(root, item["path"], independent=True)
        if file_sha(p) != item["sha256"]:
            raise BoundedError("CASH_RESTATEMENT_TYPED_MEMBER_CHANGED")
        for b in pq.ParquetFile(p).iter_batches(batch_size=1024):
            total.extend(b.to_pylist())
            if len(total) > 65536:
                raise BoundedError("CASH_RESTATEMENT_PARTITION_SCOPE_EXCEEDED")
            budget.check()
        if len(total) != item["rows"]:
            raise BoundedError("CASH_RESTATEMENT_PHYSICAL_CENSUS_CHANGED")
        return total

    raw = {r["action_id"]: r for r in rows(body["raw_member"])}
    before = {r["source_record_id"]: r for r in rows(body["before"]["dwd_corporate_action"])}
    before_terms = defaultdict(list)
    for r in rows(body["before"]["dwd_corporate_action_term"]):
        before_terms[r["corporate_action_id"]].append(r)
    selected = set(body["selected_original_keys"])
    if selected != set(before) or len(selected) != len(body["selected_original_keys"]):
        raise BoundedError("CASH_RESTATEMENT_EXACT_SELECTED_ENTITY_SET_CHANGED")
    by_id = {a["corporate_action_id"]: a for a in actions.values()}
    expected: dict[str, list[dict[str, Any]]] = {
        "dwd_corporate_action": [],
        "dwd_corporate_action_term": [],
    }
    proofs = {}
    for key in sorted(selected):
        old = before[key]
        candidate = by_id[body["selected_F10_entities"][key]]
        binding = cash_restatement_binding(
            raw[key],
            old,
            before_terms[old["corporate_action_id"]],
            candidate,
            terms[candidate["corporate_action_id"]],
            source_scope_identity=donor_body["identity"],
            raw_replay_receipt_identity=receipt["identity"],
        )
        a, ts, proof = revise_canonical_action(
            raw[key],
            old,
            candidate,
            terms[candidate["corporate_action_id"]],
            binding=binding,
            observed_at=body["observed_at"],
            revision=body["revision"],
        )
        expected["dwd_corporate_action"].append(a)
        expected["dwd_corporate_action_term"].extend(ts)
        proofs[key] = {"cash_binding": binding, "canonical_revision": proof}
    saved = json.loads((root / "evidence/new-version-proofs-safe.json").read_bytes())
    if digest(saved) != digest(proofs):
        raise BoundedError("CASH_RESTATEMENT_EXACT_SAFE_PROOFS_CHANGED")
    scans = {}
    selected_ids = {r["corporate_action_id"] for r in before.values()}
    for table in expected:
        items = [i for i in body["outputs"] if i["table"] == table]
        actual = [r for i in items for r in rows(i)]
        original = (
            list(before.values())
            if table == "dwd_corporate_action"
            else [r for rs in before_terms.values() for r in rs]
        )
        validate_revision_partition(
            original,
            actual,
            selected_ids,
            expected[table],
            observed_at=body["observed_at"],
            action=table == "dwd_corporate_action",
        )
        for i in items:
            summary = semantic_scan([root / i["path"]], ss[table], batch_rows=1024, check=budget.check)
            if summary != i["semantic_scan"]:
                raise BoundedError("CASH_RESTATEMENT_OUTPUT_SUMMARY_CHANGED")
            scans[i["path"]] = summary
    counts: Counter[str] = Counter()
    with GlobalScan(budget) as scan:
        for table in expected:
            paths = [root / i["path"] for i in body["outputs"] if i["table"] == table]
            scan.add(table, paths)
            rule = contract["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
            scan.row_rules(table, ss[table], rule)
            counts[table] = sum(i["rows"] for i in body["outputs"] if i["table"] == table)
            admitted = {
                (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                for i in body["outputs"]
                if i["table"] == table
                for r in rows(i)
            }
            scan.lineage(table, sorted(admitted))
        for table, items in body["parents"].items():
            scan.add(table, [root / i["path"] for i in items])
        for parent, column in [
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ]:
            scan.references("dwd_corporate_action", parent, [(column, column)])
        scan.reject(
            "SELECT 1 FROM dwd_corporate_action_term t WHERE NOT EXISTS("
            "SELECT 1 FROM dwd_corporate_action a "
            "WHERE a.corporate_action_id=t.corporate_action_id "
            "AND a.knowledge_from=t.knowledge_from "
            "AND a.source_revision=t.source_revision "
            "AND a.source_record_id=t.source_record_id "
            "AND a.source_artifact_sha256=t.source_artifact_sha256) LIMIT 1",
            "CASH_RESTATEMENT_GLOBAL_TERM_PARENT_VERSION_FAILED",
        )
    if dict(counts) != body["output_rows"]:
        raise BoundedError("CASH_RESTATEMENT_FULL_OUTPUT_ROW_CENSUS_CHANGED")
    result = {
        "status": "FULL_RAW_NEW_CASH_SCOPE_PASS_NOT_COMPLETE_HISTORY_OR_BASE22",
        "scope_identity": body["identity"],
        "selected_original_keys": len(selected),
        "new_actions": len(expected["dwd_corporate_action"]),
        "new_terms": len(expected["dwd_corporate_action_term"]),
        "complete_F10_raw_replay": receipt,
        "output_rows": dict(counts),
        "all_output_semantic_scans": scans,
        "old_cash_atoms_changed": 0,
        "source_priority_granted": False,
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
