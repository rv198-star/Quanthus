"""Exact shared cash admission for retained MD events outside normalization v3.

This is a bounded new source revision, never vendor priority or recovery of an
old manifest. Its caller must freeze and independently replay the complete F10
raw/receipt dependency before using the returned binding.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_canonical_action_revision import IDENTITY, SOURCE_VERSION, _sha
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
