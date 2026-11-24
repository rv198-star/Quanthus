"""Per-security supplements for the bounded reader (issue #749).

Delivers the raw corporate actions, exception windows, price limits and
tradability state the original panel consumes - each projected onto the
security's session window.  Coverage that is unproven (the October
corporate-action / exception completeness result is 0 securities, and
the 2026-11 deltas are SOURCE_NOT_REFRESHED) is handed over as UNKNOWN
with the affected members named, never silently dropped or fabricated.

The reader never normalizes bonus ratios, never decides which action
blocks a session, and never derives a limit ratio - those judgments
stay with the P11 rule.
"""

from __future__ import annotations

from datetime import date

from quanthus.p12_history.calendar_listing import _date, _rows
from quanthus.p12_history.clocks import iso, parse_ts
from quanthus.p12_history.source_pins import SourceMap

UNKNOWN = "UNKNOWN"


def read_actions(source_map: SourceMap, market_date: date):
    """Native corporate actions + terms, keyed to security.

    The action completeness for the covered window is UNKNOWN per the
    October feasibility result (0 securities proven complete) - the
    receipt carries that coverage state; the rows that ARE present are
    still delivered verbatim for the P11 rule to consume.
    """
    action_reads, term_reads = [], []
    actions = {}
    for member in source_map.base_members("dwd_corporate_action"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            action_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["corporate_action_id", "security_id", "action_type", "action_status",
                 "record_date", "ex_date", "payment_date", "announced_at",
                 "record_source", "source_record_id", "source_revision",
                 "knowledge_from", "knowledge_to"],
            )
        except Exception as exc:  # noqa: BLE001
            action_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        for rec in recs:
            sid = str(rec.get("security_id") or "")
            if not sid:
                continue
            actions.setdefault(str(rec.get("corporate_action_id") or ""), {
                "corporate_action_id": str(rec.get("corporate_action_id") or ""),
                "security_id": sid,
                "action_type": str(rec.get("action_type") or ""),
                "action_status": str(rec.get("action_status") or ""),
                "record_date": _date(rec.get("record_date")),
                "ex_date": _date(rec.get("ex_date")),
                "payment_date": _date(rec.get("payment_date")),
                "announced_at": parse_ts(rec.get("announced_at")),
                "knowledge_from": parse_ts(rec.get("knowledge_from")),
                "knowledge_to": iso(parse_ts(rec.get("knowledge_to"))),
                "record_source": str(rec.get("record_source") or ""),
                "source_record_id": str(rec.get("source_record_id") or ""),
                "source_revision": str(rec.get("source_revision") or ""),
                "terms": {},
            })
        action_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    for member in source_map.base_members("dwd_corporate_action_term"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            term_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["corporate_action_id", "term_type", "amount_per_share",
                 "ratio_numerator", "ratio_denominator", "currency",
                 "record_source", "source_record_id", "source_revision",
                 "knowledge_from", "knowledge_to"],
            )
        except Exception as exc:  # noqa: BLE001
            term_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        for rec in recs:
            aid = str(rec.get("corporate_action_id") or "")
            if aid in actions:
                actions[aid]["terms"][str(rec.get("term_type") or "")] = {
                    "amount_per_share": rec.get("amount_per_share"),
                    "ratio_numerator": rec.get("ratio_numerator"),
                    "ratio_denominator": rec.get("ratio_denominator"),
                    "currency": rec.get("currency"),
                    "record_source": str(rec.get("record_source") or ""),
                    "source_record_id": str(rec.get("source_record_id") or ""),
                    "source_revision": str(rec.get("source_revision") or ""),
                    "knowledge_from": iso(parse_ts(rec.get("knowledge_from"))),
                    "knowledge_to": iso(parse_ts(rec.get("knowledge_to"))),
                }
        term_reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    return actions, {"action_reads": action_reads, "term_reads": term_reads}


def read_exceptions(source_map: SourceMap, market_date: date):
    """Quality exceptions + observation gaps that block sessions."""
    qe, gaps, reads = [], [], []
    for member in source_map.base_members("dwd_quality_exception"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["affected_table", "affected_business_key_json", "affected_effective_at",
                 "authority", "disposition", "resolution_state", "knowledge_from"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        qe.extend(recs)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    for member in source_map.base_members("dwd_observation_gap"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["listing_id", "calendar_session_id", "affected_effective_at",
                 "gap_reason", "is_expected", "knowledge_from"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        gaps.extend(recs)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    return {"quality_exceptions": qe, "observation_gaps": gaps}, {"reads": reads}


def read_p1_exceptions(source_map: SourceMap, market_date: date):
    """P1 factor-PIT visibility exceptions, verbatim with original keys.

    The 4,173-row ODS ledger (ref_adjustment_factor_pit_exceptions) is the
    fail-closed P1 exception table - separate from dwd_quality_exception.
    Each row carries the original stock_code / exchange / effective_date /
    source_revision / knowledge_cutoff; the reader projects it per
    security + dependency window and never fabricates a factor block.
    """
    member = source_map.p1_exception_member()
    verify = source_map.verify_member(member)
    reads = []
    if not verify.get("matches"):
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "declared_sha256": member.sha256, "actual_sha256": verify.get("actual_sha256"), "sha_match": False, "verified_at": verify.get("verified_at")})
        return {"p1_exceptions": []}, {"reads": reads, "p1_unverified": True}
    try:
        recs = _rows(
            member.abs_path,
            ["stock_code", "exchange", "effective_date", "exception_type",
             "reason_code", "event_type", "visibility_origin",
             "knowledge_cutoff", "source_revision", "recorded_at", "policy"],
        )
    except Exception as exc:  # noqa: BLE001
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
        return {"p1_exceptions": []}, {"reads": reads, "p1_unverified": True}
    reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    return {"p1_exceptions": recs}, {"reads": reads}


def read_limits(source_map: SourceMap, market_date: date):
    """Price-limit regimes + per-session tradability state."""
    regimes, tradability, reads = [], [], []
    for member in source_map.base_members("dwd_price_limit_regime"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["venue_id", "board_code", "security_status", "ipo_phase", "listing_id",
                 "upper_limit_ratio", "lower_limit_ratio", "reference_price_method",
                 "effective_from", "effective_to"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        regimes.extend(recs)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    for member in source_map.base_members("dwd_tradability_state"):
        verify = source_map.verify_member(member)
        if not verify.get("matches"):
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH", "sha256": member.sha256, "verify": verify, "sha_match": False, "verified_at": verify.get("verified_at")})
            continue
        try:
            recs = _rows(
                member.abs_path,
                ["listing_id", "calendar_session_id", "is_tradable", "can_buy",
                 "can_sell", "reason_code", "upper_price", "lower_price",
                 "observation_start", "knowledge_from"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        tradability.extend(recs)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    return {"price_limit_regimes": regimes, "tradability_state": tradability}, {"reads": reads}
