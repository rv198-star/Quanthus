"""Native, source-labelled nonuniform terms without a generic cash entitlement.

The additive candidate contract distinguishes literal populations, unknown tax
basis, money per source denominator and the publisher's separate printed recap.
It does not make unknown gross cash usable or infer any shareholder membership.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_canonical_action_revision import SOURCE_VERSION, _sha
from .base22_cash_restatement import _scan_cash_global
from .base22_financial import _clock
from .base22_notice_nonuniform_facts import parse_nonuniform_facts
from .base22_notice_pdf import decode_proven_notice
from .base22_precision import decimal_profile
from .base22_sources import _tree
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-native-literal-nonuniform-terms-v1"
PRIORITY_ID = "implemented_issuer_literal_population_claims_replace_ambiguous_scalar-v1"
CONFIG = "base22-nonuniform-terms.v1.json"
PREFIX = "SOURCE_DECLARED_NONUNIFORM_"
SOURCE = "p10_official_issuer_nonuniform_literal_terms"
TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
REPLAY_MODULES = (
    "base22.py",
    "base22_action_archive.py",
    "base22_action_authority.py",
    "base22_action_factor.py",
    "base22_august.py",
    "base22_bounded.py",
    "base22_canonical_action_revision.py",
    "base22_cash_restatement.py",
    "base22_f10_dividend.py",
    "base22_f10_replay.py",
    "base22_f10_sources.py",
    "base22_financial.py",
    "base22_holder_exact.py",
    "base22_nonuniform_revision.py",
    "base22_normalization_sources.py",
    "base22_notice_a_scope.py",
    "base22_notice_ab_scope.py",
    "base22_notice_action.py",
    "base22_notice_adjustment.py",
    "base22_notice_current_scope.py",
    "base22_notice_date_layout.py",
    "base22_notice_domestic_scope.py",
    "base22_notice_equivalent_scope.py",
    "base22_notice_gross_layout.py",
    "base22_notice_h_reference.py",
    "base22_notice_identity_scope.py",
    "base22_notice_implementation_labels.py",
    "base22_notice_issuer_titles.py",
    "base22_notice_labels.py",
    "base22_notice_literal_statement.py",
    "base22_notice_nonuniform_facts.py",
    "base22_notice_pdf.py",
    "base22_notice_scheme.py",
    "base22_output_audit.py",
    "base22_outputs.py",
    "base22_precision.py",
    "base22_sources.py",
    "daily_price_basis.py",
    "disclosure_event.py",
    "financial_reporting.py",
    "ownership_capital.py",
)
_NUM = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_NAMED = "LITERAL_THREE_NAMED_PRE_IPO_SHAREHOLDERS"
_PUBLIC = "LITERAL_SOCIAL_PUBLIC_SHARES"
_ROLES = {
    ("DECLARED_CLASS_CASH", _PUBLIC): (
        "CASH_TAX_UNAVAILABLE_PUBLIC",
        "SOURCE_NOT_EXPLICITLY_DECLARED",
        1,
    ),
    ("DECLARED_CLASS_CASH", _NAMED): (
        "CASH_TAX_UNAVAILABLE_THREE_NAMED_PRE_IPO",
        "SOURCE_NOT_EXPLICITLY_DECLARED",
        1,
    ),
    ("DECLARED_GROSS_CASH_COMPONENT", "LITERAL_ISSUER_ELIGIBLE_ORIGINAL_SHARE_BASE"): (
        "GROSS_CASH_ELIGIBLE_ORIGINAL_BASE",
        "EXPLICIT_GROSS",
        10,
    ),
    ("DECLARED_GROSS_CASH_COMPONENT", "LITERAL_ALL_SHAREHOLDERS"): (
        "GROSS_CASH_ALL_SHAREHOLDERS",
        "EXPLICIT_GROSS",
        10,
    ),
    ("DECLARED_TRANSFER_COMPONENT", "LITERAL_ALL_SHAREHOLDERS"): (
        "TRANSFER_ALL_SHAREHOLDERS",
        "NOT_APPLICABLE",
        10,
    ),
    ("DECLARED_PRINTED_SCOPE_RECAP_CASH", "LITERAL_PRINTED_COMBINED_SCOPE_RECAP"): (
        "PRINTED_SCOPE_RECAP_GROSS_CASH",
        "EXPLICIT_GROSS",
        10,
    ),
}


def parse_native_claims(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Consume literal facts, also binding complete source population witnesses."""
    facts = parse_nonuniform_facts(document, target)
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["text"]))
    roles = {(r["kind"], r["source_population_role"]) for r in facts["facts"]}
    populations: dict[str, str] = {}
    if facts["mode"].startswith("NAMED_AND_PUBLIC"):
        start = normalized.find("二、分配方案")
        end = normalized.find("三、相关日期", start)
        if (
            start < 0
            or end <= start
            or roles != {("DECLARED_CLASS_CASH", _PUBLIC), ("DECLARED_CLASS_CASH", _NAMED)}
        ):
            raise BoundedError("NONUNIFORM_COMPLETE_CURRENT_NAMED_POPULATIONS_REQUIRED")
        block = normalized[start:end]
        # These are literal final statements, not evaluation of the intervening
        # compensation formula, average or ex-reference price.
        patterns = {
            _PUBLIC: [
                rf"社会公众股(?:实际每股分配金额为|每股实际分红金额为)({_NUM})元",
                rf"社会公众股合计(?:持有)?\d.*?实际每股分配金额为({_NUM})元",
            ],
            _NAMED: [
                rf"(?:原三家实际每股分配金额为|三家大股东每股实际分红金额为)({_NUM})元",
                rf"三家大股东华能集团、云能投集团、合和集团合计持有.*?实际每股分配金额为({_NUM})元",
            ],
        }
        for fact in facts["facts"]:
            role = fact["source_population_role"]
            matches = [m for pattern in patterns[role] for m in re.finditer(pattern, block)]
            if len(matches) != 1 or Decimal(matches[0][1]) != Decimal(
                fact["literal_numeric_token"]
            ):
                raise BoundedError("NONUNIFORM_CURRENT_CLASS_STATEMENT_MISSING_OR_CONFLICTING")
            populations[role] = fact["source_population_literal"]
    else:
        begin = re.search(r"本公司\d{4}年半年度权益分派方案为:", normalized)
        ending = re.search(r"二、权益登记日与除权除息日", normalized)
        if not begin or not ending:
            raise BoundedError("NONUNIFORM_COMPLETE_MIXED_POPULATION_BOUNDARY_REQUIRED")
        block, marker, recap = normalized[begin.end() : ending.start()].partition(
            "注:以上1、2项合计共向"
        )
        items = re.split(r"(?<!\d)[12]、", block)
        if len(items) != 3 or len(roles) not in {3, 4}:
            raise BoundedError("NONUNIFORM_COMPLETE_MIXED_POPULATIONS_REQUIRED")
        populations = {
            "LITERAL_ISSUER_ELIGIBLE_ORIGINAL_SHARE_BASE": items[1],
            "LITERAL_ALL_SHAREHOLDERS": items[2],
        }
        if marker:
            populations["LITERAL_PRINTED_COMBINED_SCOPE_RECAP"] = marker + recap
    proof = {
        "contract_id": CONTRACT_ID,
        "source_fact_proof": facts,
        "action_id": target["action_id"],
        "stock_code": target["stock_code"],
        "ex_date": target["ex_date"],
        "document_id": document["document_id"],
        "facts": facts["facts"],
        "population_witnesses": populations,
        "source_claim_representation_qualified": True,
        "general_gross_cash_qualified": False,
        "actual_holder_membership_inferred": False,
        "aggregate_computed": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return proof


def _literal_terms(proof: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    facts = proof.get("facts", [])
    base = proof.get("source_fact_proof", {})
    if facts != base.get("facts") or base.get("identity") != digest(
        {k: v for k, v in base.items() if k != "identity"}
    ):
        raise BoundedError("NONUNIFORM_ORIGINAL_LITERAL_FACT_PROOF_CHANGED")
    out = []
    for fact in facts:
        role = (fact["kind"], fact["source_population_role"])
        spec = _ROLES.get(role)
        if not spec or (fact["tax_basis"], fact["literal_denominator"]) != spec[1:]:
            raise BoundedError("NONUNIFORM_UNKNOWN_OR_INFERRED_TERM_COORDINATES")
        token = fact["literal_numeric_token"]
        if not isinstance(token, str) or not re.fullmatch(_NUM, token):
            raise BoundedError("NONUNIFORM_LITERAL_DECIMAL_INVALID")
        value = Decimal(token)
        is_transfer = fact["kind"] == "DECLARED_TRANSFER_COMPONENT"
        if (
            not decimal_profile(value, precision=38, scale=10)["fits"]
            or fact["literal_unit"] != ("SHARES" if is_transfer else "MAINLAND_SOURCE_YUAN")
            or not proof["population_witnesses"].get(role[1])
        ):
            raise BoundedError("NONUNIFORM_LITERAL_UNIT_PRECISION_OR_POPULATION_INVALID")
        out.append(
            (
                PREFIX + spec[0],
                {
                    "currency": None if is_transfer else "CNY",
                    "amount_per_share": value if spec[2] == 1 else None,
                    "ratio_numerator": value if spec[2] != 1 else None,
                    "ratio_denominator": Decimal(spec[2]) if spec[2] != 1 else None,
                    "option_id": None,
                    "subscription_price": None,
                },
            )
        )
    keys = {k for k, _ in out}
    if len(keys) != len(out) or len(out) not in {2, 3, 4}:
        raise BoundedError("NONUNIFORM_COMPLETE_UNIQUE_LITERAL_TERM_GROUP_REQUIRED")
    if len(out) == 2 and keys != {
        PREFIX + _ROLES[("DECLARED_CLASS_CASH", r)][0] for r in (_NAMED, _PUBLIC)
    }:
        raise BoundedError("NONUNIFORM_COMPLETE_NAMED_TERM_PAIR_REQUIRED")
    if len(out) != 2 and not {PREFIX + _ROLES[r][0] for r in list(_ROLES)[2:5]} <= keys:
        raise BoundedError("NONUNIFORM_COMPLETE_MIXED_TERM_GROUP_REQUIRED")
    return out


def revise_nonuniform_terms(
    raw: dict[str, Any],
    before: dict[str, Any],
    before_terms: list[dict[str, Any]],
    proof: dict[str, Any],
    *,
    binding: dict[str, Any],
    observed_at: Any,
    revision: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Close exact native versions; open source-labelled claims at the real clock."""
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
        or before.get("action_type") not in {"CASH_DIVIDEND", "MIXED_DISTRIBUTION"}
        or before.get("action_status") != "IMPLEMENTED"
        or proof.get("contract_id") != CONTRACT_ID
        or proof.get("action_id") != key
        or proof.get("stock_code") != raw.get("stock_code")
        or proof.get("ex_date") != raw["ex_date"].isoformat()
        or proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
        or proof.get("consumer_admission") != "NOT_GRANTED"
        or any(
            proof.get(f) is not False
            for f in (
                "actual_holder_membership_inferred",
                "aggregate_computed",
                "historical_PIT_admission",
                "general_gross_cash_qualified",
            )
        )
    ):
        raise BoundedError("NONUNIFORM_EXACT_IMPLEMENTED_EVENT_AND_CLAIM_PROOF_REQUIRED")
    if (
        binding.get("contract_id") != CONTRACT_ID
        or binding.get("priority_id") != PRIORITY_ID
        or binding.get("document_id") != proof.get("document_id")
        or not re.fullmatch(r"\d{1,20}", str(binding.get("document_id", "")))
        or binding.get("original_raw_row_sha256") != digest(raw)
        or binding.get("before_action_sha256") != digest(before)
        or binding.get("before_terms_sha256") != sorted(digest(t) for t in before_terms)
        or binding.get("claim_proof_sha256") != digest(proof)
        or any(
            not _sha(binding.get(f))
            for f in ("PDF_sha256", "publisher_detail_sha256", "source_scope_identity")
        )
        or binding.get("old_normalization_manifest_restored") is not False
        or binding.get("historical_PIT_admission") is not False
        or not re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision)
    ):
        raise BoundedError("NONUNIFORM_COMPLETE_SOURCE_BINDING_REQUIRED")
    clock, acquired = _clock(observed_at), _clock(binding.get("actual_new_acquisition"))
    if (
        not isinstance(before.get("knowledge_from"), datetime)
        or clock <= before["knowledge_from"]
        or clock < acquired
    ):
        raise BoundedError("NONUNIFORM_REAL_NEW_KNOWLEDGE_REQUIRED")
    allowed = {"CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}
    if not 0 < len(before_terms) <= 3 or len({r["term_type"] for r in before_terms}) != len(
        before_terms
    ):
        raise BoundedError("NONUNIFORM_EXACT_OLD_TERM_GROUP_REQUIRED")
    for term in before_terms:
        if (
            term.get("term_type") not in allowed
            or term.get("corporate_action_id") != before["corporate_action_id"]
            or term.get("corporate_action_term_id")
            != _stable_id("corporate_action_term", before["corporate_action_id"], term["term_type"])
            or any(term.get(f) != before.get(f) for f in SOURCE_VERSION)
            or term.get("option_id") is not None
            or term.get("subscription_price") is not None
        ):
            raise BoundedError("NONUNIFORM_EXACT_OLD_TERM_IDENTITY_REQUIRED")
    literal = _literal_terms(proof)
    mixed = any("TRANSFER_ALL" in typ for typ, _ in literal)
    if mixed != (before["action_type"] == "MIXED_DISTRIBUTION"):
        raise BoundedError("NONUNIFORM_COMPLETE_ACTION_TYPE_SCOPE_REQUIRED")
    for field in ("bonus_shares", "transfer_shares"):
        value = raw.get(field)
        if value is not None:
            value = Decimal(str(value))
            if (
                not value.is_finite()
                or value < 0
                or (field == "bonus_shares" or not mixed)
                and value != 0
            ):
                raise BoundedError("NONUNIFORM_UNACCOUNTED_RAW_NONCASH_SCOPE")
    new_action = {
        **before,
        "record_source": SOURCE,
        "source_record_id": binding["document_id"] + "#action=" + key,
        "source_revision": revision,
        "source_artifact_sha256": binding["PDF_sha256"],
        "knowledge_from": clock,
        "knowledge_to": None,
        "announced_at": None,
    }
    new_terms = [
        {
            **before_terms[0],
            **{f: new_action[f] for f in SOURCE_VERSION},
            **coordinates,
            "corporate_action_term_id": _stable_id(
                "corporate_action_term", before["corporate_action_id"], typ
            ),
            "corporate_action_id": before["corporate_action_id"],
            "term_type": typ,
        }
        for typ, coordinates in literal
    ]
    result = {
        "contract_id": CONTRACT_ID,
        "binding_sha256": digest(binding),
        "new_action_sha256": digest(new_action),
        "new_terms_sha256": sorted(digest(r) for r in new_terms),
        "source_claim_representation_qualified": True,
        "general_gross_cash_qualified": False,
        "tax_basis_unavailable_terms": sum("TAX_UNAVAILABLE" in t["term_type"] for t in new_terms),
        "native_scalar_retained_only_as_closed_history": True,
        "original_populations_retained": True,
        "actual_holder_membership_inferred": False,
        "aggregate_computed": False,
        "old_atoms_preserved_except_interval_closure": True,
        "original_publication_unavailable": True,
        "new_knowledge_from": clock,
        "actual_new_acquisition": acquired,
        "consumer_admission": "NOT_GRANTED",
        "historical_PIT_admission": False,
        "old_normalization_manifest_restored": False,
    }
    result["identity"] = digest(result)
    return (
        [{**before, "knowledge_to": clock}, new_action],
        [{**r, "knowledge_to": clock} for r in before_terms] + new_terms,
        result,
    )


def audit_nonuniform_revision_scope(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Replay original PDFs, native versions and exact bounded serialized claims."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "PRIMARY_ISSUER_NATIVE_NONUNIFORM_LITERAL_NEW_KNOWLEDGE_SCOPE"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("priority_id") != PRIORITY_ID
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(f) is not False
            for f in (
                "READY",
                "complete_Base22",
                "historical_PIT_admission",
                "old_normalization_manifest_restored",
                "general_gross_cash_qualified",
            )
        )
        or body.get("pdftotext_sha256") != file_sha(Path("/usr/bin/pdftotext"))
    ):
        raise BoundedError("NONUNIFORM_NATIVE_SCOPE_INVALID")
    code = Path(__file__).parent
    if set(body.get("code_closure", {})) != set(REPLAY_MODULES):
        raise BoundedError("NONUNIFORM_NATIVE_COMPLETE_CODE_CLOSURE_REQUIRED")
    for name, sha in body["code_closure"].items():
        if file_sha(code / name) != sha:
            raise BoundedError("NONUNIFORM_NATIVE_CODE_CLOSURE_CHANGED")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("NONUNIFORM_NATIVE_PHYSICAL_SET_CHANGED")
    for rel, member in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != member["size_bytes"] or file_sha(p) != member["sha256"]:
            raise BoundedError("NONUNIFORM_NATIVE_MEMBER_CHANGED")
        budget.check()
    pinned = Path(__file__).resolve().parents[4] / "config/pipelines" / CONFIG
    if (root / "contract" / CONFIG).read_bytes() != pinned.read_bytes():
        raise BoundedError("NONUNIFORM_NATIVE_CONTRACT_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    registration = json.loads((root / "evidence/write-registration.json").read_bytes())
    targets = {t["action_id"]: t for t in body["targets"]}
    if (
        not 0 < len(targets) <= 3
        or len(targets) != len(body["targets"])
        or registration.get("identity")
        != digest({k: v for k, v in registration.items() if k != "identity"})
        or registration["identity"] != body["registration_identity"]
        or registration["UTC"] != body["observed_at"]
        or registration["exact_targets"] != sorted(targets)
        or registration["exact_documents"] != sorted(d["document_id"] for d in body["documents"])
    ):
        raise BoundedError("NONUNIFORM_NATIVE_FROZEN_SELECTION_CHANGED")

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        p = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if (
            not 0 <= item["rows"] <= 65536
            or p.metadata.num_rows != item["rows"]
            or (
                item.get("table") in ss
                and not p.schema_arrow.equals(ss[item["table"]], check_metadata=False)
            )
        ):
            raise BoundedError("NONUNIFORM_NATIVE_ROW_BOUND_OR_SCHEMA_CHANGED")
        return p.read().to_pylist()  # type: ignore[no-any-return]

    raw_selected = [r for r in rows(body["raw_member"]) if r["action_id"] in targets]
    raw = {r["action_id"]: r for r in raw_selected}
    original = rows(body["before"][TABLES[0]])
    before = {r["source_record_id"]: r for r in original}
    if (
        set(raw) != set(targets)
        or len(raw_selected) != len(targets)
        or set(before) != set(targets)
        or len(original) != len(targets)
    ):
        raise BoundedError("NONUNIFORM_NATIVE_EXACT_RAW_BEFORE_SET_CHANGED")
    old_terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows(body["before"][TABLES[1]]):
        old_terms[row["corporate_action_id"]].append(row)
    expected: dict[str, list[dict[str, Any]]] = {n: [] for n in TABLES}
    actual_proofs = {}
    docs_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    original_preparation = json.loads((root / "evidence/source-facts-descriptor.json").read_bytes())
    original_selection = {
        d["document_id"]: d["target_action_ids"] for d in original_preparation["documents"]
    }
    if (
        original_preparation.get("identity")
        != digest({k: v for k, v in original_preparation.items() if k != "identity"})
        or original_preparation["identity"] != body["source_preparation_identity"]
        or original_selection
        != {d["document_id"]: d["target_action_ids"] for d in body["documents"]}
        or len(original_selection) != len(body["documents"])
    ):
        raise BoundedError("NONUNIFORM_NATIVE_COMPLETE_ORIGINAL_DOCUMENT_SELECTION_CHANGED")
    prepared_facts = (
        pq.ParquetFile(root / "evidence/prepared-source-facts.parquet").read().to_pylist()
    )
    if len(prepared_facts) != original_preparation["fact_rows"]:
        raise BoundedError("NONUNIFORM_NATIVE_IMMUTABLE_PREREQUISITE_CENSUS_CHANGED")
    for d in body["documents"]:
        prepared_document = next(
            p for p in original_preparation["documents"] if p["document_id"] == d["document_id"]
        )
        if any(
            d[f] != prepared_document[f]
            for f in ("actual_new_acquisition", "text_sha256", "decode_evidence_identity")
        ) or any(
            file_sha(root / d[f])
            != original_preparation["physical_members"][prepared_document[f]]["sha256"]
            for f in ("PDF", "publisher_detail")
        ):
            raise BoundedError("NONUNIFORM_NATIVE_ORIGINAL_SOURCE_CLOCK_OR_MEMBER_CHANGED")
        if _clock(d["actual_new_acquisition"]) > _clock(body["observed_at"]):
            raise BoundedError("NONUNIFORM_NATIVE_ACQUISITION_CHRONOLOGY_INVALID")
        detail = json.loads((root / d["publisher_detail"]).read_bytes())["announcement"]
        if str(detail["announcementId"]) != d["document_id"]:
            raise BoundedError("NONUNIFORM_NATIVE_PUBLISHER_ID_CHANGED")
        destination = budget.root / (d["document_id"] + ".txt")
        text, decoded = decode_proven_notice(root / d["PDF"], destination)
        if (
            file_sha(destination) != d["text_sha256"]
            or decoded["identity"] != d["decode_evidence_identity"]
        ):
            raise BoundedError("NONUNIFORM_NATIVE_ORIGINAL_PDF_REPLAY_CHANGED")
        document = {
            "document_id": d["document_id"],
            "text": text,
            "publisher_security_code": str(detail["secCode"]),
            "publisher_title": detail["announcementTitle"],
        }
        for key in d["target_action_ids"]:
            if key not in targets:
                raise BoundedError("NONUNIFORM_NATIVE_UNKNOWN_DOCUMENT_TARGET")
            claim = parse_native_claims(document, targets[key])
            expected_facts = [
                {
                    **f,
                    "action_id": key,
                    "document_id": d["document_id"],
                    "source_proof_identity": claim["source_fact_proof"]["identity"],
                    "source_acquired_at": d["actual_new_acquisition"],
                    "governance_observed_at": original_preparation["observed_at"],
                    "original_publication_at": None,
                }
                for f in claim["facts"]
            ]
            if Counter(digest(f) for f in expected_facts) != Counter(
                digest(f) for f in prepared_facts if f["action_id"] == key
            ):
                raise BoundedError("NONUNIFORM_NATIVE_LITERAL_PREREQUISITE_REPLAY_CHANGED")
            docs_by_target[key].append(claim)
    for key, target in targets.items():
        if (
            digest(raw[key]) != target["original_raw_row_sha256"]
            or digest(before[key]) != target["native_row_sha256"]
            or len(docs_by_target[key]) != 1
        ):
            raise BoundedError("NONUNIFORM_NATIVE_TARGET_HASH_OR_COMPLETE_DOCUMENT_SCOPE_CHANGED")
        binding = json.loads((root / body["bindings"][key]).read_bytes())
        d = next(d for d in body["documents"] if key in d["target_action_ids"])
        if (
            binding["PDF_sha256"] != file_sha(root / d["PDF"])
            or binding["publisher_detail_sha256"] != file_sha(root / d["publisher_detail"])
            or binding["actual_new_acquisition"] != d["actual_new_acquisition"]
            or binding["source_scope_identity"] != body["source_scope_identity"]
        ):
            raise BoundedError("NONUNIFORM_NATIVE_LOCAL_BINDING_CHANGED")
        actions, terms, proof = revise_nonuniform_terms(
            raw[key],
            before[key],
            old_terms[before[key]["corporate_action_id"]],
            docs_by_target[key][0],
            binding=binding,
            observed_at=body["observed_at"],
            revision=body["source_revision"],
        )
        expected[TABLES[0]].extend(actions)
        expected[TABLES[1]].extend(terms)
        actual_proofs[key] = digest(proof)
    if actual_proofs != body["revision_proof_sha256"]:
        raise BoundedError("NONUNIFORM_NATIVE_REVISION_PROOF_CHANGED")
    for table in TABLES:
        members = [i for i in body["outputs"] if i["table"] == table]
        if not members or len({i["partition"] for i in members}) != len(members):
            raise BoundedError("NONUNIFORM_NATIVE_PHYSICAL_PARTITION_SET_INVALID")
        actual = [r for i in members for r in rows(i)]
        if (
            Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected[table])
            or len(actual) != body["output_rows"][table]
        ):
            raise BoundedError("NONUNIFORM_NATIVE_SERIALIZED_ALLOWED_CHANGES_DIFFER")
        for member in members:
            if not member["rows"] or any(
                partition(r, contract["tables"][table]) != member["partition"] for r in rows(member)
            ):
                raise BoundedError("NONUNIFORM_NATIVE_PHYSICAL_PARTITION_COORDINATES_CHANGED")
            scan = semantic_scan([root / member["path"]], ss[table], check=budget.check)
            if scan != member["semantic_scan"]:
                raise BoundedError("NONUNIFORM_NATIVE_RECOMPUTED_SEMANTICS_DIFFER")
    global_proof = _scan_cash_global(root, body, budget, contract, ss, rows)
    if dict(global_proof) != body["global_checks"]:
        raise BoundedError("NONUNIFORM_NATIVE_GLOBAL_CLOSURE_CHANGED")
    return {
        "status": "NATIVE_LITERAL_POPULATION_REPLAY_PASS_NOT_GENERAL_GROSS_CASH",
        "identity": body["identity"],
        "output_rows": body["output_rows"],
        "new_canonical_knowledge_versions": len(targets),
        "general_gross_cash_qualified": False,
        "tax_basis_unavailable_terms": body["tax_basis_unavailable_terms"],
        "consumer_admission": "NOT_GRANTED",
    }
