"""Versioned point-in-time security identity and code-validity contracts.

An exchange code identifies a listing, not necessarily the economic security over its
entire apparent history.  The raw provider can expose predecessor bars under a
successor code, so all downstream DWD/PIT builds must first restrict every code to a
non-overlapping validity interval and then use ``security_id`` as the economic key.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError, find_project_root


class CodeValidityError(ConfigurationError):
    """Raised when a code-validity mapping cannot safely normalize history."""


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodeValidityError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(_require_string(value, field_name))
    except ValueError as exc:
        raise CodeValidityError(f"{field_name} must be an ISO date") from exc


def _require_aware_datetime(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_require_string(value, field_name))
    except ValueError as exc:
        raise CodeValidityError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CodeValidityError(f"{field_name} must include a UTC offset")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class CodeValidityInterval:
    """One code's inclusive interval for one economic security."""

    security_id: str
    stock_code: str
    exchange: str
    valid_from: date
    valid_to: date | None
    source: str
    evidence_locator: str
    evidence_sha256: str
    source_revision: str
    knowledge_cutoff: datetime

    def __post_init__(self) -> None:
        required = {
            "security_id": self.security_id,
            "stock_code": self.stock_code,
            "exchange": self.exchange,
            "source": self.source,
            "evidence_locator": self.evidence_locator,
            "evidence_sha256": self.evidence_sha256,
            "source_revision": self.source_revision,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise CodeValidityError(f"code validity fields must not be empty: {', '.join(missing)}")
        if not self.evidence_sha256.startswith("sha256:") or len(self.evidence_sha256) != 71:
            raise CodeValidityError("evidence_sha256 must use sha256:<64 lowercase hex characters>")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise CodeValidityError("valid_to cannot precede valid_from")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise CodeValidityError("knowledge_cutoff must include a UTC offset")

    def contains(self, market_date: date) -> bool:
        return market_date >= self.valid_from and (
            self.valid_to is None or market_date <= self.valid_to
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "security_id": self.security_id,
            "stock_code": self.stock_code,
            "exchange": self.exchange,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
            "source": self.source,
            "evidence_locator": self.evidence_locator,
            "evidence_sha256": self.evidence_sha256,
            "source_revision": self.source_revision,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
        }


def _intervals_overlap(left: CodeValidityInterval, right: CodeValidityInterval) -> bool:
    left_end = left.valid_to or date.max
    right_end = right.valid_to or date.max
    return left.valid_from <= right_end and right.valid_from <= left_end


@dataclass(frozen=True)
class CodeValidityMapping:
    """Frozen alias overrides plus their source evidence."""

    path: Path
    version: str
    source_revision: str
    intervals: tuple[CodeValidityInterval, ...]

    @property
    def digest(self) -> str:
        payload = {
            "version": self.version,
            "source_revision": self.source_revision,
            "intervals": [interval.to_dict() for interval in self.intervals],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_intervals(
    intervals: Iterable[CodeValidityInterval],
) -> tuple[CodeValidityInterval, ...]:
    ordered = tuple(
        sorted(
            intervals,
            key=lambda item: (
                item.security_id,
                item.stock_code,
                item.valid_from,
                item.valid_to or date.max,
            ),
        )
    )
    seen: set[tuple[str, str, date]] = set()
    by_identity: dict[tuple[str, str], list[CodeValidityInterval]] = defaultdict(list)
    by_code: dict[tuple[str, str], list[CodeValidityInterval]] = defaultdict(list)
    for interval in ordered:
        key = (interval.security_id, interval.stock_code, interval.valid_from)
        if key in seen:
            raise CodeValidityError(
                "code validity intervals must have unique identity/code/start keys"
            )
        seen.add(key)
        by_identity[(interval.security_id, interval.exchange)].append(interval)
        by_code[(interval.stock_code, interval.exchange)].append(interval)

    for description, groups in (("economic identity", by_identity), ("stock code", by_code)):
        for key, group in groups.items():
            ordered_group = sorted(
                group,
                key=lambda item: (item.valid_from, item.valid_to or date.max, item.stock_code),
            )
            for prior, current in zip(ordered_group, ordered_group[1:], strict=False):
                if _intervals_overlap(prior, current):
                    raise CodeValidityError(
                        "overlapping code validity intervals for "
                        f"{description} {key[0]} on {key[1]}"
                    )
    return ordered


def load_code_validity_mapping(path: Path | None = None) -> CodeValidityMapping:
    """Load the frozen code-alias overrides; fail closed on unsupported schemas."""

    resolved = (path or find_project_root() / "config" / "security-code-validity.v1.json").resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CodeValidityError(f"code validity mapping does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise CodeValidityError(f"invalid JSON in code validity mapping: {exc}") from exc
    if not isinstance(payload, dict):
        raise CodeValidityError("code validity mapping root must be an object")
    if payload.get("schema_version") != "1.0.0":
        raise CodeValidityError("code validity mapping schema_version must be 1.0.0")
    if payload.get("status") != "frozen":
        raise CodeValidityError("code validity mapping status must be frozen")
    records = payload.get("intervals")
    if not isinstance(records, list) or not records:
        raise CodeValidityError("code validity mapping intervals must be a non-empty array")

    parsed: list[CodeValidityInterval] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CodeValidityError(f"intervals[{index}] must be an object")
        valid_to_value = record.get("valid_to")
        valid_to = None if valid_to_value is None else _require_date(valid_to_value, "valid_to")
        parsed.append(
            CodeValidityInterval(
                security_id=_require_string(record.get("security_id"), "security_id"),
                stock_code=_require_string(record.get("stock_code"), "stock_code"),
                exchange=_require_string(record.get("exchange"), "exchange"),
                valid_from=_require_date(record.get("valid_from"), "valid_from"),
                valid_to=valid_to,
                source=_require_string(record.get("source"), "source"),
                evidence_locator=_require_string(
                    record.get("evidence_locator"), "evidence_locator"
                ),
                evidence_sha256=_require_string(record.get("evidence_sha256"), "evidence_sha256"),
                source_revision=_require_string(record.get("source_revision"), "source_revision"),
                knowledge_cutoff=_require_aware_datetime(
                    record.get("knowledge_cutoff"), "knowledge_cutoff"
                ),
            )
        )
    return CodeValidityMapping(
        path=resolved,
        version=_require_string(payload.get("mapping_version"), "mapping_version"),
        source_revision=_require_string(payload.get("source_revision"), "source_revision"),
        intervals=_validate_intervals(parsed),
    )


def materialize_code_validity_rows(
    master_rows: Iterable[Mapping[str, Any]],
    mapping: CodeValidityMapping,
    *,
    master_evidence_sha256: str,
) -> list[dict[str, Any]]:
    """Overlay evidenced aliases on the provider master without mutating the master.

    Non-aliased codes retain their provider identity/lifecycle.  Mapped codes must
    exist in the master and match its exchange; the final table is validated for both
    code reuse and alias overlap before a build can publish it.
    """

    master_by_code: dict[str, Mapping[str, Any]] = {}
    for row in master_rows:
        stock_code = _require_string(row.get("stock_code"), "master.stock_code")
        if stock_code in master_by_code:
            raise CodeValidityError(f"master has duplicate stock_code {stock_code}")
        master_by_code[stock_code] = row

    overrides = {interval.stock_code: interval for interval in mapping.intervals}
    unknown = sorted(set(overrides) - set(master_by_code))
    if unknown:
        raise CodeValidityError(
            "code validity mapping references unknown master codes: " + ", ".join(unknown)
        )

    if not master_evidence_sha256.startswith("sha256:") or len(master_evidence_sha256) != 71:
        raise CodeValidityError("master_evidence_sha256 must be a SHA-256 digest")
    rows: list[dict[str, Any]] = []
    for stock_code, row in sorted(master_by_code.items()):
        exchange = _require_string(row.get("exchange"), "master.exchange")
        override = overrides.get(stock_code)
        if override is not None:
            if override.exchange != exchange:
                raise CodeValidityError(
                    f"code validity exchange disagrees with master for {stock_code}: "
                    f"{override.exchange} != {exchange}"
                )
            interval = override
        else:
            list_date = row.get("list_date")
            if not isinstance(list_date, date):
                raise CodeValidityError(f"master.list_date must be a date for {stock_code}")
            delist_date = row.get("delist_date")
            if delist_date is not None and not isinstance(delist_date, date):
                raise CodeValidityError(
                    f"master.delist_date must be a date or null for {stock_code}"
                )
            built_at = row.get("built_at")
            cutoff = (
                built_at
                if isinstance(built_at, datetime)
                else datetime.combine(list_date, datetime.min.time(), tzinfo=timezone.utc)
            )
            interval = CodeValidityInterval(
                security_id=_require_string(row.get("security_id"), "master.security_id"),
                stock_code=stock_code,
                exchange=exchange,
                valid_from=list_date,
                valid_to=delist_date,
                source="ref_security_master",
                evidence_locator=f"ref_security_master.parquet#stock_code={stock_code}",
                evidence_sha256=master_evidence_sha256,
                source_revision="ref_security_master",
                knowledge_cutoff=cutoff,
            )
        rows.append(interval.to_dict())
    _validate_intervals(
        CodeValidityInterval(
            security_id=str(row["security_id"]),
            stock_code=str(row["stock_code"]),
            exchange=str(row["exchange"]),
            valid_from=date.fromisoformat(str(row["valid_from"])),
            valid_to=date.fromisoformat(str(row["valid_to"])) if row["valid_to"] else None,
            source=str(row["source"]),
            evidence_locator=str(row["evidence_locator"]),
            evidence_sha256=str(row["evidence_sha256"]),
            source_revision=str(row["source_revision"]),
            knowledge_cutoff=datetime.fromisoformat(str(row["knowledge_cutoff"])),
        )
        for row in rows
    )
    for row in rows:
        row["mapping_version"] = mapping.version
        row["mapping_digest"] = mapping.digest
    return rows


def build_code_validity_table(
    *, root: Path, mapping: CodeValidityMapping, output: Path
) -> tuple[int, str]:
    """Write a new Parquet mapping from an immutable security-master input."""

    root = root.resolve()
    output = output.resolve()
    master_path = root / "ref_security_master.parquet"
    if not master_path.is_file():
        raise CodeValidityError(f"security master does not exist: {master_path}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite code-validity output: {output}")
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise CodeValidityError("P1 dependencies are missing; run make bootstrap-p1") from exc

    master_rows = pq.read_table(master_path).to_pylist()
    rows = materialize_code_validity_rows(
        master_rows,
        mapping,
        master_evidence_sha256=_sha256(master_path),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), output, compression="zstd")
    return len(rows), _sha256(output)
