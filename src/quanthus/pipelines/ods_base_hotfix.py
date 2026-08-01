"""Build and verify an immutable child Hotfix for an ODS Base.

The Hotfix never rewrites its parent.  Parent repair evidence remains authoritative
on overlapping daily keys; newly acquired rows fill only keys absent from the parent
evidence.  Every other artifact is scoped to the audited security set.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, cast

from quanthus.providers.daily_history import daily_schema, status_schema


class OdsBaseHotfixError(RuntimeError):
    """Raised when a Hotfix workspace, package, or parent binding is invalid."""


EXPECTED_PARENT_BUNDLE = "ods-base-202607-v1"
EXPECTED_PARENT_MANIFEST_SHA256 = (
    "sha256:553665772eb9746dcbca3318ae15f1c9b421f351c6e734f8e475ee8309a71d4c"
)
EXPECTED_PARENT_CONTENT_DIGEST = (
    "sha256:91fb3be70e7b80cb22eaa800c00bb267a9a89202b83a14614c31b9d7a2f5d4d1"
)
EXPECTED_SCOPE_COUNT = 209
THROUGH_DATE = date(2026, 7, 31)
RECENT_SESSIONS = tuple(date(2026, 7, value) for value in (27, 28, 29, 30, 31))
NEW_LISTING_CODE = "688806"
ALLOWED_RECEIPT_STATUSES = frozenset({"success_nonempty", "justified_empty"})


@dataclass(frozen=True)
class OdsBaseHotfixBundle:
    root: Path
    hotfix_id: str
    parent_bundle_id: str
    scope_count: int
    file_count: int
    size_bytes: int
    manifest_sha256: str
    content_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "hotfix_id": self.hotfix_id,
            "parent_bundle_id": self.parent_bundle_id,
            "scope_count": self.scope_count,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsBaseHotfixError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise OdsBaseHotfixError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_once_or_verify(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if _read_object(path) != dict(value):
            raise OdsBaseHotfixError(f"existing derived JSON differs: {path}")
        return
    _write_json(path, value)


def _safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise OdsBaseHotfixError(f"unsafe relative path: {value!r}")
    return Path(*path.parts)


def _parent_identity(parent_base: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_base = parent_base.resolve()
    manifest_path = parent_base / "manifest.json"
    manifest_sha = _sha256(manifest_path)
    manifest = _read_object(manifest_path)
    if manifest_sha != EXPECTED_PARENT_MANIFEST_SHA256:
        raise OdsBaseHotfixError(f"unexpected parent manifest: {manifest_sha}")
    if manifest.get("bundle_id") != EXPECTED_PARENT_BUNDLE:
        raise OdsBaseHotfixError(f"unexpected parent bundle: {manifest.get('bundle_id')}")
    if manifest.get("content_digest") != EXPECTED_PARENT_CONTENT_DIGEST:
        raise OdsBaseHotfixError(
            f"unexpected parent content digest: {manifest.get('content_digest')}"
        )
    content = manifest.get("content")
    if not isinstance(content, dict) or content.get("through_date") != THROUGH_DATE.isoformat():
        raise OdsBaseHotfixError("parent Base does not have the expected through date")
    return manifest, {
        "bundle_id": EXPECTED_PARENT_BUNDLE,
        "manifest_sha256": manifest_sha,
        "content_digest": EXPECTED_PARENT_CONTENT_DIGEST,
        "through_date": THROUGH_DATE.isoformat(),
    }


def _audit_scope(
    audit_path: Path, parent_identity: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    audit = _read_object(audit_path)
    if (
        audit.get("base_bundle_id") != parent_identity["bundle_id"]
        or audit.get("base_manifest_sha256") != parent_identity["manifest_sha256"]
        or audit.get("base_content_digest") != parent_identity["content_digest"]
    ):
        raise OdsBaseHotfixError("security-gap audit is not bound to the parent Base")
    rows = audit.get("securities")
    if not isinstance(rows, list) or len(rows) != EXPECTED_SCOPE_COUNT:
        raise OdsBaseHotfixError("security-gap audit does not contain exactly 209 securities")
    codes = [str(row.get("stock_code") or "") for row in rows if isinstance(row, dict)]
    if len(codes) != EXPECTED_SCOPE_COUNT or len(set(codes)) != EXPECTED_SCOPE_COUNT:
        raise OdsBaseHotfixError("security-gap audit has missing or duplicate security codes")
    if any(
        len(code) != 6 or not code.isdigit() or not code.startswith(("0", "3", "6"))
        for code in codes
    ):
        raise OdsBaseHotfixError("security-gap audit contains an unsupported equity code")
    exchanges = Counter(str(row.get("exchange")) for row in rows)
    if exchanges != Counter({"SSE": 83, "SZSE": 126}):
        raise OdsBaseHotfixError(f"unexpected audited exchange split: {dict(exchanges)}")
    repair_count = sum(bool(row.get("repair_276_present")) for row in rows)
    absent = [row for row in rows if row.get("classification") == "truly_absent"]
    if repair_count != 208 or [str(row.get("stock_code")) for row in absent] != [NEW_LISTING_CODE]:
        raise OdsBaseHotfixError("audited repair/absent split is not 208 plus 688806")
    return audit, sorted(rows, key=lambda row: str(row["stock_code"]))


def prepare_hotfix_workspace(
    *,
    parent_base: Path,
    audit_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    """Create the exact 209-security provider universe and immutable attempt identity."""

    parent_base = parent_base.resolve()
    workspace = workspace.resolve()
    _manifest, parent_identity = _parent_identity(parent_base)
    audit, scope = _audit_scope(audit_path.resolve(), parent_identity)
    identity = {
        "schema_version": 1,
        "kind": "ods_base_hotfix_workspace",
        "parent": parent_identity,
        "scope_count": len(scope),
        "scope_digest": _content_digest(scope),
        "audit_sha256": _sha256(audit_path.resolve()),
        "through_date": THROUGH_DATE.isoformat(),
    }
    identity_path = workspace / "WORKSPACE.json"
    if workspace.exists():
        if not identity_path.is_file() or _read_object(identity_path) != identity:
            raise OdsBaseHotfixError(
                f"existing Hotfix workspace has a different identity: {workspace}"
            )
        return identity

    staging = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.", dir=workspace.parent))
    try:
        evidence = staging / "evidence"
        evidence.mkdir()
        shutil.copyfile(audit_path.resolve(), evidence / "security-gap-audit.json")
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        scope_rows = [
            {
                "security_id": str(row["security_id"]),
                "stock_code": str(row["stock_code"]),
                "stock_name": str(row["name"]),
                "exchange": str(row["exchange"]),
                "list_date": row.get("list_date"),
                "classification": str(row["classification"]),
                "parent_repair_present": bool(row["repair_276_present"]),
            }
            for row in scope
        ]
        scope_dir = staging / "scope"
        scope_dir.mkdir()
        pq.write_table(pa.Table.from_pylist(scope_rows), scope_dir / "security_scope.parquet")
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": row["stock_code"],
                        "stock_name": row["stock_name"],
                        "market": "沪A" if row["exchange"] == "SSE" else "深A",
                    }
                    for row in scope_rows
                ]
            ),
            scope_dir / "stock_list.parquet",
        )
        for name in ("provider", "assembled"):
            (staging / name).mkdir()
        _write_json(staging / "WORKSPACE.json", identity)
        os.replace(staging, workspace)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return identity


def _parent_file_map(parent_manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    content = parent_manifest.get("content")
    files = content.get("files") if isinstance(content, Mapping) else None
    if not isinstance(files, list):
        raise OdsBaseHotfixError("parent Base has no file inventory")
    result: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise OdsBaseHotfixError("parent file inventory contains a non-object")
        path = str(item.get("path") or "")
        if path in result:
            raise OdsBaseHotfixError(f"duplicate parent file path: {path}")
        result[path] = item
    return result


def _repair_number(
    raw: Sequence[Any],
    index: int,
    *,
    tradestatus: str,
    code: str,
    observed: str,
    zero_when_suspended: bool = False,
) -> float | None:
    value = str(raw[index]).strip()
    if not value:
        return 0.0 if zero_when_suspended and tradestatus == "0" else None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise OdsBaseHotfixError(
            f"parent repair numeric value is invalid for {code}/{observed}"
        ) from exc
    if not math.isfinite(parsed):
        raise OdsBaseHotfixError(f"parent repair numeric value is not finite for {code}/{observed}")
    return parsed


def _normalize_parent_repair(
    *, parent_base: Path, parent_files: Mapping[str, Mapping[str, Any]], code: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    relative = f"payload/source-evidence/none_missing_276_raw_v1_20260725/raw_shards/{code}.json"
    item = parent_files.get(relative)
    if item is None:
        raise OdsBaseHotfixError(f"parent repair shard is missing for {code}")
    path = parent_base / _safe_relative(relative)
    if _sha256(path) != item.get("sha256"):
        raise OdsBaseHotfixError(f"parent repair shard checksum mismatch for {code}")
    payload = _read_object(path)
    request = (payload.get("meta") or {}).get("request") or {}
    rows = payload.get("rows")
    if (
        payload.get("security_id") != code
        or payload.get("provider") != "baostock"
        or payload.get("frequency") != "d"
        or payload.get("adjustflag") != "3"
        or not isinstance(rows, list)
        or int(payload.get("n_rows", -1)) != len(rows)
        or (payload.get("meta") or {}).get("error_code") != "0"
    ):
        raise OdsBaseHotfixError(f"parent repair shard contract is invalid for {code}")
    acquired_at = str(request.get("fetched_at_utc") or "")
    bars: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, list) or len(raw) != 13:
            raise OdsBaseHotfixError(f"parent repair row schema is invalid for {code}")
        observed = str(raw[0])
        if str(raw[1]).split(".")[-1] != code or str(raw[8]) != "3":
            raise OdsBaseHotfixError(f"parent repair row identity is invalid for {code}")
        tradestatus = str(raw[11])

        open_value = _repair_number(raw, 2, tradestatus=tradestatus, code=code, observed=observed)
        high_value = _repair_number(raw, 3, tradestatus=tradestatus, code=code, observed=observed)
        low_value = _repair_number(raw, 4, tradestatus=tradestatus, code=code, observed=observed)
        close_value = _repair_number(raw, 5, tradestatus=tradestatus, code=code, observed=observed)
        volume = _repair_number(
            raw,
            6,
            tradestatus=tradestatus,
            code=code,
            observed=observed,
            zero_when_suspended=True,
        )
        amount = _repair_number(
            raw,
            7,
            tradestatus=tradestatus,
            code=code,
            observed=observed,
            zero_when_suspended=True,
        )
        if any(
            value is None or value < 0
            for value in (open_value, high_value, low_value, close_value, volume, amount)
        ):
            raise OdsBaseHotfixError(f"parent repair OHLCV is invalid for {code}/{observed}")
        assert open_value is not None
        assert high_value is not None
        assert low_value is not None
        assert close_value is not None
        if (
            low_value > high_value
            or not low_value <= open_value <= high_value
            or not low_value <= close_value <= high_value
        ):
            raise OdsBaseHotfixError(f"parent repair OHLC order is invalid for {code}/{observed}")
        bars.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": observed,
                "date": observed,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume,
                "amount": amount,
                "turnover_rate": _repair_number(
                    raw,
                    9,
                    tradestatus=tradestatus,
                    code=code,
                    observed=observed,
                ),
                "pct_change": _repair_number(
                    raw,
                    10,
                    tradestatus=tradestatus,
                    code=code,
                    observed=observed,
                ),
                "adj_mode": "none",
                "adj_version": "none_missing_276_raw_v1_20260725.adjustflag=3",
                "source": "baostock.query_history_k_data_plus.adjustflag=3",
                "acquired_at": acquired_at,
                "built_at": acquired_at,
            }
        )
        statuses.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": observed,
                "date": observed,
                "tradestatus": tradestatus,
                "isST": str(raw[12]),
                "source": "baostock.query_history_k_data_plus",
                "acquired_at": acquired_at,
                "built_at": acquired_at,
            }
        )
    return (
        bars,
        statuses,
        {
            "stock_code": code,
            "parent_path": relative,
            "parent_sha256": str(item["sha256"]),
            "parent_rows": len(bars),
            "request_start": request.get("start_date"),
            "request_end": request.get("end_date"),
        },
    )


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OdsBaseHotfixError("pyarrow is required for ODS Hotfix operations") from exc
    if not path.is_file():
        raise OdsBaseHotfixError(f"required Parquet is missing: {path}")
    return cast(list[dict[str, Any]], pq.read_table(path).to_pylist())


def _receipt_summary(
    path: Path,
    *,
    expected_codes: set[str],
    expected_dataset: str,
    requests_per_code: int = 1,
) -> dict[str, Any]:
    rows = _read_parquet_rows(path)
    expected_count = len(expected_codes) * requests_per_code
    if len(rows) != expected_count:
        raise OdsBaseHotfixError(
            f"{expected_dataset} receipt count is {len(rows)}, expected {expected_count}"
        )
    actual_codes = Counter(str(row.get("security_id")) for row in rows)
    if actual_codes != Counter({code: requests_per_code for code in expected_codes}):
        raise OdsBaseHotfixError(f"{expected_dataset} receipt scope is invalid")
    if any(str(row.get("dataset")) != expected_dataset for row in rows):
        raise OdsBaseHotfixError(f"{expected_dataset} receipt identity is invalid")
    invalid = [row for row in rows if str(row.get("status")) not in ALLOWED_RECEIPT_STATUSES]
    if invalid:
        raise OdsBaseHotfixError(
            f"{expected_dataset} has {len(invalid)} failed or invalid receipts"
        )
    return {
        "receipt_count": len(rows),
        "success_nonempty": sum(row.get("status") == "success_nonempty" for row in rows),
        "justified_empty": sum(row.get("status") == "justified_empty" for row in rows),
    }


def _parquet_codes(
    path: Path, candidates: Sequence[str] = ("security_id", "stock_code")
) -> set[str]:
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    column = next((name for name in candidates if name in parquet.schema_arrow.names), None)
    if column is None:
        raise OdsBaseHotfixError(f"Parquet has no security code column: {path}")
    values: set[str] = set()
    for batch in parquet.iter_batches(columns=[column], batch_size=131072):
        values.update(
            str(value).zfill(6) for value in batch.column(0).to_pylist() if value is not None
        )
    return values


def assemble_daily_hotfix(
    *,
    parent_base: Path,
    workspace: Path,
) -> dict[str, Any]:
    """Merge parent repair rows with provider rows using fill-absent precedence."""

    parent_base = parent_base.resolve()
    workspace = workspace.resolve()
    parent_manifest, parent_identity = _parent_identity(parent_base)
    audit, scope = _audit_scope(workspace / "evidence" / "security-gap-audit.json", parent_identity)
    del audit
    codes = {str(row["stock_code"]) for row in scope}
    provider_root = workspace / "provider" / "daily"
    provider_bars = _read_parquet_rows(provider_root / "daily_history.parquet")
    provider_statuses = _read_parquet_rows(provider_root / "daily_status_history.parquet")
    receipt_summary = _receipt_summary(
        provider_root / "daily_history.receipts.parquet",
        expected_codes=codes,
        expected_dataset="daily_ohlcv_none_history",
    )
    if {str(row.get("security_id")) for row in provider_bars} != codes:
        raise OdsBaseHotfixError("daily provider output does not cover the exact 209 scope")
    provider_status_by_key = {
        (str(row["security_id"]), str(row["date"])): row for row in provider_statuses
    }
    if len(provider_status_by_key) != len(provider_statuses):
        raise OdsBaseHotfixError("daily provider status contains duplicate keys")
    provider_by_key = {(str(row["security_id"]), str(row["date"])): row for row in provider_bars}
    if len(provider_by_key) != len(provider_bars) or set(provider_by_key) != set(
        provider_status_by_key
    ):
        raise OdsBaseHotfixError("daily provider bar/status keys do not reconcile")

    parent_files = _parent_file_map(parent_manifest)
    parent_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    parent_status_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    lineage: list[dict[str, Any]] = []
    for row in scope:
        code = str(row["stock_code"])
        if not row.get("repair_276_present"):
            continue
        bars, statuses, item = _normalize_parent_repair(
            parent_base=parent_base,
            parent_files=parent_files,
            code=code,
        )
        lineage.append(item)
        for bar, status in zip(bars, statuses, strict=True):
            key = (code, str(bar["date"]))
            if key in parent_by_key:
                raise OdsBaseHotfixError(f"duplicate parent repair key: {key}")
            parent_by_key[key] = bar
            parent_status_by_key[key] = status

    comparison_columns = ("open", "high", "low", "close", "volume", "amount")
    reconciliation: list[dict[str, Any]] = []
    mismatches = 0
    for key in sorted(set(parent_by_key) | set(provider_by_key)):
        parent = parent_by_key.get(key)
        provider = provider_by_key.get(key)
        if parent is not None and provider is not None:
            matches = all(
                math.isclose(
                    float(parent[column]),
                    float(provider[column]),
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
                for column in comparison_columns
            )
            classification = "same" if matches else "changed_provider_observation"
            mismatches += not matches
        elif parent is not None:
            matches = True
            classification = "parent_only"
        else:
            matches = True
            classification = "provider_fill"
        reconciliation.append(
            {
                "security_id": key[0],
                "stock_code": key[0],
                "date": key[1],
                "parent_present": parent is not None,
                "provider_present": provider is not None,
                "value_match": matches,
                "classification": classification,
                "selected_source": "parent_repair" if parent is not None else "provider_hotfix",
            }
        )
    if mismatches:
        raise OdsBaseHotfixError(
            f"daily provider cross-check differs from parent repair on {mismatches} keys"
        )

    merged_bars = dict(parent_by_key)
    merged_statuses = dict(parent_status_by_key)
    for key, row in provider_by_key.items():
        if key not in merged_bars:
            merged_bars[key] = row
            merged_statuses[key] = provider_status_by_key[key]
    recent_missing = [
        (code, session.isoformat())
        for code in sorted(codes)
        for session in RECENT_SESSIONS
        if (code, session.isoformat()) not in merged_bars
    ]
    if recent_missing:
        raise OdsBaseHotfixError(
            f"daily Hotfix is missing {len(recent_missing)} recent security/session keys"
        )
    new_listing_dates = {
        str(row["date"]) for key, row in merged_bars.items() if key[0] == NEW_LISTING_CODE
    }
    if min(new_listing_dates, default="9999-99-99") != "2026-07-21":
        raise OdsBaseHotfixError("688806 daily history does not begin on its listing date")
    if set(merged_bars) != set(merged_statuses):
        raise OdsBaseHotfixError("assembled daily bar/status keys do not reconcile")

    import pyarrow as pa
    import pyarrow.parquet as pq

    assembled = workspace / "assembled"
    assembled.mkdir(exist_ok=True)
    outputs = (
        assembled / "daily_ohlcv_none_hotfix.parquet",
        assembled / "daily_status_observation_hotfix.parquet",
        assembled / "daily_source_reconciliation.parquet",
        assembled / "parent_repair_lineage.json",
        assembled / "daily_assembly_summary.json",
    )
    if any(path.exists() for path in outputs):
        if not all(path.is_file() for path in outputs):
            raise OdsBaseHotfixError("assembled daily Hotfix output set is incomplete")
        saved = _read_object(outputs[4])
        identities = saved.get("output_sha256")
        metrics = saved.get("metrics")
        if not isinstance(identities, dict) or not isinstance(metrics, dict):
            raise OdsBaseHotfixError("daily assembly summary is invalid")
        for path in outputs[:4]:
            if identities.get(path.name) != _sha256(path):
                raise OdsBaseHotfixError(f"assembled daily Hotfix checksum mismatch: {path}")
        return metrics
    pq.write_table(
        pa.Table.from_pylist(
            [merged_bars[key] for key in sorted(merged_bars)], schema=daily_schema()
        ),
        outputs[0],
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [merged_statuses[key] for key in sorted(merged_statuses)], schema=status_schema()
        ),
        outputs[1],
        compression="zstd",
    )
    pq.write_table(pa.Table.from_pylist(reconciliation), outputs[2], compression="zstd")
    _write_json(
        outputs[3],
        {
            "schema_version": 1,
            "parent": parent_identity,
            "precedence": "parent_repair_wins_on_overlap_provider_fills_absent_keys",
            "repair_shards": sorted(lineage, key=lambda item: item["stock_code"]),
        },
    )
    metrics = {
        "scope_count": len(codes),
        "parent_repair_security_count": len(lineage),
        "parent_repair_rows": len(parent_by_key),
        "provider_rows": len(provider_by_key),
        "provider_fill_rows": sum(key not in parent_by_key for key in provider_by_key),
        "output_rows": len(merged_bars),
        "overlap_rows": sum(key in parent_by_key for key in provider_by_key),
        "overlap_mismatches": mismatches,
        "recent_session_keys": len(codes) * len(RECENT_SESSIONS),
        "receipts": receipt_summary,
    }
    _write_json(
        outputs[4],
        {
            "schema_version": 1,
            "metrics": metrics,
            "output_sha256": {path.name: _sha256(path) for path in outputs[:4]},
        },
    )
    return metrics


def _minute_summary(root: Path, *, codes: set[str], frequency: str) -> dict[str, Any]:
    import pyarrow.compute as pc  # type: ignore[import-untyped]
    import pyarrow.parquet as pq

    expected = {f"{code}_{frequency}.parquet" for code in codes}
    actual = {path.name for path in root.glob(f"*_{frequency}.parquet") if path.is_file()}
    if actual != expected:
        raise OdsBaseHotfixError(
            f"{frequency} minute file scope differs: missing={len(expected - actual)} "
            f"extra={len(actual - expected)}"
        )
    total_rows = 0
    earliest: str | None = None
    latest: str | None = None
    for code in sorted(codes):
        path = root / f"{code}_{frequency}.parquet"
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows <= 0:
            raise OdsBaseHotfixError(f"minute file is empty: {path}")
        if not {"stock_code", "ts", "open", "high", "low", "close", "volume", "amount"}.issubset(
            parquet.schema_arrow.names
        ):
            raise OdsBaseHotfixError(f"minute schema is invalid: {path}")
        table = pq.read_table(path, columns=["stock_code", "ts"])
        observed_codes = {str(value).zfill(6) for value in table.column(0).to_pylist()}
        if observed_codes != {code}:
            raise OdsBaseHotfixError(f"minute file identity is invalid: {path}")
        minimum = str(pc.min(table.column(1)).as_py())
        maximum = str(pc.max(table.column(1)).as_py())
        if maximum[:10] > THROUGH_DATE.isoformat():
            raise OdsBaseHotfixError(f"minute file exceeds parent through date: {path}")
        total_rows += parquet.metadata.num_rows
        earliest = minimum if earliest is None else min(earliest, minimum)
        latest = maximum if latest is None else max(latest, maximum)
    checkpoint = _read_object(root / "checkpoint.json")
    status = _read_object(root / "status.json")
    symbols = checkpoint.get("symbols")
    if not isinstance(symbols, dict) or set(symbols) != codes:
        raise OdsBaseHotfixError(f"{frequency} checkpoint scope is invalid")
    failed = [
        code
        for code, item in symbols.items()
        if not isinstance(item, dict) or item.get("status") != "done"
    ]
    if failed or status.get("state") != "done" or int(status.get("error", -1)) != 0:
        raise OdsBaseHotfixError(f"{frequency} collector did not finish cleanly")
    return {
        "security_count": len(codes),
        "row_count": total_rows,
        "earliest": earliest,
        "latest": latest,
        "collector_state": status.get("state"),
    }


def _validate_collection(workspace: Path, codes: set[str]) -> dict[str, Any]:
    provider = workspace / "provider"
    valuation_root = provider / "valuation"
    fundamentals = provider / "fundamentals"
    minute = provider / "minute"
    valuation = valuation_root / "valuation_history.parquet"
    if _parquet_codes(valuation) != codes:
        raise OdsBaseHotfixError("valuation history does not cover the exact 209 scope")
    valuation_receipts = _receipt_summary(
        valuation_root / "valuation_history.receipts.parquet",
        expected_codes=codes,
        expected_dataset="valuation_history",
    )
    required_fundamentals = {
        "raw_f10.parquet": ("raw_f10", 1),
        "financial_statements.parquet": ("financial_statements", 3),
        "industry_membership.parquet": ("industry_membership", 1),
    }
    fundamentals_summary: dict[str, Any] = {}
    for filename, (dataset, requests_per_code) in required_fundamentals.items():
        path = fundamentals / filename
        if not path.is_file():
            raise OdsBaseHotfixError(f"fundamentals artifact is missing: {path}")
        output_codes = _parquet_codes(path)
        receipts = path.with_suffix(path.suffix + ".receipts.parquet")
        receipt = _receipt_summary(
            receipts,
            expected_codes=codes,
            expected_dataset=dataset,
            requests_per_code=requests_per_code,
        )
        if not output_codes.issubset(codes):
            raise OdsBaseHotfixError(f"{dataset} output contains an out-of-scope security")
        success_codes = {
            str(row["security_id"])
            for row in _read_parquet_rows(receipts)
            if row.get("status") == "success_nonempty"
        }
        if output_codes != success_codes:
            raise OdsBaseHotfixError(
                f"{dataset} output codes do not reconcile with successful receipts"
            )
        fundamentals_summary[dataset] = {
            "output_security_count": len(output_codes),
            **receipt,
        }
    quarterly = fundamentals / "financial_quarterly.parquet"
    if not quarterly.is_file() or not _parquet_codes(quarterly).issubset(codes):
        raise OdsBaseHotfixError("financial quarterly projection is missing or out of scope")
    return {
        "valuation": {
            "row_count": _parquet_row_count(valuation),
            **valuation_receipts,
        },
        "fundamentals": fundamentals_summary,
        "minute_1m": _minute_summary(minute / "research_1m", codes=codes, frequency="1m"),
        "minute_5m": _minute_summary(minute / "prod_5m", codes=codes, frequency="5m"),
    }


def _parquet_row_count(path: Path) -> int:
    import pyarrow.parquet as pq

    return int(pq.ParquetFile(path).metadata.num_rows)


def _member_specs(workspace: Path, codes: set[str]) -> list[tuple[str, str, Path, str]]:
    specs: list[tuple[str, str, Path, str]] = [
        (
            "security_gap_audit",
            "evidence",
            workspace / "evidence/security-gap-audit.json",
            "payload/evidence/security-gap-audit.json",
        ),
        (
            "security_hotfix_scope",
            "source_identity",
            workspace / "scope/security_scope.parquet",
            "payload/scope/security_scope.parquet",
        ),
        (
            "security_hotfix_stock_list",
            "provider_request_scope",
            workspace / "scope/stock_list.parquet",
            "payload/scope/stock_list.parquet",
        ),
        (
            "daily_ohlcv_none_hotfix",
            "source_market_data",
            workspace / "assembled/daily_ohlcv_none_hotfix.parquet",
            "payload/daily/daily_ohlcv_none_hotfix.parquet",
        ),
        (
            "daily_status_observation_hotfix",
            "source_status_evidence",
            workspace / "assembled/daily_status_observation_hotfix.parquet",
            "payload/daily/daily_status_observation_hotfix.parquet",
        ),
        (
            "daily_source_reconciliation",
            "source_reconciliation",
            workspace / "assembled/daily_source_reconciliation.parquet",
            "payload/evidence/daily_source_reconciliation.parquet",
        ),
        (
            "parent_repair_lineage",
            "source_lineage",
            workspace / "assembled/parent_repair_lineage.json",
            "payload/evidence/parent_repair_lineage.json",
        ),
        (
            "daily_assembly_summary",
            "quality_evidence",
            workspace / "assembled/daily_assembly_summary.json",
            "payload/evidence/daily_assembly_summary.json",
        ),
        (
            "daily_provider_window",
            "provider_source_evidence",
            workspace / "provider/daily/daily_history.parquet",
            "payload/provider/daily/daily_history.parquet",
        ),
        (
            "daily_provider_status",
            "provider_source_evidence",
            workspace / "provider/daily/daily_status_history.parquet",
            "payload/provider/daily/daily_status_history.parquet",
        ),
        (
            "daily_provider_receipts",
            "provider_receipt",
            workspace / "provider/daily/daily_history.receipts.parquet",
            "payload/provider/daily/daily_history.receipts.parquet",
        ),
        (
            "daily_provider_metadata",
            "source_lineage",
            workspace / "provider/daily/daily_history.parquet.meta.json",
            "payload/provider/daily/daily_history.parquet.meta.json",
        ),
        (
            "valuation_history_hotfix",
            "source_market_data",
            workspace / "provider/valuation/valuation_history.parquet",
            "payload/valuation/valuation_history.parquet",
        ),
        (
            "valuation_history_receipts",
            "provider_receipt",
            workspace / "provider/valuation/valuation_history.receipts.parquet",
            "payload/valuation/valuation_history.receipts.parquet",
        ),
        (
            "valuation_history_metadata",
            "source_lineage",
            workspace / "provider/valuation/valuation_history.parquet.meta.json",
            "payload/valuation/valuation_history.parquet.meta.json",
        ),
    ]
    fundamentals = workspace / "provider/fundamentals"
    for name in (
        "raw_f10.parquet",
        "raw_f10.parquet.receipts.parquet",
        "raw_f10.parquet.meta.json",
        "financial_statements.parquet",
        "financial_statements.parquet.receipts.parquet",
        "financial_statements.parquet.meta.json",
        "financial_quarterly.parquet",
        "industry_membership.parquet",
        "industry_membership.parquet.receipts.parquet",
        "industry_membership.parquet.meta.json",
    ):
        specs.append(
            (
                f"fundamentals_{name.replace('.', '_')}",
                "provider_source_evidence" if "receipts" not in name else "provider_receipt",
                fundamentals / name,
                f"payload/fundamentals/{name}",
            )
        )
    for frequency, dirname in (("1m", "research_1m"), ("5m", "prod_5m")):
        root = workspace / "provider/minute" / dirname
        for code in sorted(codes):
            specs.append(
                (
                    f"minute_{frequency}_hotfix",
                    "source_market_data",
                    root / f"{code}_{frequency}.parquet",
                    f"payload/minute/{dirname}/{code}_{frequency}.parquet",
                )
            )
        for name in ("status.json", "checkpoint.json"):
            specs.append(
                (
                    f"minute_{frequency}_{name.removesuffix('.json')}",
                    "source_lineage",
                    root / name,
                    f"payload/evidence/minute/{dirname}/{name}",
                )
            )
    return specs


def _copy_member(source: Path, target: Path) -> tuple[str, int]:
    if not source.is_file() or source.is_symlink():
        raise OdsBaseHotfixError(f"required source file is missing or unsafe: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        target.unlink(missing_ok=True)
        raise OdsBaseHotfixError(f"source changed while copied: {source}")
    target.chmod(0o640)
    return f"sha256:{digest.hexdigest()}", before.st_size


def build_hotfix_bundle(
    *,
    parent_base: Path,
    workspace: Path,
    output: Path,
    hotfix_id: str,
    code_revision: str,
) -> OdsBaseHotfixBundle:
    parent_base = parent_base.resolve()
    workspace = workspace.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite ODS Base Hotfix: {output}")
    if output.name != hotfix_id or not hotfix_id.startswith(f"{EXPECTED_PARENT_BUNDLE}-hotfix-"):
        raise OdsBaseHotfixError("Hotfix id/output name is not bound to the parent bundle")
    parent_manifest, parent_identity = _parent_identity(parent_base)
    del parent_manifest
    _audit, scope = _audit_scope(workspace / "evidence/security-gap-audit.json", parent_identity)
    codes = {str(row["stock_code"]) for row in scope}
    collection_summary = _validate_collection(workspace, codes)
    daily_summary = assemble_daily_hotfix(
        parent_base=parent_base,
        workspace=workspace,
    )
    coverage = {
        "schema_version": 1,
        "scope_count": len(codes),
        "exchange_counts": dict(Counter(str(row["exchange"]) for row in scope)),
        "daily": daily_summary,
        **collection_summary,
        "bse_policy": "BSE_PARTIAL_NONBLOCKING",
    }
    coverage_path = workspace / "assembled/coverage_report.json"
    precedence_path = workspace / "assembled/source_precedence.json"
    _write_json_once_or_verify(coverage_path, coverage)
    _write_json_once_or_verify(
        precedence_path,
        {
            "schema_version": 1,
            "daily_key": ["stock_code", "date"],
            "rules": [
                "parent canonical rows win on overlapping keys",
                "parent repair evidence wins on overlapping repair/provider keys",
                "Hotfix rows fill only keys absent from parent canonical and repair evidence",
            ],
            "consumer_current_switch": False,
            "parent_mutation": False,
        },
    )
    specs = _member_specs(workspace, codes)
    specs.extend(
        [
            (
                "coverage_report",
                "quality_evidence",
                coverage_path,
                "payload/evidence/coverage_report.json",
            ),
            (
                "source_precedence",
                "source_contract",
                precedence_path,
                "payload/evidence/source_precedence.json",
            ),
        ]
    )
    targets = [target for _dataset, _role, _source, target in specs]
    if len(targets) != len(set(targets)):
        raise OdsBaseHotfixError("Hotfix member target paths are ambiguous")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{hotfix_id}.", dir=output.parent))
    try:
        files: list[dict[str, Any]] = []
        for dataset_id, role, source, relative in specs:
            target = staging / _safe_relative(relative)
            digest, size = _copy_member(source, target)
            suffix = source.suffix.lower()
            record: dict[str, Any] = {
                "dataset_id": dataset_id,
                "role": role,
                "path": relative,
                "format": "parquet" if suffix == ".parquet" else "json",
                "sha256": digest,
                "size_bytes": size,
            }
            if suffix == ".parquet":
                record["row_count"] = _parquet_row_count(source)
            files.append(record)
        files.sort(key=lambda item: str(item["path"]))
        content_identity = {
            "parent": parent_identity,
            "scope_digest": _content_digest(scope),
            "through_date": THROUGH_DATE.isoformat(),
            "files": files,
        }
        content_digest = _content_digest(content_identity)
        manifest = {
            "schema_version": 1,
            "kind": "ods_base_hotfix",
            "hotfix_id": hotfix_id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": code_revision,
            "parent": parent_identity,
            "scope": {
                "reason": "sse_szse_listed_universe_209_excluded_from_collection_stock_list",
                "security_count": len(codes),
                "scope_digest": _content_digest(scope),
                "through_date": THROUGH_DATE.isoformat(),
            },
            "application": {
                "mode": "fill_absent_keys_only",
                "parent_mutation": False,
                "current_switch": False,
            },
            "content": {"files": files, "coverage": coverage},
            "content_digest": content_digest,
        }
        manifest_path = staging / "manifest.json"
        _write_json(manifest_path, manifest)
        manifest_sha = _sha256(manifest_path)
        ready = staging / "READY"
        with ready.open("x", encoding="ascii") as stream:
            stream.write(f"{manifest_sha}\n")
            stream.flush()
            os.fsync(stream.fileno())
        ready.chmod(0o640)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_hotfix_bundle(output, parent_base=parent_base, deep=True)


def verify_hotfix_bundle(
    root: Path,
    *,
    parent_base: Path,
    deep: bool = False,
) -> OdsBaseHotfixBundle:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise OdsBaseHotfixError(f"Hotfix bundle root is invalid: {root}")
    parent_manifest, parent_identity = _parent_identity(parent_base.resolve())
    del parent_manifest
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    if not ready_path.is_file():
        raise OdsBaseHotfixError("Hotfix bundle is not READY")
    manifest_sha = _sha256(manifest_path)
    if ready_path.read_text(encoding="ascii").strip() != manifest_sha:
        raise OdsBaseHotfixError("Hotfix READY does not bind its manifest")
    manifest = _read_object(manifest_path)
    if manifest.get("kind") != "ods_base_hotfix" or manifest.get("hotfix_id") != root.name:
        raise OdsBaseHotfixError("Hotfix manifest identity does not match its directory")
    if manifest.get("parent") != parent_identity:
        raise OdsBaseHotfixError("Hotfix parent binding is invalid")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope.get("security_count") != EXPECTED_SCOPE_COUNT:
        raise OdsBaseHotfixError("Hotfix manifest scope is invalid")
    content = manifest.get("content")
    files = content.get("files") if isinstance(content, dict) else None
    if not isinstance(files, list) or not files:
        raise OdsBaseHotfixError("Hotfix manifest has no file inventory")
    paths: set[str] = set()
    total_size = 0
    for item in files:
        if not isinstance(item, dict):
            raise OdsBaseHotfixError("Hotfix file inventory contains a non-object")
        relative = str(item.get("path") or "")
        if relative in paths:
            raise OdsBaseHotfixError(f"duplicate Hotfix file path: {relative}")
        paths.add(relative)
        target = root / _safe_relative(relative)
        expected_size = item.get("size_bytes")
        if not target.is_file() or target.is_symlink() or target.stat().st_size != expected_size:
            raise OdsBaseHotfixError(f"Hotfix file is missing or has wrong size: {relative}")
        if deep and _sha256(target) != item.get("sha256"):
            raise OdsBaseHotfixError(f"Hotfix file checksum mismatch: {relative}")
        total_size += int(expected_size)
    content_identity = {
        "parent": parent_identity,
        "scope_digest": scope.get("scope_digest"),
        "through_date": scope.get("through_date"),
        "files": files,
    }
    expected_content_digest = _content_digest(content_identity)
    if manifest.get("content_digest") != expected_content_digest:
        raise OdsBaseHotfixError("Hotfix content digest is invalid")
    return OdsBaseHotfixBundle(
        root=root,
        hotfix_id=root.name,
        parent_bundle_id=EXPECTED_PARENT_BUNDLE,
        scope_count=EXPECTED_SCOPE_COUNT,
        file_count=len(files),
        size_bytes=total_size,
        manifest_sha256=manifest_sha,
        content_digest=expected_content_digest,
    )
