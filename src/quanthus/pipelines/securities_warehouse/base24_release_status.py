"""Resolve sealed Base 2.4 build state against exact, later cold receipts.

This is a metadata status reader, not a payload audit or consumer admission.
It never edits a sealed image, opens archived source roots, or replaces the
physical readers' integrity checks. Receipt pins come from reviewed bindings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, load_contract
from .base22_sources import SourceAssessmentError, _fingerprint, _member, physical_path
from .base23_delivery import HARD_CHECKS
from .base24_delivery import CONTRACT_ID, PURPOSE
from .base24_image import KIND as DWD_KIND
from .base24_ods_image import CONTRACT as ODS_CONTRACT
from .base24_ods_image import KIND as ODS_KIND
from .base24_ods_image import PURPOSE as ODS_PURPOSE

CONTRACT = "quanthus.base24-recorded-delivery-status.v1"
OUTER_CHECKS = (
    "two_independent_source_denied_cold_runs",
    "missing_and_corrupt_fail_closed",
)
NEGATIVES = {
    "dwd": {
        "missing": "INDEPENDENT_FILE_REQUIRED",
        "corrupt": "NATIVE_IMAGE_PHYSICAL_BYTES_CHANGED",
    },
    "ods": {
        "missing": "BASE24_ODS_PHYSICAL_FILE_SET_CHANGED",
        "corrupt": "BASE24_ODS_PHYSICAL_BYTES_CHANGED",
    },
}
MAX_METADATA_BYTES = 64 * 1024 * 1024


class ReleaseStatusError(RuntimeError):
    """Metadata or cold evidence cannot establish the recorded delivery state."""


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ReleaseStatusError("BASE24_STATUS_SHA256_INVALID")
    return value


def _bytes(path: Path) -> bytes:
    try:
        path = physical_path(path)
        before = _fingerprint(path)
        if before[2] > MAX_METADATA_BYTES:
            raise ReleaseStatusError("BASE24_STATUS_METADATA_TOO_LARGE")
        with path.open("rb") as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES or _fingerprint(path) != before:
            raise ReleaseStatusError("BASE24_STATUS_METADATA_CHANGED")
        return raw
    except (OSError, SourceAssessmentError) as exc:
        raise ReleaseStatusError("BASE24_STATUS_METADATA_UNREADABLE") from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise ValueError("object required")
        # Reject NaN/Infinity too; status evidence must be reproducible JSON.
        canonical(value)
        return value
    except (ValueError, TypeError) as exc:
        raise ReleaseStatusError("BASE24_STATUS_JSON_INVALID") from exc


def _checksum(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _image(root: Path, pin: Mapping[str, Any], product: str) -> tuple[dict[str, Any], str]:
    raw = _bytes(root / "manifest.json")
    if _checksum(raw) != _sha(pin.get("manifest_sha256")):
        raise ReleaseStatusError("BASE24_STATUS_MANIFEST_SHA256_MISMATCH")
    ready = _bytes(root / "READY")
    if ready.strip() != _checksum(raw).encode("ascii"):
        raise ReleaseStatusError("BASE24_STATUS_READY_BINDING_MISMATCH")
    manifest = _json(raw)
    content = manifest if product == "dwd" else manifest.get("content", {})
    if (
        manifest.get("kind") != (DWD_KIND if product == "dwd" else ODS_KIND)
        or content.get("business_timezone") != "Asia/Shanghai"
        or content.get("consumer_admission") != "NOT_GRANTED"
        or content.get("execution_authorized", False) is not False
        or content.get("purpose") != (PURPOSE if product == "dwd" else ODS_PURPOSE)
        or content.get("business_through" if product == "dwd" else "through_date") != "2026-09-30"
        or content.get("default_intervals" if product == "dwd" else "default_frequencies")
        != ["1d", "5m"]
    ):
        raise ReleaseStatusError("BASE24_STATUS_IMAGE_CONTRACT_MISMATCH")
    if product == "dwd":
        if manifest.get("version") != "2.4" or _checksum(
            _bytes(root / "MATERIALIZATION.json")
        ) != manifest.get("materialization_sha256"):
            raise ReleaseStatusError("BASE24_STATUS_MATERIALIZATION_BINDING_MISMATCH")
    elif manifest.get("content_digest") != digest(content):
        raise ReleaseStatusError("BASE24_STATUS_ODS_CONTENT_IDENTITY_MISMATCH")
    return manifest, _checksum(ready)


def _cold_receipt(
    receipt: dict[str, Any],
    manifest: dict[str, Any],
    product: str,
    pin: Mapping[str, Any],
    ready_sha: str,
) -> None:
    identity = digest({k: v for k, v in receipt.items() if k != "identity"})[7:]
    if (
        receipt.get("identity") != identity
        or receipt.get("product") != product
        or receipt.get("source_manifest_sha256") != pin["manifest_sha256"]
        or receipt.get("source_READY_sha256") != ready_sha
    ):
        raise ReleaseStatusError("BASE24_STATUS_RECEIPT_BINDING_MISMATCH")
    if receipt.get("status") != "PASS" or any(
        receipt.get(k) is not True
        for k in (
            "source_unchanged",
            "distinct_new_processes",
            "two_independent_physical_relocations",
            "missing_and_corrupt_fail_closed",
        )
    ):
        raise ReleaseStatusError("BASE24_STATUS_COLD_ACCEPTANCE_INCOMPLETE")
    if product == "dwd" and (
        receipt.get("payload_byte_corruption_fail_closed") is not True
        or receipt.get("source_materialization_sha256") != manifest["materialization_sha256"]
    ):
        raise ReleaseStatusError("BASE24_STATUS_PAYLOAD_NEGATIVE_OR_BINDING_MISSING")
    results = receipt.get("results")
    if (
        not isinstance(results, list)
        or len(results) != 4
        or any(not isinstance(row, dict) for row in results)
        or {row.get("label") for row in results} != {"move1", "move2", "missing", "corrupt"}
    ):
        raise ReleaseStatusError("BASE24_STATUS_COLD_RESULTS_INCOMPLETE")
    pids: set[int] = set()
    expected_audit = (
        manifest.get("scan_identity")
        if product == "dwd"
        else manifest["content"].get("audit_identity")
    )
    if receipt.get("audit_identity") != expected_audit:
        raise ReleaseStatusError("BASE24_STATUS_AUDIT_BINDING_MISMATCH")
    for row in results:
        pid = row.get("pid")
        if (
            type(pid) is not int
            or pid <= 0
            or pid in pids
            or row.get("product") != product
            or row.get("new_process") is not True
            or row.get("kernel_readonly_image") is not True
            or row.get("source_roots_mounted_inaccessible") is not True
            or row.get("network_namespace_probe") != "PASS"
        ):
            raise ReleaseStatusError("BASE24_STATUS_COLD_ISOLATION_INCOMPLETE")
        pids.add(pid)
        child = row.get("receipt", {})
        if row["label"] in NEGATIVES[product]:
            if (
                child.get("status") != "EXPECTED_FAIL_CLOSED"
                or child.get("reason") != NEGATIVES[product][row["label"]]
            ):
                raise ReleaseStatusError("BASE24_STATUS_DAMAGE_NEGATIVE_INCOMPLETE")
        elif (
            child.get("identity") != expected_audit
            or (
                product == "dwd"
                and (
                    child.get("summaries") != manifest["summaries"]
                    or child.get("assessment") != manifest["assessment"]
                    or child.get("global_keys_versions_references_lineage") != "PASS"
                )
            )
            or (
                product == "ods"
                and (
                    child.get("status") != "PASS"
                    or child.get("contract") != ODS_CONTRACT
                    or child.get("source_roots_dereferenced") is not False
                )
            )
        ):
            raise ReleaseStatusError("BASE24_STATUS_COLD_AUDIT_MISMATCH")


def _effective_dwd(manifest: dict[str, Any], expected_tables: set[str]) -> dict[str, Any]:
    assessment = manifest.get("assessment", {})
    if (
        len(expected_tables) != 33
        or set(manifest.get("summaries", {})) != expected_tables
        or set(assessment.get("tables", [])) != expected_tables
    ):
        raise ReleaseStatusError("BASE24_STATUS_EXACT_TABLE_SET_MISMATCH")
    checks = assessment.get("structural_checks", {})
    limitations = assessment.get("limitations")
    if (
        assessment.get("contract_id") != CONTRACT_ID
        or assessment.get("purpose") != PURPOSE
        or assessment.get("build_eligible") is not True
        or assessment.get("failed_checks") != []
        or assessment.get("consumer_admission") != "NOT_GRANTED"
        or set(checks) != set(HARD_CHECKS)
        or any(v not in {"PASS", "NOT_RUN"} for v in checks.values())
        or any(checks[k] != "PASS" for k in HARD_CHECKS if k not in OUTER_CHECKS)
        or set(assessment.get("not_run_checks", []))
        != {k for k, v in checks.items() if v == "NOT_RUN"}
        or not isinstance(limitations, list)
        or len(limitations) != assessment.get("known_limitation_count")
        or assessment.get("identity")
        != digest({k: v for k, v in assessment.items() if k != "identity"})
    ):
        raise ReleaseStatusError("BASE24_STATUS_INNER_ASSESSMENT_FAILED")
    return {
        "sealed_independent_acceptance": manifest.get("independent_acceptance"),
        "effective_structural_checks": {**checks, **dict.fromkeys(OUTER_CHECKS, "PASS")},
        "exact_tables": sorted(expected_tables),
        "known_limitation_count": len(limitations),
        "limitations": limitations,
        "limitations_are_repaired_data": False,
    }


def resolve_delivery_status(
    binding: Mapping[str, Any],
    roots: Mapping[str, Path],
    evidence_root: Path,
    *,
    expected_tables: set[str],
) -> dict[str, Any]:
    """Combine recorded proofs without reinterpreting their uses or rerunning payloads."""
    if (
        binding.get("contract") != CONTRACT
        or binding.get("version") != "2.4"
        or binding.get("business_through") != "2026-09-30"
        or binding.get("business_timezone") != "Asia/Shanghai"
        or binding.get("purpose") != "governance_baseline"
        or binding.get("consumer_admission") != "NOT_GRANTED"
        or binding.get("execution_authorized") is not False
        or set(binding.get("products", {})) != {"ods", "dwd"}
        or set(roots) != {"ods", "dwd"}
    ):
        raise ReleaseStatusError("BASE24_STATUS_BINDING_CONTRACT_MISMATCH")
    products = {}
    try:
        for product in ("dwd", "ods"):
            pin = binding["products"][product]
            manifest, ready_sha = _image(roots[product], pin, product)
            raw = _bytes(_member(evidence_root, pin["acceptance_path"]))
            if _checksum(raw) != _sha(pin.get("acceptance_sha256")):
                raise ReleaseStatusError("BASE24_STATUS_RECEIPT_SHA256_MISMATCH")
            receipt = _json(raw)
            # Inner failures cannot be overwritten by an outer PASS.
            extra = _effective_dwd(manifest, expected_tables) if product == "dwd" else {}
            _cold_receipt(receipt, manifest, product, pin, ready_sha)
            products[product] = {
                "manifest_sha256": pin["manifest_sha256"],
                "acceptance_sha256": pin["acceptance_sha256"],
                "effective_independent_acceptance": "PASS",
                "consumer_admission": "NOT_GRANTED",
                **extra,
            }
    except (KeyError, TypeError, AttributeError, OSError, SourceAssessmentError) as exc:
        raise ReleaseStatusError("BASE24_STATUS_METADATA_OR_EVIDENCE_INVALID") from exc
    result = {
        "contract": CONTRACT,
        "status": "PASS_RECORDED_PAIRED_IMAGE_ACCEPTANCE",
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "binding_identity": digest(binding),
        "products": products,
        "governance_read": "AVAILABLE_WITH_EXISTING_PHYSICAL_READER_CHECKS",
        "current_payload_verification": "NOT_RUN_STATUS_RESOLUTION_ONLY",
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "historical_pit_granted": False,
        "complete_market_coverage_granted": False,
        "october_daily_or_weekly_qualified_by_this_status": False,
    }
    result["identity"] = digest(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--dwd-root", type=Path, required=True)
    parser.add_argument("--ods-root", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, default=Path("config/pipelines"))
    args = parser.parse_args()
    _, model, _ = load_contract(args.config_root)
    result = resolve_delivery_status(
        _json(_bytes(args.binding)),
        {"dwd": args.dwd_root, "ods": args.ods_root},
        args.evidence_root,
        expected_tables={row["name"] for row in model["tables"]},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
