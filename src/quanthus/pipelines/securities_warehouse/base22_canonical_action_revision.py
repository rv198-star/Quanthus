"""Source-bound new knowledge versions on the retained canonical event identity.

Inputs are accepted raw-replay-qualified domain observations. The batch owns and
verifies that complete closure before calling this pure projection. Neither this
projection nor a same-date match can restore the missing legacy normalization or
arbitrate conflicting providers. Old versions and unknown clocks remain explicit.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-canonical-action-raw-successor-v1"
MAX_PARTITION_ROWS = 65536
SOURCES = {
    "p10_retained_eastmoney_implementation",
    "base22_f10/dividend_implemented",
    "base22_retained_factor/retrospective",
}
IDENTITY = ("company_id", "security_id", "effective_at", "ex_date")
SOURCE_VERSION = (
    "record_source",
    "source_revision",
    "source_artifact_sha256",
    "source_record_id",
    "effective_at",
    "knowledge_from",
    "knowledge_to",
    "announced_at",
)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def revise_canonical_action(
    original_raw: dict[str, Any],
    before: dict[str, Any],
    qualified_action: dict[str, Any],
    qualified_terms: list[dict[str, Any]],
    *,
    binding: dict[str, Any],
    observed_at: Any,
    revision: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Bind an uncontested complete raw scope, never choose a conflicting amount.

    Every incoming typed atom is retained, except the explicit stable old event/
    term IDs, real new knowledge time and explicit successor revision. Missing
    donor terms are left missing; a zero in another source cannot fill them.
    """
    original_key = original_raw.get("source_row_key")
    if (
        not isinstance(original_key, str)
        or not original_key
        or before.get("corporate_action_id")
        != _stable_id("corporate_action", "corporate_actions", original_key)
        or before.get("source_record_id") != original_key
        or before.get("record_source") != "corporate_actions"
        or before.get("knowledge_to") is not None
        or type(original_raw.get("ex_date")) is not date
        or original_raw["ex_date"] > THROUGH
        or before.get("ex_date") != original_raw["ex_date"]
        or any(qualified_action.get(k) != before.get(k) for k in IDENTITY)
    ):
        raise BoundedError("CANONICAL_ACTION_EXACT_OLD_RAW_ENTITY_OR_DATE_BINDING_FAILED")
    if (
        binding.get("disposition") != "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR"
        or binding.get("historical_PIT_admission") is not False
        or binding.get("old_manifest_restored") is not False
        or not _sha(binding.get("source_scope_identity"))
        or not _sha(binding.get("raw_replay_receipt_sha256"))
        or binding.get("original_raw_row_sha256") != digest(original_raw)
        or binding.get("qualified_action_sha256") != digest(qualified_action)
        or binding.get("qualified_term_rows_sha256") != sorted(digest(r) for r in qualified_terms)
        or qualified_action.get("record_source") not in SOURCES
        or not _sha(qualified_action.get("source_artifact_sha256"))
        or qualified_action.get("announced_at") is not None
        or qualified_action.get("knowledge_to") is not None
        or re.fullmatch(re.escape(CONTRACT_ID) + r":sha256:[0-9a-f]{64}", revision) is None
    ):
        raise BoundedError("CANONICAL_ACTION_SOURCE_NOT_EXPLICITLY_RAW_QUALIFIED_UNCONTESTED")
    clock = _clock(observed_at)
    for value in (before.get("knowledge_from"), qualified_action.get("knowledge_from")):
        if not isinstance(value, datetime) or value.utcoffset() is None or clock <= value:
            raise BoundedError("CANONICAL_ACTION_NEW_KNOWLEDGE_MUST_FOLLOW_REAL_SOURCE")
    if not qualified_terms or len(qualified_terms) > 8:
        raise BoundedError("CANONICAL_ACTION_COMPLETE_TERM_SCOPE_INVALID")
    parent_id = before["corporate_action_id"]
    new_action = {
        **qualified_action,
        "corporate_action_id": parent_id,
        "source_revision": revision,
        "knowledge_from": clock,
        "knowledge_to": None,
    }
    seen = set()
    terms = []
    for row in qualified_terms:
        typ = row.get("term_type")
        option = row.get("option_id")
        if (
            row.get("corporate_action_id") != qualified_action["corporate_action_id"]
            or any(row.get(k) != qualified_action.get(k) for k in SOURCE_VERSION)
            or not isinstance(typ, str)
            or not typ
            or option is not None
            or typ in seen
        ):
            raise BoundedError("CANONICAL_ACTION_TERM_SOURCE_VERSION_OR_IDENTITY_FAILED")
        for f in ("amount_per_share", "ratio_numerator", "ratio_denominator", "subscription_price"):
            number = row.get(f)
            if number is not None and (not isinstance(number, Decimal) or not number.is_finite()):
                raise BoundedError("CANONICAL_ACTION_EXACT_TYPED_TERM_NUMBER_REQUIRED")
        seen.add(typ)
        terms.append(
            {
                **row,
                "corporate_action_id": parent_id,
                "corporate_action_term_id": _stable_id("corporate_action_term", parent_id, typ),
                "source_revision": revision,
                "knowledge_from": clock,
                "knowledge_to": None,
            }
        )
    proof = {
        "contract_id": CONTRACT_ID,
        "original_raw_row_sha256": digest(original_raw),
        "original_before_action_sha256": digest(before),
        "qualified_action_sha256": digest(qualified_action),
        "qualified_term_rows_sha256": sorted(digest(r) for r in qualified_terms),
        "source_scope_identity": binding["source_scope_identity"],
        "source_raw_replay_receipt_sha256": binding["raw_replay_receipt_sha256"],
        "new_action_sha256": digest(new_action),
        "new_terms_sha256": sorted(digest(r) for r in terms),
        "observed_at": clock,
        "old_entity_and_business_coordinates_preserved": True,
        "old_typed_atoms_changed": 0,
        "absent_terms_imputed": False,
        "conflicting_source_priority_granted": False,
        "provider_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "old_normalization_manifest_restored": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return new_action, terms, proof


def validate_revision_partition(
    before_rows: Iterable[dict[str, Any]],
    after_rows: Iterable[dict[str, Any]],
    selected_parent_ids: set[str],
    new_rows: list[dict[str, Any]],
    *,
    observed_at: Any,
    action: bool,
) -> dict[str, Any]:
    """Every old atom survives; only selected open intervals may end at this clock.

    Exact new bodies come from the raw-bound projection, not a file-count claim.
    Global keys and transitive references are independently checked by GlobalScan.
    """
    clock = _clock(observed_at)
    identifier = "corporate_action_id" if action else "corporate_action_term_id"
    versions = set()
    expected: Counter[str] = Counter()
    count = 0
    selected = set()
    for old in before_rows:
        count += 1
        if count > MAX_PARTITION_ROWS:
            raise BoundedError("CANONICAL_ACTION_PARTITION_BOUND_EXCEEDED")
        version = old[identifier], old["effective_at"], old["knowledge_from"]
        if version in versions:
            raise BoundedError("CANONICAL_ACTION_DUPLICATE_BEFORE_VERSION")
        versions.add(version)
        if old["corporate_action_id"] in selected_parent_ids:
            if old.get("knowledge_to") is not None or clock <= old["knowledge_from"]:
                raise BoundedError("CANONICAL_ACTION_SELECTED_OLD_INTERVAL_NOT_OPEN")
            selected.add(old["corporate_action_id"])
            expected[digest({**old, "knowledge_to": clock})] += 1
        else:
            expected[digest(old)] += 1
    for row in new_rows:
        version = row[identifier], row["effective_at"], row["knowledge_from"]
        if (
            row["corporate_action_id"] not in selected_parent_ids
            or row["knowledge_from"] != clock
            or row.get("knowledge_to") is not None
            or version in versions
        ):
            raise BoundedError("CANONICAL_ACTION_NEW_VERSION_NOT_EXACT_SELECTED_SCOPE")
        versions.add(version)
        expected[digest(row)] += 1
    actual: Counter[str] = Counter()
    total = 0
    for row in after_rows:
        total += 1
        if total > MAX_PARTITION_ROWS * 2:
            raise BoundedError("CANONICAL_ACTION_OUTPUT_PARTITION_BOUND_EXCEEDED")
        actual[digest(row)] += 1
    if actual != expected or total != count + len(new_rows):
        raise BoundedError("CANONICAL_ACTION_UNAUTHORIZED_ATOM_KEY_OR_VERSION_CHANGE")
    if action and selected != selected_parent_ids:
        raise BoundedError("CANONICAL_ACTION_REQUIRED_OLD_PARENT_MISSING")
    return {
        "before_rows": count,
        "after_rows": total,
        "new_versions": len(new_rows),
        "old_atoms_changed_except_selected_interval_end": 0,
        "new_terms_can_be_missing_only_when_raw_source_does_not_supply_them": True,
    }
