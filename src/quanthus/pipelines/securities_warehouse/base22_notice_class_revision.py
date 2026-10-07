"""Scoped complete issuer class terms, retaining the ambiguous old scalar as history.

Class roles label exact source claims; they never imply actual holder membership,
listing classes, a dividend-adjustment factor or one generic cash entitlement.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_canonical_action_revision import SOURCE_VERSION, _sha
from .base22_cash_restatement import _scan_cash_global
from .base22_financial import _clock
from .base22_notice_class_terms import _CLASS_ROLES, select_class_cash_candidates
from .base22_notice_class_terms import CONTRACT_ID as PARSER_ID
from .base22_notice_pdf import CONTRACT_ID as PDF_ID
from .base22_notice_pdf import decode_proven_notice
from .base22_precision import decimal_profile
from .base22_sources import _tree
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-primary-issuer-literal-class-terms-revision-v1"
PRIORITY_ID = "explicit_implemented_issuer_class_terms_over_ambiguous_normalized_uniform_cash-v1"
SOURCE = "p10_official_issuer_literal_class_entitlements"
TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
PREFIX = "SOURCE_CLASS_GROSS_CASH_"
CONFIG = "base22-issuer-class-terms.v1.json"
REPLAY_MODULES = (
    "base22.py",
    "base22_bounded.py",
    "base22_sources.py",
    "base22_financial.py",
    "base22_precision.py",
    "base22_canonical_action_revision.py",
    "base22_cash_restatement.py",
    "base22_notice_class_terms.py",
    "base22_notice_class_revision.py",
    "base22_notice_pdf.py",
    "base22_notice_action.py",
    "base22_notice_current_scope.py",
    "base22_notice_scheme.py",
    "disclosure_event.py",
)


def load_class_contract(root: Path) -> dict[str, Any]:
    pinned = Path(__file__).resolve().parents[4] / "config/pipelines" / CONFIG
    actual: dict[str, Any] = json.loads((root / CONFIG).read_bytes())
    if actual != json.loads(pinned.read_bytes()) or actual["contract_id"] != CONTRACT_ID:
        raise BoundedError("CLASS_REVISION_FROZEN_CONTRACT_CHANGED")
    if file_sha(root / "base22-independent-image.v1.json") != actual["base_contract_sha256"]:
        raise BoundedError("CLASS_REVISION_BASE_CONTRACT_CHANGED")
    return actual


def revise_class_terms(
    raw: dict[str, Any],
    before: dict[str, Any],
    before_terms: list[dict[str, Any]],
    notice: dict[str, Any],
    *,
    binding: dict[str, Any],
    observed_at: Any,
    revision: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Close the exact ambiguous native group and add only explicit class atoms."""
    key = raw.get("action_id")
    if (
        not isinstance(key, str)
        or not key
        or before.get("corporate_action_id")
        != _stable_id("corporate_action", "corporate_actions", key)
        or before.get("record_source") != "corporate_actions"
        or before.get("source_record_id") != key
        or before.get("knowledge_to") is not None
        or raw.get("progress") != "实施"
        or type(raw.get("ex_date")) is not date
        or raw["ex_date"] > THROUGH
        or before.get("ex_date") != raw["ex_date"]
        or before.get("action_type") != "CASH_DIVIDEND"
        or before.get("action_status") != "IMPLEMENTED"
        or notice.get("contract_id") != PARSER_ID
        or notice.get("stock_code") != raw.get("stock_code")
        or notice.get("ex_date") != raw["ex_date"]
        or notice.get("identity") != digest({k: v for k, v in notice.items() if k != "identity"})
        or any(
            notice.get(f) is not False
            for f in (
                "general_cash_scalar_selected",
                "virtual_or_average_cash_computed",
                "actual_holder_membership_inferred",
                "historical_PIT_admission",
            )
        )
        or notice.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("CLASS_REVISION_EXACT_OLD_EVENT_AND_LITERAL_NOTICE_REQUIRED")
    if (
        binding.get("contract_id") != CONTRACT_ID
        or binding.get("priority_id") != PRIORITY_ID
        or binding.get("disposition") != "EXACT_PRIMARY_LITERAL_CLASS_TERMS"
        or binding.get("old_normalization_manifest_restored") is not False
        or binding.get("historical_PIT_admission") is not False
        or binding.get("original_raw_row_sha256") != digest(raw)
        or binding.get("before_action_sha256") != digest(before)
        or binding.get("before_terms_sha256") != sorted(digest(t) for t in before_terms)
        or binding.get("notice_proof_sha256") != digest(notice)
        or not all(
            _sha(binding.get(f))
            for f in (
                "PDF_sha256",
                "publisher_detail_sha256",
                "source_scope_identity",
            )
        )
        or not re.fullmatch(r"\d{1,20}", str(binding.get("document_id", "")))
        or not re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision)
    ):
        raise BoundedError("CLASS_REVISION_COMPLETE_SOURCE_BINDING_REQUIRED")
    clock, acquisition = _clock(observed_at), _clock(binding.get("actual_new_acquisition"))
    old_clock = before.get("knowledge_from")
    if (
        not isinstance(old_clock, datetime)
        or old_clock.utcoffset() is None
        or clock <= old_clock
        or clock < acquisition
    ):
        raise BoundedError("CLASS_REVISION_REAL_NEW_KNOWLEDGE_REQUIRED")
    try:
        shares = [
            None if raw.get(f) is None else Decimal(str(raw[f]))
            for f in ("bonus_shares", "transfer_shares")
        ]
    except InvalidOperation:
        raise BoundedError("CLASS_REVISION_RAW_NONCASH_SCOPE_INVALID") from None
    if any(v is not None and (not v.is_finite() or v != 0) for v in shares):
        raise BoundedError("CLASS_REVISION_RAW_NONCASH_SCOPE_UNQUALIFIED")
    if not 0 < len(before_terms) <= 3:
        raise BoundedError("CLASS_REVISION_EXACT_BEFORE_TERM_SET_REQUIRED")
    cash, seen = [], set()
    for term in before_terms:
        typ = term.get("term_type")
        if (
            typ
            not in {"CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}
            or typ in seen
            or term.get("corporate_action_id") != before["corporate_action_id"]
            or term.get("corporate_action_term_id")
            != _stable_id("corporate_action_term", before["corporate_action_id"], typ)
            or any(term.get(k) != before.get(k) for k in SOURCE_VERSION)
            or term.get("option_id") is not None
            or term.get("subscription_price") is not None
        ):
            raise BoundedError("CLASS_REVISION_OLD_TERM_IDENTITY_CHANGED")
        seen.add(typ)
        if typ == "CASH_AMOUNT_PER_SHARE":
            if (
                term.get("currency") != "CNY"
                or not isinstance(term.get("amount_per_share"), Decimal)
                or not term["amount_per_share"].is_finite()
                or term.get("ratio_numerator") is not None
                or term.get("ratio_denominator") is not None
            ):
                raise BoundedError("CLASS_REVISION_OLD_CASH_UNIT_INVALID")
            cash.append(term)
        elif (
            term.get("currency") is not None
            or term.get("amount_per_share") is not None
            or term.get("ratio_numerator") != Decimal(0)
            or term.get("ratio_denominator") != Decimal(1)
        ):
            raise BoundedError("CLASS_REVISION_NONZERO_NONCASH_TERM_UNQUALIFIED")
    if len(cash) != 1:
        raise BoundedError("CLASS_REVISION_EXACT_OLD_UNIFORM_TERM_REQUIRED")
    literal = notice["scheme"]["terms"]
    labels = {t["source_class_label"] for t in literal}
    if len(literal) != 2 or frozenset(labels) not in {
        frozenset({"限售股股东", "流通股股东"}),
        frozenset({"限售股股东", "无限售股股东"}),
        frozenset({"大股东", "中小股东"}),
    }:
        raise BoundedError("CLASS_REVISION_COMPLETE_LITERAL_PAIR_REQUIRED")
    new = {
        **before,
        "record_source": SOURCE,
        "source_record_id": binding["document_id"] + "#action=" + key,
        "source_revision": revision,
        "source_artifact_sha256": binding["PDF_sha256"],
        "knowledge_from": clock,
        "knowledge_to": None,
        "announced_at": None,
    }
    new_terms = []
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        for claim in literal:
            value = claim["amount_per_share"]
            if (
                claim["class_role"] != _CLASS_ROLES[claim["source_class_label"]]
                or claim["currency"] != "CNY"
                or claim["tax_basis"] != "EXPLICIT_GROSS"
                or not isinstance(value, Decimal)
                or value < 0
                or not decimal_profile(value, precision=38, scale=10)["fits"]
            ):
                raise BoundedError("CLASS_REVISION_LITERAL_ROLE_AND_EXACT_GROSS_AMOUNT_REQUIRED")
            typ = PREFIX + claim["class_role"]
            new_terms.append(
                {
                    **cash[0],
                    **{k: new[k] for k in SOURCE_VERSION},
                    "corporate_action_term_id": _stable_id(
                        "corporate_action_term", new["corporate_action_id"], typ
                    ),
                    "term_type": typ,
                    "amount_per_share": value.quantize(Decimal("1e-10")),
                }
            )
    actions = [{**before, "knowledge_to": clock}, new]
    terms = [{**t, "knowledge_to": clock} for t in before_terms] + new_terms
    proof = {
        "contract_id": CONTRACT_ID,
        "priority_id": PRIORITY_ID,
        "binding_sha256": digest(binding),
        "before_action_sha256": digest(before),
        "before_terms_sha256": sorted(digest(t) for t in before_terms),
        "new_action_sha256": digest(new),
        "new_class_terms_sha256": sorted(digest(t) for t in new_terms),
        "class_roles": sorted(t["class_role"] for t in literal),
        "literal_zero_class_terms": sum(t["amount_per_share"] == 0 for t in literal),
        "old_atoms_preserved_except_interval_closure": True,
        "uniform_cash_emitted": False,
        "actual_holder_membership_inferred": False,
        "virtual_average_FX_computed": False,
        "noncash_terms_imputed": False,
        "new_knowledge_from": clock,
        "actual_new_acquisition": acquisition,
        "publication_unavailable": True,
        "business_coordinates": "retained_native_claims_not_new_PDF_assertions",
        "raw_unknown_share_fields": [
            f for f in ("bonus_shares", "transfer_shares") if raw.get(f) is None
        ],
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return actions, terms, proof


def audit_class_revision_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full local PDF replay, exact allowed changes, schema and global parent/version closure."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "PRIMARY_ISSUER_LITERAL_CLASS_NEW_KNOWLEDGE_SCOPE"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("priority_id") != PRIORITY_ID
        or body.get("parser_profile") != PARSER_ID
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("decoder_profile") != PDF_ID
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "complete_Base22",
                "historical_PIT_admission",
                "old_normalization_manifest_restored",
            )
        )
        or file_sha(Path("/usr/bin/pdftotext")) != body.get("pdftotext_sha256")
        or set(body.get("code_closure", {})) != set(REPLAY_MODULES)
    ):
        raise BoundedError("CLASS_REVISION_LOCAL_SCOPE_INVALID")
    for name, sha in body["code_closure"].items():
        if file_sha(Path(__file__).parent / name) != sha:
            raise BoundedError("CLASS_REVISION_CODE_CLOSURE_CHANGED")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("CLASS_REVISION_PHYSICAL_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        p = _physical(root, relative, independent=True)
        if file_sha(p) != member["sha256"] or p.stat().st_size != member["size_bytes"]:
            raise BoundedError("CLASS_REVISION_MEMBER_CHANGED")
        budget.check()
    config = load_class_contract(root / "contract")
    if config["priority_id"] != body["priority_id"]:
        raise BoundedError("CLASS_REVISION_PRIORITY_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    registration = json.loads((root / "evidence/write-registration.json").read_bytes())
    if (
        registration["identity"]
        != digest({k: v for k, v in registration.items() if k != "identity"})
        or registration["identity"] != body["registration_identity"]
        or registration["UTC"] != body["observed_at"]
        or registration["exact_targets"] != sorted(t["action_id"] for t in body["targets"])
    ):
        raise BoundedError("CLASS_REVISION_FROZEN_SELECTION_CHANGED")

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        physical = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if (
            physical.metadata.num_rows != item["rows"]
            or not 0 <= item["rows"] <= 65536
            or (
                item.get("table") in ss
                and not physical.schema_arrow.equals(ss[item["table"]], check_metadata=False)
            )
        ):
            raise BoundedError("CLASS_REVISION_SCHEMA_OR_ROW_BOUND_CHANGED")
        return physical.read().to_pylist()  # type: ignore[no-any-return]

    targets = body["targets"]
    ids = {t["action_id"] for t in targets}
    rr = [r for r in rows(body["raw_member"]) if r["action_id"] in ids]
    raw = {r["action_id"]: r for r in rr}
    br = rows(body["before"][TABLES[0]])
    before = {r["source_record_id"]: r for r in br}
    old_terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for term in rows(body["before"][TABLES[1]]):
        old_terms[term["corporate_action_id"]].append(term)
    if (
        len(ids) != len(targets)
        or set(raw) != ids
        or set(before) != ids
        or len(rr) != len(ids)
        or len(br) != len(ids)
    ):
        raise BoundedError("CLASS_REVISION_EXACT_NATIVE_AND_RAW_SCOPE_CHANGED")
    for t in targets:
        key = t["action_id"]
        if (
            digest(raw[key]) != t["original_raw_row_sha256"]
            or digest(before[key]) != t["native_row_sha256"]
            or raw[key]["stock_code"] != t["stock_code"]
            or raw[key]["ex_date"].isoformat() != t["ex_date"]
        ):
            raise BoundedError("CLASS_REVISION_TARGET_IDENTITY_CHANGED")
    documents, docs = [], {}
    capture = json.loads((root / "evidence/capture-safe.json").read_bytes())
    acquired = {d["document_id"]: d for d in capture["keys"]}
    if capture["identity"] != body["source_scope_identity"]:
        raise BoundedError("CLASS_REVISION_CAPTURE_IDENTITY_CHANGED")
    for d in body["documents"]:
        detail = json.loads((root / d["publisher_detail"]).read_bytes())["announcement"]
        if (
            d["document_id"] in docs
            or str(detail["announcementId"]) != d["document_id"]
            or acquired[d["document_id"]]["actual_new_observation_at"]
            != d["actual_new_acquisition"]
            or acquired[d["document_id"]]["pdf_sha256"] != file_sha(root / d["PDF"])
        ):
            raise BoundedError("CLASS_REVISION_PUBLISHER_OR_ACQUISITION_CHANGED")
        destination = budget.root / (d["document_id"] + ".txt")
        text, proof = decode_proven_notice(root / d["PDF"], destination)
        if (
            file_sha(destination) != d["text_sha256"]
            or proof["identity"] != d["decode_evidence_identity"]
        ):
            raise BoundedError("CLASS_REVISION_LOCAL_PDF_DECODE_CHANGED")
        documents.append(
            {
                "document_id": d["document_id"],
                "target_action_ids": d["target_action_ids"],
                "publisher_security_code": str(detail["secCode"]),
                "publisher_title": detail["announcementTitle"],
                "text": text,
            }
        )
        docs[d["document_id"]] = d
        budget.check()
    selected, selection = select_class_cash_candidates(targets, documents)
    if (
        selection != body["selection_evidence"]
        or set(selected) != ids
        or set(body["bindings"]) != ids
    ):
        raise BoundedError("CLASS_REVISION_COMPLETE_SELECTION_REPLAY_CHANGED")
    expected: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    reproduced = {}
    for key, candidate in selected.items():
        b, d = body["bindings"][key], docs[candidate["document_id"]]
        if (
            b["document_id"] != d["document_id"]
            or b["PDF_sha256"] != file_sha(root / d["PDF"])
            or b["publisher_detail_sha256"] != file_sha(root / d["publisher_detail"])
            or b["actual_new_acquisition"] != d["actual_new_acquisition"]
            or b["source_scope_identity"] != body["source_scope_identity"]
        ):
            raise BoundedError("CLASS_REVISION_DOCUMENT_BINDING_CHANGED")
        a, ts, proof = revise_class_terms(
            raw[key],
            before[key],
            old_terms[before[key]["corporate_action_id"]],
            candidate["proof"],
            binding=b,
            observed_at=body["observed_at"],
            revision=body["revision"],
        )
        expected[TABLES[0]].update(digest(r) for r in a)
        expected[TABLES[1]].update(digest(r) for r in ts)
        reproduced[key] = proof
    if digest(reproduced) != digest(body["proofs"]):
        raise BoundedError("CLASS_REVISION_EXACT_ALLOWED_CHANGES_CHANGED")
    observed: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    counts: Counter[str] = Counter()
    parts = set()
    for i in body["outputs"]:
        table, part = i["table"], i["partition"]
        if table not in TABLES or (table, part) in parts:
            raise BoundedError("CLASS_REVISION_OUTPUT_PARTITION_INVALID")
        parts.add((table, part))
        actual = rows(i)
        if not actual or any(partition(r, contract["tables"][table]) != part for r in actual):
            raise BoundedError("CLASS_REVISION_OUTPUT_PARTITION_INVALID")
        if semantic_scan([root / i["path"]], ss[table], check=budget.check) != i["semantic_scan"]:
            raise BoundedError("CLASS_REVISION_RECOMPUTED_SUMMARY_CHANGED")
        observed[table].update(digest(r) for r in actual)
        counts[table] += len(actual)
    if observed != expected or dict(counts) != body["output_rows"]:
        raise BoundedError("CLASS_REVISION_UNAPPROVED_TYPED_OUTPUT")
    _scan_cash_global(root, body, budget, contract, ss, rows)
    receipt = {
        "status": "PRIMARY_LITERAL_CLASS_PDF_TYPED_REPLAY_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "exact_documents": len(documents),
        "new_canonical_knowledge_versions": len(reproduced),
        "output_rows": dict(counts),
        "literal_zero_class_terms": sum(p["literal_zero_class_terms"] for p in reproduced.values()),
        "global_keys_parents_time_lineage": "PASS",
        "uniform_cash_emitted": False,
        "historical_PIT_admission": False,
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
