"""Build and verify immutable replay-facing corporate-action revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.corporate_action_corpus import (
    CorporateActionCorpusAuditReport,
    CorporateActionCorpusPaths,
    audit_corporate_action_corpus,
)
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy

NORMALIZED_DATASET = "corporate_actions_normalized"
EXCEPTION_DATASET = "corporate_action_exceptions"
NORMALIZED_FILE = f"{NORMALIZED_DATASET}.parquet"
EXCEPTION_FILE = f"{EXCEPTION_DATASET}.parquet"
REPORT_FILE = "audit_report.json"
CHANGE_FILE = "exception_changes.json"
MANIFEST_FILE = "manifest.json"
CHECKSUM_FILE = "CHECKSUMS.sha256"
REVISION_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
KNOWN_EXCEPTION_REASON_CODES = frozenset(
    {
        "authoritative_evidence_ambiguous",
        "authoritative_key_mismatch",
        "authoritative_notice_not_point_in_time",
        "authoritative_profile_invalid",
        "authoritative_profile_mismatch",
        "authoritative_progress_mismatch",
        "authoritative_source_mismatch",
        "cash_unit_ambiguous",
        "duplicate_action",
        "empty_action",
        "empty_authoritative_action",
        "empty_normalization_batch",
        "factor_not_point_in_time",
        "factor_ratio_mismatch",
        "factor_visibility_missing",
        "invalid_authoritative_evidence",
        "invalid_cash_candidate",
        "invalid_evidence",
        "invalid_normalized_action",
        "late_action_evidence",
        "missing_announce_date",
        "missing_cross_check",
        "missing_effective_date",
        "unsupported_cross_check",
    }
)


class CorporateActionRevisionError(RuntimeError):
    """Raised when a corporate-action revision cannot be built or verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorporateActionRevisionError(f"cannot load JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise CorporateActionRevisionError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CorporateActionRevisionError(f"{field_name} must be timezone-aware")


def _pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise CorporateActionRevisionError(
            "P1 dependencies are missing; run `make bootstrap-p1`"
        ) from exc
    return pa, pq


def _normalized_schema(pa: Any) -> Any:
    return pa.schema(
        [
            ("action_id", pa.string()),
            ("security_code", pa.string()),
            ("effective_date", pa.date32()),
            ("known_at", pa.timestamp("us", tz="Asia/Shanghai")),
            ("cash_dividend_per_share", pa.decimal128(38, 8)),
            ("bonus_share_ratio", pa.decimal128(38, 8)),
            ("cash_unit", pa.string()),
            ("normalization_basis", pa.string()),
            ("factor_cross_check_status", pa.string()),
            ("factor_ratio_observed", pa.decimal128(38, 12)),
            ("factor_ratio_expected", pa.decimal128(38, 12)),
            ("factor_ratio_error", pa.decimal128(38, 12)),
            ("cross_check_baseline", pa.string()),
            ("normalization_version", pa.string()),
            ("normalization_policy_version", pa.string()),
            ("source_revision", pa.string()),
            ("source_row_ref", pa.string()),
            ("source_snapshot_id", pa.string()),
            ("evidence_digest", pa.string()),
            ("authority_source_revision", pa.string()),
            ("authority_source_row_ref", pa.string()),
            ("authority_evidence_digest", pa.string()),
            ("execution_authorized", pa.bool_()),
        ]
    )


def _exception_schema(pa: Any) -> Any:
    return pa.schema(
        [
            ("exception_id", pa.string()),
            ("action_id", pa.string()),
            ("security_code", pa.string()),
            ("effective_date", pa.date32()),
            ("known_at", pa.timestamp("us", tz="Asia/Shanghai")),
            ("blocked_from", pa.date32()),
            ("blocked_through", pa.date32()),
            ("reason_code", pa.string()),
            ("replay_disposition", pa.string()),
            ("source_row_ref", pa.string()),
            ("source_evidence_digest", pa.string()),
            ("authority_source_row_refs", pa.list_(pa.string())),
            ("authority_evidence_digests", pa.list_(pa.string())),
            ("exception_ledger_version", pa.string()),
            ("normalization_policy_version", pa.string()),
            ("execution_authorized", pa.bool_()),
        ]
    )


def _normalized_rows(report: CorporateActionCorpusAuditReport) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in report.normalized:
        rows.append(
            {
                "action_id": item.action_id,
                "security_code": item.security_code,
                "effective_date": item.effective_date,
                "known_at": item.known_at,
                "cash_dividend_per_share": item.cash_dividend_per_share,
                "bonus_share_ratio": item.bonus_share_ratio,
                "cash_unit": item.cash_unit.value,
                "normalization_basis": item.normalization_basis.value,
                "factor_cross_check_status": item.factor_cross_check_status.value,
                "factor_ratio_observed": item.factor_ratio_observed,
                "factor_ratio_expected": item.factor_ratio_expected,
                "factor_ratio_error": item.factor_ratio_error,
                "cross_check_baseline": (
                    None if item.cross_check_baseline is None else item.cross_check_baseline.value
                ),
                "normalization_version": report.normalization.normalization_version,
                "normalization_policy_version": item.normalization_policy_version,
                "source_revision": item.source_revision,
                "source_row_ref": item.source_row_ref,
                "source_snapshot_id": item.snapshot_id,
                "evidence_digest": item.evidence_digest,
                "authority_source_revision": item.authority_source_revision,
                "authority_source_row_ref": item.authority_source_row_ref,
                "authority_evidence_digest": item.authority_evidence_digest,
                "execution_authorized": False,
            }
        )
    return rows


def _exception_rows(report: CorporateActionCorpusAuditReport) -> list[dict[str, object]]:
    ledger = report.exception_ledger
    if ledger is None:
        raise CorporateActionRevisionError("audit report has no versioned exception ledger")
    return [
        {
            "exception_id": item.exception_id,
            "action_id": item.action_id,
            "security_code": item.security_code,
            "effective_date": item.effective_date,
            "known_at": item.known_at,
            "blocked_from": item.blocked_from,
            "blocked_through": item.blocked_through,
            "reason_code": item.reason_code,
            "replay_disposition": item.replay_disposition,
            "source_row_ref": item.source_row_ref,
            "source_evidence_digest": item.source_evidence_digest,
            "authority_source_row_refs": list(item.authority_source_row_refs),
            "authority_evidence_digests": list(item.authority_evidence_digests),
            "exception_ledger_version": ledger.version,
            "normalization_policy_version": ledger.policy_version,
            "execution_authorized": False,
        }
        for item in ledger.entries
    ]


def _parquet_row_count(path: Path) -> int:
    _, pq = _pyarrow()
    try:
        metadata = pq.ParquetFile(path).metadata
    except Exception as exc:
        raise CorporateActionRevisionError(f"dataset is not readable Parquet: {path}") from exc
    if metadata is None:
        raise CorporateActionRevisionError(f"dataset has no Parquet metadata: {path}")
    return int(metadata.num_rows)


def _exception_index(path: Path) -> dict[str, str]:
    _, pq = _pyarrow()
    try:
        rows = pq.read_table(path, columns=["exception_id", "reason_code"]).to_pylist()
    except Exception as exc:
        raise CorporateActionRevisionError(f"cannot read exception ledger: {path}") from exc
    result: dict[str, str] = {}
    for row in rows:
        exception_id = str(row["exception_id"])
        reason_code = str(row["reason_code"])
        if exception_id in result:
            raise CorporateActionRevisionError("exception ledger contains duplicate ids")
        result[exception_id] = reason_code
    return result


def _reason_counts(ids: set[str], index: Mapping[str, str]) -> dict[str, int]:
    return dict(sorted(Counter(index[item] for item in ids).items()))


def require_known_exception_reasons(reason_codes: set[str]) -> None:
    """Stop automatic publication when a new exception semantic appears."""

    unknown = sorted(reason_codes - KNOWN_EXCEPTION_REASON_CODES)
    if unknown:
        raise CorporateActionRevisionError(
            "unknown corporate-action exception reason requires review: " + ", ".join(unknown)
        )


def _change_summary(
    *,
    current: Mapping[str, str],
    current_ledger_digest: str,
    previous_revision: CorporateActionRevision | None,
) -> dict[str, Any]:
    current_ids = set(current)
    previous: dict[str, str] = {}
    previous_digest: str | None = None
    if previous_revision is not None:
        previous = _exception_index(previous_revision.output / EXCEPTION_FILE)
        previous_digest = previous_revision.exception_ledger_digest
    previous_ids = set(previous)
    new_ids = current_ids - previous_ids
    persistent_ids = current_ids & previous_ids
    resolved_ids = previous_ids - current_ids
    return {
        "schema_version": 1,
        "baseline_available": previous_revision is not None,
        "previous_revision": (None if previous_revision is None else previous_revision.revision),
        "previous_exception_ledger_digest": previous_digest,
        "current_exception_ledger_digest": current_ledger_digest,
        "counts": {
            "current": len(current_ids),
            "new": len(new_ids),
            "persistent": len(persistent_ids),
            "resolved": len(resolved_ids),
        },
        "reason_counts": {
            "current": _reason_counts(current_ids, current),
            "new": _reason_counts(new_ids, current),
            "persistent": _reason_counts(persistent_ids, current),
            "resolved": _reason_counts(resolved_ids, previous),
        },
        "new_exception_ids": sorted(new_ids),
        "resolved_exception_ids": sorted(resolved_ids),
        "execution_authorized": False,
    }


def _snapshot_source_revisions(
    report: CorporateActionCorpusAuditReport,
) -> dict[str, dict[str, str]]:
    ledger = report.exception_ledger
    if ledger is None or report.authority_source_digest is None:
        raise CorporateActionRevisionError("v2 snapshot lineage is incomplete")
    common = {
        "normalization_policy_version": report.normalization.policy_version,
        "normalization_report_digest": report.digest,
        "corporate_action_source_digest": report.action_source_digest,
        "authority_source_digest": report.authority_source_digest,
        "exception_ledger_digest": ledger.digest,
        "exception_ledger_version": ledger.version,
    }
    return {
        NORMALIZED_DATASET: {
            **common,
            "normalization_exception_count": str(report.exception_count),
        },
        EXCEPTION_DATASET: {
            **common,
            "replay_disposition": "security_fail_closed",
        },
    }


@dataclass(frozen=True)
class CorporateActionRevision:
    revision: str
    output: Path
    knowledge_cutoff: datetime
    normalized_count: int
    exception_count: int
    report_digest: str
    exception_ledger_digest: str
    checksums: Mapping[str, str]
    snapshot_source_revisions: Mapping[str, Mapping[str, str]]

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "normalized_count": self.normalized_count,
            "exception_count": self.exception_count,
            "report_digest": self.report_digest,
            "exception_ledger_digest": self.exception_ledger_digest,
            "checksums": dict(sorted(self.checksums.items())),
            "snapshot_source_revisions": {
                name: dict(sorted(revisions.items()))
                for name, revisions in sorted(self.snapshot_source_revisions.items())
            },
            "execution_authorized": False,
        }


def build_corporate_action_revision(
    *,
    paths: CorporateActionCorpusPaths,
    policy: CorporateActionNormalizationPolicy,
    scope_start: date,
    scope_end: date,
    output: Path,
    revision: str,
    knowledge_cutoff: datetime,
    previous_revision: Path | None = None,
) -> CorporateActionRevision:
    """Audit the full corpus and atomically build one immutable consumer revision."""

    _require_aware(knowledge_cutoff, "knowledge_cutoff")
    if not revision.strip():
        raise CorporateActionRevisionError("revision must not be empty")
    output = output.resolve()
    if output.name != revision:
        raise CorporateActionRevisionError("revision must match the output directory name")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite corporate-action revision: {output}")
    prior = (
        None if previous_revision is None else check_corporate_action_revision(previous_revision)
    )
    if prior is not None and prior.knowledge_cutoff > knowledge_cutoff:
        raise CorporateActionRevisionError("knowledge_cutoff cannot move behind the prior revision")

    report = audit_corporate_action_corpus(
        paths,
        policy,
        scope_start=scope_start,
        scope_end=scope_end,
    )
    if not report.ready_for_snapshot:
        raise CorporateActionRevisionError("corporate-action audit is not ready for snapshot")
    if report.exception_ledger is None:
        raise CorporateActionRevisionError("corporate-action audit produced no exception ledger")
    require_known_exception_reasons({item.reason_code for item in report.exception_ledger.entries})

    pa, pq = _pyarrow()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        normalized_path = staging / NORMALIZED_FILE
        exception_path = staging / EXCEPTION_FILE
        normalized_rows = _normalized_rows(report)
        exception_rows = _exception_rows(report)
        pq.write_table(
            pa.Table.from_pylist(normalized_rows, schema=_normalized_schema(pa)),
            normalized_path,
            compression="zstd",
        )
        pq.write_table(
            pa.Table.from_pylist(exception_rows, schema=_exception_schema(pa)),
            exception_path,
            compression="zstd",
        )
        report.write_json(staging / REPORT_FILE)
        current_index = {
            str(row["exception_id"]): str(row["reason_code"]) for row in exception_rows
        }
        changes = _change_summary(
            current=current_index,
            current_ledger_digest=report.exception_ledger.digest,
            previous_revision=prior,
        )
        _write_json(staging / CHANGE_FILE, changes)
        files = (NORMALIZED_FILE, EXCEPTION_FILE, REPORT_FILE, CHANGE_FILE)
        checksums = {name: _sha256(staging / name) for name in files}
        source_revisions = _snapshot_source_revisions(report)
        manifest = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "kind": "corporate_action_replay_revision",
            "revision": revision,
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "scope": {
                "start": scope_start.isoformat(),
                "end": scope_end.isoformat(),
            },
            "source": {
                "snapshot_id": report.snapshot_id,
                "snapshot_dataset_digests": dict(report.snapshot_dataset_digests),
                "corporate_action_source_digest": report.action_source_digest,
                "authority_source_revision": report.authority_source_revision,
                "authority_source_digest": report.authority_source_digest,
            },
            "policy": {
                "normalization_version": report.normalization.normalization_version,
                "normalization_policy_version": report.normalization.policy_version,
                "exception_ledger_version": report.exception_ledger.version,
            },
            "lineage": {
                "normalization_report_digest": report.digest,
                "exception_ledger_digest": report.exception_ledger.digest,
                "previous_revision": None if prior is None else prior.revision,
                "previous_manifest_sha256": (
                    None if prior is None else _sha256(prior.output / MANIFEST_FILE)
                ),
            },
            "counts": {
                "source_rows": report.source_row_count,
                "normalized_rows": len(normalized_rows),
                "exception_rows": len(exception_rows),
                "new_exceptions": changes["counts"]["new"],
                "persistent_exceptions": changes["counts"]["persistent"],
                "resolved_exceptions": changes["counts"]["resolved"],
            },
            "snapshot_source_revisions": source_revisions,
            "checksums": checksums,
            "ready_for_snapshot": True,
            "execution_authorized": False,
        }
        _write_json(staging / MANIFEST_FILE, manifest)
        (staging / CHECKSUM_FILE).write_text(
            "".join(
                f"{checksum.removeprefix('sha256:')}  {name}\n"
                for name, checksum in sorted(checksums.items())
            ),
            encoding="utf-8",
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return check_corporate_action_revision(output)


def check_corporate_action_revision(revision_dir: Path) -> CorporateActionRevision:
    """Verify checksums and the row-level reconciliation of one immutable revision."""

    revision_dir = revision_dir.resolve()
    manifest = _load_object(revision_dir / MANIFEST_FILE)
    if (
        manifest.get("schema_version") != REVISION_SCHEMA_VERSION
        or manifest.get("kind") != "corporate_action_replay_revision"
    ):
        raise CorporateActionRevisionError("unsupported corporate-action revision manifest")
    revision = str(manifest.get("revision", ""))
    if not revision or revision != revision_dir.name:
        raise CorporateActionRevisionError("corporate-action revision does not match its directory")
    try:
        knowledge_cutoff = datetime.fromisoformat(str(manifest["knowledge_cutoff"]))
    except (KeyError, ValueError) as exc:
        raise CorporateActionRevisionError("revision has an invalid knowledge_cutoff") from exc
    _require_aware(knowledge_cutoff, "knowledge_cutoff")
    checksums_raw = manifest.get("checksums")
    if not isinstance(checksums_raw, Mapping):
        raise CorporateActionRevisionError("revision manifest has no checksums")
    checksums = {str(name): str(value) for name, value in checksums_raw.items()}
    expected_files = {NORMALIZED_FILE, EXCEPTION_FILE, REPORT_FILE, CHANGE_FILE}
    if set(checksums) != expected_files:
        raise CorporateActionRevisionError("revision checksum inventory is not exact")
    for name, checksum in checksums.items():
        path = revision_dir / name
        if not path.is_file() or _sha256(path) != checksum:
            raise CorporateActionRevisionError(f"corporate-action checksum mismatch: {name}")
    ledger_checksums: dict[str, str] = {}
    try:
        for line in (revision_dir / CHECKSUM_FILE).read_text(encoding="utf-8").splitlines():
            digest, name = line.split(maxsplit=1)
            ledger_checksums[name] = f"sha256:{digest}"
    except (OSError, ValueError) as exc:
        raise CorporateActionRevisionError("invalid corporate-action checksum ledger") from exc
    if ledger_checksums != checksums:
        raise CorporateActionRevisionError("manifest and checksum ledger disagree")

    counts = manifest.get("counts")
    lineage = manifest.get("lineage")
    source_revisions = manifest.get("snapshot_source_revisions")
    if not isinstance(counts, Mapping) or not isinstance(lineage, Mapping):
        raise CorporateActionRevisionError("revision manifest counts or lineage are missing")
    if not isinstance(source_revisions, Mapping):
        raise CorporateActionRevisionError("revision has no snapshot source revisions")
    normalized_count = _parquet_row_count(revision_dir / NORMALIZED_FILE)
    exception_count = _parquet_row_count(revision_dir / EXCEPTION_FILE)
    try:
        declared_normalized_count = int(counts.get("normalized_rows", -1))
        declared_exception_count = int(counts.get("exception_rows", -1))
    except (TypeError, ValueError) as exc:
        raise CorporateActionRevisionError("revision manifest row counts are invalid") from exc
    if normalized_count <= 0 or normalized_count != declared_normalized_count:
        raise CorporateActionRevisionError(
            "normalized corporate-action row count does not reconcile"
        )
    if exception_count != declared_exception_count:
        raise CorporateActionRevisionError(
            "corporate-action exception row count does not reconcile"
        )
    report_digest = str(lineage.get("normalization_report_digest", ""))
    ledger_digest = str(lineage.get("exception_ledger_digest", ""))
    if _SHA256.fullmatch(report_digest) is None or _SHA256.fullmatch(ledger_digest) is None:
        raise CorporateActionRevisionError("revision report or ledger digest is invalid")
    changes = _load_object(revision_dir / CHANGE_FILE)
    change_counts = changes.get("counts")
    if not isinstance(change_counts, Mapping):
        raise CorporateActionRevisionError("exception-change summary has no counts")
    try:
        current_count = int(change_counts.get("current", -1))
        new_count = int(change_counts.get("new", -1))
        persistent_count = int(change_counts.get("persistent", -1))
        resolved_count = int(change_counts.get("resolved", -1))
        manifest_new_count = int(counts.get("new_exceptions", -1))
        manifest_persistent_count = int(counts.get("persistent_exceptions", -1))
        manifest_resolved_count = int(counts.get("resolved_exceptions", -1))
    except (TypeError, ValueError) as exc:
        raise CorporateActionRevisionError("exception-change counts are invalid") from exc
    if (
        current_count != exception_count
        or new_count < 0
        or persistent_count < 0
        or resolved_count < 0
        or new_count + persistent_count != current_count
        or manifest_new_count != new_count
        or manifest_persistent_count != persistent_count
        or manifest_resolved_count != resolved_count
    ):
        raise CorporateActionRevisionError("exception-change counts do not reconcile")
    new_ids = changes.get("new_exception_ids")
    resolved_ids = changes.get("resolved_exception_ids")
    if (
        not isinstance(new_ids, list)
        or not isinstance(resolved_ids, list)
        or len(new_ids) != new_count
        or len(resolved_ids) != resolved_count
        or len(set(str(item) for item in new_ids)) != new_count
        or len(set(str(item) for item in resolved_ids)) != resolved_count
    ):
        raise CorporateActionRevisionError("exception-change id sets do not reconcile")
    new_id_set = {str(item) for item in new_ids}
    resolved_id_set = {str(item) for item in resolved_ids}
    baseline_available = changes.get("baseline_available")
    if not isinstance(baseline_available, bool):
        raise CorporateActionRevisionError("exception-change baseline flag is invalid")
    if baseline_available is False and (
        persistent_count != 0 or resolved_count != 0 or new_count != current_count
    ):
        raise CorporateActionRevisionError("baseline-free exception changes are invalid")
    typed_revisions: dict[str, dict[str, str]] = {}
    for dataset in (NORMALIZED_DATASET, EXCEPTION_DATASET):
        revisions = source_revisions.get(dataset)
        if not isinstance(revisions, Mapping):
            raise CorporateActionRevisionError(f"snapshot lineage is missing for {dataset}")
        typed_revisions[dataset] = {str(name): str(value) for name, value in revisions.items()}
    exception_index = _exception_index(revision_dir / EXCEPTION_FILE)
    require_known_exception_reasons(set(exception_index.values()))
    current_ids = set(exception_index)
    if not new_id_set <= current_ids or resolved_id_set & current_ids:
        raise CorporateActionRevisionError("exception-change ids disagree with the current ledger")
    if baseline_available is False and new_id_set != current_ids:
        raise CorporateActionRevisionError("baseline-free new ids must equal the current ledger")
    if (
        typed_revisions[NORMALIZED_DATASET].get("normalization_report_digest") != report_digest
        or typed_revisions[EXCEPTION_DATASET].get("exception_ledger_digest") != ledger_digest
        or typed_revisions[NORMALIZED_DATASET].get("normalization_exception_count")
        != str(exception_count)
    ):
        raise CorporateActionRevisionError("snapshot lineage does not reconcile with the revision")
    if (
        manifest.get("ready_for_snapshot") is not True
        or manifest.get("execution_authorized") is not False
    ):
        raise CorporateActionRevisionError("revision readiness or authority flags are invalid")
    return CorporateActionRevision(
        revision=revision,
        output=revision_dir,
        knowledge_cutoff=knowledge_cutoff,
        normalized_count=normalized_count,
        exception_count=exception_count,
        report_digest=report_digest,
        exception_ledger_digest=ledger_digest,
        checksums=checksums,
        snapshot_source_revisions=typed_revisions,
    )


def promote_corporate_action_revision(
    *, revision_dir: Path, pointer: Path
) -> CorporateActionRevision:
    """Atomically advance the accepted corporate-action revision pointer."""

    result = check_corporate_action_revision(revision_dir)
    pointer = pointer.resolve()
    if pointer.is_file():
        current = _load_object(pointer)
        try:
            current_cutoff = datetime.fromisoformat(str(current["knowledge_cutoff"]))
        except (KeyError, ValueError) as exc:
            raise CorporateActionRevisionError(
                "current corporate-action pointer has an invalid knowledge_cutoff"
            ) from exc
        _require_aware(current_cutoff, "current knowledge_cutoff")
        if result.knowledge_cutoff < current_cutoff:
            raise CorporateActionRevisionError("refusing to move corporate-action CURRENT backward")
    payload = {
        "schema_version": 1,
        "dataset": "corporate_action_replay",
        "revision": result.revision,
        "revision_path": str(result.output),
        "knowledge_cutoff": result.knowledge_cutoff.isoformat(),
        "manifest_sha256": _sha256(result.output / MANIFEST_FILE),
        "normalized_count": result.normalized_count,
        "exception_count": result.exception_count,
        "exception_ledger_digest": result.exception_ledger_digest,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "execution_authorized": False,
    }
    pointer.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, pointer)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return result
