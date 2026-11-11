"""Shared, literal lifecycle observations and early minute evidence disposition.

These are governed domain outputs, not a new market denominator or a trading
profile. Release wrappers never become new effective/provider-acquisition dates.
Minute transport completeness never substitutes for historical mode/code evidence.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base231-joint-literal-lifecycle-minute-scope-v1"
THROUGH = date(2026, 9, 30)
FIELDS = {"security_id", "stock_code", "exchange", "name", "list_date", "delist_date",
          "status", "bs_code", "as_of", "source", "built_at"}


def lifecycle_observations(
    rows: list[dict[str, Any]], member: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    observed, keys = [], set()
    for row in rows:
        if (set(row) != FIELDS or not row["security_id"] or not row["stock_code"]
                or row["security_id"] in keys or row["exchange"] not in {"SSE", "SZSE", "BSE"}
                or row["status"] not in {"listed", "delisted"}
                or not isinstance(row["as_of"], date) or row["as_of"] > THROUGH
                or not row["source"] or row["built_at"].tzinfo is None
                or row["list_date"] is not None and row["delist_date"] is not None
                and row["list_date"] > row["delist_date"]):
            raise BoundedError("JOINT_LIFECYCLE_ORIGINAL_ATOM_INVALID")
        keys.add(row["security_id"])
        observed.append({**row, "source_member_sha256": member["sha256"],
                         "source_catalog_path": member["path"],
                         "release_business_date": date.fromisoformat(
                             member["source_business_date"]),
                         "provider_acquired_at": None})
    return observed, {
        "contract_id": CONTRACT_ID, "source_member_sha256": member["sha256"],
        "source_catalog_path": member["path"], "rows": len(rows),
        "original_atoms_sha256": digest(rows), "typed_observations_sha256": digest(observed),
        "venues": dict(Counter(r["exchange"] for r in rows)),
        "snapshot_dates": sorted({str(r["as_of"]) for r in rows}),
        "all_market_denominator_qualified": False, "historical_PIT_qualified": False,
        "effective_dates_extended": False, "consumer_admission": "NOT_GRANTED",
    }


def minute_evidence(raw: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
    if (raw.get("market_date") != member["source_business_date"]
            or date.fromisoformat(raw["market_date"]) > THROUGH
            or raw.get("bar_size") not in {"1m", "5m"}
            or not isinstance(raw.get("collector"), dict)):
        raise BoundedError("JOINT_MINUTE_SOURCE_SCOPE_CHANGED")
    collector = raw["collector"]
    missing = ["original_provider_adjustment_mode", "original_deployed_collector_code_identity",
               "field_precision_and_original_time_semantic_contract"]
    # A newly supplied string is not historical authority. This bounded gate has
    # no original deployment/mode authority input and cannot silently grant it.
    if collector.get("fetch_mode") not in {"incr", "full"}:
        missing.append("unqualified_fetch_mode")
    if raw.get("coverage_ok") is not True or collector.get("exit_code") != 0:
        missing.append("transport_or_collection_failure")
    return {
        "contract_id": CONTRACT_ID, "source_member_sha256": member["sha256"],
        "source_catalog_path": member["path"], "business_date": raw["market_date"],
        "bar_size": raw["bar_size"], "collector_provider": collector.get("provider"),
        "collector_api": collector.get("api"), "fetch_mode": collector.get("fetch_mode"),
        "session_policy_version": raw.get("session_policy_version"),
        "transport_coverage_ok": raw.get("coverage_ok"), "source_footer_rows": raw.get("row_count"),
        "state": "HELD_ORIGINAL_MINUTE_SEMANTIC_EVIDENCE", "missing": missing,
        "missing_scope": "selected_retained_source_not_global_absence",
        "actual_price_rows_read": 0, "consumer_admission": "NOT_GRANTED",
        "other_domains_blocked": False,
    }
