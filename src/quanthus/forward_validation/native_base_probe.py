"""Read-only P6 consumer-surface probe for an independent native Base image.

This code never reads market-data rows, settles targets, opens a protected research partition,
publishes a release, or authorizes P6 paper entry. A structural match is not consumer admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BASE22_KIND = "dwd_base22_native_purpose_qualified_candidate_v2"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024

# Only the minimum P6 decision-facing schema surface is tested. The 33-table
# governance-delivery inventory is NOT a P6 strategy admission prerequisite.
_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "dwd_security_bar": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "listing_id",
         "calendar_session_id", "interval_size", "price_basis", "open", "high",
         "low", "close", "volume", "turnover")
    ),
    "dwd_tradability_state": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "listing_id",
         "calendar_session_id", "is_tradable", "can_buy", "can_sell")
    ),
    "dwd_trading_calendar": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "venue_id",
         "calendar_session_id", "trade_date", "is_open")
    ),
    "dwd_listing": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "effective_from",
         "effective_to", "listing_id", "security_id", "venue_id", "listing_code")
    ),
    "dwd_security": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "effective_from",
         "effective_to", "security_id", "company_id", "instrument_type")
    ),
    "dwd_adjustment_factor": frozenset(
        ("source_artifact_sha256", "knowledge_from", "knowledge_to", "effective_from",
         "effective_to", "security_id", "factor_value", "anchor_date")
    ),
    "dwd_price_limit_regime": frozenset(),
    "dwd_listing_status_interval": frozenset(),
    "dwd_security_status_interval": frozenset(),
    "dwd_corporate_action": frozenset(),
    "dwd_corporate_action_term": frozenset(),
    "dwd_observation_gap": frozenset(),
}

# Exact 2026 scope for time-partitioned decision-facing inputs. No recursive 2025
# bar walk is performed. Other entity/reference tables are separately typed.
_RELATIVE_SUBTREES: dict[str, str] = {
    "dwd_security_bar": "interval_size=1d/observation_start_year=2026",
    "dwd_tradability_state": "observation_start_year=2026",
    "dwd_trading_calendar": "trade_date_year=2026",
    "dwd_listing": "all",
    "dwd_security": "all",
    "dwd_adjustment_factor": "all",
    "dwd_price_limit_regime": "all",
    "dwd_listing_status_interval": "all",
    "dwd_security_status_interval": "all",
    "dwd_corporate_action": "",
    "dwd_corporate_action_term": "",
    "dwd_observation_gap": "",
}


class NativeBaseProbeError(ValueError):
    """Structural source identity or file contract cannot be trusted."""


@dataclass(frozen=True)
class NativeBaseProbe:
    schema_version: str
    source_version: str
    business_through: str
    manifest_sha256: str
    source_kind: str
    structural_compatible: bool
    consumer_authorized: bool
    consumer_admission: str
    restricted_uses: tuple[str, ...]
    verified_metadata_tables: tuple[str, ...]
    deficiencies: tuple[str, ...]
    claim_ceiling: str = "METADATA_ONLY_NO_PIT_NO_P6_ADMISSION_NO_OFFICIAL_CYCLE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_object(path: Path, *, max_size: int = _MAX_MANIFEST_BYTES) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_size:
        raise NativeBaseProbeError(f"source_json_missing_oversized_or_symlink:{path.name}")
    try:
        obj = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise NativeBaseProbeError(f"source_json_invalid:{path.name}") from exc
    if not isinstance(obj, dict):
        raise NativeBaseProbeError(f"source_json_invalid_type:{path.name}")
    return obj


def inspect_native_base(
    image_root: Path, *, expected_manifest_sha256: str,
) -> NativeBaseProbe:
    """Validate pinned identity + minimum P6 Parquet footer surface, never any rows.

    This is deliberately not a complete P10 native image audit or an endorsement of
    trading_entry, PIT knowledge, correct pricing, independent cold reproduction,
    full daily continuity, feature semantics, or P6 activation.
    """
    if not _DIGEST.fullmatch(expected_manifest_sha256):
        raise NativeBaseProbeError("expected_manifest_sha256_invalid")
    root = image_root.resolve(strict=True)
    if not root.is_dir():
        raise NativeBaseProbeError("base_image_root_not_directory")
    path = root / "manifest.json"
    manifest = _json_object(path)
    observed = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    if observed != expected_manifest_sha256:
        raise NativeBaseProbeError("base_manifest_digest_mismatch")
    ready = root / "READY"
    if ready.is_symlink() or not ready.is_file():
        raise NativeBaseProbeError("base_ready_manifest_mismatch")
    try:
        ready_digest = ready.read_text(encoding="ascii").strip()
    except (UnicodeError, OSError) as exc:
        raise NativeBaseProbeError("base_ready_manifest_unreadable") from exc
    if ready_digest != observed:
        raise NativeBaseProbeError("base_ready_manifest_mismatch")

    version = str(manifest.get("version", ""))
    kind = str(manifest.get("kind", ""))
    through = str(manifest.get("business_through", ""))
    try:
        through_date = date.fromisoformat(through)
    except ValueError as exc:
        raise NativeBaseProbeError("base_business_through_invalid") from exc
    if through_date > date(2026, 8, 31) and version == "2.2":
        raise NativeBaseProbeError("base22_august_cutoff_drift")
    contract_path = root / "contract" / "base22-delivery.v2.json"
    contract = _json_object(contract_path)
    restricted = contract.get("restricted_uses")
    if not isinstance(restricted, list) or not all(isinstance(x, str) for x in restricted):
        raise NativeBaseProbeError("base_restricted_uses_invalid")
    if (
        contract.get("business_through") != through
        or contract.get("purpose") != manifest.get("purpose")
    ):
        raise NativeBaseProbeError("base_purpose_or_cutoff_drift")

    problems: list[str] = []
    if (version, kind) != ("2.2", _BASE22_KIND):
        problems.append("unqualified_or_unknown_native_base_version")
    if through_date < date(2026, 8, 31):
        problems.append("insufficient_declared_base_coverage")
    if manifest.get("consumer_admission") != "GRANTED_P6_TRADING_ENTRY":
        problems.append("consumer_admission_not_granted")
    if "trading_entry" in restricted:
        problems.append("trading_entry_scope_restricted")
    if "historical_pit" in restricted:
        problems.append("historical_pit_scope_restricted")

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise NativeBaseProbeError("parquet_footer_verifier_missing") from exc
    structural: list[str] = []
    for table, required in _TABLE_COLUMNS.items():
        subdir = root / "payload" / table / _RELATIVE_SUBTREES[table]
        if not subdir.is_dir() or subdir.is_symlink():
            problems.append(f"required_table_scope_missing:{table}")
            continue
        matches = subdir.rglob("*.parquet")
        item = next(matches, None)
        if item is None or item.is_symlink() or root not in item.resolve().parents:
            problems.append(f"required_parquet_missing_or_unsafe:{table}")
            continue
        try:
            meta = pq.ParquetFile(item)
            present = frozenset(meta.schema_arrow.names)
            if meta.metadata.num_rows < 1 or not required.issubset(present):
                problems.append(f"parquet_required_columns_or_rows_missing:{table}")
                continue
        except (OSError, ValueError) as exc:
            problems.append(f"parquet_footer_invalid:{table}:{type(exc).__name__}")
            continue
        structural.append(table)

    return NativeBaseProbe(
        schema_version="quanthus.p6-native-base-probe.v1",
        source_version=version,
        business_through=through,
        manifest_sha256=observed,
        source_kind=kind,
        structural_compatible=len(structural) == len(_TABLE_COLUMNS)
        and "unqualified_or_unknown_native_base_version" not in problems
        and "insufficient_declared_base_coverage" not in problems,
        consumer_authorized=False,  # Only a separately signed P6 decision can grant this.
        consumer_admission=str(manifest.get("consumer_admission")),
        restricted_uses=tuple(sorted(restricted)),
        verified_metadata_tables=tuple(structural),
        deficiencies=tuple(problems) + (
            "not_a_physical_checksum_or_row_by_row_validation",
            "PIT_and_source_lineage_not_independently_accepted",
            "no_P6_consumer_or_human_deployment_authority",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only P6 native Base metadata probe")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        result = inspect_native_base(
            args.root, expected_manifest_sha256=args.manifest_sha256
        )
    except (NativeBaseProbeError, OSError) as exc:
        print(json.dumps({"status": "NOT_QUALIFIED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False))
    # A metadata-only probe must never be mistaken for a green P6 admission gate.
    return 3  # Candidate metadata inspected; business consumer is NOT authorized.


if __name__ == "__main__":
    raise SystemExit(main())
