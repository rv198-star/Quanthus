"""Read-only adapter from the real P1 corpus shape to normalization evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from quanthus.corporate_action_normalization import (
    MARKET_TIMEZONE,
    NORMALIZATION_VERSION,
    AuthoritativeCorporateActionEvidence,
    CashUnit,
    CorporateActionNormalizationError,
    CorporateActionNormalizationException,
    CorporateActionNormalizationPolicy,
    CorporateActionNormalizationReport,
    CorporateActionSourceEvidence,
    CrossCheckBaseline,
    NormalizedCorporateAction,
    audit_corporate_action_normalization,
)

CORPUS_ADAPTER_VERSION = "2.0.0"
REQUIRED_SNAPSHOT_DATASETS = {
    "adjustment_factors",
    "daily_ohlcv_none",
    "daily_ohlcv_qfq",
}


class CorporateActionCorpusError(ValueError):
    """Raised when the corpus cannot be interpreted under the frozen adapter."""


def _canonical_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _write_json_atomic(path: Path, value: object) -> None:
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CorporateActionCorpusError(f"{label} must be an object")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorporateActionCorpusError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class CorporateActionCorpusPaths:
    snapshot_dir: Path
    corporate_actions: Path
    eastmoney_raw_dir: Path | None = None


@dataclass(frozen=True)
class _SnapshotInputs:
    snapshot_id: str
    raw_bars: Path
    qfq_bars: Path
    adjustment_factors: Path
    dataset_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _EastmoneyRawRow:
    value: Mapping[str, object]
    source_row_ref: str
    source_file_digest: str
    source_row_digest: str


@dataclass(frozen=True)
class _EastmoneyEvidenceIndex:
    source_revision: str
    source_digest: str
    by_exact_key: Mapping[tuple[str, date], tuple[_EastmoneyRawRow, ...]]


@dataclass(frozen=True)
class CorporateActionCorpusException:
    action_id: str
    security_code: str
    effective_date: date | None
    known_at: datetime | None
    reason_code: str
    source_row_ref: str
    source_digest: str
    authority_source_row_refs: tuple[str, ...]
    authority_evidence_digests: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "security_code": self.security_code,
            "effective_date": (
                None if self.effective_date is None else self.effective_date.isoformat()
            ),
            "known_at": None if self.known_at is None else self.known_at.isoformat(),
            "reason_code": self.reason_code,
            "source_row_ref": self.source_row_ref,
            "source_digest": self.source_digest,
            "authority_source_row_refs": list(self.authority_source_row_refs),
            "authority_evidence_digests": list(self.authority_evidence_digests),
        }


@dataclass(frozen=True)
class CorporateActionExceptionLedgerEntry:
    exception_id: str
    action_id: str
    security_code: str
    effective_date: date | None
    known_at: datetime | None
    blocked_from: date
    blocked_through: date | None
    reason_code: str
    replay_disposition: str
    source_row_ref: str
    source_evidence_digest: str
    authority_source_row_refs: tuple[str, ...]
    authority_evidence_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.exception_id, "exception_id"),
            (self.source_evidence_digest, "source_evidence_digest"),
        ):
            if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
                raise CorporateActionCorpusError(f"{field_name} must be a sha256 digest")
        if self.replay_disposition != "security_fail_closed":
            raise CorporateActionCorpusError("exception replay disposition must fail closed")
        if self.blocked_through is not None and self.blocked_through < self.blocked_from:
            raise CorporateActionCorpusError("exception blocking interval is invalid")
        if self.effective_date is not None and self.blocked_from != self.effective_date:
            raise CorporateActionCorpusError("dated exception must block from its effective date")
        if len(self.authority_source_row_refs) != len(self.authority_evidence_digests):
            raise CorporateActionCorpusError("authority exception lineage does not reconcile")
        if any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            for value in self.authority_evidence_digests
        ):
            raise CorporateActionCorpusError("authority evidence digest must be sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "exception_id": self.exception_id,
            "action_id": self.action_id,
            "security_code": self.security_code,
            "effective_date": (
                None if self.effective_date is None else self.effective_date.isoformat()
            ),
            "known_at": None if self.known_at is None else self.known_at.isoformat(),
            "blocked_from": self.blocked_from.isoformat(),
            "blocked_through": (
                None if self.blocked_through is None else self.blocked_through.isoformat()
            ),
            "reason_code": self.reason_code,
            "replay_disposition": self.replay_disposition,
            "source_row_ref": self.source_row_ref,
            "source_evidence_digest": self.source_evidence_digest,
            "authority_source_row_refs": list(self.authority_source_row_refs),
            "authority_evidence_digests": list(self.authority_evidence_digests),
        }

    def blocks(self, security_code: str, market_date: date) -> bool:
        return (
            security_code == self.security_code
            and market_date >= self.blocked_from
            and (self.blocked_through is None or market_date <= self.blocked_through)
        )


@dataclass(frozen=True)
class CorporateActionExceptionLedger:
    version: str
    policy_version: str
    action_source_digest: str
    authority_source_digest: str | None
    entries: tuple[CorporateActionExceptionLedgerEntry, ...]

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.policy_version.strip():
            raise CorporateActionCorpusError("exception-ledger identity must not be empty")
        for value, field_name in (
            (self.action_source_digest, "action_source_digest"),
            (self.authority_source_digest, "authority_source_digest"),
        ):
            if value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
                raise CorporateActionCorpusError(f"{field_name} must be a sha256 digest")
        exception_ids = [item.exception_id for item in self.entries]
        if len(exception_ids) != len(set(exception_ids)):
            raise CorporateActionCorpusError("exception-ledger ids must be unique")
        expected_order = sorted(
            self.entries,
            key=lambda item: (
                item.blocked_from,
                item.security_code,
                item.action_id,
                item.exception_id,
            ),
        )
        if list(self.entries) != expected_order:
            raise CorporateActionCorpusError("exception-ledger entries must be canonical")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "policy_version": self.policy_version,
            "action_source_digest": self.action_source_digest,
            "authority_source_digest": self.authority_source_digest,
            "entry_count": len(self.entries),
            "entries": [item.to_dict() for item in self.entries],
        }

    def write_json(self, path: Path) -> None:
        _write_json_atomic(path, self.to_dict())


@dataclass(frozen=True)
class CorporateActionCorpusAuditReport:
    adapter_version: str
    snapshot_id: str
    snapshot_dataset_digests: tuple[tuple[str, str], ...]
    action_source_digest: str
    authority_source_revision: str | None
    authority_source_digest: str | None
    scope_start: date
    scope_end: date
    source_row_count: int
    implemented_row_count: int
    excluded_not_implemented_count: int
    excluded_out_of_scope_count: int
    source_exceptions: tuple[CorporateActionCorpusException, ...]
    normalization: CorporateActionNormalizationReport
    exception_ledger: CorporateActionExceptionLedger | None

    def __post_init__(self) -> None:
        if self.scope_start > self.scope_end:
            raise CorporateActionCorpusError("scope_start must not follow scope_end")
        if {name for name, _ in self.snapshot_dataset_digests} != REQUIRED_SNAPSHOT_DATASETS:
            raise CorporateActionCorpusError("snapshot dataset digest set is not exact")
        counts = (
            self.source_row_count,
            self.implemented_row_count,
            self.excluded_not_implemented_count,
            self.excluded_out_of_scope_count,
        )
        if any(value < 0 for value in counts):
            raise CorporateActionCorpusError("corpus audit counts must be nonnegative")
        if self.source_row_count != (
            self.implemented_row_count + self.excluded_not_implemented_count
        ):
            raise CorporateActionCorpusError("source disposition does not reconcile")
        if self.implemented_row_count != (
            self.excluded_out_of_scope_count
            + len(self.source_exceptions)
            + self.normalization.input_count
        ):
            raise CorporateActionCorpusError("implemented-action disposition does not reconcile")
        if (self.authority_source_revision is None) != (self.authority_source_digest is None):
            raise CorporateActionCorpusError("authority source revision and digest must coexist")
        if self.exception_ledger is not None:
            if self.exception_ledger.policy_version != self.normalization.policy_version:
                raise CorporateActionCorpusError("exception ledger policy does not match")
            if self.exception_ledger.action_source_digest != self.action_source_digest:
                raise CorporateActionCorpusError("exception ledger action source does not match")
            if self.exception_ledger.authority_source_digest != self.authority_source_digest:
                raise CorporateActionCorpusError("exception ledger authority source does not match")

    @property
    def normalized(self) -> tuple[NormalizedCorporateAction, ...]:
        return self.normalization.normalized

    @property
    def normalization_exceptions(
        self,
    ) -> tuple[CorporateActionNormalizationException, ...]:
        return self.normalization.exceptions

    @property
    def exception_count(self) -> int:
        return len(self.source_exceptions) + len(self.normalization_exceptions)

    @property
    def ready_for_snapshot(self) -> bool:
        if not self.normalized:
            return False
        if self.exception_count == 0:
            return True
        return (
            self.exception_ledger is not None
            and len(self.exception_ledger.entries) == self.exception_count
        )

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def summary_dict(self) -> dict[str, object]:
        return {
            "adapter_version": self.adapter_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_dataset_digests": dict(self.snapshot_dataset_digests),
            "action_source_digest": self.action_source_digest,
            "authority_source_revision": self.authority_source_revision,
            "authority_source_digest": self.authority_source_digest,
            "scope_start": self.scope_start.isoformat(),
            "scope_end": self.scope_end.isoformat(),
            "source_row_count": self.source_row_count,
            "implemented_row_count": self.implemented_row_count,
            "excluded_not_implemented_count": self.excluded_not_implemented_count,
            "excluded_out_of_scope_count": self.excluded_out_of_scope_count,
            "normalization_input_count": self.normalization.input_count,
            "normalized_count": len(self.normalized),
            "source_exception_count": len(self.source_exceptions),
            "normalization_exception_count": len(self.normalization_exceptions),
            "exception_count": self.exception_count,
            "versioned_exception_count": (
                0 if self.exception_ledger is None else len(self.exception_ledger.entries)
            ),
            "exception_ledger_digest": (
                None if self.exception_ledger is None else self.exception_ledger.digest
            ),
            "source_exception_reasons": dict(
                sorted(Counter(item.reason_code for item in self.source_exceptions).items())
            ),
            "normalization_exception_reasons": dict(
                sorted(Counter(item.reason_code for item in self.normalization_exceptions).items())
            ),
            "cash_units": dict(
                sorted(Counter(item.cash_unit.value for item in self.normalized).items())
            ),
            "normalization_bases": dict(
                sorted(Counter(item.normalization_basis.value for item in self.normalized).items())
            ),
            "factor_cross_check_statuses": dict(
                sorted(
                    Counter(
                        item.factor_cross_check_status.value for item in self.normalized
                    ).items()
                )
            ),
            "cross_check_baselines": dict(
                sorted(
                    Counter(
                        (
                            "unavailable"
                            if item.cross_check_baseline is None
                            else item.cross_check_baseline.value
                        )
                        for item in self.normalized
                    ).items()
                )
            ),
            "ready_for_snapshot": self.ready_for_snapshot,
            "execution_authorized": self.execution_authorized,
            "report_digest": self.digest,
        }

    def write_json(self, path: Path) -> None:
        """Atomically persist the full audit report without publishing source data."""

        _write_json_atomic(path, self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_version": self.adapter_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_dataset_digests": dict(self.snapshot_dataset_digests),
            "action_source_digest": self.action_source_digest,
            "authority_source_revision": self.authority_source_revision,
            "authority_source_digest": self.authority_source_digest,
            "scope_start": self.scope_start.isoformat(),
            "scope_end": self.scope_end.isoformat(),
            "source_row_count": self.source_row_count,
            "implemented_row_count": self.implemented_row_count,
            "excluded_not_implemented_count": self.excluded_not_implemented_count,
            "excluded_out_of_scope_count": self.excluded_out_of_scope_count,
            "source_exceptions": [item.to_dict() for item in self.source_exceptions],
            "normalization": self.normalization.to_dict(),
            "exception_ledger": (
                None if self.exception_ledger is None else self.exception_ledger.to_dict()
            ),
            "exception_count": self.exception_count,
            "ready_for_snapshot": self.ready_for_snapshot,
            "execution_authorized": self.execution_authorized,
        }


def _load_snapshot_inputs(snapshot_dir: Path) -> _SnapshotInputs:
    root = snapshot_dir.resolve()
    manifest_path = root / "snapshot.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorporateActionCorpusError(
            f"snapshot manifest does not exist: {manifest_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CorporateActionCorpusError(f"snapshot manifest is invalid JSON: {exc}") from exc
    manifest = _require_mapping(payload, "snapshot manifest")
    snapshot = _require_mapping(manifest.get("snapshot"), "snapshot identity")
    snapshot_id = _require_text(snapshot.get("snapshot_id"), "snapshot_id")
    raw_datasets = manifest.get("datasets")
    if not isinstance(raw_datasets, Sequence) or isinstance(raw_datasets, (str, bytes)):
        raise CorporateActionCorpusError("snapshot datasets must be an array")

    resolved: dict[str, Path] = {}
    dataset_digests: dict[str, str] = {}
    for raw_dataset in raw_datasets:
        dataset = _require_mapping(raw_dataset, "snapshot dataset")
        dataset_name = _require_text(dataset.get("dataset"), "dataset name")
        if dataset_name not in REQUIRED_SNAPSHOT_DATASETS:
            continue
        if dataset_name in resolved:
            raise CorporateActionCorpusError(f"duplicate snapshot dataset: {dataset_name}")
        partitions = dataset.get("partitions")
        if (
            not isinstance(partitions, Sequence)
            or isinstance(partitions, (str, bytes))
            or len(partitions) != 1
        ):
            raise CorporateActionCorpusError(
                f"snapshot dataset {dataset_name} must have one partition"
            )
        partition = _require_text(partitions[0], f"{dataset_name} partition")
        path = (root / partition).resolve()
        if not path.is_relative_to(root):
            raise CorporateActionCorpusError(
                f"snapshot dataset {dataset_name} escapes the snapshot directory"
            )
        if not path.is_file():
            raise CorporateActionCorpusError(
                f"snapshot dataset {dataset_name} partition does not exist"
            )
        checksums = _require_mapping(
            dataset.get("content_checksums"), f"{dataset_name} content checksums"
        )
        checksum = _require_text(checksums.get(partition), f"{dataset_name} checksum")
        if not checksum.startswith("sha256:") or len(checksum) != 71:
            raise CorporateActionCorpusError(f"{dataset_name} checksum must be sha256")
        resolved[dataset_name] = path
        dataset_digests[dataset_name] = checksum

    missing = REQUIRED_SNAPSHOT_DATASETS - set(resolved)
    if missing:
        raise CorporateActionCorpusError(
            f"snapshot is missing required datasets: {', '.join(sorted(missing))}"
        )
    return _SnapshotInputs(
        snapshot_id=snapshot_id,
        raw_bars=resolved["daily_ohlcv_none"],
        qfq_bars=resolved["daily_ohlcv_qfq"],
        adjustment_factors=resolved["adjustment_factors"],
        dataset_digests=tuple(sorted(dataset_digests.items())),
    )


def _require_columns(connection: Any, view: str, required: set[str]) -> None:
    rows = connection.execute(f"DESCRIBE {view}").fetchall()
    available = {str(row[0]) for row in rows}
    missing = required - available
    if missing:
        raise CorporateActionCorpusError(
            f"{view} is missing required columns: {', '.join(sorted(missing))}"
        )


def _source_digest(row: Mapping[str, object]) -> str:
    return _digest(dict(row))


def _source_exception(
    row: Mapping[str, object],
    reason_code: str,
    authority_candidates: Sequence[_EastmoneyRawRow] = (),
) -> CorporateActionCorpusException:
    effective_date = row.get("ex_date")
    announce_date = row.get("announce_date")
    return CorporateActionCorpusException(
        action_id=str(row.get("action_id") or "missing-action-id"),
        security_code=str(row.get("stock_code") or "missing-security-code"),
        effective_date=effective_date if isinstance(effective_date, date) else None,
        known_at=(_knowledge_time(announce_date) if isinstance(announce_date, date) else None),
        reason_code=reason_code,
        source_row_ref=str(row.get("source_row_key") or "missing-source-row-ref"),
        source_digest=_source_digest(row),
        authority_source_row_refs=tuple(item.source_row_ref for item in authority_candidates),
        authority_evidence_digests=tuple(item.source_row_digest for item in authority_candidates),
    )


def _decimal(value: object, *, zero_when_missing: bool = False) -> Decimal | None:
    if value is None:
        return Decimal("0") if zero_when_missing else None
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise CorporateActionNormalizationError(
            "invalid_evidence", "numeric source evidence must be finite"
        )
    return parsed


def _knowledge_time(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=MARKET_TIMEZONE)


def _source_precision_match_quality(
    source_value: Decimal,
    authority_value: Decimal,
    *,
    input_quantum: Decimal,
    match_quantum_floor: Decimal,
    absolute_tolerance: Decimal,
) -> Decimal | None:
    canonical_source = source_value.quantize(input_quantum, rounding=ROUND_HALF_UP).normalize()
    if authority_value == canonical_source:
        return Decimal("0")
    if canonical_source == 0:
        return None
    source_quantum = (
        match_quantum_floor
        if canonical_source == canonical_source.to_integral_value()
        else Decimal("1").scaleb(cast(int, canonical_source.as_tuple().exponent))
    )
    lower_bound = canonical_source - source_quantum / Decimal("2")
    upper_bound = canonical_source + source_quantum
    if lower_bound <= authority_value < upper_bound:
        return abs(authority_value - canonical_source) / source_quantum
    absolute_error = abs(authority_value - canonical_source)
    if absolute_error <= absolute_tolerance:
        return Decimal("1") + absolute_error / absolute_tolerance
    return None


def _raw_date(value: object, field_name: str) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise CorporateActionNormalizationError(
            "invalid_authoritative_evidence", f"{field_name} must contain an ISO date"
        )
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise CorporateActionNormalizationError(
            "invalid_authoritative_evidence", f"{field_name} must contain an ISO date"
        ) from exc


def _load_eastmoney_evidence(
    raw_dir: Path,
    security_codes: Sequence[str],
) -> _EastmoneyEvidenceIndex:
    root = raw_dir.resolve()
    if not root.is_dir():
        raise CorporateActionCorpusError(f"Eastmoney raw directory does not exist: {root}")
    meta_path = root.parent / "META.json"
    try:
        meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorporateActionCorpusError(
            f"Eastmoney authority metadata does not exist: {meta_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CorporateActionCorpusError("Eastmoney authority metadata is invalid JSON") from exc
    meta = _require_mapping(meta_raw, "Eastmoney authority metadata")
    source_revision = _require_text(meta.get("revision"), "Eastmoney source revision")
    file_bindings: list[dict[str, str | None]] = [
        {"path": "META.json", "digest": _file_digest(meta_path)}
    ]
    exact: dict[tuple[str, date], list[_EastmoneyRawRow]] = {}
    for security_code in sorted(set(security_codes)):
        path = (root / f"{security_code}.json").resolve()
        if not path.is_relative_to(root):
            raise CorporateActionCorpusError("Eastmoney authority path escapes raw directory")
        relative_path = path.relative_to(root.parent).as_posix()
        if not path.is_file():
            file_bindings.append({"path": relative_path, "digest": None})
            continue
        file_digest = _file_digest(path)
        file_bindings.append({"path": relative_path, "digest": file_digest})
        try:
            payload_raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorporateActionCorpusError(
                f"Eastmoney authority file is invalid JSON: {path}"
            ) from exc
        payload = _require_mapping(payload_raw, f"Eastmoney payload {path.name}")
        payload_code = payload.get("code", payload.get("stock_code"))
        if str(payload_code or "") != security_code:
            raise CorporateActionCorpusError(
                f"Eastmoney payload code does not match filename: {path.name}"
            )
        rows = payload.get("rows") or []
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise CorporateActionCorpusError(f"Eastmoney rows must be an array: {path.name}")
        for index, raw_row in enumerate(rows):
            row = _require_mapping(raw_row, f"Eastmoney row {path.name}#{index}")
            row_code = str(row.get("SECURITY_CODE") or "")
            if row_code != security_code:
                raise CorporateActionCorpusError(
                    f"Eastmoney row security does not match file: {path.name}#{index}"
                )
            raw_effective_date = row.get("EX_DIVIDEND_DATE")
            if raw_effective_date is None:
                continue
            try:
                effective_date = _raw_date(raw_effective_date, "EX_DIVIDEND_DATE")
            except CorporateActionNormalizationError as exc:
                raise CorporateActionCorpusError(str(exc)) from exc
            value = dict(row)
            exact.setdefault((security_code, effective_date), []).append(
                _EastmoneyRawRow(
                    value=value,
                    source_row_ref=f"{relative_path}#rows[{index}]",
                    source_file_digest=file_digest,
                    source_row_digest=_digest(value),
                )
            )
    source_digest = _digest(
        {
            "source_revision": source_revision,
            "files": file_bindings,
        }
    )
    return _EastmoneyEvidenceIndex(
        source_revision=source_revision,
        source_digest=source_digest,
        by_exact_key={key: tuple(value) for key, value in exact.items()},
    )


def _authority_from_raw(
    raw: _EastmoneyRawRow,
    *,
    source_revision: str,
    source_cash_unit: CashUnit,
) -> AuthoritativeCorporateActionEvidence:
    row = raw.value
    notice_date = _raw_date(row.get("NOTICE_DATE"), "NOTICE_DATE")
    return AuthoritativeCorporateActionEvidence(
        security_code=str(row.get("SECURITY_CODE") or ""),
        effective_date=_raw_date(row.get("EX_DIVIDEND_DATE"), "EX_DIVIDEND_DATE"),
        known_at=_knowledge_time(notice_date),
        cash_per_ten_shares=_decimal(row.get("PRETAX_BONUS_RMB"), zero_when_missing=True)
        or Decimal("0"),
        bonus_per_ten_shares=_decimal(row.get("BONUS_RATIO"), zero_when_missing=True)
        or Decimal("0"),
        transfer_per_ten_shares=_decimal(row.get("IT_RATIO"), zero_when_missing=True)
        or Decimal("0"),
        source_cash_unit=source_cash_unit,
        assignment_progress=str(row.get("ASSIGN_PROGRESS") or ""),
        implementation_plan=str(row.get("IMPL_PLAN_PROFILE") or ""),
        source_revision=source_revision,
        source_row_ref=raw.source_row_ref,
        source_file_digest=raw.source_file_digest,
        source_row_digest=raw.source_row_digest,
    )


def _resolve_authoritative_evidence(
    evidence: CorporateActionSourceEvidence,
    candidates: Sequence[_EastmoneyRawRow],
    policy: CorporateActionNormalizationPolicy,
    *,
    source_revision: str,
) -> AuthoritativeCorporateActionEvidence | None:
    if not candidates:
        return None
    input_quantum = policy.source_input_quantum
    match_quantum_floor = policy.source_match_quantum_floor
    cash_tolerance = policy.cash_per_share_tolerance
    share_tolerance = policy.share_per_ten_tolerance
    if (
        input_quantum is None
        or match_quantum_floor is None
        or cash_tolerance is None
        or share_tolerance is None
    ):
        return None
    matches: list[tuple[Decimal, AuthoritativeCorporateActionEvidence]] = []
    validation_error: CorporateActionNormalizationError | None = None
    for raw in candidates:
        row = raw.value
        authority_cash = _decimal(row.get("PRETAX_BONUS_RMB"), zero_when_missing=True) or Decimal(
            "0"
        )
        authority_bonus = _decimal(row.get("BONUS_RATIO"), zero_when_missing=True) or Decimal("0")
        authority_transfer = _decimal(row.get("IT_RATIO"), zero_when_missing=True) or Decimal("0")
        bonus_quality = _source_precision_match_quality(
            evidence.raw_bonus_shares,
            authority_bonus,
            input_quantum=input_quantum,
            match_quantum_floor=match_quantum_floor,
            absolute_tolerance=share_tolerance,
        )
        transfer_quality = _source_precision_match_quality(
            evidence.raw_transfer_shares,
            authority_transfer,
            input_quantum=input_quantum,
            match_quantum_floor=match_quantum_floor,
            absolute_tolerance=share_tolerance,
        )
        if bonus_quality is None or transfer_quality is None:
            continue
        cash_units: list[tuple[Decimal, CashUnit]] = []
        if evidence.raw_cash_value == 0 and authority_cash == 0:
            cash_units.append((Decimal("0"), CashUnit.ZERO))
        elif evidence.raw_cash_value > 0 and authority_cash > 0:
            per_ten_quality = _source_precision_match_quality(
                evidence.raw_cash_value,
                authority_cash,
                input_quantum=input_quantum,
                match_quantum_floor=match_quantum_floor,
                absolute_tolerance=cash_tolerance * Decimal("10"),
            )
            if per_ten_quality is not None:
                cash_units.append((per_ten_quality, CashUnit.PER_TEN_SHARES))
            per_share_quality = _source_precision_match_quality(
                evidence.raw_cash_value,
                authority_cash / Decimal("10"),
                input_quantum=input_quantum,
                match_quantum_floor=match_quantum_floor,
                absolute_tolerance=cash_tolerance,
            )
            if per_share_quality is not None:
                cash_units.append((per_share_quality, CashUnit.PER_SHARE))
        if not cash_units:
            continue
        cash_units.sort(key=lambda item: (item[0], item[1].value))
        if len(cash_units) > 1 and cash_units[0][0] == cash_units[1][0]:
            continue
        cash_quality, cash_unit = cash_units[0]
        try:
            authority = _authority_from_raw(
                raw,
                source_revision=source_revision,
                source_cash_unit=cash_unit,
            )
            if authority.assignment_progress != policy.eastmoney_assignment_progress:
                raise CorporateActionNormalizationError(
                    "authoritative_progress_mismatch",
                    "exact authority row is not an implemented allocation",
                )
            matches.append((bonus_quality + transfer_quality + cash_quality, authority))
        except CorporateActionNormalizationError as exc:
            validation_error = exc
    if validation_error is not None:
        raise validation_error
    matches.sort(key=lambda item: (item[0], item[1].source_row_ref))
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise CorporateActionNormalizationError(
            "authoritative_evidence_ambiguous",
            "source fields do not select one exact implementation plan",
        )
    if not matches:
        raise CorporateActionNormalizationError(
            "authoritative_source_mismatch",
            "exact implementation evidence disagrees with source action fields",
        )
    return matches[0][1]


def _evidence_from_row(
    row: Mapping[str, object],
    *,
    snapshot_id: str,
    action_source_digest: str,
    authority_candidates: Sequence[_EastmoneyRawRow] = (),
    authority_source_revision: str | None = None,
    policy: CorporateActionNormalizationPolicy | None = None,
) -> CorporateActionSourceEvidence:
    effective_date = row.get("ex_date")
    announce_date = row.get("announce_date")
    if not isinstance(effective_date, date):
        raise CorporateActionNormalizationError(
            "missing_effective_date", "implemented action has no effective date"
        )
    if not isinstance(announce_date, date):
        raise CorporateActionNormalizationError(
            "missing_announce_date", "implemented action has no announce date"
        )

    current_factor = _decimal(row.get("factor_after"))
    current_factor_cutoff = row.get("factor_knowledge_cutoff")
    if current_factor is not None and not isinstance(current_factor_cutoff, date):
        raise CorporateActionNormalizationError(
            "factor_visibility_missing", "current factor has no knowledge cutoff"
        )
    if isinstance(current_factor_cutoff, date) and current_factor_cutoff >= effective_date:
        raise CorporateActionNormalizationError(
            "factor_not_point_in_time", "current factor was not visible before effective date"
        )

    prior_close = _decimal(row.get("prior_close"))
    prior_factor = _decimal(row.get("prior_factor"))
    prior_factor_cutoff = row.get("prior_factor_knowledge_cutoff")
    qfq_close = _decimal(row.get("prior_qfq_close"))
    factor_before: Decimal | None = None
    baseline: CrossCheckBaseline | None = None
    if prior_close is not None and current_factor is not None:
        if (
            prior_factor is not None
            and isinstance(prior_factor_cutoff, date)
            and prior_factor_cutoff < effective_date
        ):
            factor_before = prior_factor
            baseline = CrossCheckBaseline.PRIOR_FACTOR
        elif qfq_close is not None:
            factor_before = qfq_close / prior_close
            baseline = CrossCheckBaseline.PRIOR_QFQ_RATIO

    has_cross_check = factor_before is not None and baseline is not None
    source_name = str(row.get("source") or "unknown-corporate-action-source")
    evidence = CorporateActionSourceEvidence(
        action_id=str(row.get("action_id") or ""),
        security_code=str(row.get("stock_code") or ""),
        effective_date=effective_date,
        known_at=_knowledge_time(announce_date),
        raw_cash_value=_decimal(row.get("cash_per_share"), zero_when_missing=True) or Decimal("0"),
        raw_bonus_shares=_decimal(row.get("bonus_shares"), zero_when_missing=True) or Decimal("0"),
        raw_transfer_shares=_decimal(row.get("transfer_shares"), zero_when_missing=True)
        or Decimal("0"),
        prior_close=prior_close if has_cross_check else None,
        factor_before=factor_before if has_cross_check else None,
        factor_after=current_factor if has_cross_check else None,
        cross_check_baseline=baseline if has_cross_check else None,
        source_revision=f"{source_name}|{action_source_digest}",
        source_row_ref=str(row.get("source_row_key") or ""),
        snapshot_id=snapshot_id,
    )
    if authority_candidates:
        if authority_source_revision is None or policy is None:
            raise CorporateActionNormalizationError(
                "invalid_authoritative_evidence", "authority resolution context is incomplete"
            )
        authority = _resolve_authoritative_evidence(
            evidence,
            authority_candidates,
            policy,
            source_revision=authority_source_revision,
        )
        return replace(evidence, authoritative_evidence=authority)
    return evidence


def _row_mapping(columns: Sequence[str], values: Sequence[object]) -> dict[str, object]:
    return dict(zip(columns, values, strict=True))


def _exception_ledger_entry(
    *,
    action_id: str,
    security_code: str,
    effective_date: date | None,
    known_at: datetime | None,
    reason_code: str,
    source_row_ref: str,
    source_evidence_digest: str,
    authority_source_row_refs: tuple[str, ...] = (),
    authority_evidence_digests: tuple[str, ...] = (),
    scope_start: date,
) -> CorporateActionExceptionLedgerEntry:
    blocked_from = (
        effective_date
        if effective_date is not None
        else (known_at.astimezone(MARKET_TIMEZONE).date() if known_at is not None else scope_start)
    )
    identity = {
        "action_id": action_id,
        "security_code": security_code,
        "effective_date": None if effective_date is None else effective_date.isoformat(),
        "known_at": None if known_at is None else known_at.isoformat(),
        "blocked_from": blocked_from.isoformat(),
        "blocked_through": None,
        "reason_code": reason_code,
        "replay_disposition": "security_fail_closed",
        "source_row_ref": source_row_ref,
        "source_evidence_digest": source_evidence_digest,
        "authority_source_row_refs": list(authority_source_row_refs),
        "authority_evidence_digests": list(authority_evidence_digests),
    }
    return CorporateActionExceptionLedgerEntry(
        exception_id=_digest(identity),
        action_id=action_id,
        security_code=security_code,
        effective_date=effective_date,
        known_at=known_at,
        blocked_from=blocked_from,
        blocked_through=None,
        reason_code=reason_code,
        replay_disposition="security_fail_closed",
        source_row_ref=source_row_ref,
        source_evidence_digest=source_evidence_digest,
        authority_source_row_refs=authority_source_row_refs,
        authority_evidence_digests=authority_evidence_digests,
    )


def audit_corporate_action_corpus(
    paths: CorporateActionCorpusPaths,
    policy: CorporateActionNormalizationPolicy,
    *,
    scope_start: date,
    scope_end: date,
) -> CorporateActionCorpusAuditReport:
    """Audit one explicit date range without mutating or publishing source data."""

    if scope_start > scope_end:
        raise CorporateActionCorpusError("scope_start must not follow scope_end")
    if policy.require_eastmoney_raw and paths.eastmoney_raw_dir is None:
        raise CorporateActionCorpusError(
            "normalization policy requires an explicit Eastmoney raw directory"
        )
    snapshot = _load_snapshot_inputs(paths.snapshot_dir)
    snapshot_paths = {
        "daily_ohlcv_none": snapshot.raw_bars,
        "daily_ohlcv_qfq": snapshot.qfq_bars,
        "adjustment_factors": snapshot.adjustment_factors,
    }
    for dataset_name, expected_digest in snapshot.dataset_digests:
        if _file_digest(snapshot_paths[dataset_name]) != expected_digest:
            raise CorporateActionCorpusError(f"snapshot dataset checksum mismatch: {dataset_name}")
    actions_path = paths.corporate_actions.resolve()
    if not actions_path.is_file():
        raise CorporateActionCorpusError(f"corporate-action source does not exist: {actions_path}")
    action_source_digest = _file_digest(actions_path)
    authority_index: _EastmoneyEvidenceIndex | None = None
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise CorporateActionCorpusError(
            "P1 dependencies are missing; run `make bootstrap-p1`"
        ) from exc

    connection = duckdb.connect(":memory:")
    try:
        views = {
            "corporate_actions": actions_path,
            "raw_bars": snapshot.raw_bars,
            "qfq_bars": snapshot.qfq_bars,
            "adjustment_factors": snapshot.adjustment_factors,
        }
        for view, path in views.items():
            connection.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet({_sql_string(str(path))})"
            )
        _require_columns(
            connection,
            "corporate_actions",
            {
                "action_id",
                "stock_code",
                "ex_date",
                "announce_date",
                "cash_per_share",
                "bonus_shares",
                "transfer_shares",
                "progress",
                "source",
                "source_row_key",
            },
        )

        if paths.eastmoney_raw_dir is not None:
            code_rows = connection.execute(
                """
                SELECT DISTINCT stock_code
                FROM corporate_actions
                WHERE progress = '实施' AND ex_date BETWEEN ? AND ?
                ORDER BY stock_code
                """,
                [scope_start, scope_end],
            ).fetchall()
            authority_index = _load_eastmoney_evidence(
                paths.eastmoney_raw_dir,
                [str(row[0]) for row in code_rows],
            )
        _require_columns(connection, "raw_bars", {"stock_code", "date", "close"})
        _require_columns(connection, "qfq_bars", {"stock_code", "date", "close"})
        _require_columns(
            connection,
            "adjustment_factors",
            {
                "stock_code",
                "effective_date",
                "fore_adjust_factor",
                "knowledge_cutoff",
            },
        )

        counts = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE progress = '实施')
            FROM corporate_actions
            """
        ).fetchone()
        if counts is None:
            raise CorporateActionCorpusError("corporate-action count query returned no row")
        source_row_count = int(counts[0])
        implemented_row_count = int(counts[1])
        excluded_not_implemented_count = source_row_count - implemented_row_count

        missing_cursor = connection.execute(
            """
            SELECT action_id, stock_code, ex_date, announce_date, cash_per_share,
                   bonus_shares, transfer_shares, source, source_row_key
            FROM corporate_actions
            WHERE progress = '实施' AND ex_date IS NULL
            ORDER BY stock_code, announce_date, action_id
            """
        )
        missing_columns = [str(item[0]) for item in missing_cursor.description]
        source_exceptions = [
            _source_exception(_row_mapping(missing_columns, row), "missing_effective_date")
            for row in missing_cursor.fetchall()
        ]

        excluded_row = connection.execute(
            """
            SELECT count(*)
            FROM corporate_actions
            WHERE progress = '实施'
              AND ex_date IS NOT NULL
              AND (ex_date < ? OR ex_date > ?)
            """,
            [scope_start, scope_end],
        ).fetchone()
        if excluded_row is None:
            raise CorporateActionCorpusError("out-of-scope count query returned no row")
        excluded_out_of_scope_count = int(excluded_row[0])

        cursor = connection.execute(
            """
            WITH factor_history AS (
                SELECT stock_code,
                       effective_date,
                       fore_adjust_factor AS factor_after,
                       knowledge_cutoff AS factor_knowledge_cutoff,
                       lag(fore_adjust_factor) OVER factor_order AS prior_factor,
                       lag(knowledge_cutoff) OVER factor_order
                           AS prior_factor_knowledge_cutoff
                FROM adjustment_factors
                WINDOW factor_order AS (
                    PARTITION BY stock_code ORDER BY effective_date
                )
            ),
            scoped_actions AS (
                SELECT *
                FROM corporate_actions
                WHERE progress = '实施'
                  AND ex_date BETWEEN ? AND ?
            )
            SELECT a.action_id,
                   a.stock_code,
                   a.ex_date,
                   a.announce_date,
                   a.cash_per_share,
                   a.bonus_shares,
                   a.transfer_shares,
                   a.source,
                   a.source_row_key,
                   r.close AS prior_close,
                   q.close AS prior_qfq_close,
                   f.factor_after,
                   f.factor_knowledge_cutoff,
                   f.prior_factor,
                   f.prior_factor_knowledge_cutoff
            FROM scoped_actions a
            ASOF LEFT JOIN raw_bars r
              ON a.stock_code = r.stock_code AND a.ex_date > r.date
            LEFT JOIN qfq_bars q
              ON q.stock_code = r.stock_code AND CAST(q.date AS DATE) = CAST(r.date AS DATE)
            LEFT JOIN factor_history f
              ON f.stock_code = a.stock_code AND f.effective_date = a.ex_date
            ORDER BY a.ex_date, a.stock_code, a.action_id
            """,
            [scope_start, scope_end],
        )
        columns = [str(item[0]) for item in cursor.description]
        evidence_rows: list[tuple[CorporateActionSourceEvidence, Mapping[str, object]]] = []
        for values in cursor.fetchall():
            row = _row_mapping(columns, values)
            row_effective_date = row.get("ex_date")
            if not isinstance(row_effective_date, date):
                raise CorporateActionCorpusError(
                    "scoped corporate-action query returned an invalid effective date"
                )
            authority_candidates = (
                ()
                if authority_index is None
                else authority_index.by_exact_key.get(
                    (str(row.get("stock_code") or ""), row_effective_date),
                    (),
                )
            )
            try:
                evidence_rows.append(
                    (
                        _evidence_from_row(
                            row,
                            snapshot_id=snapshot.snapshot_id,
                            action_source_digest=action_source_digest,
                            authority_candidates=authority_candidates,
                            authority_source_revision=(
                                None if authority_index is None else authority_index.source_revision
                            ),
                            policy=policy,
                        ),
                        row,
                    )
                )
            except CorporateActionNormalizationError as exc:
                source_exceptions.append(
                    _source_exception(row, exc.reason_code, authority_candidates)
                )
    finally:
        connection.close()

    duplicate_ids = {
        action_id
        for action_id, count in Counter(item.action_id for item, _ in evidence_rows).items()
        if count > 1
    }
    eligible_evidence: list[CorporateActionSourceEvidence] = []
    for item, source_row in evidence_rows:
        if item.action_id in duplicate_ids:
            source_exceptions.append(_source_exception(source_row, "duplicate_action"))
        else:
            eligible_evidence.append(item)
    source_exceptions.sort(
        key=lambda item: (
            item.effective_date or date.min,
            item.security_code,
            item.action_id,
            item.source_digest,
        )
    )
    normalization = (
        audit_corporate_action_normalization(eligible_evidence, policy)
        if eligible_evidence
        else CorporateActionNormalizationReport(
            normalization_version=NORMALIZATION_VERSION,
            policy_version=policy.policy_version,
            normalized=(),
            exceptions=(),
        )
    )
    exception_ledger: CorporateActionExceptionLedger | None = None
    if policy.exception_ledger_version is not None:
        ledger_entries = [
            _exception_ledger_entry(
                action_id=item.action_id,
                security_code=item.security_code,
                effective_date=item.effective_date,
                known_at=item.known_at,
                reason_code=item.reason_code,
                source_row_ref=item.source_row_ref,
                source_evidence_digest=item.source_digest,
                authority_source_row_refs=item.authority_source_row_refs,
                authority_evidence_digests=item.authority_evidence_digests,
                scope_start=scope_start,
            )
            for item in source_exceptions
        ]
        ledger_entries.extend(
            _exception_ledger_entry(
                action_id=item.action_id,
                security_code=item.security_code,
                effective_date=item.effective_date,
                known_at=item.known_at,
                reason_code=item.reason_code,
                source_row_ref=item.source_row_ref,
                source_evidence_digest=item.evidence_digest,
                scope_start=scope_start,
            )
            for item in normalization.exceptions
        )
        ledger_entries.sort(
            key=lambda item: (
                item.blocked_from,
                item.security_code,
                item.action_id,
                item.exception_id,
            )
        )
        exception_ledger = CorporateActionExceptionLedger(
            version=policy.exception_ledger_version,
            policy_version=policy.policy_version,
            action_source_digest=action_source_digest,
            authority_source_digest=(
                None if authority_index is None else authority_index.source_digest
            ),
            entries=tuple(ledger_entries),
        )
    return CorporateActionCorpusAuditReport(
        adapter_version=CORPUS_ADAPTER_VERSION,
        snapshot_id=snapshot.snapshot_id,
        snapshot_dataset_digests=snapshot.dataset_digests,
        action_source_digest=action_source_digest,
        authority_source_revision=(
            None if authority_index is None else authority_index.source_revision
        ),
        authority_source_digest=(
            None if authority_index is None else authority_index.source_digest
        ),
        scope_start=scope_start,
        scope_end=scope_end,
        source_row_count=source_row_count,
        implemented_row_count=implemented_row_count,
        excluded_not_implemented_count=excluded_not_implemented_count,
        excluded_out_of_scope_count=excluded_out_of_scope_count,
        source_exceptions=tuple(source_exceptions),
        normalization=normalization,
        exception_ledger=exception_ledger,
    )
