"""Primary issuer cash evidence as a truthful new canonical knowledge version.

The explicit priority applies only to gross CNY cash in an implemented notice
with exact dated identity. It does not recover legacy normalization, arbitrate
share terms, infer historical clocks, or admit any consumer. Callers bind and
independently replay the original PDF and retained raw/before closure.
"""

from __future__ import annotations

import json
import re
import resource
import subprocess
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
from .base22_notice_action import CONTRACT_ID as PARSER_CONTRACT
from .base22_notice_action import select_notice_cash_candidates
from .base22_notice_adjustment import CONTRACT_ID as ADJUSTMENT_CONTRACT
from .base22_notice_pdf import CONTRACT_ID as PDF_CONTRACT
from .base22_notice_pdf import decode_proven_notice
from .base22_notice_scheme import CONTRACT_ID as SCHEME_CONTRACT
from .base22_precision import decimal_profile
from .base22_sources import _tree
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-primary-issuer-cash-restatement-v1"
PRIORITY_ID = "issuer-implemented-gross-cny-cash-over-normalized-and-aggregator-v1"
SOURCE = "p10_official_issuer_cash_retained_event_coordinates"
TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")


def restate_notice_cash(
    raw: dict[str, Any],
    before: dict[str, Any],
    before_terms: list[dict[str, Any]],
    notice_atom: dict[str, Any],
    *,
    binding: dict[str, Any],
    observed_at: Any,
    revision: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Close exact old versions and add cash only; unknown share terms stay absent.

    Old record/payment/effective coordinates are inherited original claims with
    separate retained lineage, not new assertions by the issuer PDF. Date-only
    announcement claims remain outside announced_at. Nonzero share observations
    need their own complete qualification and block this cash-only projection.
    """
    key = raw.get("action_id")
    if (
        not isinstance(key, str)
        or not key
        or before.get("corporate_action_id")
        != _stable_id("corporate_action", "corporate_actions", key)
        or before.get("source_record_id") != key
        or before.get("record_source") != "corporate_actions"
        or before.get("knowledge_to") is not None
        or type(raw.get("ex_date")) is not date
        or raw["ex_date"] > THROUGH
        or before.get("ex_date") != raw["ex_date"]
        or raw.get("progress") != "实施"
        or before.get("action_type") != "CASH_DIVIDEND"
        or before.get("action_status") != "IMPLEMENTED"
        or notice_atom.get("contract_id")
        not in {PARSER_CONTRACT, SCHEME_CONTRACT, ADJUSTMENT_CONTRACT}
        or notice_atom.get("stock_code") != raw.get("stock_code")
        or notice_atom.get("ex_date") != raw["ex_date"]
        or notice_atom.get("currency") != "CNY"
    ):
        raise BoundedError("NOTICE_RESTATEMENT_EXACT_OLD_EVENT_AND_ISSUER_REQUIRED")
    amount = notice_atom.get("cash_per_share")
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount <= 0
        or not decimal_profile(amount, precision=38, scale=10)["fits"]
        or notice_atom.get("cash_atom_sha256") != digest(["CNY", amount])
    ):
        raise BoundedError("NOTICE_RESTATEMENT_EXACT_GROSS_CASH_REQUIRED")
    try:
        raw_cash = Decimal(str(raw.get("cash_per_share")))
        shares = [
            None if raw.get(f) is None else Decimal(str(raw[f]))
            for f in ("bonus_shares", "transfer_shares")
        ]
    except InvalidOperation:
        raise BoundedError("NOTICE_RESTATEMENT_RAW_SCALAR_INVALID") from None
    if (
        not raw_cash.is_finite()
        or raw_cash <= 0
        or any(v is not None and (not v.is_finite() or v != 0) for v in shares)
    ):
        raise BoundedError("NOTICE_RESTATEMENT_NONCASH_SCOPE_NOT_QUALIFIED")
    if (
        binding.get("contract_id") != CONTRACT_ID
        or binding.get("priority_id") != PRIORITY_ID
        or binding.get("disposition") != "EXACT_PRIMARY_ISSUER_CASH_ONLY"
        or binding.get("old_normalization_manifest_restored") is not False
        or binding.get("historical_PIT_admission") is not False
        or binding.get("original_raw_row_sha256") != digest(raw)
        or binding.get("before_action_sha256") != digest(before)
        or binding.get("before_terms_sha256") != sorted(digest(t) for t in before_terms)
        or binding.get("notice_atom_sha256") != digest(notice_atom)
        or not all(
            _sha(binding.get(f))
            for f in ("PDF_sha256", "publisher_detail_sha256", "source_scope_identity")
        )
        or not _sha(notice_atom.get("original_text_sha256"))
        or not isinstance(binding.get("document_id"), str)
        or re.fullmatch(r"\d{1,20}", binding["document_id"]) is None
        or re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision) is None
    ):
        raise BoundedError("NOTICE_RESTATEMENT_COMPLETE_SOURCE_BINDING_REQUIRED")
    clock, acquisition = _clock(observed_at), _clock(binding.get("actual_new_acquisition"))
    old_clock = before.get("knowledge_from")
    if (
        not isinstance(old_clock, datetime)
        or old_clock.utcoffset() is None
        or clock <= old_clock
        or clock < acquisition
    ):
        raise BoundedError("NOTICE_RESTATEMENT_REAL_NEW_KNOWLEDGE_REQUIRED")
    if not 0 < len(before_terms) <= 8:
        raise BoundedError("NOTICE_RESTATEMENT_BEFORE_TERM_SET_INVALID")
    cash = []
    seen = set()
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
            raise BoundedError("NOTICE_RESTATEMENT_BEFORE_TERM_BINDING_INVALID")
        seen.add(typ)
        if typ == "CASH_AMOUNT_PER_SHARE":
            if (
                term.get("currency") != "CNY"
                or term.get("ratio_numerator") is not None
                or term.get("ratio_denominator") is not None
                or not isinstance(term.get("amount_per_share"), Decimal)
            ):
                raise BoundedError("NOTICE_RESTATEMENT_BEFORE_CASH_UNIT_INVALID")
            cash.append(term)
        elif (
            term.get("currency") is not None
            or term.get("amount_per_share") is not None
            or term.get("ratio_numerator") != Decimal(0)
            or term.get("ratio_denominator") != Decimal(1)
        ):
            raise BoundedError("NOTICE_RESTATEMENT_NONCASH_SCOPE_NOT_QUALIFIED")
    if (
        len(cash) != 1
        or not cash[0]["amount_per_share"].is_finite()
        or cash[0]["amount_per_share"] <= 0
    ):
        raise BoundedError("NOTICE_RESTATEMENT_EXACT_BEFORE_CASH_REQUIRED")
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
    # The fixed Parquet column has scale ten. Padding is exact: any significant
    # rounding traps, and the original numeric token/Decimal remains in evidence.
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        physical_amount = amount.quantize(Decimal("1e-10"))
    term = {**cash[0], **{k: new[k] for k in SOURCE_VERSION}, "amount_per_share": physical_amount}
    actions = [{**before, "knowledge_to": clock}, new]
    terms = [{**t, "knowledge_to": clock} for t in before_terms] + [term]
    proof = {
        "contract_id": CONTRACT_ID,
        "priority_id": PRIORITY_ID,
        "binding_sha256": digest(binding),
        "before_action_sha256": digest(before),
        "before_terms_sha256": sorted(digest(t) for t in before_terms),
        "new_action_sha256": digest(new),
        "new_cash_term_sha256": digest(term),
        "cash_changed": cash[0]["amount_per_share"] != amount,
        "retained_raw_vs_before_cash_equal": raw_cash == cash[0]["amount_per_share"],
        "old_atoms_preserved_except_interval_closure": True,
        "other_terms_requalified": False,
        "raw_unknown_share_fields": [
            f for f in ("bonus_shares", "transfer_shares") if raw.get(f) is None
        ],
        "new_knowledge_from": clock,
        "actual_new_acquisition": acquisition,
        "publication_unavailable": True,
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return actions, terms, proof


def decode_local_notice(pdf: Path, destination: Path) -> str:
    """Decode a bounded original PDF, with no supplier or original-root access."""
    if pdf.stat().st_size > 10 * 1024**2 or destination.exists():
        raise BoundedError("NOTICE_REPLAY_INPUT_OR_TEMP_INVALID")

    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))

    result = subprocess.run(
        ["/usr/bin/pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(destination)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        preexec_fn=limits,
    )
    if (
        result.returncode
        or not destination.is_file()
        or not 0 < destination.stat().st_size <= 1024**2
    ):
        raise BoundedError("NOTICE_REPLAY_PDF_DECODE_FAILED")
    return destination.read_text(encoding="utf-8")


def audit_notice_restatement_scope(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Reconstruct all selected issuer decisions and typed changes from local raw.

    Whole-history union admission is separate. This scoped artifact has complete
    original PDFs, publisher replies, raw events and exact before versions.
    Archived paths are strings; runtime never dereferences any source_root.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "PRIMARY_ISSUER_CASH_NEW_KNOWLEDGE_SCOPE"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("priority_id") != PRIORITY_ID
        or body.get("consumer_admission") != "NOT_GRANTED"
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
        or body.get("decoder_profile", "legacy-pdftotext-layout-v1")
        not in {"legacy-pdftotext-layout-v1", PDF_CONTRACT}
    ):
        raise BoundedError("NOTICE_RESTATEMENT_LOCAL_SCOPE_INVALID")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("NOTICE_RESTATEMENT_PHYSICAL_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != member["sha256"] or path.stat().st_size != member["size_bytes"]:
            raise BoundedError("NOTICE_RESTATEMENT_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        path = _physical(root, item["path"], independent=True)
        physical = pq.ParquetFile(path)
        if (
            physical.metadata.num_rows != item["rows"]
            or item["rows"] > 65536
            or (
                item.get("table") in ss
                and not physical.schema_arrow.equals(ss[item["table"]], check_metadata=False)
            )
        ):
            raise BoundedError("NOTICE_RESTATEMENT_SCHEMA_OR_ROW_BOUND_CHANGED")
        return physical.read().to_pylist()  # type: ignore[no-any-return]

    targets = body["targets"]
    ids = {t["action_id"] for t in targets}
    raw_rows = [r for r in rows(body["raw_member"]) if r["action_id"] in ids]
    raw = {r["action_id"]: r for r in raw_rows}
    before_rows = rows(body["before"][TABLES[0]])
    before = {r["source_record_id"]: r for r in before_rows}
    before_terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for term in rows(body["before"][TABLES[1]]):
        before_terms[term["corporate_action_id"]].append(term)
    if (
        set(raw) != ids
        or set(before) != ids
        or len(ids) != len(targets)
        or len(raw) != len(raw_rows)
        or len(before) != len(before_rows)
    ):
        raise BoundedError("NOTICE_RESTATEMENT_EXACT_OLD_RAW_SCOPE_CHANGED")
    for target in targets:
        key = target["action_id"]
        if (
            digest(raw[key]) != target["original_raw_row_sha256"]
            or digest(before[key]) != target["native_row_sha256"]
            or raw[key]["stock_code"] != target["stock_code"]
            or raw[key]["ex_date"].isoformat() != target["ex_date"]
        ):
            raise BoundedError("NOTICE_RESTATEMENT_TARGET_BEFORE_IDENTITY_CHANGED")
    documents = []
    for doc in body["documents"]:
        detail = json.loads((root / doc["publisher_detail"]).read_bytes())["announcement"]
        if str(detail["announcementId"]) != doc["document_id"]:
            raise BoundedError("NOTICE_RESTATEMENT_PUBLISHER_DOCUMENT_CHANGED")
        destination = budget.root / (doc["document_id"] + ".txt")
        if body.get("decoder_profile") == PDF_CONTRACT:
            text, decode_evidence = decode_proven_notice(root / doc["PDF"], destination)
            if decode_evidence["identity"] != doc.get("decode_evidence_identity"):
                raise BoundedError("NOTICE_RESTATEMENT_PROVEN_PDF_DECODE_CHANGED")
        else:
            text = decode_local_notice(root / doc["PDF"], destination)
        if file_sha(destination) != doc["text_sha256"]:
            raise BoundedError("NOTICE_RESTATEMENT_ORIGINAL_PDF_TEXT_CHANGED")
        documents.append(
            {
                "document_id": doc["document_id"],
                "target_action_ids": doc["target_action_ids"],
                "publisher_security_code": str(detail["secCode"]),
                "publisher_title": detail["announcementTitle"],
                "text": text,
            }
        )
        budget.check()
    selected, selection = select_notice_cash_candidates(
        targets,
        documents,
        parser_profile=body.get("parser_profile", PARSER_CONTRACT),
    )
    if selection != body["selection_evidence"]:
        raise BoundedError("NOTICE_RESTATEMENT_RECOMPUTED_SELECTION_CHANGED")
    if set(body["bindings"]) != set(selected):
        raise BoundedError("NOTICE_RESTATEMENT_BINDING_SET_CHANGED")
    docs = {d["document_id"]: d for d in body["documents"]}
    expected: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    reproduced = {}
    held = {}
    for key, candidate in selected.items():
        binding = body["bindings"][key]
        doc = docs[candidate["document_id"]]
        if (
            binding["document_id"] != doc["document_id"]
            or binding["PDF_sha256"] != file_sha(root / doc["PDF"])
            or binding["publisher_detail_sha256"] != file_sha(root / doc["publisher_detail"])
            or binding["actual_new_acquisition"] != doc["actual_new_acquisition"]
            or binding["source_scope_identity"] != body["source_scope_identity"]
        ):
            raise BoundedError("NOTICE_RESTATEMENT_SELECTED_DOCUMENT_BINDING_CHANGED")
        try:
            actions, terms, proof = restate_notice_cash(
                raw[key],
                before[key],
                before_terms[before[key]["corporate_action_id"]],
                candidate["atom"],
                binding=binding,
                observed_at=body["observed_at"],
                revision=body["revision"],
            )
        except BoundedError as exc:
            if str(exc) != "NOTICE_RESTATEMENT_NONCASH_SCOPE_NOT_QUALIFIED":
                raise
            held[key] = str(exc)
            continue
        reproduced[key] = proof
        expected[TABLES[0]].update(digest(r) for r in actions)
        expected[TABLES[1]].update(digest(r) for r in terms)
    # Sealed UTF-8 evidence stores clocks as canonical strings; a cold reader
    # compares the complete canonical representation, not Python object types.
    if digest(reproduced) != digest(body["proofs"]) or held != body["cash_only_held"]:
        raise BoundedError("NOTICE_RESTATEMENT_RECOMPUTED_ALLOWED_CHANGE_CHANGED")
    counts: Counter[str] = Counter()
    observed: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    parts = set()
    for item in body["outputs"]:
        table, part = item["table"], item["partition"]
        if table not in TABLES or (table, part) in parts:
            raise BoundedError("NOTICE_RESTATEMENT_OUTPUT_PARTITION_INVALID")
        parts.add((table, part))
        actual = rows(item)
        if any(partition(r, contract["tables"][table]) != part for r in actual):
            raise BoundedError("NOTICE_RESTATEMENT_OUTPUT_PARTITION_INVALID")
        if (
            semantic_scan([root / item["path"]], ss[table], check=budget.check)
            != item["semantic_scan"]
        ):
            raise BoundedError("NOTICE_RESTATEMENT_OUTPUT_SUMMARY_CHANGED")
        counts[table] += len(actual)
        observed[table].update(digest(r) for r in actual)
    if observed != expected or dict(counts) != body["output_rows"]:
        raise BoundedError("NOTICE_RESTATEMENT_UNAPPROVED_TYPED_CHANGE")
    _scan_cash_global(root, body, budget, contract, ss, rows)
    receipt = {
        "status": "PRIMARY_ISSUER_RAW_PDF_TYPED_REPLAY_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "exact_documents": len(documents),
        "new_canonical_knowledge_versions": len(reproduced),
        "output_rows": dict(counts),
        "cash_changed_keys": sum(p["cash_changed"] for p in reproduced.values()),
        "cash_only_held": held,
        "global_keys_parents_time_lineage": "PASS",
        "historical_PIT_admission": False,
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
