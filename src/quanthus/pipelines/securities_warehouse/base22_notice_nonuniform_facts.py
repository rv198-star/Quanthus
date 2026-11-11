"""Bounded literal source facts for named populations and mixed distributions.

These are typed preparation facts, deliberately not scalar cash repair atoms.
Unavailable tax basis and unproved membership remain unavailable. No component
sum, compensation, dilution, currency conversion or PIT time is calculated.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_notice_ab_scope import _market_dates
from .base22_notice_action import _IMPLEMENTED_TITLE, MAX_TEXT_BYTES
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-nonuniform-literal-source-facts-preparation-v1"
_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_PUBLIC = re.compile(rf"社会公众股每股实际分派现金红利(?P<amount>{_NUMBER})元")
_NAMED = re.compile(
    r"上市前原三家大股东(?P<names>中国华能集团有限公司、云南省能源投资集团有限公司、"
    rf"云南合和\(集团\)股份有限公司)每股实际分派现金红利(?P<amount>{_NUMBER})元"
)
_MIXED_CASH = re.compile(rf"每(?P<denominator>10|1)股派(?P<amount>{_NUMBER})元\(含税(?:\)|[,，])")
_TRANSFER = re.compile(
    rf"以资本公积金向全体股东每(?P<denominator>10|1)股转增(?P<amount>{_NUMBER})股"
)


def parse_nonuniform_facts(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    text = document["text"]
    if not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_TEXT_BYTES:
        raise BoundedError("NONUNIFORM_FACTS_TEXT_BOUND_INVALID")
    original = unicodedata.normalize("NFKC", text)
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    code = target["stock_code"]
    first = next((line for line in original.splitlines() if line.strip()), "")
    header = re.search(r"(?:证券代码|股票代码)[:：]?\s*(\d{6})(?!\d)", first)
    if (
        not header
        or header[1] != code
        or document["publisher_security_code"] != code
        or not _IMPLEMENTED_TITLE.search(title)
    ):
        raise BoundedError("NONUNIFORM_FACTS_EXACT_IMPLEMENTED_IDENTITY_REQUIRED")
    normalized = re.sub(r"\s+", "", original)
    if any(s in normalized for s in ("美元", "港币", "港元", "B股", "H股")):
        raise BoundedError("NONUNIFORM_FACTS_FOREIGN_SCOPE_UNQUALIFIED")
    ex = date.fromisoformat(target["ex_date"])
    dates, date_proof = _market_dates(normalized.replace("除权(除息)日", "除权(息)日"))
    if ex > THROUGH or dates != {ex}:
        raise BoundedError("NONUNIFORM_FACTS_EXACT_BUSINESS_DATE_REQUIRED")
    lines = original.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    facts = []
    if "上市前原三家" in normalized:
        if not headings:
            raise BoundedError("NONUNIFORM_FACTS_COMPLETE_SOURCE_SECTIONS_REQUIRED")
        intro = re.sub(r"\s+", "", "\n".join(lines[: headings[0][0]]))
        public, named = list(_PUBLIC.finditer(intro)), list(_NAMED.finditer(intro))
        if len(public) != 1 or len(named) != 1:
            raise BoundedError("NONUNIFORM_FACTS_EXACT_PUBLIC_AND_THREE_NAMED_CLASSES_REQUIRED")
        if "差异化分红" not in normalized:
            raise BoundedError("NONUNIFORM_FACTS_CURRENT_CLASS_SCOPE_REQUIRED")
        for role, claim in [
            ("LITERAL_SOCIAL_PUBLIC_SHARES", public[0]),
            ("LITERAL_THREE_NAMED_PRE_IPO_SHAREHOLDERS", named[0]),
        ]:
            facts.append(
                {
                    "kind": "DECLARED_CLASS_CASH",
                    "source_population_role": role,
                    "source_population_literal": "社会公众股"
                    if role.startswith("LITERAL_SOCIAL")
                    else claim["names"],
                    "literal_numeric_token": claim["amount"],
                    "literal_denominator": 1,
                    "literal_unit": "MAINLAND_SOURCE_YUAN",
                    "tax_basis": "SOURCE_NOT_EXPLICITLY_DECLARED",
                    "original_claim_sha256": digest(claim[0]),
                    "source_population_witness_sha256": digest(claim[0]),
                }
            )
        mode = "NAMED_AND_PUBLIC_SOURCE_DECLARATIONS_NOT_GROSS_INFERENCE"
    else:
        begin = re.search(r"本公司\d{4}年半年度权益分派方案为:", normalized)
        end = re.search(r"二、权益登记日与除权除息日", normalized)
        if not begin or not end or begin.end() >= end.start():
            raise BoundedError("NONUNIFORM_FACTS_COMPLETE_MIXED_PLAN_BOUNDARY_REQUIRED")
        block = normalized[begin.end() : end.start()]
        component_block, note_marker, note = block.partition("注:以上1、2项合计共向")
        if "注:" in component_block:
            raise BoundedError("NONUNIFORM_FACTS_NONEMPTY_NOTE_SCOPE_UNACCOUNTED")
        items = re.split(r"(?<!\d)[12]、", component_block)
        if (
            len(items) != 3
            or "非公开发行" not in items[1]
            or "向全体股东" in items[1]
            or "向全体股东" not in items[2]
        ):
            raise BoundedError("NONUNIFORM_FACTS_EXACT_NUMBERED_POPULATION_SCOPE_REQUIRED")
        cash = list(_MIXED_CASH.finditer(component_block))
        shares = list(_TRANSFER.finditer(component_block))
        if (
            len(cash) != 2
            or len(shares) != 1
            or "非公开发行" not in block
            or "不参与" not in normalized
            and "扣除" not in block
            or "向全体股东" not in block
        ):
            raise BoundedError(
                "NONUNIFORM_FACTS_COMPLETE_MIXED_COMPONENTS_AND_POPULATIONS_REQUIRED"
            )
        for role, claim in [
            ("LITERAL_ISSUER_ELIGIBLE_ORIGINAL_SHARE_BASE", cash[0]),
            ("LITERAL_ALL_SHAREHOLDERS", cash[1]),
            ("LITERAL_ALL_SHAREHOLDERS", shares[0]),
        ]:
            facts.append(
                {
                    "kind": "DECLARED_TRANSFER_COMPONENT"
                    if claim is shares[0]
                    else "DECLARED_GROSS_CASH_COMPONENT",
                    "source_population_role": role,
                    "source_population_literal": "向全体股东"
                    if role.endswith("ALL_SHAREHOLDERS")
                    else None,
                    "source_population_witness_sha256": digest(
                        items[2] if role.endswith("ALL_SHAREHOLDERS") else items[1]
                    ),
                    "literal_numeric_token": claim["amount"],
                    "literal_denominator": int(claim["denominator"]),
                    "literal_unit": "SHARES" if claim is shares[0] else "MAINLAND_SOURCE_YUAN",
                    "tax_basis": "NOT_APPLICABLE" if claim is shares[0] else "EXPLICIT_GROSS",
                    "original_claim_sha256": digest(claim[0]),
                }
            )
        if note_marker:
            recap = re.search(
                rf"每(?P<denominator>10|1)股转增(?P<shares>{_NUMBER})股派发现金"
                rf"(?P<amount>{_NUMBER})元\(含税(?:\)|[,，])",
                note,
            )
            if not recap or "注:" in note:
                raise BoundedError("NONUNIFORM_FACTS_EXACT_PRINTED_SCOPE_RECAP_REQUIRED")
            facts.append(
                {
                    "kind": "DECLARED_PRINTED_SCOPE_RECAP_CASH",
                    "source_population_role": "LITERAL_PRINTED_COMBINED_SCOPE_RECAP",
                    "source_population_literal": None,
                    "source_population_witness_sha256": digest(note_marker + note),
                    "literal_numeric_token": recap["amount"],
                    "literal_denominator": int(recap["denominator"]),
                    "literal_unit": "MAINLAND_SOURCE_YUAN",
                    "tax_basis": "EXPLICIT_GROSS",
                    "original_claim_sha256": digest(recap[0]),
                }
            )
        mode = "MIXED_LITERAL_COMPONENTS_NOT_COMPUTED_COMBINED_ENTITLEMENT"
    for fact in facts:
        value = Decimal(fact["literal_numeric_token"])
        if value < 0 or not decimal_profile(value, precision=38, scale=10)["fits"]:
            raise BoundedError("NONUNIFORM_FACTS_EXACT_DECIMAL_REQUIRED")
        exponent = value.as_tuple().exponent
        assert isinstance(exponent, int)
        fact["literal_scale"] = max(0, -exponent)
    result = {
        "contract_id": CONTRACT_ID,
        "action_id": target["action_id"],
        "stock_code": code,
        "document_id": document["document_id"],
        "business_ex_date": target["ex_date"],
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(document["publisher_title"]),
        "mode": mode,
        "facts": facts,
        "date_proof": date_proof,
        "actual_holder_membership_inferred": False,
        "aggregate_computed": False,
        "normalized_cash_scalar": None,
        "canonical_DWD_repair_qualified": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "next_gate": "VERSIONED_NONUNIFORM_NATIVE_TERM_MODEL_AND_FULL_SOURCE_REPLAY",
    }
    result["identity"] = digest(result)
    return result


def nonuniform_fact_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    fields = [
        (name, pa.int64() if name in {"literal_denominator", "literal_scale"} else pa.string())
        for name in (
            "action_id",
            "document_id",
            "kind",
            "source_population_role",
            "source_population_literal",
            "literal_numeric_token",
            "literal_denominator",
            "literal_unit",
            "tax_basis",
            "original_claim_sha256",
            "source_population_witness_sha256",
            "literal_scale",
            "source_proof_identity",
            "source_acquired_at",
            "governance_observed_at",
            "original_publication_at",
        )
    ]
    return pa.schema(fields)


def audit_nonuniform_fact_scope(root: Any, body: dict[str, Any], budget: Any) -> dict[str, Any]:
    """Small full local replay; no archived source path is dereferenced."""
    import json
    from collections import Counter
    from pathlib import Path

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import _physical, file_sha
    from .base22_financial import _clock
    from .base22_notice_pdf import decode_proven_notice
    from .base22_sources import _tree

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "NONUNIFORM_LITERAL_SOURCE_FACTS_PREPARATION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "complete_Base22",
                "canonical_DWD_repair_qualified",
                "historical_PIT_admission",
            )
        )
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("parser_sha256") != file_sha(Path(__file__))
        or body.get("pdftotext_sha256") != file_sha(Path("/usr/bin/pdftotext"))
    ):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_IDENTITY_OR_BOUNDARY_INVALID")
    code_dir = Path(__file__).parent
    if set(body.get("code_closure", {})) != {p.name for p in code_dir.glob("*.py")}:
        raise BoundedError("NONUNIFORM_FACT_SCOPE_COMPLETE_CODE_CLOSURE_REQUIRED")
    for name, sha in body["code_closure"].items():
        if file_sha(code_dir / name) != sha:
            raise BoundedError("NONUNIFORM_FACT_SCOPE_CODE_CLOSURE_CHANGED")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("NONUNIFORM_FACT_SCOPE_PHYSICAL_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        p = _physical(root, relative, independent=True)
        if p.stat().st_size != member["size_bytes"] or file_sha(p) != member["sha256"]:
            raise BoundedError("NONUNIFORM_FACT_SCOPE_MEMBER_CHANGED")
        budget.check()
    targets = {t["action_id"]: t for t in body["targets"]}
    if not 0 < len(targets) <= 3 or len(targets) != len(body["targets"]):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_TARGET_BOUND_INVALID")
    registration = json.loads((root / "evidence/write-registration.json").read_bytes())
    if (
        registration.get("identity")
        != digest({k: v for k, v in registration.items() if k != "identity"})
        or registration["identity"] != body["registration_identity"]
        or registration["UTC"] != body["observed_at"]
        or registration["exact_targets"] != sorted(targets)
        or registration["exact_documents"] != sorted(d["document_id"] for d in body["documents"])
    ):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_FROZEN_SELECTION_CHANGED")
    raw = {}
    for batch in pq.ParquetFile(_physical(root, body["raw_member"], independent=True)).iter_batches(
        1024
    ):
        for row in batch.to_pylist():
            if row["action_id"] in targets:
                if row["action_id"] in raw:
                    raise BoundedError("NONUNIFORM_FACT_SCOPE_DUPLICATE_RAW_KEY")
                raw[row["action_id"]] = row
        budget.check()
    before = (
        pq.ParquetFile(_physical(root, body["before_member"], independent=True)).read().to_pylist()
    )
    if set(raw) != set(targets) or len(before) != len(targets):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_EXACT_BEFORE_OR_RAW_SET_CHANGED")
    for row in before:
        key = row["source_record_id"]
        if (
            key not in targets
            or digest(row) != targets[key]["native_row_sha256"]
            or digest(raw[key]) != targets[key]["original_raw_row_sha256"]
        ):
            raise BoundedError("NONUNIFORM_FACT_SCOPE_BEFORE_OR_RAW_BINDING_CHANGED")
    expected = []
    for doc in body["documents"]:
        detail = json.loads((root / doc["publisher_detail"]).read_bytes())["announcement"]
        if str(detail["announcementId"]) != doc["document_id"]:
            raise BoundedError("NONUNIFORM_FACT_SCOPE_PUBLISHER_ID_CHANGED")
        if _clock(doc["actual_new_acquisition"]) > _clock(body["observed_at"]):
            raise BoundedError("NONUNIFORM_FACT_SCOPE_REAL_ACQUISITION_CHRONOLOGY_REQUIRED")
        destination = budget.root / (doc["document_id"] + ".txt")
        text, decode = decode_proven_notice(root / doc["PDF"], destination)
        if (
            file_sha(destination) != doc["text_sha256"]
            or decode["identity"] != doc["decode_evidence_identity"]
        ):
            raise BoundedError("NONUNIFORM_FACT_SCOPE_ORIGINAL_PDF_REPLAY_CHANGED")
        source = {
            "document_id": doc["document_id"],
            "text": text,
            "publisher_security_code": str(detail["secCode"]),
            "publisher_title": detail["announcementTitle"],
        }
        for key in doc["target_action_ids"]:
            if key not in targets:
                raise BoundedError("NONUNIFORM_FACT_SCOPE_UNKNOWN_TARGET")
            proof = parse_nonuniform_facts(source, targets[key])
            if digest(proof) != body["proof_sha256"][key]:
                raise BoundedError("NONUNIFORM_FACT_SCOPE_SOURCE_FACT_PROOF_CHANGED")
            for fact in proof["facts"]:
                expected.append(
                    {
                        **fact,
                        "action_id": key,
                        "document_id": doc["document_id"],
                        "source_proof_identity": proof["identity"],
                        "source_acquired_at": doc["actual_new_acquisition"],
                        "governance_observed_at": body["observed_at"],
                        "original_publication_at": None,
                    }
                )
    physical = pq.ParquetFile(_physical(root, body["fact_member"], independent=True))
    if not physical.schema_arrow.equals(nonuniform_fact_schema()):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_PHYSICAL_SCHEMA_CHANGED")
    actual = physical.read().to_pylist()
    if (
        Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
        or len(actual) != body["fact_rows"]
    ):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_TYPED_LITERAL_FACTS_CHANGED")
    if len({(r["action_id"], r["kind"], r["source_population_role"]) for r in actual}) != len(
        actual
    ):
        raise BoundedError("NONUNIFORM_FACT_SCOPE_DUPLICATE_COMPONENT_KEY")
    budget.check()
    return {
        "status": "NONUNIFORM_LITERAL_SOURCE_FACTS_REPLAY_PASS_NOT_DWD_REPAIR",
        "identity": body["identity"],
        "source_fact_rows": len(actual),
        "native_before_unchanged": True,
        "canonical_DWD_repair_qualified": False,
        "consumer_admission": "NOT_GRANTED",
    }
