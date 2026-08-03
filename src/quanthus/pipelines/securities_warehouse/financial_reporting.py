"""Decode provider-native financial-statement envelopes for Issue #61."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zoneinfo import ZoneInfo


class FinancialReportingBuildError(RuntimeError):
    """Raised when a provider financial-statement envelope is not lossless."""


_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DecodedFinancialEnvelope:
    """Verified source payload plus its provider report periods."""

    raw_payload: bytes
    report_list: Mapping[str, Any]


@dataclass(frozen=True)
class StatementRowAccounting:
    """Explicit disposition counts for one provider statement envelope."""

    heading_rows: int
    null_placeholders: int
    valid_facts: int
    invalid_rows: int


@dataclass(frozen=True)
class FinancialStatementSource:
    """One checksum-bound statement-envelope source selected from the input plan."""

    label: str
    path: Path
    relative_path: str
    source_sha256: str
    source_revision: str
    precedence: Literal["parent", "hotfix"]


@dataclass(frozen=True)
class FinancialReportingSourceAudit:
    """Deterministic source accounting used to calibrate the #61 candidate build."""

    report: dict[str, Any]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"{field} must be a non-empty string")
    return value


def _unpack_byte_literal(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise FinancialReportingBuildError("raw_payload_gzip must be bytes")
    try:
        decoded = ast.literal_eval(bytes(value).decode("ascii"))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise FinancialReportingBuildError(
            "raw_payload_gzip is not an ASCII bytes literal"
        ) from exc
    if not isinstance(decoded, bytes):
        raise FinancialReportingBuildError("raw_payload_gzip literal must decode to bytes")
    return decoded


def decode_financial_envelope(row: Mapping[str, Any]) -> DecodedFinancialEnvelope:
    """Losslessly unpack, decompress, hash-check, and parse one ODS envelope."""

    packed = _unpack_byte_literal(row.get("raw_payload_gzip"))
    try:
        raw = gzip.decompress(packed)
    except OSError as exc:
        raise FinancialReportingBuildError("raw_payload_gzip is not a valid gzip stream") from exc
    declared_bytes = row.get("raw_payload_bytes")
    if not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool):
        raise FinancialReportingBuildError("raw_payload_bytes must be an integer")
    if len(raw) != declared_bytes:
        raise FinancialReportingBuildError("raw payload length differs from its envelope")
    declared_sha256 = _text(row.get("raw_payload_sha256"), "raw_payload_sha256")
    actual_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual_sha256 != declared_sha256:
        raise FinancialReportingBuildError("raw payload SHA-256 differs from its envelope")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FinancialReportingBuildError("raw financial payload is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise FinancialReportingBuildError("raw financial payload root must be an object")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, Mapping) else None
    report_list = data.get("report_list") if isinstance(data, Mapping) else None
    if not isinstance(report_list, Mapping):
        raise FinancialReportingBuildError("financial payload has no object report_list")
    return DecodedFinancialEnvelope(raw_payload=raw, report_list=report_list)


def conservative_provider_timestamp(value: object) -> datetime | None:
    """Convert a provider date or local timestamp to a UTC availability timestamp.

    A date-only value represents no more than a calendar-day disclosure, so its
    availability is conservatively set to the end of that Shanghai calendar day.
    """

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FinancialReportingBuildError("provider timestamp must be a string")
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 8 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        elif len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=_SHANGHAI)
    except ValueError as exc:
        raise FinancialReportingBuildError("provider timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


def classify_statement_rows(report: Mapping[str, Any]) -> StatementRowAccounting:
    """Classify every provider item without silently dropping malformed rows."""

    items = report.get("data")
    if not isinstance(items, list):
        raise FinancialReportingBuildError("provider report data must be an array")
    headings = 0
    nulls = 0
    facts = 0
    invalid = 0
    for item in items:
        if not isinstance(item, Mapping):
            invalid += 1
            continue
        field = item.get("item_field")
        display_type = item.get("item_display_type")
        if display_type == 1 or not isinstance(field, str) or not field.strip():
            headings += 1
            continue
        value = item.get("item_value")
        if value is None or (isinstance(value, str) and not value.strip()):
            nulls += 1
        else:
            facts += 1
    return StatementRowAccounting(headings, nulls, facts, invalid)


def iter_statement_reports(
    envelope: DecodedFinancialEnvelope,
) -> Iterator[tuple[str, Mapping[str, Any], StatementRowAccounting]]:
    """Yield provider reports in deterministic reporting-period order."""

    for period, value in sorted(envelope.report_list.items(), key=lambda item: str(item[0])):
        if not isinstance(period, str) or not period:
            raise FinancialReportingBuildError("financial report period must be a non-empty string")
        if not isinstance(value, Mapping):
            raise FinancialReportingBuildError(f"financial report {period} must be an object")
        yield period, value, classify_statement_rows(value)


_ACCOUNTING_FIELDS = (
    "envelope_rows",
    "report_periods",
    "heading_rows",
    "null_placeholders",
    "valid_facts",
    "invalid_rows",
    "materializable_fact_rows",
    "uncompressed_bytes",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FinancialReportingBuildError(f"cannot hash source file: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _source_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"financial source {field} must be a non-empty string")
    return value


def _source_path(ods_package: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or relative.as_posix() == "." or ".." in relative.parts:
        raise FinancialReportingBuildError(f"unsafe financial source path: {relative_path}")
    root = ods_package.resolve()
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise FinancialReportingBuildError(
            f"financial source is not a physical file: {relative_path}"
        )
    return path


def select_financial_statement_sources(
    ledger_records: Sequence[Mapping[str, Any]], ods_package: Path
) -> tuple[FinancialStatementSource, FinancialStatementSource]:
    """Resolve the exact parent-first statement sources from a sealed Base ledger.

    The July 2026 Base retains the complete weekly corpus under the generic
    ``financial_statements`` identity and a 209-security Hotfix under the
    ``fundamentals_financial_statements_parquet`` identity.  The latter is a
    fill-only overlay on the business key ``(stock_code, statement_type)``.
    """

    candidates = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "modeled"
        and PurePosixPath(str(record.get("path", ""))).name == "financial_statements.parquet"
    ]
    parent = [
        record
        for record in candidates
        if record.get("mapping_identity") == "financial_statements"
        and record.get("role") == "retained_immutable_source_gap_fill"
    ]
    hotfix = [
        record
        for record in candidates
        if record.get("mapping_identity") == "fundamentals_financial_statements_parquet"
    ]
    if len(parent) != 1 or len(hotfix) != 1 or len(candidates) != 2:
        raise FinancialReportingBuildError(
            "Base input plan must contain exactly one full financial-statements source "
            "and one fill-only financial-statements Hotfix"
        )

    def build(record: Mapping[str, Any], *, precedence: Literal["parent", "hotfix"]):
        relative_path = _source_text(record, "path")
        path = _source_path(ods_package, relative_path)
        declared_sha256 = _source_text(record, "sha256")
        if _sha256(path) != declared_sha256:
            raise FinancialReportingBuildError(
                f"financial source checksum differs from input plan: {relative_path}"
            )
        return FinancialStatementSource(
            label=_source_text(record, "mapping_identity"),
            path=path,
            relative_path=relative_path,
            source_sha256=declared_sha256,
            source_revision=str(record.get("source_revision", "")),
            precedence=precedence,
        )

    return build(parent[0], precedence="parent"), build(hotfix[0], precedence="hotfix")


def _empty_accounting() -> Counter[str]:
    return Counter({field: 0 for field in _ACCOUNTING_FIELDS})


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    stock_code = row.get("stock_code")
    statement_type = row.get("statement_type")
    security_id = row.get("security_id")
    if not isinstance(stock_code, str) or not stock_code:
        raise FinancialReportingBuildError("financial source row has no stock_code")
    if not isinstance(statement_type, str) or not statement_type:
        raise FinancialReportingBuildError("financial source row has no statement_type")
    if not isinstance(security_id, str) or not security_id:
        raise FinancialReportingBuildError("financial source row has no security_id")
    return stock_code, statement_type


def _analyze_envelope(
    row: Mapping[str, Any],
) -> tuple[str, Counter[str], set[tuple[str, str]]]:
    statement_type = _row_identity(row)[1]
    envelope = decode_financial_envelope(row)
    accounting = _empty_accounting()
    concept_coordinates: set[tuple[str, str]] = set()
    accounting["envelope_rows"] += 1
    accounting["uncompressed_bytes"] += len(envelope.raw_payload)
    for _period, report, report_accounting in iter_statement_reports(envelope):
        accounting["report_periods"] += 1
        accounting["heading_rows"] += report_accounting.heading_rows
        accounting["null_placeholders"] += report_accounting.null_placeholders
        accounting["valid_facts"] += report_accounting.valid_facts
        accounting["invalid_rows"] += report_accounting.invalid_rows
        accounting["materializable_fact_rows"] += (
            report_accounting.null_placeholders + report_accounting.valid_facts
        )
        items = report.get("data")
        if not isinstance(items, list):
            raise FinancialReportingBuildError("provider report data must be an array")
        for item in items:
            if not isinstance(item, Mapping):
                continue
            field = item.get("item_field")
            if (
                item.get("item_display_type") == 1
                or not isinstance(field, str)
                or not field.strip()
            ):
                continue
            concept_coordinates.add((statement_type, field.strip()))
    digest = row.get("raw_payload_sha256")
    if not isinstance(digest, str) or not digest:
        raise FinancialReportingBuildError("financial source row has no raw_payload_sha256")
    return digest, accounting, concept_coordinates


def _accounting_dict(value: Counter[str]) -> dict[str, int]:
    return {field: int(value[field]) for field in _ACCOUNTING_FIELDS}


def audit_financial_statement_sources(
    sources: Sequence[FinancialStatementSource], *, batch_size: int = 32
) -> FinancialReportingSourceAudit:
    """Stream the full statement corpus and prove its overlay disposition.

    This intentionally avoids DuckDB and full-table materialization.  Each
    Parquet batch is decoded, hash-checked, accounted, then released before the
    next batch.  The resulting fact upper bound is the maximum number of source
    items the #61 candidate must materialize before explicit quality exceptions.
    """

    if batch_size < 1:
        raise FinancialReportingBuildError("batch_size must be positive")
    if [source.precedence for source in sources] != ["parent", "hotfix"]:
        raise FinancialReportingBuildError("financial statement sources must be parent then Hotfix")
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError(
            "pyarrow is required for financial source audit"
        ) from exc

    source_accounting: dict[str, Counter[str]] = {
        source.label: _empty_accounting() for source in sources
    }
    selected_accounting: dict[str, Counter[str]] = {
        source.label: _empty_accounting() for source in sources
    }
    source_key_counts: Counter[str] = Counter()
    selected_key_counts: Counter[str] = Counter()
    parent_payloads: dict[tuple[str, str], str] = {}
    hotfix_keys: set[tuple[str, str]] = set()
    source_concepts: dict[str, set[tuple[str, str]]] = {
        source.label: set() for source in sources
    }
    selected_concepts: set[tuple[str, str]] = set()
    overlap_rows = 0
    hotfix_fill_rows = 0

    for source in sources:
        try:
            parquet = pq.ParquetFile(source.path)
        except (OSError, ValueError) as exc:
            raise FinancialReportingBuildError(
                f"cannot open financial statement source: {source.relative_path}"
            ) from exc
        required = {
            "security_id",
            "stock_code",
            "statement_type",
            "raw_payload_gzip",
            "raw_payload_sha256",
            "raw_payload_bytes",
        }
        present = set(parquet.schema_arrow.names)
        missing = sorted(required.difference(present))
        if missing:
            raise FinancialReportingBuildError(
                f"financial statement source lacks required columns: {missing}"
            )
        try:
            batches = parquet.iter_batches(batch_size=batch_size)
            for batch in batches:
                for raw_row in batch.to_pylist():
                    row = dict(raw_row)
                    key = _row_identity(row)
                    digest, row_accounting, row_concepts = _analyze_envelope(row)
                    source_accounting[source.label].update(row_accounting)
                    source_concepts[source.label].update(row_concepts)
                    source_key_counts[source.label] += 1
                    if source.precedence == "parent":
                        if key in parent_payloads:
                            raise FinancialReportingBuildError(
                                f"duplicate parent financial statement key: {key}"
                            )
                        parent_payloads[key] = digest
                        selected_accounting[source.label].update(row_accounting)
                        selected_concepts.update(row_concepts)
                        selected_key_counts[source.label] += 1
                        continue
                    if key in hotfix_keys:
                        raise FinancialReportingBuildError(
                            f"duplicate Hotfix financial statement key: {key}"
                        )
                    hotfix_keys.add(key)
                    parent_digest = parent_payloads.get(key)
                    if parent_digest is not None:
                        overlap_rows += 1
                        if parent_digest != digest:
                            raise FinancialReportingBuildError(
                                "financial statement parent/Hotfix overlap has different "
                                "raw payload "
                                f"for key: {key}"
                            )
                        continue
                    hotfix_fill_rows += 1
                    selected_accounting[source.label].update(row_accounting)
                    selected_concepts.update(row_concepts)
                    selected_key_counts[source.label] += 1
        except FinancialReportingBuildError:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed Parquet must fail the audit
            raise FinancialReportingBuildError(
                f"cannot stream financial statement source: {source.relative_path}"
            ) from exc

    all_source = _empty_accounting()
    selected = _empty_accounting()
    for source in sources:
        all_source.update(source_accounting[source.label])
        selected.update(selected_accounting[source.label])
    source_rows = {
        source.label: {
            "path": source.relative_path,
            "source_sha256": source.source_sha256,
            "source_revision": source.source_revision,
            "precedence": source.precedence,
            "input_accounting": _accounting_dict(source_accounting[source.label]),
            "selected_accounting": _accounting_dict(selected_accounting[source.label]),
            "input_business_key_count": int(source_key_counts[source.label]),
            "selected_business_key_count": int(selected_key_counts[source.label]),
            "unique_provider_concept_count": len(source_concepts[source.label]),
        }
        for source in sources
    }
    report = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_source_audit",
        "decoder_contract": "byte_literal_gzip_sha256_json_v1",
        "source_order": [source.label for source in sources],
        "business_key": ["stock_code", "statement_type"],
        "source_rows": source_rows,
        "overlay": {
            "policy": "parent_base_wins_on_overlapping_business_keys_hotfix_fills_absent_keys_only",
            "overlap_mismatch_policy": "fail_build",
            "parent_overlap_rows": overlap_rows,
            "hotfix_fill_rows": hotfix_fill_rows,
            "selected_envelope_rows": int(sum(selected_key_counts.values())),
        },
        "all_source_accounting": _accounting_dict(all_source),
        "selected_source_accounting": _accounting_dict(selected),
        "candidate_size_calibration": {
            "dwd_financial_report_rows": int(selected["report_periods"]),
            "dwd_financial_fact_upper_bound": int(selected["materializable_fact_rows"]),
            "provider_native_concept_coordinate_count": len(selected_concepts),
            "selected_uncompressed_bytes": int(selected["uncompressed_bytes"]),
        },
    }
    return FinancialReportingSourceAudit(report=report)


def write_financial_reporting_source_audit(
    output: Path, audit: FinancialReportingSourceAudit
) -> Path:
    """Write an immutable, deterministic source-audit evidence document."""

    if output.is_symlink():
        raise FinancialReportingBuildError(
            "financial source audit output must not be a symbolic link"
        )
    payload = (json.dumps(audit.report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    if output.exists():
        if not output.is_file() or output.read_bytes() != payload:
            raise FinancialReportingBuildError(
                "financial source audit output already exists with different content"
            )
        return output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise FinancialReportingBuildError(
            "financial source audit parent must be a physical directory"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            if not output.is_file() or output.read_bytes() != payload:
                raise FinancialReportingBuildError(
                    "financial source audit output was concurrently written with different content"
                ) from exc
        directory_descriptor = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return output.resolve()
