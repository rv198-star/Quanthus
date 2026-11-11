"""Exact complete retained frames as new observations, never inferred exits."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base23_ownership import ownership_observation
from .base231_holder_scope import certify_frames
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import ownership_capital_schemas

CONTRACT_ID = "p10-base231-retained-complete-holder-frame-recovery-v1"
TABLE = "dwd_shareholding_observation"
_SOURCE_FIELDS = {
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
    "parser_version",
}


def business_digest(rows: list[dict[str, Any]]) -> str:
    """Compare native physical decimals; retain all literal business fields."""
    import pyarrow as pa  # type: ignore[import-untyped]

    normalized = pa.Table.from_pylist(rows, schema=ownership_capital_schemas()[TABLE]).to_pylist()
    if normalized != rows:
        raise BoundedError("HOLDER_RECOVERY_NATIVE_NUMERIC_VALUE_CHANGED")
    return digest(
        sorted(digest({k: v for k, v in r.items() if k not in _SOURCE_FIELDS}) for r in normalized)
    )


def recover_frame(
    raw: dict[str, Any],
    *,
    source_sha256: str,
    source_revision: str,
    raw_row_sha256: str,
    business_date: date,
    holding_type: str,
    expected_business_sha256: str,
    resolver: FinancialCompanyResolver,
    component_contract: dict[str, Any],
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reparse a complete raw body and select only the qualified dated frame.

    Source priority is unnecessary only when the complete business bundles
    agree. The caller freezes that consensus and retains every contributing
    source identity; disagreeing bundles require a separate disposition.
    """
    import pyarrow as pa

    if (
        digest(raw) != raw_row_sha256
        or business_date > date(2026, 9, 30)
        or holding_type not in {"TOP_SHAREHOLDERS", "TOP_FLOATING_SHAREHOLDERS"}
    ):
        raise BoundedError("HOLDER_RECOVERY_EXACT_RAW_OR_SCOPE_CHANGED")
    output, proof = ownership_observation(
        raw,
        source_sha256=source_sha256,
        revision=source_revision,
        resolver=resolver,
        observed_at=observed_at,
        component_contract=component_contract,
    )
    cert = certify_frames(proof, output[TABLE])
    matching = [
        f
        for f in cert["frames"]
        if f["business_date"] == str(business_date) and f["holding_type"] == holding_type
    ]
    if len(matching) != 1:
        raise BoundedError("HOLDER_RECOVERY_COMPLETE_FRAME_NOT_PROVEN")
    values = [
        r
        for r in output[TABLE]
        if r["holding_type"] == holding_type
        and r["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date() == business_date
    ]
    if not values or business_digest(values) != expected_business_sha256:
        raise BoundedError("HOLDER_RECOVERY_FROZEN_BUSINESS_BUNDLE_CHANGED")
    typed = pa.Table.from_pylist(values, schema=ownership_capital_schemas()[TABLE]).to_pylist()
    return typed, dict(
        contract_id=CONTRACT_ID,
        original_raw_row_sha256=raw_row_sha256,
        original_member_sha256=source_sha256,
        business_date=business_date,
        holding_type=holding_type,
        rows=len(typed),
        business_sha256=business_digest(typed),
        native_rows_sha256=digest(typed),
        original_acquired_at=raw["acquired_at"],
        new_knowledge_at=observed_at,
        provider_PIT_qualified=False,
        latest_capture_complete_inferred=False,
        absent_holder_is_exit=False,
        consumer_admission="NOT_GRANTED",
    )
