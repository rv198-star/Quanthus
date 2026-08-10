"""Domain-accounting and quality-sidecar contracts for Issue #64."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.model_extension import (
    verify_additive_model_extension,
)


class QualityGovernanceBuildError(RuntimeError):
    """Raised when domain accounting cannot support a lossless quality sidecar."""


@dataclass(frozen=True)
class QualityGovernanceBuildResult:
    """A sealed, non-published Issue #64 candidate."""

    root: Path
    semantic_digest: str
    row_count: int


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
    normalized = value.replace("Z", "+00:00")
    if (
        len(normalized) >= 3
        and normalized[-3] in {"+", "-"}
        and normalized[-2:].isdigit()
    ):
        normalized += ":00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QualityGovernanceBuildError(f"{field} must be an ISO-8601 timestamp") from exc
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
        or contract.get("contract_id") != "quanthus-dwd-domain-source-accounting-receipt-v1"
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
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts
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
            affected_at = _timestamp(raw["affected_effective_at"], "affected_effective_at")
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


def verify_quality_models(base_model_path: Path, model_extension_path: Path) -> str:
    """Verify the #64 additive fields and return the extension digest."""

    extension, digest = verify_additive_model_extension(base_model_path, model_extension_path)
    columns = extension["table_extensions"]["dwd_quality_exception"]["columns"]
    expected = {
        "rule_version": "string",
        "affected_domain": "string",
        "disposition": "string",
    }
    if columns != expected:
        raise QualityGovernanceBuildError("quality model extension differs")
    return digest


def quality_governance_schema() -> Any:
    """Return the v1 plus additive v1.1 physical quality schema."""

    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise QualityGovernanceBuildError("pyarrow is required for quality DWD rows") from exc
    return pa.schema(
        [
            pa.field("record_source", pa.string(), nullable=False),
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("source_revision", pa.string(), nullable=False),
            pa.field("source_artifact_sha256", pa.string(), nullable=False),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("affected_effective_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("detected_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("quality_exception_id", pa.string(), nullable=False),
            pa.field("affected_table", pa.string(), nullable=False),
            pa.field("affected_business_key_json", pa.string(), nullable=False),
            pa.field("rule_id", pa.string(), nullable=False),
            pa.field("classification", pa.string(), nullable=False),
            pa.field("severity", pa.string(), nullable=False),
            pa.field("authority", pa.string(), nullable=False),
            pa.field("evidence_digest", pa.string(), nullable=False),
            pa.field("resolution_state", pa.string(), nullable=False),
            pa.field("rule_version", pa.string(), nullable=False),
            pa.field("affected_domain", pa.string(), nullable=False),
            pa.field("disposition", pa.string(), nullable=False),
        ]
    )


_CANDIDATES = (
    ("listed_company", "dwd_listed_company_candidate"),
    ("trading_calendar", "dwd_trading_calendar_candidate"),
    ("security_market_observation", "dwd_market_observation_candidate"),
    ("tradability_state", "dwd_tradability_state_candidate"),
    ("disclosure_company_event", "dwd_disclosure_event_candidate"),
    ("financial_reporting", "dwd_financial_reporting_candidate"),
    ("industry_classification", "dwd_industry_classification_candidate"),
    ("ownership_capital", "dwd_ownership_capital_candidate"),
)
_QUALITY_PARTITION_COLUMN = "detected_at_year"


def _write_quality_rows(
    work: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    default_detected_at: str,
) -> list[str]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise QualityGovernanceBuildError("pyarrow is required for quality build") from exc

    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        detected = row.get("detected_at")
        if not isinstance(detected, datetime) or detected.utcoffset() is None:
            raise QualityGovernanceBuildError("quality detected_at must be timezone-aware")
        grouped.setdefault(detected.year, []).append(row)
    if not grouped:
        grouped[_timestamp(default_detected_at, "quality default detected_at").year] = []

    files: list[str] = []
    schema = quality_governance_schema()
    for year, values in sorted(grouped.items()):
        payload = (
            work
            / "payload/governance/dwd_quality_exception"
            / f"{_QUALITY_PARTITION_COLUMN}={year:04d}"
            / "part-00000.parquet"
        )
        payload.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(values, schema=schema),
            payload,
            compression="zstd",
            version="2.6",
        )
        files.append(payload.relative_to(work).as_posix())
    return files


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualityGovernanceBuildError(f"cannot read governed JSON: {path}") from exc
    if not isinstance(value, dict):
        raise QualityGovernanceBuildError(f"governed JSON is not an object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(_canonical_json(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="ascii")
    os.replace(temporary, path)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _file_record(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _source(
    identity: str,
    eligible: int,
    *,
    modeled: int = 0,
    reconciliation_only: int = 0,
    ods_only: int = 0,
    deferred: int = 0,
    quality_exception: int = 0,
    **evidence: object,
) -> dict[str, object]:
    return {
        "source_identity": identity,
        "eligible_rows": eligible,
        "modeled": modeled,
        "reconciliation_only": reconciliation_only,
        "ods_only": ods_only,
        "deferred": deferred,
        "quality_exception": quality_exception,
        **evidence,
    }


def _receipt(
    domain: str,
    manifest_sha: str,
    run_id: str,
    plan_digest: str,
    sources: Sequence[Mapping[str, object]],
    exceptions: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "dwd_domain_source_accounting_receipt",
        "contract_id": "quanthus-dwd-domain-source-accounting-receipt-v1",
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "domain": domain,
        "candidate_manifest_sha256": manifest_sha,
        "sources": list(sources),
        "exceptions": list(exceptions),
    }


def _candidate(
    path: Path, expected_kind: str, run_id: str, plan_digest: str
) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_dir():
        raise QualityGovernanceBuildError(f"candidate is not a physical directory: {path}")
    manifest_path = path / "manifest.json"
    manifest = _object(manifest_path)
    manifest_sha = _sha256(manifest_path)
    try:
        ready = (path / "READY").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise QualityGovernanceBuildError(f"candidate READY is unreadable: {path}") from exc
    if (
        ready != manifest_sha
        or manifest.get("kind") != expected_kind
        or manifest.get("run_id") != run_id
        or manifest.get("input_plan_digest") != plan_digest
    ):
        raise QualityGovernanceBuildError(f"candidate binding differs: {path}")
    return manifest, manifest_sha


def _listed_company_receipt(
    path: Path, manifest_sha: str, run_id: str, plan_digest: str
) -> dict[str, object]:
    reconciliation = _object(path / "reconciliation/source-row-reconciliation.json")
    sources = []
    for raw in reconciliation["sources"]:
        rows = int(raw["source_rows"])
        outcome = str(raw["outcome"])
        identity = f"{raw['dataset_id']}|{raw['path']}"
        if outcome == "selected_authority":
            sources.append(_source(identity, rows, modeled=rows))
        elif outcome == "selected_company_identity_map":
            modeled = int(raw["matched_rows"])
            sources.append(
                _source(identity, rows, modeled=modeled, reconciliation_only=rows - modeled)
            )
        else:
            sources.append(_source(identity, rows, reconciliation_only=rows))
    return _receipt("listed_company", manifest_sha, run_id, plan_digest, sources)


def _calendar_receipt(
    path: Path, manifest_sha: str, run_id: str, plan_digest: str, detected_at: str
) -> dict[str, object]:
    reconciliation = _object(path / "reconciliation/source-row-reconciliation.json")
    build = _object(path / "receipts/trading-calendar-build.json")
    sources = []
    for raw in reconciliation["sources"]:
        rows = int(raw["source_rows"])
        outcome = str(raw["outcome"])
        identity = f"{raw['dataset_id']}|{raw['path']}"
        if outcome == "selected_latest_market_authority":
            excluded = int(reconciliation["excluded_noneligible_rows"])
            eligible = rows - excluded
            sources.append(
                _source(
                    identity,
                    eligible,
                    modeled=eligible,
                    observed_rows=rows,
                    excluded_noneligible_rows=excluded,
                )
            )
        elif outcome == "merged_nonconflicting_history":
            contributed = int(raw["contributed_rows"])
            sources.append(
                _source(identity, rows, modeled=contributed, reconciliation_only=rows - contributed)
            )
        else:
            sources.append(_source(identity, rows, reconciliation_only=rows))
    limitation = build["coverage_exceptions"][0]
    policy_identity = "trading_calendar|BSE_PARTIAL_NONBLOCKING"
    sources.append(_source(policy_identity, 1, quality_exception=1))
    exception = {
        "source_identity": policy_identity,
        "rule_id": str(limitation["code"]),
        "rule_version": "1",
        "affected_domain": "tradability_state",
        "affected_table": "dwd_trading_calendar",
        "affected_business_key": {
            "venue": "BSE",
            "minimum_trade_date": limitation["minimum_trade_date"],
            "maximum_trade_date": limitation["maximum_trade_date"],
        },
        "affected_effective_at": f"{limitation['minimum_trade_date']}T00:00:00+08:00",
        "detected_at": detected_at,
        "classification": "BOUNDED_HISTORICAL_COVERAGE",
        "severity": "WARN",
        "authority": str(limitation["policy"]),
        "source_record_id": "BSE:pre-venue-effective-range",
        "source_revision": manifest_sha,
        "source_artifact_sha256": manifest_sha,
        "evidence_digest": _digest(limitation),
        "disposition": "ACCEPTED_LIMITATION",
        "resolution_state": "OPEN_ACCEPTED",
    }
    return _receipt("trading_calendar", manifest_sha, run_id, plan_digest, sources, [exception])


def _market_receipt(
    path: Path,
    manifest_sha: str,
    run_id: str,
    plan_digest: str,
    detected_at: str,
    source_sha: Mapping[str, str],
) -> dict[str, object]:
    reconciliation = _object(path / "reconciliation/source-row-reconciliation.json")
    build = _object(path / "receipts/market-observation-build.json")
    sources = []
    for identity, raw in sorted(reconciliation["sources"].items()):
        rows = int(raw["declared_rows"])
        modeled, reconciled, exceptions = rows, 0, 0
        if identity == "daily_ohlcv_unadjusted":
            reconciled = int(
                reconciliation["historical_code_alias_reconciliation"]["folded_bar_alias_rows"]
            )
            exceptions = int(reconciliation["accepted_quality_exceptions"])
            modeled -= reconciled + exceptions
        sources.append(
            _source(
                identity,
                rows,
                modeled=modeled,
                reconciliation_only=reconciled,
                quality_exception=exceptions,
            )
        )
    raw = build["quality_exceptions"][0]
    source_identity = str(raw["record_source"])
    exception = {
        "source_identity": source_identity,
        "rule_id": str(raw["rule_id"]),
        "rule_version": "1",
        "affected_domain": "security_market_observation",
        "affected_table": str(raw["affected_table"]),
        "affected_business_key": json.loads(raw["affected_business_key_json"]),
        "affected_effective_at": json.loads(raw["affected_business_key_json"])["observation_start"],
        "detected_at": detected_at,
        "classification": str(raw["classification"]),
        "severity": str(raw["severity"]),
        "authority": str(raw["authority"]),
        "source_record_id": str(raw["source_record_id"]),
        "source_revision": str(raw["source_revision"]),
        "source_artifact_sha256": source_sha[source_identity],
        "evidence_digest": str(raw["evidence_digest"]),
        "disposition": "ACCEPTED_LIMITATION",
        "resolution_state": "OPEN_ACCEPTED",
    }
    return _receipt(
        "security_market_observation", manifest_sha, run_id, plan_digest, sources, [exception]
    )


def _tradability_receipt(
    path: Path, manifest_sha: str, run_id: str, plan_digest: str
) -> dict[str, object]:
    reconciliation = _object(path / "reconciliation/source-row-reconciliation.json")
    resolution = reconciliation["source_resolution"]
    groups = (
        ("daily_status_keys", "daily_status_unresolved"),
        ("price_regime_rows", "price_override_unresolved"),
        ("st_status_rows", "st_status_unresolved_in_scope"),
        ("suspension_rows", "suspension_unresolved_in_scope"),
    )
    sources = []
    for count_key, unresolved_key in groups:
        rows = int(resolution[count_key])
        unresolved = int(resolution[unresolved_key])
        sources.append(_source(count_key, rows, modeled=rows - unresolved, deferred=unresolved))
    return _receipt("tradability_state", manifest_sha, run_id, plan_digest, sources)


def _financial_receipt(
    path: Path, manifest_sha: str, run_id: str, plan_digest: str
) -> dict[str, object]:
    audit = _object(path / "audits/financial-source-audit.json")
    sources = []
    native = audit["component_audits"]["native"]["audit"]["source_rows"]
    for identity, raw in sorted(native.items()):
        rows = int(raw["selected_rows"])
        sources.append(
            _source(identity, rows, modeled=rows, source_artifact_sha256=raw["source_sha256"])
        )
    statement = audit["component_audits"]["statement"]["audit"]["source_rows"]
    for identity, raw in sorted(statement.items()):
        rows = int(raw["selected_business_key_count"])
        sources.append(
            _source(identity, rows, modeled=rows, source_artifact_sha256=raw["source_sha256"])
        )
    return _receipt("financial_reporting", manifest_sha, run_id, plan_digest, sources)


def _industry_receipt(
    path: Path, manifest_sha: str, run_id: str, plan_digest: str
) -> dict[str, object]:
    reconciliation = _object(path / "reconciliation/source-row-reconciliation.json")
    build = _object(path / "receipts/industry-classification-build.json")
    sources = [
        _source(
            identity,
            int(rows),
            modeled=int(rows),
            source_artifact_sha256=build["source_sha256"][identity],
        )
        for identity, rows in sorted(reconciliation["source_rows"].items())
    ]
    return _receipt("industry_classification", manifest_sha, run_id, plan_digest, sources)


def _accepted_exception(
    *,
    source_identity: str,
    rule_id: str,
    domain: str,
    table: str,
    business_key: Mapping[str, object],
    effective_at: datetime,
    detected_at: str,
    authority: str,
    source_record_id: str,
    source_revision: str,
    source_sha: str,
    classification: str,
) -> dict[str, object]:
    evidence = {
        "source_identity": source_identity,
        "source_record_id": source_record_id,
        "source_revision": source_revision,
        "source_artifact_sha256": source_sha,
        "rule_id": rule_id,
        "affected_business_key": business_key,
    }
    return {
        "source_identity": source_identity,
        "rule_id": rule_id,
        "rule_version": "1",
        "affected_domain": domain,
        "affected_table": table,
        "affected_business_key": dict(business_key),
        "affected_effective_at": effective_at.isoformat(),
        "detected_at": detected_at,
        "classification": classification,
        "severity": "WARN",
        "authority": authority,
        "source_record_id": source_record_id,
        "source_revision": source_revision,
        "source_artifact_sha256": source_sha,
        "evidence_digest": _digest(evidence),
        "disposition": "ACCEPTED_LIMITATION",
        "resolution_state": "OPEN_ACCEPTED",
    }


def _extract_terminal_exceptions(
    *,
    ods_package: Path,
    ledger: Mapping[str, Any],
    listed_company_candidate: Path,
    run_id: str,
    detected_at: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], Mapping[str, Any], Mapping[str, Any]]:
    from quanthus.pipelines.securities_warehouse import disclosure_event as disclosure
    from quanthus.pipelines.securities_warehouse import ownership_capital as ownership

    records = [dict(raw) for raw in ledger["records"]]
    resolver = disclosure._load_company_resolver(listed_company_candidate)
    disclosure_sources = disclosure._select_sources(records, ods_package)
    disclosure_exceptions: list[dict[str, object]] = []
    source = disclosure_sources["announcements"]
    connection = sqlite3.connect(f"file:{source.path}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            "SELECT code,publish_time,ann_id FROM announcements ORDER BY rowid"
        )
        for code, publish_time, ann_id in cursor:
            published = disclosure._provider_timestamp(publish_time, "announcement publish_time")
            company, reason = resolver.resolve_for_report(str(code), period_end=published.date())
            if company is None and str(reason) == "NO_LISTED_COMPANY_IDENTITY":
                disclosure_exceptions.append(
                    _accepted_exception(
                        source_identity="announcements",
                        rule_id="NO_LISTED_COMPANY_IDENTITY",
                        domain="disclosure_company_event",
                        table="dwd_disclosure",
                        business_key={"ann_id": str(ann_id), "stock_code": str(code)},
                        effective_at=published,
                        detected_at=detected_at,
                        authority="ODS_BASE_V1_2_IDENTITY_BOUNDARY",
                        source_record_id=str(ann_id),
                        source_revision=run_id,
                        source_sha=source.sha256,
                        classification="UNRESOLVED_COMPANY_IDENTITY",
                    )
                )
    finally:
        connection.close()

    ownership_sources = ownership._select_sources(records, ods_package)
    ownership_exceptions: list[dict[str, object]] = []
    source = ownership_sources["holder_changes"]
    connection = sqlite3.connect(f"file:{source.path}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            "SELECT code,change_date,disclosure_date,row_key FROM holder_changes ORDER BY rowid"
        )
        for code, changed, disclosed, row_key in cursor:
            coordinate = ownership._report_date(changed or disclosed, "holder change date")
            company, reason = resolver.resolve_for_report(str(code), period_end=coordinate)
            if company is None and str(reason) == "NO_LISTED_COMPANY_IDENTITY":
                ownership_exceptions.append(
                    _accepted_exception(
                        source_identity="holder_changes",
                        rule_id="NO_LISTED_COMPANY_IDENTITY",
                        domain="ownership_capital",
                        table="dwd_shareholding_change",
                        business_key={"row_key": str(row_key), "stock_code": str(code)},
                        effective_at=ownership._effective_start(coordinate),
                        detected_at=detected_at,
                        authority="ODS_BASE_V1_2_IDENTITY_BOUNDARY",
                        source_record_id=str(row_key),
                        source_revision=run_id,
                        source_sha=source.sha256,
                        classification="UNRESOLVED_COMPANY_IDENTITY",
                    )
                )
    finally:
        connection.close()

    selected: set[tuple[str, str]] = set()
    for source_id in ("parent_f10", "hotfix_f10"):
        source = ownership_sources[source_id]
        import pyarrow.parquet as pq

        for batch in pq.ParquetFile(source.path).iter_batches(batch_size=1024):
            for raw in batch.to_pylist():
                category = str(raw.get("category"))
                if category not in ownership._F10_CATEGORIES:
                    continue
                key = (str(raw.get("stock_code")), category)
                if key in selected:
                    continue
                selected.add(key)
                coordinate = ownership._report_date(
                    raw.get("market_date") or raw.get("provider_updated_date"), "F10 date"
                )
                company, _reason = resolver.resolve_for_report(key[0], period_end=coordinate)
                if company is None:
                    continue
                expanded = ownership.materialize_f10_rows(
                    raw,
                    company_id=company.company_id,
                    security_id=company.security_id,
                    source_sha256=source.sha256,
                )
                if not any(expanded.values()):
                    record_id = f"stock_code={key[0]}&category={category}"
                    ownership_exceptions.append(
                        _accepted_exception(
                            source_identity=source_id,
                            rule_id=f"EMPTY_F10_{category}",
                            domain="ownership_capital",
                            table="dwd_shareholding_observation",
                            business_key={"stock_code": key[0], "category": category},
                            effective_at=ownership._effective_start(coordinate),
                            detected_at=detected_at,
                            authority="ODS_BASE_V1_2_F10_PARSER",
                            source_record_id=record_id,
                            source_revision=str(raw.get("source_revision") or "provider-native"),
                            source_sha=source.sha256,
                            classification="EMPTY_STRUCTURED_F10_RESULT",
                        )
                    )
    return disclosure_exceptions, ownership_exceptions, disclosure_sources, ownership_sources


def _disclosure_receipt(
    path: Path,
    manifest_sha: str,
    run_id: str,
    plan_digest: str,
    exceptions: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    audit = _object(path / "source-audit.json")
    expected = int(
        audit["source_rows"]["announcements"]["identity_quality_exceptions"].get(
            "NO_LISTED_COMPANY_IDENTITY", 0
        )
    )
    if len(exceptions) != expected:
        raise QualityGovernanceBuildError("disclosure terminal exception extraction differs")
    sources = []
    for identity, raw in sorted(audit["source_rows"].items()):
        rows = int(raw["input_rows"])
        terminal = expected if identity == "announcements" else 0
        sources.append(
            _source(
                identity,
                rows,
                modeled=rows - terminal,
                quality_exception=terminal,
                source_artifact_sha256=audit["source_sha256"][identity],
            )
        )
    return _receipt(
        "disclosure_company_event", manifest_sha, run_id, plan_digest, sources, exceptions
    )


def _ownership_receipt(
    path: Path,
    manifest_sha: str,
    run_id: str,
    plan_digest: str,
    exceptions: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    audit = _object(path / "source-audit.json")
    counts = Counter(str(raw["source_identity"]) for raw in exceptions)
    expected_holder = int(
        audit["source_rows"]["holder_changes"]["identity_quality_exceptions"].get(
            "NO_LISTED_COMPANY_IDENTITY", 0
        )
    )
    if counts["holder_changes"] != expected_holder or sum(counts.values()) != expected_holder + 1:
        raise QualityGovernanceBuildError("ownership terminal exception extraction differs")
    sources = []
    holder_rows = int(audit["source_rows"]["holder_changes"]["input_rows"])
    sources.append(
        _source(
            "holder_changes",
            holder_rows,
            modeled=holder_rows - counts["holder_changes"],
            quality_exception=counts["holder_changes"],
            source_artifact_sha256=audit["source_sha256"]["holder_changes"],
        )
    )
    shareholder_rows = int(audit["source_rows"]["shareholder_count"]["input_rows"])
    sources.append(
        _source(
            "shareholder_count",
            shareholder_rows,
            modeled=shareholder_rows,
            source_artifact_sha256=audit["source_sha256"]["shareholder_count"],
        )
    )
    for identity in ("parent_f10", "hotfix_f10"):
        rows = int(audit["source_rows"]["f10"]["sources"][identity]["selected_ownership_rows"])
        sources.append(
            _source(
                identity,
                rows,
                modeled=rows - counts[identity],
                quality_exception=counts[identity],
                source_artifact_sha256=audit["source_sha256"][identity],
            )
        )
    return _receipt("ownership_capital", manifest_sha, run_id, plan_digest, sources, exceptions)


def verify_quality_governance_candidate(
    output: Path, *, run_id: str, plan_digest: str
) -> QualityGovernanceBuildResult:
    manifest = _object(output / "manifest.json")
    manifest_sha = _sha256(output / "manifest.json")
    if (
        output.is_symlink()
        or manifest.get("kind") != "dwd_quality_governance_candidate"
        or manifest.get("run_id") != run_id
        or manifest.get("input_plan_digest") != plan_digest
        or (output / "READY").read_text(encoding="ascii").strip() != manifest_sha
    ):
        raise QualityGovernanceBuildError("quality candidate binding differs")
    declared = {str(raw["path"]): raw for raw in manifest["files"]}
    physical = {
        item.relative_to(output).as_posix()
        for item in output.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    if physical != set(declared) | {"manifest.json", "READY"}:
        raise QualityGovernanceBuildError("quality candidate inventory differs")
    for relative, raw in declared.items():
        item = output / relative
        if item.stat().st_size != raw["size_bytes"] or _sha256(item) != raw["sha256"]:
            raise QualityGovernanceBuildError(f"quality candidate file differs: {relative}")
    return QualityGovernanceBuildResult(
        output.resolve(),
        str(manifest["semantic_digest"]),
        int(manifest["row_counts"]["dwd_quality_exception"]),
    )


def build_quality_governance(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    model_extension_path: Path,
    accounting_contract_path: Path,
    candidates: Mapping[str, Path],
    output: Path,
    run_id: str,
) -> QualityGovernanceBuildResult:
    """Backfill conserved domain receipts and seal the final DWD object."""

    from quanthus.pipelines.securities_warehouse.trading_calendar import _verify_plan_package

    if set(candidates) != {domain for domain, _kind in _CANDIDATES} or output.is_symlink():
        raise QualityGovernanceBuildError("quality candidate arguments differ")
    plan, ledger = _verify_plan_package(input_plan)
    if plan.get("run_id") != run_id:
        raise QualityGovernanceBuildError("quality run differs from input plan")
    plan_digest = str(plan["plan_digest"])
    detected_at = str(plan["plan"]["knowledge_cutoff"])
    contract, contract_sha = load_quality_contract(accounting_contract_path)
    extension_sha = verify_quality_models(model_path, model_extension_path)
    manifests: dict[str, tuple[dict[str, Any], str]] = {}
    for domain, kind in _CANDIDATES:
        manifests[domain] = _candidate(candidates[domain], kind, run_id, plan_digest)
    source_sha = {
        str(raw.get("dataset_id")): str(raw.get("sha256"))
        for raw in ledger["records"]
        if isinstance(raw, Mapping)
        and isinstance(raw.get("dataset_id"), str)
        and isinstance(raw.get("sha256"), str)
    }
    disclosure_exceptions, ownership_exceptions, _disclosure_sources, _ownership_sources = (
        _extract_terminal_exceptions(
            ods_package=ods_package,
            ledger=ledger,
            listed_company_candidate=candidates["listed_company"],
            run_id=run_id,
            detected_at=detected_at,
        )
    )
    receipts = [
        _listed_company_receipt(
            candidates["listed_company"], manifests["listed_company"][1], run_id, plan_digest
        ),
        _calendar_receipt(
            candidates["trading_calendar"],
            manifests["trading_calendar"][1],
            run_id,
            plan_digest,
            detected_at,
        ),
        _market_receipt(
            candidates["security_market_observation"],
            manifests["security_market_observation"][1],
            run_id,
            plan_digest,
            detected_at,
            source_sha,
        ),
        _tradability_receipt(
            candidates["tradability_state"], manifests["tradability_state"][1], run_id, plan_digest
        ),
        _disclosure_receipt(
            candidates["disclosure_company_event"],
            manifests["disclosure_company_event"][1],
            run_id,
            plan_digest,
            disclosure_exceptions,
        ),
        _financial_receipt(
            candidates["financial_reporting"],
            manifests["financial_reporting"][1],
            run_id,
            plan_digest,
        ),
        _industry_receipt(
            candidates["industry_classification"],
            manifests["industry_classification"][1],
            run_id,
            plan_digest,
        ),
        _ownership_receipt(
            candidates["ownership_capital"],
            manifests["ownership_capital"][1],
            run_id,
            plan_digest,
            ownership_exceptions,
        ),
    ]
    rows = quality_exception_rows(
        receipts, contract=contract, run_id=run_id, input_plan_digest=plan_digest
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        output.parent / f".{output.name}.lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o640,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise QualityGovernanceBuildError("quality lock is not physical")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if output.exists():
            return verify_quality_governance_candidate(
                output, run_id=run_id, plan_digest=plan_digest
            )
        work = output.with_name(f".{output.name}.work")
        if work.exists():
            shutil.rmtree(work)
        for receipt in receipts:
            _write_json(work / "receipts/domain-accounting" / f"{receipt['domain']}.json", receipt)
        _write_quality_rows(work, rows, default_detected_at=detected_at)
        files = sorted(item for item in work.rglob("*") if item.is_file())
        identity = {
            "run_id": run_id,
            "input_plan_digest": plan_digest,
            "model_sha256": _sha256(model_path),
            "model_extension_sha256": extension_sha,
            "accounting_contract_sha256": contract_sha,
            "upstream_candidate_manifests": {
                domain: value[1] for domain, value in sorted(manifests.items())
            },
        }
        records = [_file_record(work, item) for item in files]
        semantic_digest = _digest({**identity, "files": records})
        manifest = {
            "schema_version": 1,
            "kind": "dwd_quality_governance_candidate",
            **identity,
            "semantic_digest": semantic_digest,
            "owned_tables": ["dwd_quality_exception"],
            "row_counts": {"dwd_quality_exception": len(rows)},
            "domain_receipt_count": len(receipts),
            "files": records,
        }
        _write_json(work / "manifest.json", manifest)
        _write_text(work / "READY", _sha256(work / "manifest.json") + "\n")
        os.replace(work, output)
        return verify_quality_governance_candidate(output, run_id=run_id, plan_digest=plan_digest)
    finally:
        os.close(descriptor)
