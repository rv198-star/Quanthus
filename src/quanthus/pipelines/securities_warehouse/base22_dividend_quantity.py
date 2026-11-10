"""New, source-bound knowledge versions for legacy per-ten-share ratio errors."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from quanthus.corporate_action_normalization import _digest as raw_digest

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .disclosure_event import _action_coordinate, _decimal, _provider_timestamp, _stable_id

CONTRACT_ID = "p10-base22-native-dividend-perten-ratio-revision-v1"
FIELDS = {
    "BONUS_SHARES_PER_SHARE": "bonus_shares",
    "TRANSFER_SHARES_PER_SHARE": "transfer_shares",
}
MAX_PARTITION_ROWS = 65536


def dividend_raw_identity(raw: dict[str, Any]) -> tuple[str, str]:
    """Reproduce the frozen legacy source key without inventing date availability."""
    row = dict(raw, ex_date=raw.get("ex_dividend_date"))
    try:
        effective, _ = _action_coordinate(row)
    except (ValueError, RuntimeError) as exc:
        raise BoundedError("DIVIDEND_QUANTITY_RAW_DATE_INVALID") from exc
    if effective > THROUGH or not isinstance(raw.get("stock_code"), str):
        raise BoundedError("DIVIDEND_QUANTITY_RAW_IDENTITY_INVALID")
    source_key = _stable_id(
        "action_source",
        raw["stock_code"],
        effective.isoformat(),
        str(raw.get("announce_date") or ""),
        str(raw.get("dividend_per_share") or raw.get("cash_per_share") or ""),
        str(raw.get("bonus_shares") or ""),
        str(raw.get("transfer_shares") or ""),
    )
    return _stable_id("corporate_action", "dividend_history", source_key), source_key


def correct_dividend_quantity(
    raw: dict[str, Any],
    action: dict[str, Any],
    before: dict[str, Any],
    *,
    raw_sha256: str,
    observed_at: Any,
    revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep the exact old numerator and express its evidenced per-ten unit as /10.

    Unlike dividing into Decimal(38,10), the existing numerator/denominator schema
    preserves ratios requiring eleven decimal places. Old versions/zero/cash are
    not replaced; callers close only this old open interval at the real new clock.
    """
    if re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision) is None:
        raise BoundedError("DIVIDEND_QUANTITY_REVISION_NOT_EXPLICIT")
    term_type = before.get("term_type")
    field = FIELDS.get(term_type) if isinstance(term_type, str) else None
    if field is None:
        raise BoundedError("DIVIDEND_QUANTITY_CASH_OR_UNKNOWN_TERM_FORBIDDEN")
    action_id, source_key = dividend_raw_identity(raw)
    if any(
        before.get(f) != action.get(f)
        for f in (
            "corporate_action_id",
            "source_record_id",
            "record_source",
            "source_revision",
            "source_artifact_sha256",
            "knowledge_from",
            "effective_at",
            "announced_at",
        )
    ) or any(
        r.get("corporate_action_id") != action_id
        or r.get("source_record_id") != source_key
        or r.get("record_source") != "dividend_history"
        or r.get("source_artifact_sha256") != raw_sha256
        for r in (action, before)
    ):
        raise BoundedError("DIVIDEND_QUANTITY_EXACT_ORIGINAL_ROW_BINDING_INVALID")
    if before.get("corporate_action_term_id") != _stable_id(
        "corporate_action_term", action_id, before["term_type"]
    ):
        raise BoundedError("DIVIDEND_QUANTITY_TERM_ID_INVALID")
    try:
        expected = _decimal(raw.get(field), "retained share quantity")
    except (ValueError, RuntimeError) as exc:
        raise BoundedError("DIVIDEND_QUANTITY_RAW_NUMBER_INVALID") from exc
    if (
        expected is None
        or expected <= 0
        or isinstance(raw.get(field), bool)
        or before.get("ratio_numerator") != expected
        or before.get("ratio_denominator") != Decimal(1)
        or any(
            before.get(f) is not None
            for f in (
                "amount_per_share",
                "currency",
                "subscription_price",
                "option_id",
                "knowledge_to",
            )
        )
        or action.get("knowledge_to") is not None
    ):
        raise BoundedError("DIVIDEND_QUANTITY_ONLY_RAW_BOUND_NONZERO_OPEN_RATIO_ALLOWED")
    clock = _clock(observed_at)
    if not isinstance(before.get("knowledge_from"), datetime) or clock <= before["knowledge_from"]:
        raise BoundedError("DIVIDEND_QUANTITY_NEW_KNOWLEDGE_NOT_LATER")
    new = {
        **before,
        "ratio_denominator": Decimal("10.0000000000"),
        "source_revision": revision,
        "knowledge_from": clock,
        "knowledge_to": None,
        # Retained provider date has no proven publication instant. Preserve the
        # original date/raw/before evidence, not a fictitious new available time.
        "announced_at": None,
    }
    proof = {
        "contract_id": CONTRACT_ID,
        "old_typed_row_sha256": digest(before),
        "complete_original_raw_row_sha256": raw_digest(raw),
        "new_typed_row_sha256": digest(new),
        "raw_source_field": field,
        "raw_quantity_unit": "per_ten_shares",
        "new_ratio_denominator": 10,
        "old_numerator_all_atoms_preserved": True,
        "original_before_interval_preserved_until": clock,
        "provider_acquisition": None,
        "historical_publication_instant": None,
        "knowledge_disposition": "ACTUAL_NEW_P10_GOVERNANCE_OBSERVATION",
        "cash_source_priority_granted": False,
        "old_normalization_recovered": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return new, proof


def correct_dividend_action_type(
    raw: dict[str, Any],
    before: dict[str, Any],
    *,
    raw_sha256: str,
    observed_at: Any,
    revision: str,
) -> dict[str, Any]:
    """Version the same parent event's evidenced cash/bonus/transfer composition.

    The old builder defaulted every dividend source event to CASH_DIVIDEND. This
    dependent correction retains status, identity and business coordinate, and
    does not arbitrate between providers or change any cash amount.
    """
    action_id, key = dividend_raw_identity(raw)
    effective, _ = _action_coordinate(dict(raw, ex_date=raw.get("ex_dividend_date")))
    progress = raw.get("progress")
    statuses = {"实施": "IMPLEMENTED", "预案": "PROPOSED", "不分配": "NO_DISTRIBUTION"}
    status = statuses.get(progress) if isinstance(progress, str) else None
    if (
        before.get("corporate_action_id") != action_id
        or before.get("source_record_id") != key
        or before.get("record_source") != "dividend_history"
        or before.get("source_artifact_sha256") != raw_sha256
        or status is None
        or before.get("action_status") != status
        or before.get("effective_at") != _provider_timestamp(effective, "original business date")
        or before.get("knowledge_to") is not None
        or re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision) is None
    ):
        raise BoundedError("DIVIDEND_QUANTITY_PARENT_EXACT_BINDING_INVALID")
    values = {}
    for field in ("dividend_per_share", "bonus_shares", "transfer_shares"):
        try:
            value = _decimal(raw.get(field), "retained action composition")
        except (ValueError, RuntimeError) as exc:
            raise BoundedError("DIVIDEND_QUANTITY_PARENT_RAW_NUMBER_INVALID") from exc
        if value is None or value < 0 or isinstance(raw.get(field), bool):
            raise BoundedError("DIVIDEND_QUANTITY_PARENT_COMPOSITION_UNKNOWN")
        if value > 0:
            values[field] = value
    if not any(field in values for field in ("bonus_shares", "transfer_shares")):
        raise BoundedError("DIVIDEND_QUANTITY_PARENT_NO_SHARE_CORRECTION")
    typ = (
        "MIXED_DISTRIBUTION"
        if len(values) > 1
        else (
            "BONUS_SHARE_DISTRIBUTION" if "bonus_shares" in values else "CAPITAL_RESERVE_TRANSFER"
        )
    )
    clock = _clock(observed_at)
    if clock <= before["knowledge_from"]:
        raise BoundedError("DIVIDEND_QUANTITY_PARENT_CLOCK_NOT_LATER")
    return {
        **before,
        "action_type": typ,
        "announced_at": None,
        "source_revision": revision,
        "knowledge_from": clock,
        "knowledge_to": None,
    }


def validate_quantity_versions(
    before_rows: Iterable[dict[str, Any]],
    after_rows: Iterable[dict[str, Any]],
    corrections: dict[tuple[Any, ...], dict[str, Any]],
    *,
    action: bool = False,
) -> dict[str, Any]:
    """Exact bounded partition conservation; no new source can overwrite old atoms."""
    expected = {}
    selected = set()
    count = 0
    fields = (
        ("corporate_action_id", "effective_at")
        if action
        else ("corporate_action_id", "corporate_action_term_id", "effective_at")
    )
    for old in before_rows:
        count += 1
        if count > MAX_PARTITION_ROWS:
            raise BoundedError("DIVIDEND_QUANTITY_PARTITION_SCOPE_EXCEEDED")
        key = tuple(old[k] for k in fields)
        version = (*key, old["knowledge_from"])
        if version in expected:
            raise BoundedError("DIVIDEND_QUANTITY_DUPLICATE_BEFORE_VERSION")
        preserved = old
        if key in corrections:
            new = corrections[key]
            if (
                key in selected
                or old["knowledge_to"] is not None
                or new["knowledge_from"] <= old["knowledge_from"]
            ):
                raise BoundedError("DIVIDEND_QUANTITY_BEFORE_INTERVAL_CONFLICT")
            selected.add(key)
            preserved = {**old, "knowledge_to": new["knowledge_from"]}
            expected[(*key, new["knowledge_from"])] = digest(new)
        expected[version] = digest(preserved)
    if selected != set(corrections):
        raise BoundedError("DIVIDEND_QUANTITY_REQUIRED_BEFORE_KEY_MISSING")
    total = 0
    for row in after_rows:
        total += 1
        if total > MAX_PARTITION_ROWS * 2:
            raise BoundedError("DIVIDEND_QUANTITY_OUTPUT_SCOPE_EXCEEDED")
        version = tuple(row[k] for k in (*fields, "knowledge_from"))
        if expected.pop(version, None) != digest(row):
            raise BoundedError("DIVIDEND_QUANTITY_UNAUTHORIZED_ATOM_OR_KEY_CHANGE")
    if expected or total != count + len(corrections):
        raise BoundedError("DIVIDEND_QUANTITY_REQUIRED_VERSION_MISSING")
    return {
        "before_rows": count,
        "after_rows": total,
        "exact_new_versions": len(corrections),
        "permitted_old_changes": "ONLY_SELECTED_OPEN_KNOWLEDGE_TO",
        "unselected_and_zero_and_cash_atoms_preserved": True,
    }
