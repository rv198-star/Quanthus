"""Version-neutral Base profile with the existing exact structural protections.

Business cutoff is a validated parameter of the standard profile. Legacy Base
2.3/2.4 callers keep their original evaluator and frozen contract behavior.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from .base22_bounded import BoundedError
from .base23_native_image import NativeImageProfile
from .base24_delivery import (
    HARD_CHECKS,
    INTERVALS,
    ONE_MINUTE_DISPOSITION,
    PURPOSE,
)
from .base24_delivery import (
    assess_delivery as assess_legacy,
)
from .base24_delivery import (
    validate_contract as validate_legacy,
)
from .base24_image import validate_default_intervals
from .base231_native_keys import UniqueBusinessKeyScan
from .base_build_inputs import GENERIC_KINDS, identity

CONTRACT = "p10-standard-base-1d5m-delivery-v1"
UPDATED_TABLES = frozenset({
    "dwd_disclosure", "dwd_disclosure_subject", "dwd_index_bar", "dwd_security_bar",
    "dwd_security_valuation", "dwd_tradability_state", "dwd_trading_calendar",
})
REFERENCE_TABLES = frozenset({
    "dwd_listed_company", "dwd_company_identifier", "dwd_security",
    "dwd_security_identifier", "dwd_listing",
})


class StandardGlobalScan(UniqueBusinessKeyScan):
    """Evaluate the actual target cutoff without changing older release scans."""

    def __init__(self, *args: Any, through: date, **kwargs: Any) -> None:
        if type(through) is not date:
            raise BoundedError("BUILD_PROFILE_CUTOFF_INVALID")
        self.through = through
        super().__init__(*args, **kwargs)

    def _cutoff_predicate(self, cutoff: str, field: Any) -> str:
        import pyarrow as pa

        if pa.types.is_timestamp(field.type) and field.type.tz is not None:
            upper = datetime.combine(self.through + timedelta(days=1), time(),
                                     tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
            return f"{cutoff}>=TIMESTAMPTZ '{upper.isoformat()}'"
        expr = (f"CAST(timezone('Asia/Shanghai',{cutoff}) AS DATE)"
                if pa.types.is_timestamp(field.type) else cutoff)
        return f"{expr}>DATE '{self.through.isoformat()}'"


def delivery_contract(legacy: dict[str, Any], *, version: str, through: str,
                      predecessor_version: str) -> dict[str, Any]:
    """Wrap reviewed invariants; this is not a configurable gate override."""
    validate_legacy(legacy)
    result = copy.deepcopy(legacy)
    result.update(contract_id=CONTRACT, business_through=through, base_version=version,
                  predecessor_version=predecessor_version,
                  transition_rule="Paired predecessor plus admitted interval deliveries; "
                                  "per-table update/inherit and limitations remain explicit.")
    result["table_policy"] = {
        "daily_updated_tables": sorted(UPDATED_TABLES),
        "reference_extension_tables": sorted(REFERENCE_TABLES),
        "reference_extension_rule": "SOURCE_REPLAYED_IDENTITY_VERSIONS_NO_PREDECESSOR_REWRITE",
        "other_tables": "INHERIT_ORIGINAL_ROWS_AND_COVERAGE_NO_REFRESH_INFERRED",
        "unhandled_nonempty_artifacts": "REJECT_UNLESS_EXPLICIT_EXISTING_ODS_ONLY_DISPOSITION",
        "closed_day_events": "REQUIRED_THROUGH_TARGET_CUTOFF",
        "incoming_conflict": "RETAIN_IDENTICAL_OR_EXPLICIT_TEMPORAL_REVISION_OTHERWISE_REJECT",
        "inherited_held": "EXACT_PREDECESSOR_SET_NO_EXPANSION",
        "new_quality_held": 0,
    }
    validate_contract(result)
    return result


def _legacy_view(contract: dict[str, Any]) -> dict[str, Any]:
    original = copy.deepcopy(contract)
    for key in ("base_version", "predecessor_version", "table_policy"):
        original.pop(key, None)
    original.update(contract_id="p10-base24-purpose-qualified-1d5m-delivery-v1",
                    business_through="2026-09-30")
    return original


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("contract_id") != CONTRACT:
        raise BoundedError("BUILD_PROFILE_CONTRACT_INVALID")
    try:
        through = date.fromisoformat(contract["business_through"])
        current = contract["base_version"]
        predecessor = contract["predecessor_version"]
        versions = []
        for value in (predecessor, current):
            if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
                raise ValueError("version")
            parts = [int(v) for v in value.split(".")]
            versions.append(tuple(parts + [0] * (3 - len(parts))))
        if versions[0] >= versions[1] or through.isoformat() != contract["business_through"]:
            raise ValueError("scope")
    except (KeyError, TypeError, ValueError) as exc:
        raise BoundedError("BUILD_PROFILE_VERSION_OR_CUTOFF_INVALID") from exc
    validate_legacy(_legacy_view(contract))
    expected = {
        "daily_updated_tables": sorted(UPDATED_TABLES),
        "reference_extension_tables": sorted(REFERENCE_TABLES),
        "reference_extension_rule": "SOURCE_REPLAYED_IDENTITY_VERSIONS_NO_PREDECESSOR_REWRITE",
        "other_tables": "INHERIT_ORIGINAL_ROWS_AND_COVERAGE_NO_REFRESH_INFERRED",
        "unhandled_nonempty_artifacts": "REJECT_UNLESS_EXPLICIT_EXISTING_ODS_ONLY_DISPOSITION",
        "closed_day_events": "REQUIRED_THROUGH_TARGET_CUTOFF",
        "incoming_conflict": "RETAIN_IDENTICAL_OR_EXPLICIT_TEMPORAL_REVISION_OTHERWISE_REJECT",
        "inherited_held": "EXACT_PREDECESSOR_SET_NO_EXPANSION",
        "new_quality_held": 0,
    }
    if contract.get("table_policy") != expected:
        raise BoundedError("BUILD_PROFILE_TABLE_POLICY_CHANGED")


def assess_delivery(*, contract: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    validate_contract(contract)
    result = assess_legacy(contract=_legacy_view(contract), **kwargs)
    result.update(contract_id=CONTRACT, business_through=contract["business_through"],
                  base_version=contract["base_version"],
                  predecessor_version=contract["predecessor_version"],
                  table_policy=contract["table_policy"])
    result["identity"] = identity({k: v for k, v in result.items() if k != "identity"})
    return result


def profile(contract: dict[str, Any], *, content: dict[str, Any] | None = None
            ) -> NativeImageProfile:
    validate_contract(contract)
    fields: list[tuple[str, Any]] = [
        ("default_intervals", list(INTERVALS)),
        ("one_minute_disposition", ONE_MINUTE_DISPOSITION),
    ]
    if content is not None:
        if (content.get("role") != "dwd"
                or content.get("base_version") != contract["base_version"]
                or content.get("through_date") != contract["business_through"]):
            raise BoundedError("BUILD_PROFILE_CONTENT_SCOPE_INVALID")
        fields.append(("content", content))
    return NativeImageProfile(
        kind=GENERIC_KINDS["dwd"], purpose=PURPOSE, version=contract["base_version"],
        business_through=contract["business_through"],
        contract_filename="base-build-delivery.v1.json", hard_checks=HARD_CHECKS,
        validate_contract=validate_contract, assess_delivery=assess_delivery,
        validate_table=validate_default_intervals, manifest_fields=tuple(fields),
        scan_factory=partial(StandardGlobalScan,
                             through=date.fromisoformat(contract["business_through"])),
    )
