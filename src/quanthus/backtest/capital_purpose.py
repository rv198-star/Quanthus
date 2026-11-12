"""Explicit, exact-key P10 input for the bounded capital counterfactual."""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.hf_dwd_research import digest, file_digest, read


def require(value: Any, reason: str) -> None:
    if not value:
        raise ValueError("CAPITAL_PURPOSE_" + reason)


def normalized(row: dict[str, Any]) -> dict[str, Any]:
    """Compare the complete native row, preserving null and numeric semantics."""
    result = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime, Decimal)):
            value = str(value)
        result[key] = value
    return result


def verify_package(q: dict[str, Any], root: Path) -> dict[str, Any]:
    descriptor = q["purpose"]
    declared = descriptor["members"]
    names = [item["path"] for item in declared]
    require(len(names) == len(set(names)) and len(names) > 1, "MEMBER_SET")
    require(sum(item["bytes"] for item in declared) <= 4 * 1024 * 1024, "BYTE_LIMIT")
    for item in declared:
        name = item["path"]
        require(Path(name).name == name and not (root / name).is_symlink(), "MEMBER_PATH")
        path = root / name
        require(path.is_file(), "MEMBER_MISSING")
        require(path.stat().st_size == item["bytes"], "MEMBER_BYTES")
        require(file_digest(path) == item["sha256"], "MEMBER_HASH")
    require({p.name for p in root.iterdir()} == set(names), "UNDECLARED_MEMBER")
    path = root / descriptor["purpose_path"]
    require(path.name in names and file_digest(path) == descriptor["purpose_sha256"], "MANIFEST")
    return read(path)


def copy_package(q: dict[str, Any], cache: Path, target: Path) -> dict[str, Any]:
    """Staging only: each Job owns distinct, verified physical input bytes."""
    verify_package(q, cache)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in q["purpose"]["members"]:
        destination = target / item["path"]
        require(not destination.exists(), "NONEMPTY_JOB_PURPOSE")
        shutil.copyfile(cache / item["path"], destination)
        copied += destination.stat().st_size
    verify_package(q, target)
    return {"files": len(q["purpose"]["members"]), "copied_bytes": copied}


def load_purpose(q: dict[str, Any], root: Path) -> dict[str, Any]:
    """Validate the frozen package before reading any result-bearing native rows."""
    m = verify_package(q, root)
    require(m["schema"] == "quanthus.p10-capital-purpose.v1", "SCHEMA")
    require(m["status"] == "qualified_scoped_retrospective", "QUALIFICATION")
    require(m["native_authority"] == q["authority"], "NATIVE_AUTHORITY")
    require(m["rule_digest"] == digest(m["rule"]), "RULE_HASH")
    known = datetime.fromisoformat(m["knowledge_from"])
    qualified = datetime.fromisoformat(m["qualified_at"])
    observed = datetime.fromisoformat(m["observed_at"])
    cutoff = datetime.fromisoformat(q["source_knowledge_cutoff"])
    require(all(t.tzinfo is not None for t in (known, qualified, observed, cutoff)), "CLOCK_AWARE")
    require(observed <= known == qualified <= cutoff and m["knowledge_to"] is None, "CLOCK")
    require(m["qualified_at"] == q["purpose"]["qualified_at"], "QUALIFIED_CLOCK_BINDING")
    require(m["review_roles"]["review_status"] == "pass_after_package_completion", "REVIEW")
    for item in m["members"]:
        require(item in q["purpose"]["members"], "INNER_MEMBER")
    require(set(m["documents"].values()) == {x["path"] for x in m["members"]}, "DOCUMENT_CLOSURE")
    docs = {key: read(root / value) for key, value in m["documents"].items()}
    require(docs["source-receipts"]["source_hash_probe"] == m["sources"], "RAW_RECEIPT_BINDING")
    scope, event = m["scope"], m["effective_event"]
    require(scope["use"] == "guarded_retrospective_capital_only", "USE")
    require(scope["uncovered_policy"] == "original_native_guard", "UNSCOPED_POLICY")
    day = date.fromisoformat(scope["ex_date"])
    require(date(2020, 1, 2) <= day <= date(2021, 12, 31), "DATE_SCOPE")
    require(event["ex_date"] == scope["ex_date"], "EVENT_DATE")
    require(event["action_type"] == "CASH_DIVIDEND" and event["currency"] == "CNY", "EVENT_TYPE")
    require(event["bonus_shares"] is None and event["transfer_shares"] is None, "UNKNOWN_SHARES")
    em = docs["em-parser-proof"]["raw"]
    block = docs["f10-exact-block"]["raw_block"]
    f10 = docs["f10-parser-proof"]["matched_record"]
    history = docs["history-identity-proof"]
    require(em["SECURITY_CODE"] == f10["stock_code"] == scope["stock_code"], "RAW_SECURITY")
    require(em["EX_DIVIDEND_DATE"][:10] == f10["ex_date"] == scope["ex_date"], "RAW_DATE")
    require(em["REPORT_DATE"][:10] == event["report_date"], "REPORT_DATE")
    require(em["NOTICE_DATE"][:10] == event["implementation_notice_date"], "NOTICE_DATE")
    require(
        em["EQUITY_RECORD_DATE"][:10] == f10["record_date"] == event["record_date"], "RECORD_DATE"
    )
    require(f10["payment_date"] == event["payment_date"], "PAYMENT_DATE")
    em_match = re.fullmatch(
        r"10派(\d+(?:\.\d+)?)元\(含税,扣税后\d+(?:\.\d+)?元\)", em["IMPL_PLAN_PROFILE"]
    )
    f10_match = re.search(r"10派(\d+(?:\.\d+)?)元\(含税\)", block)
    require(em_match is not None and f10_match is not None, "EXPLICIT_TEN_SHARE_TEXT")
    assert em_match is not None and f10_match is not None
    cash = Decimal(em_match.group(1)) / 10
    require(
        cash == Decimal(f10_match.group(1)) / 10 == Decimal(em["PRETAX_BONUS_RMB"]) / 10, "RAW_CASH"
    )
    require(
        cash == Decimal(event["cash_per_share"]) == Decimal(f10["cash_per_share"]), "EFFECTIVE_CASH"
    )
    require(history["exact_matches"] == 1, "HISTORY_UNIQUENESS")
    raw_history = history["raw_row"]
    require(
        raw_history["stock_code"] == scope["stock_code"] and raw_history["progress"] == "实施",
        "HISTORY_IDENTITY",
    )
    require(raw_history["ex_dividend_date"][:10] == scope["ex_date"], "HISTORY_DATE")
    require(
        raw_history["announce_date"][:10] == event["implementation_notice_date"], "HISTORY_NOTICE"
    )
    require(docs["independent-review"]["verdict"] == "pass", "ROLE_RECEIPT")
    return {"manifest": m, "documents": docs, "known": known, "cash": cash}


def effective_event(
    q: dict[str, Any],
    purpose: dict[str, Any],
    actions: list[dict[str, Any]],
    terms: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Replace exactly the reviewed observation group in memory, preserving its rows."""
    m, docs = purpose["manifest"], purpose["documents"]
    scope, event = m["scope"], m["effective_event"]
    expected = docs["source-records"]
    source_actions = [a for a in expected["dwd_corporate_action"] if a["knowledge_to"] is None]
    source_terms = [t for t in expected["dwd_corporate_action_term"] if t["knowledge_to"] is None]
    require(
        sorted(a["corporate_action_id"] for a in actions) == scope["covered_latest_actions"],
        "ACTION_CLOSURE",
    )
    require(
        sorted((normalized(a) for a in actions), key=lambda a: a["corporate_action_id"])
        == sorted(source_actions, key=lambda a: a["corporate_action_id"]),
        "ACTION_BODY",
    )
    actual_terms = [t for a in actions for t in terms[a["corporate_action_id"]]]
    require(
        sorted((normalized(t) for t in actual_terms), key=lambda t: t["corporate_action_term_id"])
        == sorted(source_terms, key=lambda t: t["corporate_action_term_id"]),
        "TERM_BODY",
    )
    require(
        sorted(t["corporate_action_term_id"] for t in actual_terms)
        == scope["covered_latest_terms"],
        "TERM_CLOSURE",
    )
    require(
        {x["action_id"] for x in m["source_relations"]} == set(scope["covered_latest_actions"]),
        "ACTION_RELATIONS",
    )
    require(
        {x["term_id"] for x in m["term_relations"]} == set(scope["covered_latest_terms"]),
        "TERM_RELATIONS",
    )
    require(
        len(m["source_relations"]) == len(actions)
        and len(m["term_relations"]) == len(actual_terms),
        "RELATION_UNIQUENESS",
    )
    by_term = {t["corporate_action_term_id"]: t for t in source_terms}
    for relation in m["term_relations"]:
        t = by_term[relation["term_id"]]
        require(
            relation["action_id"] == t["corporate_action_id"]
            and relation["source_record_id"] == t["source_record_id"]
            and relation["effective_event_id"] == event["economic_event_id"],
            "TERM_RELATION_IDENTITY",
        )
        require(
            relation["term_type"] == t["term_type"]
            and relation["original_amount_per_share"] == t["amount_per_share"]
            and relation["original_ratio_numerator"] == t["ratio_numerator"]
            and relation["original_ratio_denominator"] == t["ratio_denominator"],
            "TERM_RELATION_VALUE",
        )
    raw_hashes = {"sha256:" + s["sha256"].removeprefix("sha256:") for s in m["sources"]}
    for a in actions:
        require(
            a["security_id"] == scope["security_id"] and str(a["ex_date"]) == scope["ex_date"],
            "NATIVE_KEY",
        )
        require(a["source_artifact_sha256"] in raw_hashes, "RAW_ARTIFACT_BINDING")
    f10 = docs["f10-parser-proof"]["matched_record"]
    require(any(a["source_record_id"] == f10["source_row_key"] for a in actions), "F10_SOURCE_ID")
    history = docs["history-identity-proof"]
    require(
        any(
            a["corporate_action_id"] == history["reconstructed_action_id"]
            and a["source_record_id"] == history["reconstructed_source_record_id"]
            for a in actions
        ),
        "HISTORY_SOURCE_ID",
    )
    revision = m["purpose_version"] + "@" + q["purpose"]["revision"]
    header = dict(
        actions[0],
        corporate_action_id=event["economic_event_id"],
        record_source="p10_r4_exact_cash_event",
        source_record_id=event["economic_event_id"],
        source_revision=revision,
        source_artifact_sha256=q["purpose"]["purpose_sha256"],
        knowledge_from=purpose["known"],
        knowledge_to=None,
        announced_at=None,
        effective_at=datetime.fromisoformat(event["effective_at"]),
        record_date=date.fromisoformat(event["record_date"]),
        payment_date=date.fromisoformat(event["payment_date"]),
    )
    term = dict(
        header,
        corporate_action_term_id=event["economic_event_id"] + "-cash",
        term_type="CASH_AMOUNT_PER_SHARE",
        option_id=None,
        currency="CNY",
        amount_per_share=purpose["cash"],
        ratio_numerator=None,
        ratio_denominator=None,
        subscription_price=None,
    )
    receipt = {
        "economic_event_id": event["economic_event_id"],
        "source_action_ids": scope["covered_latest_actions"],
        "source_term_ids": scope["covered_latest_terms"],
        "purpose_sha256": q["purpose"]["purpose_sha256"],
        "purpose_known_at": str(purpose["known"]),
        "source_relations": m["source_relations"],
        "cash_per_share": str(purpose["cash"]),
        "scope": scope,
        "native_rows_preserved": True,
    }
    return header, [term], receipt
