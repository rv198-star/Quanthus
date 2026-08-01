"""Seal the bounded BaoStock 1990-2010 trading-calendar history Hotfix."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.ods_base import (
    OdsBasePolicy,
    _canonical_digest,
    _parquet_metadata,
    _sha256,
)
from quanthus.pipelines.ods_base_rollup import (
    CALENDAR_HISTORY_APPLICATION,
    OdsBaseRollupError,
    verify_ods_base_rollup,
)


class OdsCalendarHistoryHotfixError(OdsBaseRollupError):
    """Raised when calendar history cannot be sealed without ambiguity."""


@dataclass(frozen=True)
class OdsCalendarHistoryHotfix:
    root: Path
    hotfix_id: str
    manifest_sha256: str
    content_digest: str
    calendar_date_count: int
    open_date_count: int
    row_count: int


_DATASET_ID = "trading_calendar_history_hotfix"
_RELATIVE = "payload/hotfix/calendar/ref_trading_calendar_1990_2010.parquet"
_VENUES = ("SSE", "SZSE")


def _normalize_built_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OdsCalendarHistoryHotfixError("built_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OdsCalendarHistoryHotfixError("built_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _calendar_dates(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_minimum_date: date,
    expected_maximum_date: date,
    expected_date_count: int,
    expected_open_date_count: int,
) -> list[tuple[date, bool]]:
    result: list[tuple[date, bool]] = []
    seen: set[date] = set()
    for raw in rows:
        try:
            trade_date = date.fromisoformat(str(raw["calendar_date"]))
        except (KeyError, ValueError) as exc:
            raise OdsCalendarHistoryHotfixError("BaoStock calendar date is invalid") from exc
        is_open_raw = str(raw.get("is_trading_day"))
        if is_open_raw not in {"0", "1"} or trade_date in seen:
            raise OdsCalendarHistoryHotfixError("BaoStock calendar identity is invalid")
        seen.add(trade_date)
        result.append((trade_date, is_open_raw == "1"))
    result.sort(key=lambda item: item[0])
    if (
        len(result) != expected_date_count
        or sum(item[1] for item in result) != expected_open_date_count
        or not result
        or result[0][0] != expected_minimum_date
        or result[-1][0] != expected_maximum_date
    ):
        raise OdsCalendarHistoryHotfixError("BaoStock calendar coverage differs from the gate")
    if any(
        (right[0] - left[0]).days != 1
        for left, right in zip(result, result[1:], strict=False)
    ):
        raise OdsCalendarHistoryHotfixError("BaoStock calendar does not form a daily spine")
    return result


def _parent_identity(
    parent_base: Path, policy: OdsBasePolicy
) -> tuple[dict[str, Any], dict[str, Any]]:
    verified = verify_ods_base_rollup(parent_base, policy=policy, deep=True)
    try:
        manifest = json.loads((parent_base / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsCalendarHistoryHotfixError("cannot read parent manifest") from exc
    content = manifest.get("content")
    if not isinstance(content, dict):
        raise OdsCalendarHistoryHotfixError("parent content is invalid")
    return (
        {
            "bundle_id": verified.bundle_id,
            "manifest_sha256": verified.manifest_sha256,
            "content_digest": verified.content_digest,
            "through_date": verified.through_date.isoformat(),
        },
        content,
    )


def _parent_calendar_keys(
    parent_base: Path, content: Mapping[str, Any]
) -> set[tuple[str, date, str]]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise OdsCalendarHistoryHotfixError("pyarrow is required") from exc
    files = content.get("files")
    if not isinstance(files, list):
        raise OdsCalendarHistoryHotfixError("parent file inventory is invalid")
    result: set[tuple[str, date, str]] = set()
    for item in files:
        if (
            not isinstance(item, Mapping)
            or "trading_calendar" not in str(item.get("dataset_id"))
            or item.get("format") != "parquet"
        ):
            continue
        path = parent_base / str(item.get("path"))
        table = pq.read_table(path)
        if not {"exchange", "trade_date", "session"}.issubset(table.column_names):
            continue
        for row in table.select(["exchange", "trade_date", "session"]).to_pylist():
            raw_date = row["trade_date"]
            trade_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(raw_date)
            result.add((str(row["exchange"]), trade_date, str(row["session"])))
    if not result:
        raise OdsCalendarHistoryHotfixError("parent has no governed trading calendar")
    return result


def build_ods_calendar_history_hotfix(
    *,
    parent_base: Path,
    output: Path,
    hotfix_id: str,
    policy: OdsBasePolicy,
    code_revision: str,
    built_at: str,
    provider_rows: Sequence[Mapping[str, Any]],
    provider_receipt: Mapping[str, Any],
    expected_minimum_date: date = date(1990, 12, 19),
    expected_maximum_date: date = date(2010, 12, 31),
    expected_date_count: int = 7_318,
    expected_open_date_count: int = 4_911,
) -> OdsCalendarHistoryHotfix:
    """Build one immutable additive calendar-history Hotfix without changing pointers."""

    parent_base = parent_base.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite calendar Hotfix: {output}")
    if output.name != hotfix_id:
        raise OdsCalendarHistoryHotfixError("Hotfix output and identity differ")
    if len(code_revision) != 40 or any(char not in "0123456789abcdef" for char in code_revision):
        raise OdsCalendarHistoryHotfixError("code_revision must be a full Git object id")
    acquired_at = _normalize_built_at(built_at)
    dates = _calendar_dates(
        provider_rows,
        expected_minimum_date=expected_minimum_date,
        expected_maximum_date=expected_maximum_date,
        expected_date_count=expected_date_count,
        expected_open_date_count=expected_open_date_count,
    )
    parent_identity, parent_content = _parent_identity(parent_base, policy)
    parent_keys = _parent_calendar_keys(parent_base, parent_content)
    rows = [
        {
            "exchange": venue,
            "trade_date": trade_date,
            "is_open": int(is_open),
            "session": "day",
            "source": "baostock.query_trade_dates",
            "built_at": acquired_at,
        }
        for trade_date, is_open in dates
        for venue in _VENUES
    ]
    keys = {(row["exchange"], row["trade_date"], row["session"]) for row in rows}
    overlap = keys & parent_keys
    if len(keys) != len(rows) or overlap:
        raise OdsCalendarHistoryHotfixError("calendar Hotfix overlaps the parent business keys")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{hotfix_id}.", dir=output.parent))
    staging = staging_parent / hotfix_id
    staging.mkdir()
    try:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise OdsCalendarHistoryHotfixError("pyarrow is required") from exc
        schema = pa.schema(
            [
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("trade_date", pa.date32(), nullable=False),
                pa.field("is_open", pa.int8(), nullable=False),
                pa.field("session", pa.string(), nullable=False),
                pa.field("source", pa.string(), nullable=False),
                pa.field("built_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        )
        target = staging / _RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(rows, schema=schema),
            target,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
        )
        file_entry: dict[str, Any] = {
            "dataset_id": _DATASET_ID,
            "role": "canonical_reference",
            "format": "parquet",
            "path": _RELATIVE,
            "sha256": _sha256(target),
            "size_bytes": target.stat().st_size,
            **_parquet_metadata(target, ("trade_date",)),
        }
        scope_digest = _canonical_digest(
            {
                "dates": [[item[0].isoformat(), item[1]] for item in dates],
                "venues": list(_VENUES),
            }
        )
        scope = {
            "kind": "calendar_history",
            "reason": "complete_nonfabricated_calendar_fk_coverage_for_pre_2011_daily_bars",
            "venues": list(_VENUES),
            "calendar_date_count": len(dates),
            "minimum_date": dates[0][0].isoformat(),
            "maximum_date": dates[-1][0].isoformat(),
            "scope_digest": scope_digest,
            "through_date": parent_identity["through_date"],
        }
        coverage = {
            "calendar": {
                "calendar_date_count": len(dates),
                "open_date_count": sum(item[1] for item in dates),
                "expanded_row_count": len(rows),
                "overlap_business_keys": 0,
                "conflicting_business_keys": 0,
                "venues": list(_VENUES),
            }
        }
        content_identity = {
            "parent": parent_identity,
            "scope_digest": scope_digest,
            "through_date": scope["through_date"],
            "files": [file_entry],
        }
        manifest = {
            "schema_version": 1,
            "kind": "ods_base_hotfix",
            "hotfix_id": hotfix_id,
            "built_at": acquired_at.isoformat(),
            "code_revision": code_revision,
            "parent": parent_identity,
            "scope": scope,
            "application": CALENDAR_HISTORY_APPLICATION,
            "acquisition": dict(provider_receipt),
            "content": {"files": [file_entry], "coverage": coverage},
            "content_digest": _canonical_digest(content_identity),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")
        os.replace(staging, output)
        staging_parent.rmdir()
        return OdsCalendarHistoryHotfix(
            root=output,
            hotfix_id=hotfix_id,
            manifest_sha256=_sha256(output / "manifest.json"),
            content_digest=str(manifest["content_digest"]),
            calendar_date_count=len(dates),
            open_date_count=sum(item[1] for item in dates),
            row_count=len(rows),
        )
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
