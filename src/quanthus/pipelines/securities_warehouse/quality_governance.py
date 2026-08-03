"""Domain-accounting and quality-sidecar contracts for Issue #64."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.model_extension import (
    verify_additive_model_extension,
)


class QualityGovernanceBuildError(RuntimeError):
    """Raised when domain accounting cannot support a lossless quality sidecar."""


_TERMINAL = (
    "modeled",
    "reconciliation_only",
    "ods_only",
    "deferred",
    "quality_exception",
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise QualityGovernanceBuildError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QualityGovernanceBuildError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QualityGovernanceBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def load_quality_contract(path: Path) -> tuple[dict[str, Any], str]:
    """Load the exact domain-accounting contract used by all DWD builders."""

    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualityGovernanceBuildError("cannot read quality accounting contract") from exc
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != 1
        or contract.get("contract_id")
        != "quanthus-dwd-domain-source-accounting-receipt-v1"
        or contract.get("terminal_dispositions") != list(_TERMINAL)
        or not isinstance(contract.get("exception_required_fields"), list)
        or not contract["exception_required_fields"]
    ):
        raise QualityGovernanceBuildError("quality accounting contract is unsupported")
    return contract, _sha256(path)


def validate_domain_accounting_receipt(
    receipt: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    run_id: str,
    input_plan_digest: str,
) -> None:
    """Validate one domain receipt and prove source-row conservation."""

    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "dwd_domain_source_accounting_receipt"
        or receipt.get("contract_id") != contract.get("contract_id")
        or receipt.get("run_id") != run_id
        or receipt.get("input_plan_digest") != input_plan_digest
        or not isinstance(receipt.get("domain"), str)
        or not receipt["domain"]
        or not isinstance(receipt.get("candidate_manifest_sha256"), str)
    ):
        raise QualityGovernanceBuildError("domain accounting receipt binding differs")
    sources = receipt.get("sources")
    exceptions = receipt.get("exceptions")
    if not isinstance(sources, list) or not isinstance(exceptions, list):
        raise QualityGovernanceBuildError("domain accounting receipt payload is invalid")
    exception_count = 0
    identities: set[str] = set()
    for raw in sources:
        if not isinstance(raw, Mapping):
            raise QualityGovernanceBuildError("domain source accounting row is invalid")
        identity = raw.get("source_identity")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise QualityGovernanceBuildError("domain source identity is empty or duplicate")
        identities.add(identity)
        counts = [raw.get(field) for field in ("eligible_rows", *_TERMINAL)]
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts
        ):
            raise QualityGovernanceBuildError("domain source accounting count is invalid")
        if counts[0] != sum(counts[1:]):
            raise QualityGovernanceBuildError(
                f"domain source accounting does not conserve rows: {identity}"
            )
        exception_count += int(raw["quality_exception"])
    if exception_count != len(exceptions):
        raise QualityGovernanceBuildError("quality exception detail count differs")
    required = set(contract["exception_required_fields"])
    for raw in exceptions:
        if not isinstance(raw, Mapping) or not required.issubset(raw):
            raise QualityGovernanceBuildError("quality exception detail is incomplete")
        if raw.get("source_identity") not in identities:
            raise QualityGovernanceBuildError("quality exception source is not accounted")
        if raw.get("disposition") in contract.get("blocking_dispositions", []):
            raise QualityGovernanceBuildError("blocking quality exception rejects candidate")


def quality_exception_rows(
    receipts: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
    run_id: str,
    input_plan_digest: str,
) -> list[dict[str, Any]]:
    """Convert validated accepted exceptions into deterministic #64 DWD rows."""

    rows: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str, datetime]] = set()
    retained = set(contract.get("retained_dispositions", []))
    for receipt in receipts:
        validate_domain_accounting_receipt(
            receipt,
            contract=contract,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
        )
        for raw in receipt["exceptions"]:
            disposition = str(raw["disposition"])
            if disposition not in retained:
                raise QualityGovernanceBuildError(
                    f"quality exception disposition is unsupported: {disposition}"
                )
            affected_key = _canonical_json(raw["affected_business_key"])
            affected_at = _timestamp(
                raw["affected_effective_at"], "affected_effective_at"
            )
            detected_at = _timestamp(raw["detected_at"], "detected_at")
            key = (
                str(raw["affected_table"]),
                affected_key,
                str(raw["rule_id"]),
                affected_at,
            )
            if key in keys:
                raise QualityGovernanceBuildError("duplicate quality exception business key")
            keys.add(key)
            quality_id = _stable_id(
                "quality_exception",
                receipt["domain"],
                str(raw["affected_table"]),
                affected_key,
                str(raw["rule_id"]),
                str(raw["rule_version"]),
                affected_at.isoformat(),
                str(raw["source_revision"]),
            )
            rows.append(
                {
                    "record_source": str(raw["source_identity"]),
                    "source_record_id": str(raw["source_record_id"]),
                    "source_revision": str(raw["source_revision"]),
                    "source_artifact_sha256": str(raw["source_artifact_sha256"]),
                    "knowledge_from": detected_at,
                    "knowledge_to": None,
                    "affected_effective_at": affected_at,
                    "detected_at": detected_at,
                    "quality_exception_id": quality_id,
                    "affected_domain": str(raw["affected_domain"]),
                    "affected_table": str(raw["affected_table"]),
                    "affected_business_key_json": affected_key,
                    "rule_id": str(raw["rule_id"]),
                    "rule_version": str(raw["rule_version"]),
                    "classification": str(raw["classification"]),
                    "severity": str(raw["severity"]),
                    "authority": str(raw["authority"]),
                    "evidence_digest": str(raw["evidence_digest"]),
                    "disposition": disposition,
                    "resolution_state": str(raw["resolution_state"]),
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            row["affected_table"],
            row["affected_business_key_json"],
            row["rule_id"],
            row["affected_effective_at"],
        ),
    )


def verify_quality_models(
    base_model_path: Path, model_extension_path: Path
) -> str:
    """Verify the #64 additive fields and return the extension digest."""

    extension, digest = verify_additive_model_extension(
        base_model_path, model_extension_path
    )
    columns = extension["table_extensions"]["dwd_quality_exception"]["columns"]
    expected = {
        "rule_version": "string",
        "affected_domain": "string",
        "disposition": "string",
    }
    if columns != expected:
        raise QualityGovernanceBuildError("quality model extension differs")
    return digest
