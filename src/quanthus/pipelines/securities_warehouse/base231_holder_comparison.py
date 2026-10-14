"""Compare complete source matrices without merging ranks or inferring exits.

An official source-reported view is a separate observation, not proof that an
older provider value was historically false. Differences retain exact hashes.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_holder_components_v2 import _scalar

CONTRACT_ID = "p10-base231-exact-holder-source-difference-classification-v1"


def compare_matrices(
    official: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> dict[str, Any]:
    """Classify only shared source fields; unknown percentages stay unknown."""
    if not official or not previous:
        raise BoundedError("HOLDER_COMPARISON_EMPTY_SOURCE")
    frames = {
        (r["security_id"], r["company_id"], r["holding_type"], r["observation_start"])
        for r in official + previous
    }
    if len(frames) != 1 or next(iter(frames))[3].astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date() > date(2026, 9, 30):
        raise BoundedError("HOLDER_COMPARISON_DIFFERENT_COORDINATES")
    if len(official) != 10 or sorted(r["holder_rank"] for r in official) != list(range(1, 11)):
        raise BoundedError("HOLDER_COMPARISON_OFFICIAL_COMPLETE_RANKS_REQUIRED")
    for row in official + previous:
        unit = row["holding_quantity_unit"]
        if unit in {"SHARE", "股"}:
            continue
        # Retained F10 quantities are already normalized by the pinned parser.
        # Check the original atom rather than multiplying the native value twice.
        raw = row.get("raw_holding_quantity", "")
        if unit not in {"万股", "亿股"} or not isinstance(raw, str) or unit[0] not in raw:
            raise BoundedError("HOLDER_COMPARISON_SHARE_UNIT_REQUIRED")
        if _scalar(raw, "")[0] != row["holding_quantity"]:
            raise BoundedError("HOLDER_COMPARISON_NATIVE_UNIT_EVIDENCE_CHANGED")
    old = {r["holder_match_key"]: r for r in previous}
    new = {r["holder_match_key"]: r for r in official}
    if len(old) != len(previous) or len(new) != len(official) or None in old or None in new:
        raise BoundedError("HOLDER_COMPARISON_DUPLICATE_OR_UNKNOWN_HOLDER")
    counts: Counter[str] = Counter()
    differences = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        kinds = []
        if a is None or b is None:
            kinds.append("OFFICIAL_ONLY_MEMBER" if a is None else "PREVIOUS_ONLY_MEMBER_NOT_EXIT")
        else:
            for field, label in [
                ("holding_quantity", "QUANTITY"),
                ("holding_percentage", "PERCENTAGE"),
            ]:
                if (
                    a[field] is None
                    or b[field] is None
                    or field == "holding_percentage"
                    and (
                        a.get("holding_percentage_unit") != "PERCENT"
                        or b.get("holding_percentage_unit") != "PERCENT"
                    )
                ):
                    counts[label + "_NOT_COMPARABLE"] += 1
                elif a[field] != b[field]:
                    kinds.append(label + "_DIFFERENCE")
            if a["holder_rank"] != b["holder_rank"]:
                kinds.append("SOURCE_ORDER_DIFFERENCE")
        counts.update(kinds)
        differences.append(
            dict(
                holder_key_sha256=digest(key),
                classes=kinds,
                previous_row_sha256=digest(a) if a is not None else None,
                official_row_sha256=digest(b) if b is not None else None,
            )
        )
    quantity_equivalent = (
        set(old) == set(new)
        and not counts["QUANTITY_DIFFERENCE"]
        and not counts["QUANTITY_NOT_COMPARABLE"]
    )
    return dict(
        contract_id=CONTRACT_ID,
        previous_matrix_sha256=digest(previous),
        official_matrix_sha256=digest(official),
        previous_rows=len(previous),
        official_rows=10,
        classifications=dict(counts),
        differences=differences,
        same_members_and_quantities=quantity_equivalent,
        permitted_current_source_view="COMPLETE_OFFICIAL_SOURCE_REPORTED_MATRIX",
        historical_provider_conflict_arbitrated=False,
        flat_union_allowed=False,
        missing_member_is_exit=False,
        old_values_overwritten=False,
        consumer_admission="NOT_GRANTED",
    )
