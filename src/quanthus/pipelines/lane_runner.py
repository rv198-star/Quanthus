"""Hermes ODS five-lane shadow/production runner bound to the schedule contract.

Phase B runs beside legacy cron. Outputs land under an isolated shadow root so
canonical production paths and live timers stay untouched until GO_FOR_CUTOVER.

Contract rules enforced here:
- READY is written last by publish_ods_release; nothing is written to the release
  directory after READY.
- partial / failed / skipped-mandatory never produce READY.
- market + intraday share resource-group lock pve_market_api.
- watermarks advance only after READY success with monotonic checks.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.contracts import (
    OdsReleaseError,
    publish_ods_release,
    verify_ods_release,
)
from quanthus.pipelines.schedule_contracts import (
    ScheduleContractError,
    load_schedule_contract,
)
from quanthus.providers.baostock_transport import set_socket_timeout
from quanthus.providers.minute_recovery import load_minute_recovery_policy
from quanthus.providers.trading_calendar import (
    CalendarRefreshError,
    refresh_calendar,
)

SH = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
PRODUCER = "hermes-ods-lane-runner-v5"
INDEX_CODES: dict[str, str] = {
    "000001.SH": "sh.000001",
    "000300.SH": "sh.000300",
    "399001.SZ": "sz.399001",
}

MANDATORY: dict[str, set[str]] = {
    "market-daily-core": {
        "daily_ohlcv_none_delta",
        "daily_status_observation_delta",
        "security_master",
        "stock_list",
        "trading_calendar",
        "index_daily_delta",
        "valuation_daily_delta",
        "adjustment_evidence_delta",
        "lane_status",
        "acquisition_log",
    },
    "market-daily-core-closed": {
        "trading_calendar",
        "lane_status",
    },
    "calendar-daily-events": {
        "announcements_delta",
        "suspensions_delta",
        "corporate_actions_delta",
        "dividends_delta",
        "factor_evidence_delta",
        "financial_disclosures_delta",
        "lane_status",
        "acquisition_log",
    },
    "weekly-corrections": {
        "shareholder_count",
        "raw_f10",
        "financial_statements",
        "financial_quarterly",
        "corporate_actions",
        "dividends",
        "adjustment_evidence",
        "st_announcement_trail",
        "security_lifecycle",
        "industry_membership",
        "fundamentals_receipts",
        "reconciliation_summary",
        "reconciliation_keys",
        "lane_status",
    },
    "intraday-archive": {
        "intraday_bars",
        "intraday_meta",
        "trading_calendar",
        "lane_status",
        "acquisition_log",
    },
}


class LaneRunError(RuntimeError):
    """Raised when a lane attempt cannot complete its contract."""


_FAILURE_INJECTION_STAGES = {"acquisition", "pre_seal", "transfer"}


def _failure_injection_stage(*, shadow_root: Path) -> str | None:
    """Return an explicitly authorized shadow-only failure injection stage."""

    stage = (os.environ.get("HERMES_ODS_FAILURE_INJECTION_STAGE") or "").strip()
    enabled = os.environ.get("HERMES_ODS_ENABLE_FAILURE_INJECTION") == "1"
    if not stage:
        return None
    if not enabled:
        raise LaneRunError(
            "HERMES_ODS_FAILURE_INJECTION_STAGE requires "
            "HERMES_ODS_ENABLE_FAILURE_INJECTION=1"
        )
    if stage not in _FAILURE_INJECTION_STAGES:
        raise LaneRunError(
            "HERMES_ODS_FAILURE_INJECTION_STAGE must be one of: "
            + ", ".join(sorted(_FAILURE_INJECTION_STAGES))
        )
    if "shadow" not in {part.lower() for part in shadow_root.resolve().parts}:
        raise LaneRunError("failure injection is restricted to a shadow root")
    return stage


def _inject_failure(configured_stage: str | None, current_stage: str) -> None:
    if configured_stage == current_stage:
        raise LaneRunError(f"deterministic failure injection at {current_stage}")


def _now() -> datetime:
    return datetime.now(UTC)


def _now_sh_iso() -> str:
    return datetime.now(SH).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    # numpy / pyarrow scalars
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except Exception:
            pass
    return str(value)


def _write_parquet(rows: Sequence[Mapping[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise LaneRunError("pyarrow is required to seal lane artifacts") from exc
    clean_rows = [{str(k): _jsonable(v) for k, v in dict(row).items()} for row in rows]
    table = pa.Table.from_pylist(clean_rows) if clean_rows else pa.table({})
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return len(clean_rows)


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise LaneRunError("pyarrow is required to read lane inputs") from exc
    return pq.read_table(path).to_pylist()


def _read_parquet_filter_date(
    path: Path,
    *,
    market_date: date,
    date_col: str = "date",
) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, filters=[(date_col, "=", market_date)])
        return table.to_pylist()
    except Exception:
        target = market_date.isoformat()
        out: list[dict[str, Any]] = []
        for row in _read_parquet(path):
            value = row.get(date_col) or row.get("trade_date") or row.get("market_date")
            if value is None:
                continue
            if hasattr(value, "date"):
                value = value.date()
            if str(value)[:10] == target:
                out.append(dict(row))
        return out


def session_preflight(*, calendar: Path, market_date: date) -> str:
    """Return both_open | both_closed | missing_or_disagree for SSE/SZSE."""

    calendar = calendar.resolve()
    if not calendar.is_file():
        return "missing_or_disagree"
    rows = _read_parquet(calendar)
    states: dict[str, int] = {}
    for row in rows:
        exchange = str(row.get("exchange") or "").upper()
        if exchange not in {"SSE", "SZSE"}:
            continue
        trade_date = row.get("trade_date") or row.get("date")
        if trade_date is None:
            continue
        if hasattr(trade_date, "date"):
            trade_date = trade_date.date()
        trade_date = date.fromisoformat(str(trade_date)[:10])
        if trade_date != market_date:
            continue
        is_open = row.get("is_open")
        try:
            states[exchange] = int(is_open)
        except (TypeError, ValueError):
            return "missing_or_disagree"
    if set(states) != {"SSE", "SZSE"}:
        return "missing_or_disagree"
    if states["SSE"] not in {0, 1} or states["SZSE"] not in {0, 1}:
        return "missing_or_disagree"
    if states["SSE"] != states["SZSE"]:
        return "missing_or_disagree"
    return "both_open" if states["SSE"] == 1 else "both_closed"


@dataclass
class DatasetStatus:
    name: str
    status: str
    row_count: int | None = None
    sha256: str | None = None
    source_revision: str | None = None
    watermark: str | None = None
    knowledge_cutoff: str | None = None
    duration_seconds: float | None = None
    detail: str | None = None
    path: str | None = None
    source_request: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "row_count": self.row_count,
            "sha256": self.sha256,
            "source_revision": self.source_revision,
            "watermark": self.watermark,
            "knowledge_cutoff": self.knowledge_cutoff,
            "duration_seconds": self.duration_seconds,
            "detail": self.detail,
            "path": self.path,
            "source_request": self.source_request,
        }


@dataclass
class LaneRunResult:
    lane_id: str
    attempt_id: str
    market_date: date
    status: str
    attempt_dir: Path
    release_dir: Path | None
    datasets: list[DatasetStatus] = field(default_factory=list)
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    preflight: str | None = None
    preflight_evidence: dict[str, Any] | None = None
    detail: str | None = None
    stream: str | None = None
    schedule_id: str | None = None
    schedule_contract_revision: str | None = None
    schedule_config_sha256: str | None = None
    run_mode: str = "scheduled"
    recovery_deadline_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "attempt_id": self.attempt_id,
            "market_date": self.market_date.isoformat(),
            "status": self.status,
            "attempt_dir": str(self.attempt_dir),
            "release_dir": str(self.release_dir) if self.release_dir else None,
            "datasets": [item.to_dict() for item in self.datasets],
            "started_at": self.started_at.isoformat(),
            "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
            "preflight": self.preflight,
            "preflight_evidence": self.preflight_evidence,
            "detail": self.detail,
            "stream": self.stream,
            "producer": PRODUCER,
            "schedule_id": self.schedule_id,
            "schedule_contract_revision": self.schedule_contract_revision,
            "schedule_config_sha256": self.schedule_config_sha256,
            "run_mode": self.run_mode,
            "recovery_deadline_at": (
                None
                if self.recovery_deadline_at is None
                else self.recovery_deadline_at.isoformat()
            ),
        }


@dataclass(frozen=True)
class LanePaths:
    home: Path
    shadow_root: Path
    calendar: Path
    lock_root: Path


def resolve_paths(
    *,
    home: Path | None = None,
    shadow_root: Path | None = None,
    calendar: Path | None = None,
) -> LanePaths:
    root = Path(os.environ.get("ASTOCK_ROOT") or "/root/work/astock").resolve()
    resolved_home = (
        home or Path(os.environ.get("ASTOCK_HOME") or (root / "data"))
    ).resolve()
    resolved_shadow = (
        shadow_root
        or Path(
            os.environ.get("HERMES_ODS_SHADOW_ROOT")
            or (resolved_home / "shadow" / "hermes-ods-v1")
        )
    ).resolve()
    resolved_calendar = (
        calendar
        or Path(
            os.environ.get("HERMES_ODS_CALENDAR")
            or (resolved_home / "ref_trading_calendar.parquet")
        )
    ).resolve()
    lock_root = Path(
        os.environ.get("HERMES_ODS_LOCK_ROOT") or (resolved_shadow / "locks")
    ).resolve()
    return LanePaths(
        home=resolved_home,
        shadow_root=resolved_shadow,
        calendar=resolved_calendar,
        lock_root=lock_root,
    )


def _attempt_id(market_date: date, started: datetime) -> str:
    stamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{market_date.strftime('%Y%m%d')}-{stamp}"


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise LaneRunError(f"lane lock busy: {path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} at={_now().isoformat()}\n")
    handle.flush()
    return handle


def _resource_group_for(lane_id: str) -> str | None:
    return {
        "market-daily-core": "pve_market_api",
        "intraday-archive": "pve_market_api",
        "calendar-daily-events": "pve_event_api",
        "weekly-corrections": "pve_bulk_io",
        "bootstrap": "pve_bulk_io",
    }.get(lane_id)


def _acquire_mode() -> str:
    """Fail-closed acquire mode.

    - ``provider``: live collectors (default for production shadow).
    - ``local``: fixture/home re-acquire for unit tests only.
    - ``auto`` is rejected; missing deps must fail, never silent re-seal.
    """

    raw = (os.environ.get("HERMES_ODS_ACQUIRE_MODE") or "provider").strip().lower()
    if raw in {"local", "provider"}:
        return raw
    if raw in {"", "auto"}:
        raise LaneRunError(
            "HERMES_ODS_ACQUIRE_MODE must be explicit 'provider' or 'local'; "
            "auto/silent-local is forbidden (fail-closed)"
        )
    raise LaneRunError(f"unknown HERMES_ODS_ACQUIRE_MODE={raw!r}")


def _symbol_limit() -> int | None:
    raw = os.environ.get("HERMES_ODS_SYMBOL_LIMIT", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _provider_spec() -> dict[str, Any]:
    """Load the explicit Hermes provider command contract.

    Commands are argv arrays, never shell strings. Each collector must write the
    parquet path supplied through ``{output}``; this keeps provider evidence in
    the current immutable attempt instead of re-reading a mutable local SoT.
    """

    raw = os.environ.get("HERMES_ODS_PROVIDER_SPEC", "").strip()
    if not raw:
        raise LaneRunError("HERMES_ODS_PROVIDER_SPEC is required in provider mode")
    path = Path(raw)
    if not path.is_file():
        raise LaneRunError(f"provider spec is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneRunError(f"invalid provider spec {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LaneRunError("provider spec root must be an object")
    return payload


def _run_configured_provider_collector(
    *,
    lane_id: str,
    dataset: str,
    output: Path,
    paths: LanePaths,
    market_date: date,
    window_start: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = _provider_spec()
    lanes = spec.get("lanes")
    lane = lanes.get(lane_id) if isinstance(lanes, dict) else None
    collector = lane.get(dataset) if isinstance(lane, dict) else None
    if not isinstance(collector, dict):
        raise LaneRunError(f"provider collector not configured: {lane_id}/{dataset}")
    argv_template = collector.get("argv")
    if not isinstance(argv_template, list) or not argv_template:
        raise LaneRunError(f"provider argv must be a non-empty list: {lane_id}/{dataset}")
    if not all(isinstance(item, str) for item in argv_template):
        raise LaneRunError(f"provider argv must contain only strings: {lane_id}/{dataset}")

    output.parent.mkdir(parents=True, exist_ok=True)
    receipts_output = output.with_suffix(output.suffix + ".receipts.parquet")
    quarterly_output = output.with_name("financial_quarterly.parquet")
    fundamentals_python = os.environ.get("ASTOCK_FUNDAMENTALS_PYTHON") or sys.executable
    tdx_python = (
        os.environ.get("ASTOCK_TDX_PYTHON")
        or os.environ.get("ASTOCK_MINUTE_PYTHON")
        or sys.executable
    )
    values = {
        "output": str(output),
        "receipts": str(receipts_output),
        "home": str(paths.home),
        "python": sys.executable,
        "fundamentals_python": fundamentals_python,
        "tdx_python": tdx_python,
        "stock_list": str(paths.home / "stock_list.parquet"),
        "market_date": market_date.isoformat(),
        "window_start": window_start.isoformat(),
        "statement_periods": (
            os.environ.get("HERMES_ODS_STATEMENT_PERIODS") or "8"
        ),
        "statement_workers": (
            os.environ.get("HERMES_ODS_STATEMENT_WORKERS") or "4"
        ),
        "statement_batch_size": (
            os.environ.get("HERMES_ODS_STATEMENT_BATCH_SIZE") or "100"
        ),
        "statement_checkpoint": str(
            paths.shadow_root
            / "provider-checkpoints"
            / lane_id
            / dataset
            / market_date.isoformat()
            / f"periods-{os.environ.get('HERMES_ODS_STATEMENT_PERIODS') or '8'}"
        ),
        "provider_checkpoint": str(
            paths.shadow_root
            / "provider-checkpoints"
            / lane_id
            / dataset
            / market_date.isoformat()
        ),
        "quarterly_output": str(quarterly_output),
    }
    try:
        argv = [item.format_map(values) for item in argv_template]
    except (KeyError, ValueError) as exc:
        raise LaneRunError(
            f"invalid provider argv placeholder for {lane_id}/{dataset}: {exc}"
        ) from exc
    if "{output}" not in argv_template:
        raise LaneRunError(f"provider argv must include {{output}}: {lane_id}/{dataset}")
    receipt_contract = collector.get("receipt_contract")
    if receipt_contract is not None and receipt_contract != "per_security_v1":
        raise LaneRunError(
            f"unsupported provider receipt contract: {lane_id}/{dataset}={receipt_contract}"
        )
    if receipt_contract and "{receipts}" not in argv_template:
        raise LaneRunError(
            f"provider argv must include {{receipts}}: {lane_id}/{dataset}"
        )

    import subprocess

    started = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=str(paths.home.parent),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=int(collector.get("timeout_seconds") or 3600),
        check=False,
    )
    meta = {
        "provider": collector.get("provider") or "configured-command",
        "lane_id": lane_id,
        "dataset": dataset,
        "argv": argv,
        "output": str(output),
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    if proc.returncode != 0:
        raise LaneRunError(
            f"provider collector failed {lane_id}/{dataset} exit={proc.returncode}: "
            f"{meta['stderr_tail']}"
        )
    if not output.is_file():
        raise LaneRunError(
            f"provider collector produced no attempt output: {lane_id}/{dataset}"
        )
    return_mode = str(collector.get("return_mode") or "rows")
    if return_mode not in {"rows", "artifact"}:
        raise LaneRunError(
            f"unsupported provider return mode: {lane_id}/{dataset}={return_mode}"
        )
    try:
        import pyarrow.parquet as pq

        output_row_count = int(pq.ParquetFile(output).metadata.num_rows)
    except Exception as exc:  # noqa: BLE001 - malformed parquet must block READY
        raise LaneRunError(
            f"provider collector produced invalid parquet: {lane_id}/{dataset}: {exc}"
        ) from exc
    rows = _read_parquet(output) if return_mode == "rows" else []
    if receipt_contract:
        if not receipts_output.is_file():
            raise LaneRunError(
                f"provider collector produced no receipts: {lane_id}/{dataset}"
            )
        receipts = _read_parquet(receipts_output)
        if not receipts:
            raise LaneRunError(
                f"provider collector produced zero receipts: {lane_id}/{dataset}"
            )
        allowed_statuses = {"success_nonempty", "justified_empty", "failed"}
        invalid = [
            item for item in receipts if str(item.get("status")) not in allowed_statuses
        ]
        failed = [item for item in receipts if str(item.get("status")) == "failed"]
        if invalid:
            raise LaneRunError(
                f"provider receipts contain invalid statuses: {lane_id}/{dataset}"
            )
        if failed:
            raise LaneRunError(
                f"provider receipts contain failures: {lane_id}/{dataset} "
                f"failed={len(failed)}/{len(receipts)}"
            )
        if output_row_count == 0 and collector.get("allow_all_justified_empty") is not True:
            raise LaneRunError(
                f"provider collector returned zero rows without an allowed empty contract: "
                f"{lane_id}/{dataset}"
            )
        meta["receipt_contract"] = receipt_contract
        meta["receipts"] = str(receipts_output)
        meta["receipt_count"] = len(receipts)
        meta["justified_empty_count"] = sum(
            1 for item in receipts if str(item.get("status")) == "justified_empty"
        )
        meta["receipt_sha256"] = _sha256(receipts_output)
    metadata_path = output.with_suffix(output.suffix + ".meta.json")
    if metadata_path.is_file():
        try:
            provider_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneRunError(
                f"invalid provider metadata {lane_id}/{dataset}: {exc}"
            ) from exc
        if not isinstance(provider_metadata, dict):
            raise LaneRunError(
                f"provider metadata must be an object: {lane_id}/{dataset}"
            )
        meta["provider_metadata"] = provider_metadata
        meta["completed_at"] = provider_metadata.get("completed_at")
    meta["return_mode"] = return_mode
    meta["row_count"] = output_row_count
    meta["sha256"] = _sha256(output)
    return rows, meta


def _code6(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".")[-1]
    if text.isdigit():
        return text.zfill(6)
    return text


def _is_a_share_code(code: str) -> bool:
    code = _code6(code)
    if len(code) != 6 or not code.isdigit():
        return False
    # exclude pure index-like / BSE 920 for market core scope
    if code.startswith(("920", "8", "4")):
        return False
    return code.startswith(("0", "1", "2", "3", "6"))


def _universe_from_stock_list(home: Path) -> list[str]:
    path = home / "stock_list.parquet"
    if not path.is_file():
        return []
    codes: list[str] = []
    for row in _read_parquet(path):
        code = _code6(row.get("stock_code") or row.get("security_id"))
        if _is_a_share_code(code):
            codes.append(code)
    codes = sorted(set(codes))
    limit = _symbol_limit()
    if limit is not None:
        return codes[:limit]
    return codes


def _fnum(value: Any, default: float | None = None) -> float | None:
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bs_code(code: str) -> str:
    code = _code6(code)
    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    return f"sz.{code}"


def _canonical_index_code(value: Any) -> str:
    """Normalize a legacy index identifier to the scheduler contract code."""

    raw = str(value or "").upper()
    if raw in INDEX_CODES:
        return raw
    code = _code6(raw)
    return next((item for item in INDEX_CODES if item.startswith(code)), "")


def _filter_rows_by_date(
    rows: Sequence[Mapping[str, Any]],
    *,
    market_date: date,
    date_keys: Sequence[str],
    lookback_days: int = 0,
) -> list[dict[str, Any]]:
    start = market_date - timedelta(days=lookback_days)
    selected: list[dict[str, Any]] = []
    for row in rows:
        matched: date | None = None
        for key in date_keys:
            value = row.get(key)
            if value is None:
                continue
            try:
                if hasattr(value, "date"):
                    value = value.date()
                matched = date.fromisoformat(str(value)[:10])
                break
            except ValueError:
                continue
        if matched is None:
            continue
        if start <= matched <= market_date:
            selected.append(dict(row))
    return selected


def _seal_dataset(
    *,
    name: str,
    rows: Sequence[Mapping[str, Any]],
    landing: Path,
    source_revision: str,
    knowledge_cutoff: str,
    watermark: str,
    relative_name: str | None = None,
    source_request: str | None = None,
    require_rows: bool = False,
) -> DatasetStatus:
    started = time.monotonic()
    file_name = relative_name or f"{name}.parquet"
    path = landing / file_name
    count = _write_parquet(rows, path)
    if require_rows and count <= 0:
        return DatasetStatus(
            name=name,
            status="failed",
            row_count=count,
            sha256=_sha256(path),
            source_revision=source_revision,
            watermark=watermark,
            knowledge_cutoff=knowledge_cutoff,
            duration_seconds=round(time.monotonic() - started, 3),
            detail="zero rows; READY withheld",
            path=str(path),
            source_request=source_request,
        )
    return DatasetStatus(
        name=name,
        status="ready",
        row_count=count,
        sha256=_sha256(path),
        source_revision=source_revision,
        watermark=watermark,
        knowledge_cutoff=knowledge_cutoff,
        duration_seconds=round(time.monotonic() - started, 3),
        path=str(path),
        source_request=source_request,
    )


def _copy_dataset(
    *,
    name: str,
    source: Path,
    landing: Path,
    source_revision: str,
    knowledge_cutoff: str,
    watermark: str,
    relative_name: str | None = None,
    source_request: str | None = None,
    require_rows: bool = False,
) -> DatasetStatus:
    started = time.monotonic()
    if not source.is_file():
        return DatasetStatus(
            name=name,
            status="failed",
            detail=f"missing source: {source}",
            duration_seconds=round(time.monotonic() - started, 3),
            source_request=source_request,
        )
    file_name = relative_name or f"{name}.parquet"
    dest = landing / file_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    shutil.copy2(source, tmp)
    os.replace(tmp, dest)
    try:
        import pyarrow.parquet as pq

        row_count = int(pq.ParquetFile(dest).metadata.num_rows)
    except Exception as exc:  # noqa: BLE001 - malformed parquet must block READY
        return DatasetStatus(
            name=name,
            status="failed",
            detail=f"invalid parquet: {exc}",
            duration_seconds=round(time.monotonic() - started, 3),
            path=str(dest),
            source_request=source_request,
        )
    if require_rows and row_count <= 0:
        return DatasetStatus(
            name=name,
            status="failed",
            row_count=row_count,
            sha256=_sha256(dest),
            source_revision=source_revision,
            watermark=watermark,
            knowledge_cutoff=knowledge_cutoff,
            duration_seconds=round(time.monotonic() - started, 3),
            detail="zero rows; READY withheld",
            path=str(dest),
            source_request=source_request,
        )
    return DatasetStatus(
        name=name,
        status="ready",
        row_count=row_count,
        sha256=_sha256(dest),
        source_revision=source_revision,
        watermark=watermark,
        knowledge_cutoff=knowledge_cutoff,
        duration_seconds=round(time.monotonic() - started, 3),
        path=str(dest),
        source_request=source_request,
    )


def _register_parquet_dataset(
    *,
    name: str,
    path: Path,
    source_revision: str,
    knowledge_cutoff: str,
    watermark: str,
    source_request: str | None = None,
    require_rows: bool = False,
) -> DatasetStatus:
    """Register an attempt-local provider artifact without loading or copying it."""

    started = time.monotonic()
    if not path.is_file():
        return DatasetStatus(
            name=name,
            status="failed",
            detail=f"missing provider artifact: {path}",
            duration_seconds=round(time.monotonic() - started, 3),
            source_request=source_request,
        )
    try:
        import pyarrow.parquet as pq

        row_count = int(pq.ParquetFile(path).metadata.num_rows)
    except Exception as exc:  # noqa: BLE001 - malformed parquet must block READY
        return DatasetStatus(
            name=name,
            status="failed",
            detail=f"invalid parquet: {exc}",
            duration_seconds=round(time.monotonic() - started, 3),
            path=str(path),
            source_request=source_request,
        )
    status = "failed" if require_rows and row_count <= 0 else "ready"
    return DatasetStatus(
        name=name,
        status=status,
        row_count=row_count,
        sha256=_sha256(path),
        source_revision=source_revision,
        watermark=watermark,
        knowledge_cutoff=knowledge_cutoff,
        duration_seconds=round(time.monotonic() - started, 3),
        detail="zero rows; READY withheld" if status == "failed" else None,
        path=str(path),
        source_request=source_request,
    )


def _calendar_source_request(calendar: Path) -> str:
    metadata = calendar.with_suffix(calendar.suffix + ".meta.json")
    if metadata.is_file():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            return json.dumps(
                {"operation": "refresh_then_copy", "refresh": payload},
                ensure_ascii=True,
                sort_keys=True,
            )
        except (OSError, json.JSONDecodeError):
            pass
    return f"copy:{calendar.name}"


def _seal_json_dataset(
    *,
    name: str,
    payload: Mapping[str, Any],
    landing: Path,
    source_revision: str,
    knowledge_cutoff: str,
    watermark: str,
    relative_name: str | None = None,
) -> DatasetStatus:
    started = time.monotonic()
    file_name = relative_name or f"{name}.json"
    path = landing / file_name
    _atomic_json(path, payload)
    return DatasetStatus(
        name=name,
        status="ready",
        row_count=1,
        sha256=_sha256(path),
        source_revision=source_revision,
        watermark=watermark,
        knowledge_cutoff=knowledge_cutoff,
        duration_seconds=round(time.monotonic() - started, 3),
        path=str(path),
    )


def _baostock_login_with_retry(
    bs: Any,
    *,
    max_attempts: int = 3,
    socket_timeout_seconds: float = 30,
) -> int:
    last_error = "unknown error"
    for attempt in range(1, max_attempts + 1):
        try:
            login = bs.login()
            if str(login.error_code) == "0":
                set_socket_timeout(socket_timeout_seconds)
                return attempt
            last_error = str(login.error_msg)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        if attempt < max_attempts:
            time.sleep(float(attempt))
    raise LaneRunError(f"baostock login failed after {max_attempts} attempts: {last_error}")


def _baostock_query_day_with_retry(
    bs: Any,
    *,
    code: str,
    fields: str,
    day: str,
    max_attempts: int = 3,
    socket_timeout_seconds: float = 30,
) -> tuple[list[list[str]], str, str, int, list[dict[str, str]]]:
    """Query one daily row, reconnecting only on genuine provider errors."""

    retry_errors: list[dict[str, str]] = []
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            with suppress(Exception):
                bs.logout()
            time.sleep(float(attempt - 1))
            try:
                _baostock_login_with_retry(
                    bs,
                    max_attempts=max_attempts,
                    socket_timeout_seconds=socket_timeout_seconds,
                )
            except LaneRunError as exc:
                error_code = "provider_reconnect_failed"
                error_msg = str(exc)
                retry_errors.append(
                    {
                        "attempt": str(attempt),
                        "error_code": error_code,
                        "error_msg": error_msg,
                    }
                )
                continue

        rows: list[list[str]] = []
        error_code = "provider_exception"
        error_msg = "unknown provider exception"
        try:
            response = bs.query_history_k_data_plus(
                code,
                fields,
                start_date=day,
                end_date=day,
                frequency="d",
                adjustflag="3",
            )
            error_code = str(response.error_code)
            error_msg = str(response.error_msg)
            if error_code == "0":
                while response.next():
                    rows.append(response.get_row_data())
        except Exception as exc:  # noqa: BLE001
            error_code = "provider_exception"
            error_msg = str(exc)

        if error_code == "0" and rows:
            return rows, error_code, error_msg, attempt, retry_errors
        if error_code == "0":
            # An empty successful response is a data-availability state, not a
            # broken connection. The caller may retry the missing set later.
            return (
                [],
                "empty_response",
                "provider returned no daily row",
                attempt,
                retry_errors,
            )
        retry_errors.append(
            {
                "attempt": str(attempt),
                "error_code": error_code,
                "error_msg": error_msg,
            }
        )

    return [], error_code, error_msg, max_attempts, retry_errors


def _baostock_fetch_day(
    codes: Sequence[str],
    market_date: date,
    *,
    missing_set_retry_rounds: int = 1,
    missing_set_retry_delay_seconds: float = 30,
    socket_timeout_seconds: float = 30,
    deadline_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    import baostock as bs

    if missing_set_retry_rounds < 0:
        raise LaneRunError("missing_set_retry_rounds must be non-negative")
    if missing_set_retry_delay_seconds < 0:
        raise LaneRunError("missing_set_retry_delay_seconds must be non-negative")
    day = market_date.isoformat()
    login_attempts = _baostock_login_with_retry(
        bs, socket_timeout_seconds=socket_timeout_seconds
    )
    fields = (
        "date,code,open,high,low,close,volume,amount,turn,pctChg,tradestatus,isST,"
        "peTTM,pbMRQ,psTTM,pcfNcfTTM"
    )
    bars_by_code: dict[str, dict[str, Any]] = {}
    status_by_code: dict[str, dict[str, Any]] = {}
    log_rows: list[dict[str, Any]] = []
    remaining = list(dict.fromkeys(codes))
    try:
        for scan_round in range(1, missing_set_retry_rounds + 2):
            if not remaining:
                break
            if scan_round > 1 and missing_set_retry_delay_seconds:
                time.sleep(missing_set_retry_delay_seconds)
            next_missing: list[str] = []
            for offset, code in enumerate(remaining):
                if deadline_at is not None and _now() >= deadline_at.astimezone(UTC):
                    next_missing.extend(remaining[offset:])
                    log_rows.append(
                        {
                            "event": "completion_deadline_reached",
                            "scan_round": scan_round,
                            "remaining_symbols": len(remaining) - offset,
                            "acquired_at": _now_sh_iso(),
                        }
                    )
                    break
                bs_code = _to_bs_code(code)
                request = {
                    "provider": "baostock",
                    "api": "query_history_k_data_plus",
                    "code": bs_code,
                    "start": day,
                    "end": day,
                    "frequency": "d",
                    "adjustflag": "3",
                    "fields": fields,
                }
                started = time.monotonic()
                rows, err, msg, attempts, retry_errors = _baostock_query_day_with_retry(
                    bs,
                    code=bs_code,
                    fields=fields,
                    day=day,
                    socket_timeout_seconds=socket_timeout_seconds,
                )
                acquired_at = _now_sh_iso()
                matching = next((row for row in rows if row and str(row[0]) == day), None)
                if err == "0" and matching is None:
                    err = "target_date_missing"
                    msg = f"provider response did not contain target date {day}"
                log_rows.append(
                    {
                        "security_id": code,
                        "request": request,
                        "response_error_code": err,
                        "response_error_msg": msg,
                        "response_rows": len(rows),
                        "acquired_at": acquired_at,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "attempts": attempts,
                        "initial_login_attempts": login_attempts,
                        "retry_errors": retry_errors,
                        "scan_round": scan_round,
                    }
                )
                if err != "0" or matching is None:
                    next_missing.append(code)
                    continue
                raw = matching
                # fields order matches `fields`
                bars_by_code[code] = {
                    "security_id": code,
                    "stock_code": code,
                    "market_date": day,
                    "date": day,
                    "open": _fnum(raw[2]),
                    "high": _fnum(raw[3]),
                    "low": _fnum(raw[4]),
                    "close": _fnum(raw[5]),
                    "volume": _fnum(raw[6], 0.0) or 0.0,
                    "amount": _fnum(raw[7], 0.0) or 0.0,
                    "turnover_rate": _fnum(raw[8]),
                    "pct_change": _fnum(raw[9]),
                    "adj_mode": "none",
                    "adj_version": "baostock.adjustflag=3.v1",
                    "source": "baostock.query_history_k_data_plus.adjustflag=3",
                    "acquired_at": acquired_at,
                    "built_at": acquired_at,
                    "_valuation_pe_ttm": _fnum(raw[12] if len(raw) > 12 else None),
                    "_valuation_pb": _fnum(raw[13] if len(raw) > 13 else None),
                    "_valuation_ps_ttm": _fnum(raw[14] if len(raw) > 14 else None),
                    "_valuation_pcf_ttm": _fnum(raw[15] if len(raw) > 15 else None),
                }
                status_by_code[code] = {
                    "security_id": code,
                    "stock_code": code,
                    "market_date": day,
                    "date": day,
                    "tradestatus": str(raw[10] if len(raw) > 10 else "1"),
                    "isST": str(raw[11] if len(raw) > 11 else "0"),
                    "source": "baostock.query_history_k_data_plus",
                    "acquired_at": acquired_at,
                    "built_at": acquired_at,
                }
            remaining = next_missing
    finally:
        bs.logout()
    return (
        [bars_by_code[code] for code in codes if code in bars_by_code],
        [status_by_code[code] for code in codes if code in status_by_code],
        log_rows,
    )


def _baostock_fetch_index_day(
    market_date: date,
    *,
    socket_timeout_seconds: float = 30,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch the contract's daily index set directly from Baostock."""

    import baostock as bs

    day = market_date.isoformat()
    login_attempts = _baostock_login_with_retry(
        bs, socket_timeout_seconds=socket_timeout_seconds
    )
    fields = "date,code,open,high,low,close,volume,amount,pctChg"
    rows_out: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    try:
        for index_code, bs_code in INDEX_CODES.items():
            request = {
                "provider": "baostock",
                "api": "query_history_k_data_plus",
                "code": bs_code,
                "start": day,
                "end": day,
                "frequency": "d",
                "adjustflag": "3",
                "fields": fields,
            }
            started = time.monotonic()
            rows, err, msg, attempts, retry_errors = _baostock_query_day_with_retry(
                bs,
                code=bs_code,
                fields=fields,
                day=day,
                socket_timeout_seconds=socket_timeout_seconds,
            )
            acquired_at = _now_sh_iso()
            log_rows.append(
                {
                    "index_code": index_code,
                    "request": request,
                    "response_error_code": err,
                    "response_error_msg": msg,
                    "response_rows": len(rows),
                    "acquired_at": acquired_at,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "attempts": attempts,
                    "initial_login_attempts": login_attempts,
                    "retry_errors": retry_errors,
                }
            )
            if err != "0":
                continue
            for raw in rows:
                rows_out.append(
                    {
                        "index_code": index_code,
                        "date": str(raw[0]),
                        "open": _fnum(raw[2]),
                        "high": _fnum(raw[3]),
                        "low": _fnum(raw[4]),
                        "close": _fnum(raw[5]),
                        "volume": _fnum(raw[6], 0.0) or 0.0,
                        "amount": _fnum(raw[7], 0.0) or 0.0,
                        "pct_chg": _fnum(raw[8]),
                        "source": "baostock.query_history_k_data_plus.adjustflag=3",
                        "acquired_at": acquired_at,
                    }
                )
    finally:
        bs.logout()
    return rows_out, log_rows


def _contract_deadline(market_date: date, value: str) -> datetime:
    try:
        clock = datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise LaneRunError(f"invalid schedule deadline: {value!r}") from exc
    return datetime.combine(market_date, clock, tzinfo=SH)


def _latest_observed_cutoff(
    fallback: str,
    *row_groups: Sequence[Mapping[str, Any]],
) -> str:
    candidates = [fallback, _now_sh_iso()]
    for rows in row_groups:
        candidates.extend(
            str(row["acquired_at"])
            for row in rows
            if row.get("acquired_at") not in {None, ""}
        )

    def parsed(value: str) -> datetime:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise LaneRunError(f"knowledge cutoff timestamp is timezone-naive: {value!r}")
        return stamp.astimezone(UTC)

    return max(candidates, key=parsed)


def _ods_node_id() -> str:
    return (os.environ.get("HERMES_ODS_NODE_ID") or "unknown").strip().lower()


def _market_acquisition_metrics(log_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    request_rows = [row for row in log_rows if row.get("duration_seconds") is not None]
    durations = [float(row["duration_seconds"]) for row in request_rows]
    stamps = [
        datetime.fromisoformat(str(row["acquired_at"])).astimezone(UTC)
        for row in request_rows
        if row.get("acquired_at") not in {None, ""}
    ]
    provider_error_codes = {
        "0",
        "empty_response",
        "target_date_missing",
    }
    return {
        "node_id": _ods_node_id(),
        "readiness_polls": max(
            (int(row.get("readiness_poll") or 0) for row in log_rows), default=0
        ),
        "request_count": len(request_rows),
        "empty_response_count": sum(
            str(row.get("response_error_code")) == "empty_response" for row in request_rows
        ),
        "provider_error_count": sum(
            str(row.get("response_error_code")) not in provider_error_codes
            for row in request_rows
        ),
        "reconnect_count": sum(
            max(int(row.get("attempts") or 1) - 1, 0) for row in request_rows
        ),
        "average_request_duration_seconds": (
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        "maximum_request_duration_seconds": round(max(durations), 3) if durations else None,
        "batch_duration_seconds": (
            round((max(stamps) - min(stamps)).total_seconds(), 3) if stamps else None
        ),
    }


def _baostock_wait_for_index_day(
    market_date: date,
    *,
    deadline_at: datetime,
    poll_interval_seconds: float,
    socket_timeout_seconds: float,
    progress_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Poll the complete sentinel index set before starting the stock scan."""

    if poll_interval_seconds <= 0:
        raise LaneRunError("provider readiness poll interval must be positive")
    target = market_date.isoformat()
    required = set(INDEX_CODES)
    all_logs: list[dict[str, Any]] = []
    poll = 0
    latest_rows: list[dict[str, Any]] = []
    while True:
        poll += 1
        latest_rows, poll_logs = _baostock_fetch_index_day(
            market_date,
            socket_timeout_seconds=socket_timeout_seconds,
        )
        for item in poll_logs:
            item["phase"] = "provider_readiness"
            item["readiness_poll"] = poll
        all_logs.extend(poll_logs)
        observed = {
            _canonical_index_code(row.get("index_code") or row.get("code"))
            for row in latest_rows
            if str(row.get("date") or "") == target
        }
        missing = sorted(required - observed)
        checked_at = _now_sh_iso()
        _atomic_json(
            progress_path,
            {
                "provider": "baostock",
                "node_id": _ods_node_id(),
                "target_date": target,
                "sentinel_indices": INDEX_CODES,
                "polls": poll,
                "checked_at": checked_at,
                "deadline_at": deadline_at.isoformat(),
                "ready": not missing,
                "missing_indices": missing,
                "requests": all_logs,
            },
        )
        if not missing:
            return latest_rows, all_logs
        now = _now().astimezone(SH)
        if now >= deadline_at:
            raise LaneRunError(
                "baostock provider readiness deadline reached; "
                f"missing_indices={','.join(missing)}; polls={poll}; "
                f"evidence={progress_path}"
            )
        remaining = (deadline_at - now).total_seconds()
        time.sleep(min(poll_interval_seconds, remaining))


def _local_market_acquire(
    *,
    paths: LanePaths,
    market_date: date,
    codes: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-acquire from local SoT with explicit request/response provenance.

    Used by unit tests and offline shadow. Not a silent copy: each symbol has a
    request record and acquired_at stamp.
    """

    day = market_date.isoformat()
    none_path = paths.home / "daily_ohlcv_none.parquet"
    st_candidates = [
        paths.home / "daily_status_observation.parquet",
        paths.home / "revisions" / "st_status_pit_v2_baostock_20260725" / "daily_isST.parquet",
    ]
    st_path = next((path for path in st_candidates if path.is_file()), None)
    none_by: dict[str, Mapping[str, Any]] = {}
    if none_path.is_file():
        for row in _read_parquet_filter_date(none_path, market_date=market_date):
            code = _code6(row.get("stock_code") or row.get("security_id"))
            if code:
                none_by[code] = row
    st_by: dict[str, Mapping[str, Any]] = {}
    if st_path is not None:
        for row in _read_parquet_filter_date(st_path, market_date=market_date):
            code = _code6(row.get("stock_code") or row.get("security_id"))
            if code:
                st_by[code] = row
    valuation_path = paths.home / "valuation_daily.parquet"
    valuation_by: dict[str, Mapping[str, Any]] = {}
    if valuation_path.is_file():
        for row in _read_parquet_filter_date(valuation_path, market_date=market_date):
            code = _code6(row.get("stock_code") or row.get("security_id"))
            if code:
                valuation_by[code] = row
    acquired_at = _now_sh_iso()
    bars: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    for code in codes:
        request = {
            "provider": "local-sot",
            "api": "read_parquet_filter",
            "code": code,
            "date": day,
            "sources": {
                "none": str(none_path),
                "status": str(st_path) if st_path else None,
            },
        }
        bar = none_by.get(code)
        st = st_by.get(code, {})
        log_rows.append(
            {
                "security_id": code,
                "request": request,
                "response_error_code": "0" if (bar is not None or st) else "1",
                "response_error_msg": "ok" if (bar is not None or st) else "missing",
                "response_rows": 1 if bar is not None else 0,
                "acquired_at": acquired_at,
                "duration_seconds": 0.0,
            }
        )
        if bar is not None:
            valuation = valuation_by.get(code, {})
            bars.append(
                {
                    "security_id": code,
                    "stock_code": code,
                    "market_date": day,
                    "date": day,
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": bar.get("volume") if bar.get("volume") is not None else 0.0,
                    "amount": bar.get("amount") if bar.get("amount") is not None else 0.0,
                    "turnover_rate": _fnum(bar.get("turnover_rate")),
                    "pct_change": _fnum(bar.get("pct_change")),
                    "adj_mode": "none",
                    "adj_version": str(bar.get("adj_version") or "local-sot-none-v1"),
                    "source": "local-sot.daily_ohlcv_none",
                    "acquired_at": acquired_at,
                    "built_at": str(bar.get("built_at") or acquired_at),
                    "_valuation_pe_ttm": _fnum(valuation.get("pe_ttm")),
                    "_valuation_pb": _fnum(valuation.get("pb")),
                    "_valuation_ps_ttm": _fnum(valuation.get("ps_ttm")),
                    "_valuation_pcf_ttm": _fnum(valuation.get("pcf_ttm")),
                    "_valuation_total_share": _fnum(valuation.get("total_share")),
                    "_valuation_total_mv": _fnum(valuation.get("total_mv")),
                }
            )
        tradestatus = st.get("tradestatus")
        is_st = st.get("isST")
        if tradestatus is None and bar is not None:
            tradestatus = "1"
        if tradestatus is None and is_st is None and bar is None:
            continue
        status_rows.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": day,
                "date": day,
                "tradestatus": None if tradestatus is None else str(tradestatus),
                "isST": None if is_st is None else str(is_st),
                "source": "local-sot.status",
                "acquired_at": acquired_at,
                "built_at": str(st.get("built_at") or acquired_at),
            }
        )
    return bars, status_rows, log_rows


def _build_market_open(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    provider_readiness: Mapping[str, Any] | None = None,
    completion_deadline: str | None = None,
    collection_window_experiment: Mapping[str, Any] | None = None,
    recovery_deadline_at: datetime | None = None,
) -> list[DatasetStatus]:
    mode = _acquire_mode()
    codes = _universe_from_stock_list(paths.home)
    if not codes:
        return [
            DatasetStatus(
                name="daily_ohlcv_none_delta",
                status="failed",
                detail="empty in-scope universe from stock_list",
            )
        ]
    rev = f"market-daily-{mode}-{market_date.strftime('%Y%m%d')}-v4"
    watermark = market_date.isoformat()
    request_summary = {
        "mode": mode,
        "universe_size": len(codes),
        "market_date": watermark,
        "provider": "baostock" if mode == "provider" else "local-sot",
        "node_id": _ods_node_id(),
        "provider_readiness": provider_readiness if mode == "provider" else None,
        "collection_window_experiment": (
            collection_window_experiment if mode == "provider" else None
        ),
        "run_mode": "manual_recovery" if recovery_deadline_at else "scheduled",
        "recovery_deadline_at": (
            recovery_deadline_at.isoformat() if recovery_deadline_at else None
        ),
        "excluded_from_collection_window_experiment": recovery_deadline_at is not None,
    }
    try:
        if mode == "provider":
            if provider_readiness is None or completion_deadline is None:
                raise LaneRunError("provider mode requires readiness and completion deadlines")
            socket_timeout_seconds = float(provider_readiness["socket_timeout_seconds"])
            readiness_deadline_at = recovery_deadline_at or _contract_deadline(
                market_date, str(provider_readiness["deadline"])
            )
            completion_deadline_at = recovery_deadline_at or _contract_deadline(
                market_date, completion_deadline
            )
            index_rows, index_log_rows = _baostock_wait_for_index_day(
                market_date,
                deadline_at=readiness_deadline_at,
                poll_interval_seconds=float(provider_readiness["poll_interval_seconds"]),
                socket_timeout_seconds=socket_timeout_seconds,
                progress_path=landing / "provider_readiness.json",
            )
            bars, status_rows, stock_log_rows = _baostock_fetch_day(
                codes,
                market_date,
                missing_set_retry_rounds=int(
                    provider_readiness["missing_set_retry_rounds"]
                ),
                missing_set_retry_delay_seconds=float(
                    provider_readiness["missing_set_retry_delay_seconds"]
                ),
                socket_timeout_seconds=socket_timeout_seconds,
                deadline_at=completion_deadline_at,
            )
            log_rows = [*index_log_rows, *stock_log_rows]
        else:
            bars, status_rows, log_rows = _local_market_acquire(
                paths=paths, market_date=market_date, codes=codes
            )
            index_source = paths.home / "index_daily.parquet"
            index_rows = (
                _filter_rows_by_date(
                    _read_parquet(index_source),
                    market_date=market_date,
                    date_keys=("date",),
                    lookback_days=0,
                )
                if index_source.is_file()
                else []
            )
            index_rows = [
                {
                    **row,
                    "index_code": _canonical_index_code(
                        row.get("index_code") or row.get("code")
                    ),
                }
                for row in index_rows
            ]
    except Exception as exc:  # noqa: BLE001
        return [
            DatasetStatus(
                name="daily_ohlcv_none_delta",
                status="failed",
                detail=f"acquisition failed: {exc}",
                source_request=json.dumps(request_summary, sort_keys=True),
            )
        ]

    knowledge_cutoff = _latest_observed_cutoff(
        knowledge_cutoff,
        bars,
        status_rows,
        index_rows,
        log_rows,
    )
    valuation_rows: list[dict[str, Any]] = []
    for bar in bars:
        valuation_rows.append(
            {
                "security_id": bar["security_id"],
                "stock_code": bar["stock_code"],
                "market_date": watermark,
                "date": watermark,
                "pe_ttm": bar.pop("_valuation_pe_ttm", None),
                "pb": bar.pop("_valuation_pb", None),
                "ps_ttm": bar.pop("_valuation_ps_ttm", None),
                "pcf_ttm": bar.pop("_valuation_pcf_ttm", None),
                "total_share": bar.pop("_valuation_total_share", None),
                "total_mv": bar.pop("_valuation_total_mv", None),
                "source": (
                    "baostock.query_history_k_data_plus.valuation"
                    if mode == "provider"
                    else "local-sot.valuation_daily"
                ),
                "acquired_at": bar.get("acquired_at"),
            }
        )
    datasets: list[DatasetStatus] = []
    datasets.append(
        _seal_dataset(
            name="daily_ohlcv_none_delta",
            rows=bars,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
            source_request=json.dumps(request_summary, sort_keys=True),
            require_rows=True,
        )
    )
    status = _seal_dataset(
        name="daily_status_observation_delta",
        rows=status_rows,
        landing=landing,
        source_revision=rev,
        knowledge_cutoff=knowledge_cutoff,
        watermark=watermark,
        source_request=json.dumps(request_summary, sort_keys=True),
        require_rows=True,
    )
    # coverage: every universe code must have status; tradestatus=1 requires bar
    status_codes = {str(row["security_id"]) for row in status_rows}
    bar_codes = {str(row["security_id"]) for row in bars}
    missing_status = [code for code in codes if code not in status_codes]
    t1_without_bar = [
        row["security_id"]
        for row in status_rows
        if str(row.get("tradestatus")) == "1" and row["security_id"] not in bar_codes
    ]
    if missing_status:
        status.status = "failed"
        status.detail = f"missing_status={len(missing_status)}"
    if t1_without_bar:
        status.status = "failed"
        detail = status.detail or ""
        status.detail = f"{detail} tradestatus_1_without_bar={len(t1_without_bar)}".strip()
    datasets.append(status)

    valuation = _seal_dataset(
        name="valuation_daily_delta",
        rows=valuation_rows,
        landing=landing,
        source_revision=f"valuation_daily_delta@{rev}",
        knowledge_cutoff=knowledge_cutoff,
        watermark=watermark,
        source_request=json.dumps(
            {
                "provider": "baostock" if mode == "provider" else "local-sot",
                "market_date": watermark,
                "fields": ["peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"],
                "coverage_basis": "one valuation observation per returned daily bar",
            },
            sort_keys=True,
        ),
        require_rows=True,
    )
    valuation_codes = {str(row["security_id"]) for row in valuation_rows}
    if valuation_codes != bar_codes:
        valuation.status = "failed"
        valuation.detail = (
            f"valuation_bar_coverage_mismatch="
            f"{len(valuation_codes.symmetric_difference(bar_codes))}"
        )
    valuation_fields = ("pe_ttm", "pb", "ps_ttm", "pcf_ttm", "total_mv")
    if not any(
        any(row.get(field) is not None for field in valuation_fields)
        for row in valuation_rows
    ):
        valuation.status = "failed"
        valuation.detail = "all valuation fields are null; READY withheld"
    datasets.append(valuation)

    for name, source, artifact in (
        (
            "security_master",
            paths.home / "ref_security_master.parquet",
            "ref_security_master.parquet",
        ),
        ("stock_list", paths.home / "stock_list.parquet", "stock_list.parquet"),
        ("trading_calendar", paths.calendar, "ref_trading_calendar.parquet"),
    ):
        datasets.append(
            _copy_dataset(
                name=name,
                source=source,
                landing=landing,
                source_revision=f"{name}@{rev}",
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name=artifact,
                source_request=(
                    _calendar_source_request(source)
                    if name == "trading_calendar"
                    else f"copy:{source.name}"
                ),
            )
        )

    index = _seal_dataset(
        name="index_daily_delta",
        rows=index_rows,
        landing=landing,
        source_revision=f"index_daily_delta@{rev}",
        knowledge_cutoff=knowledge_cutoff,
        watermark=watermark,
        source_request=json.dumps(
            {
                "provider": "baostock" if mode == "provider" else "local-sot",
                "codes": INDEX_CODES,
                "market_date": watermark,
            },
            sort_keys=True,
        ),
        require_rows=True,
    )
    observed_indices = {
        _canonical_index_code(row.get("index_code") or row.get("code"))
        for row in index_rows
    }
    missing_indices = sorted(set(INDEX_CODES) - observed_indices)
    if missing_indices:
        index.status = "failed"
        index.detail = f"missing_index_coverage={','.join(missing_indices)}"
    datasets.append(index)

    for name, source, keys in (
        (
            "adjustment_evidence_delta",
            paths.home / "ref_adjustment_factors.parquet",
            ("effective_date", "ex_date", "date"),
        ),
    ):
        if not source.is_file():
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="failed",
                    detail=f"missing source: {source.name}",
                    source_request=f"read:{source.name}",
                )
            )
            continue
        rows = _filter_rows_by_date(
            _read_parquet(source),
            market_date=market_date,
            date_keys=keys,
            lookback_days=0 if name.startswith("index") else 7,
        )
        datasets.append(
            _seal_dataset(
                name=name,
                rows=rows,
                landing=landing,
                source_revision=f"{name}@{rev}",
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                source_request=f"filter:{source.name}",
                require_rows=False,
            )
        )

    datasets.append(
        _seal_json_dataset(
            name="acquisition_log",
            payload={
                "lane": "market-daily-core",
                "mode": mode,
                "market_date": watermark,
                "universe_size": len(codes),
                "node_id": _ods_node_id(),
                "collection_window_experiment": collection_window_experiment,
                "run_mode": "manual_recovery" if recovery_deadline_at else "scheduled",
                "recovery_deadline_at": (
                    recovery_deadline_at.isoformat() if recovery_deadline_at else None
                ),
                "excluded_from_collection_window_experiment": (
                    recovery_deadline_at is not None
                ),
                "metrics": _market_acquisition_metrics(log_rows),
                "requests": log_rows,
                "source_revision": rev,
            },
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
        )
    )
    return datasets


def _build_market_closed(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
) -> list[DatasetStatus]:
    rev = f"calendar-closed-{market_date.strftime('%Y%m%d')}-v2"
    return [
        _copy_dataset(
            name="trading_calendar",
            source=paths.calendar,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=market_date.isoformat(),
            relative_name="ref_trading_calendar.parquet",
            source_request=_calendar_source_request(paths.calendar),
        )
    ]


def _event_source_map(paths: LanePaths) -> dict[str, tuple[Path, tuple[str, ...]]]:
    # Disclose/publish keys first. ex_date/effective_date are business fields only —
    # never primary watermark keys (today-disclosed / future-effective must not drop).
    return {
        "announcements_delta": (
            paths.home / "info_archive.db",
            ("publish_time", "ann_date", "notice_date", "announce_date", "publish_date"),
        ),
        "suspensions_delta": (
            paths.home / "ref_suspensions.parquet"
            if (paths.home / "ref_suspensions.parquet").is_file()
            else paths.home / "suspensions.parquet",
            (
                "announce_date",
                "notice_date",
                "publish_time",
                "suspend_start",
                "suspend_date",
                "market_date",
                "date",
            ),
        ),
        "corporate_actions_delta": (
            paths.home / "ref_corporate_actions.parquet",
            ("announce_date", "notice_date", "publish_time", "ex_date", "effective_date", "date"),
        ),
        "dividends_delta": (
            paths.home / "dividend_history.parquet",
            (
                "announce_date",
                "notice_date",
                "publish_time",
                "ex_date",
                "ex_dividend_date",
                "pay_date",
                "date",
            ),
        ),
        "factor_evidence_delta": (
            paths.home / "ref_adjustment_factors.parquet",
            ("announce_date", "notice_date", "publish_time", "effective_date", "ex_date", "date"),
        ),
        "financial_disclosures_delta": (
            paths.home / "financial_quarterly.parquet",
            (
                "announce_date",
                "notice_date",
                "publish_time",
                "publish_deadline",
                "report_date",
                "end_date",
                "date",
            ),
        ),
    }


def _event_lookback_days() -> int:
    raw = (os.environ.get("HERMES_ODS_EVENT_LOOKBACK_DAYS") or "7").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 7


def _event_watermark_path(paths: LanePaths) -> Path:
    return paths.shadow_root / "watermarks" / "calendar-daily-events.json"


def _load_event_watermark(paths: LanePaths) -> dict[str, Any]:
    path = _event_watermark_path(paths)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _commit_event_watermark(
    *,
    paths: LanePaths,
    market_date: date,
    attempt_id: str,
    input_watermark: str | None,
    observed_maximum: str | None,
) -> None:
    path = _event_watermark_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = _load_event_watermark(paths)
    committed = observed_maximum or market_date.isoformat()
    prior = prev.get("committed_watermark")
    if prior and str(committed) < str(prior):
        # never move watermark backward
        committed = str(prior)
    payload = {
        "lane_id": "calendar-daily-events",
        "input_watermark": input_watermark,
        "observed_maximum": observed_maximum,
        "committed_watermark": committed,
        "market_date": market_date.isoformat(),
        "attempt_id": attempt_id,
        "updated_at": _now_sh_iso(),
    }
    _atomic_json(path, payload)


def _cninfo_fetch_announcements(
    *,
    codes: Sequence[str],
    start: date,
    end: date,
    missing_set_retry_rounds: int = 1,
    missing_set_retry_delay_seconds: float = 30,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Live cninfo hisAnnouncement pull for disclose-date window."""

    if missing_set_retry_rounds < 0:
        raise LaneRunError("missing_set_retry_rounds must be non-negative")
    if missing_set_retry_delay_seconds < 0:
        raise LaneRunError("missing_set_retry_delay_seconds must be non-negative")

    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        raise LaneRunError(f"cninfo collector requires requests: {exc}") from exc

    Path(os.environ.get("ASTOCK_ROOT") or "/root/work/astock") / "toolkit"
    org_map_path = (
        Path(os.environ.get("ASTOCK_HOME") or "/root/work/astock/data")
        / "cninfo_orgid_map.json"
    )
    org_map: dict[str, str] = {}
    if org_map_path.is_file():
        try:
            org_map = json.loads(org_map_path.read_text(encoding="utf-8"))
        except Exception:
            org_map = {}

    url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": (
            "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
            "?url=disclosure/list/search"
        ),
    }
    session = requests.Session()
    rows_by_code: dict[str, list[dict[str, Any]]] = {}
    logs_by_code: dict[str, dict[str, Any]] = {}
    acquired_at = _now_sh_iso()
    se = f"{start.isoformat()}~{end.isoformat()}"
    pending = list(codes)
    for scan_round in range(1, missing_set_retry_rounds + 2):
        if scan_round > 1 and missing_set_retry_delay_seconds:
            time.sleep(missing_set_retry_delay_seconds)
        next_pending: list[str] = []
        for code in pending:
            org_id = org_map.get(code) or org_map.get(_code6(code)) or ""
            stock = f"{code},{org_id}" if org_id else code
            request = {
                "provider": "cninfo",
                "api": "hisAnnouncement/query",
                "code": code,
                "org_id": org_id,
                "seDate": se,
                "pageSize": 30,
            }
            got_total = 0
            err = "0"
            msg = "ok"
            attempt = 0
            code_rows: list[dict[str, Any]] = []
            for attempt in range(1, 4):
                page = 1
                code_rows = []
                try:
                    while page <= 40:
                        payload = {
                            "pageNum": str(page),
                            "pageSize": "30",
                            "column": "szse",
                            "tabName": "fulltext",
                            "plate": "",
                            "stock": stock,
                            "searchkey": "",
                            "secid": "",
                            "category": "",
                            "trade": "",
                            "seDate": se,
                            "sortName": "",
                            "sortType": "",
                            "isHLtitle": "false",
                        }
                        resp = session.post(url, data=payload, headers=headers, timeout=30)
                        if resp.status_code != 200:
                            raise RuntimeError(f"HTTP {resp.status_code}")
                        body = resp.json()
                        batch = body.get("announcements") or []
                        for item in batch:
                            ann_id = str(item.get("announcementId") or "")
                            title = (item.get("announcementTitle") or "").strip()
                            if not ann_id or not title:
                                continue
                            ts_ms = item.get("announcementTime")
                            try:
                                from datetime import datetime as _dt

                                pub = _dt.fromtimestamp(int(ts_ms) / 1000).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            except Exception:
                                continue
                            code_rows.append(
                                {
                                    "security_id": code,
                                    "stock_code": code,
                                    "ann_id": ann_id,
                                    "title": title,
                                    "publish_time": pub,
                                    "announce_date": pub[:10],
                                    "name": item.get("secName") or "",
                                    "url": (
                                        f"http://www.cninfo.com.cn/new/disclosure/detail?"
                                        f"stockCode={code}&announcementId={ann_id}"
                                    ),
                                    "source": "cninfo.hisAnnouncement",
                                    "acquired_at": acquired_at,
                                }
                            )
                        has_more = bool(body.get("hasMore"))
                        if has_more and page == 40:
                            raise RuntimeError("pagination limit reached with hasMore=true")
                        if not has_more or not batch:
                            break
                        page += 1
                except Exception as exc:  # noqa: BLE001
                    err = "1"
                    msg = str(exc)
                    if attempt < 3:
                        session.close()
                        session = requests.Session()
                        time.sleep(0.2 * attempt)
                        continue
                else:
                    got_total = len(code_rows)
                    err = "0"
                    msg = "ok"
                break

            previous = logs_by_code.get(code)
            retry_errors = list(previous.get("missing_set_retry_errors", [])) if previous else []
            total_attempts = int(previous.get("attempts", 0)) + attempt if previous else attempt
            if previous and str(previous.get("response_error_code")) != "0":
                retry_errors.append(
                    {
                        "scan_round": int(previous["scan_round"]),
                        "response_error_code": str(previous["response_error_code"]),
                        "response_error_msg": str(previous["response_error_msg"]),
                    }
                )
            logs_by_code[code] = {
                "security_id": code,
                "request": request,
                "attempts": total_attempts,
                "scan_round": scan_round,
                "missing_set_retry_errors": retry_errors,
                "response_error_code": err,
                "response_error_msg": msg,
                "response_rows": got_total,
                "acquired_at": acquired_at,
            }
            if err == "0":
                rows_by_code[code] = code_rows
            else:
                next_pending.append(code)
        pending = next_pending
        if not pending:
            break

    session.close()
    rows = [row for code in codes for row in rows_by_code.get(code, [])]
    logs = [logs_by_code[code] for code in codes]
    return rows, logs


def _read_announcements_for_date(db_path: Path, market_date: date) -> list[dict[str, Any]]:
    import sqlite3

    day = market_date.isoformat()
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM announcements WHERE substr(publish_time, 1, 10) = ?",
            (day,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _build_calendar_events(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    attempt_id: str | None = None,
) -> list[DatasetStatus]:
    rev = f"calendar-events-{market_date.strftime('%Y%m%d')}-v3"
    lookback = _event_lookback_days()
    prev = _load_event_watermark(paths)
    input_wm = prev.get("committed_watermark")
    # window: max(input_wm - 0, market_date - lookback) .. market_date
    start = market_date - timedelta(days=lookback)
    if input_wm:
        try:
            # re-sweep from committed day inclusive (overlap for upstream revisions)
            start = min(start, date.fromisoformat(str(input_wm)[:10]))
        except ValueError:
            start = start
    watermark = market_date.isoformat()
    mode = _acquire_mode()
    datasets: list[DatasetStatus] = []
    log_rows: list[dict[str, Any]] = []
    observed_max: str | None = None

    # Announcements: provider = cninfo live; local = sqlite/db or parquet with disclose keys
    if mode == "provider":
        codes = _universe_from_stock_list(paths.home)
        if codes:
            ann_rows, ann_logs = _cninfo_fetch_announcements(
                codes=codes,
                start=start,
                end=market_date,
            )
        else:
            ann_rows = []
            ann_logs = [
                {
                    "dataset": "announcements_delta",
                    "response_error_code": "1",
                    "response_error_msg": "empty in-scope universe from stock_list",
                    "response_rows": 0,
                    "acquired_at": _now_sh_iso(),
                }
            ]
        ann_rows = _filter_rows_by_date(
            ann_rows,
            market_date=market_date,
            date_keys=("publish_time", "announce_date"),
            lookback_days=(market_date - start).days,
        )
        log_rows.extend(ann_logs)
        failed_requests = [
            row for row in ann_logs if str(row.get("response_error_code")) != "0"
        ]
        for row in ann_rows:
            pub = str(row.get("publish_time") or row.get("announce_date") or "")
            if pub and (observed_max is None or pub > observed_max):
                observed_max = pub
        request = {
            "provider": "cninfo",
            "api": "hisAnnouncement/query",
            "window_start": start.isoformat(),
            "window_end": market_date.isoformat(),
            "universe_size": len(codes),
            "input_watermark": input_wm,
            "lookback_days": lookback,
        }
        if failed_requests:
            datasets.append(
                DatasetStatus(
                    name="announcements_delta",
                    status="failed",
                    row_count=len(ann_rows),
                    detail=(
                        "cninfo acquisition incomplete: "
                        f"{len(failed_requests)}/{len(ann_logs)} requests failed"
                    ),
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    source_request=json.dumps(request, sort_keys=True),
                )
            )
        else:
            datasets.append(
                _seal_dataset(
                    name="announcements_delta",
                    rows=ann_rows,
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    source_request=json.dumps(request, sort_keys=True),
                    require_rows=False,
                )
            )
    else:
        # local fixture path: read db/parquet with disclose-date filter + lookback
        source, keys = _event_source_map(paths)["announcements_delta"]
        request = {
            "provider": "local-event-fixture",
            "dataset": "announcements_delta",
            "path": str(source),
            "window_start": start.isoformat(),
            "window_end": market_date.isoformat(),
            "date_keys": list(keys),
            "input_watermark": input_wm,
            "lookback_days": lookback,
        }
        acquired_at = _now_sh_iso()
        if not source.exists():
            log_rows.append(
                {
                    "dataset": "announcements_delta",
                    "request": request,
                    "response_error_code": "1",
                    "response_error_msg": "source_absent",
                    "acquired_at": acquired_at,
                }
            )
            datasets.append(
                DatasetStatus(
                    name="announcements_delta",
                    status="failed",
                    detail=f"mandatory source absent: {source.name}",
                    source_request=json.dumps(request, sort_keys=True),
                )
            )
        else:
            if source.suffix == ".db":
                # inclusive lookback window on publish_time
                import sqlite3

                con = sqlite3.connect(str(source))
                try:
                    con.row_factory = sqlite3.Row
                    rows = con.execute(
                        "SELECT * FROM announcements "
                        "WHERE substr(publish_time, 1, 10) >= ? "
                        "AND substr(publish_time, 1, 10) <= ?",
                        (start.isoformat(), market_date.isoformat()),
                    ).fetchall()
                    ann_rows = [dict(row) for row in rows]
                finally:
                    con.close()
            else:
                ann_rows = _filter_rows_by_date(
                    _read_parquet(source),
                    market_date=market_date,
                    date_keys=keys,
                    lookback_days=lookback,
                )
            for row in ann_rows:
                row["acquired_at"] = acquired_at
                pub = str(
                    row.get("publish_time")
                    or row.get("announce_date")
                    or row.get("notice_date")
                    or ""
                )
                if pub and (observed_max is None or pub > observed_max):
                    observed_max = pub
            log_rows.append(
                {
                    "dataset": "announcements_delta",
                    "request": request,
                    "response_error_code": "0",
                    "response_error_msg": "ok",
                    "response_rows": len(ann_rows),
                    "acquired_at": acquired_at,
                }
            )
            datasets.append(
                _seal_dataset(
                    name="announcements_delta",
                    rows=ann_rows,
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    source_request=json.dumps(request, sort_keys=True),
                    require_rows=False,
                )
            )

    # Remaining event datasets must come from this attempt in provider mode.
    for name, (source, keys) in _event_source_map(paths).items():
        if name == "announcements_delta":
            continue
        if mode == "provider":
            acquired_at = _now_sh_iso()
            raw_output = landing / "provider_raw" / f"{name}.parquet"
            try:
                rows, request = _run_configured_provider_collector(
                    lane_id="calendar-daily-events",
                    dataset=name,
                    output=raw_output,
                    paths=paths,
                    market_date=market_date,
                    window_start=start,
                )
                rows = _filter_rows_by_date(
                    rows,
                    market_date=market_date,
                    date_keys=keys,
                    lookback_days=(market_date - start).days,
                )
                request["selected_row_count"] = len(rows)
            except Exception as exc:  # noqa: BLE001
                request = {
                    "provider": "configured-command",
                    "dataset": name,
                    "output": str(raw_output),
                    "window_start": start.isoformat(),
                    "window_end": market_date.isoformat(),
                }
                log_rows.append(
                    {
                        "dataset": name,
                        "request": request,
                        "response_error_code": "1",
                        "response_error_msg": str(exc),
                        "acquired_at": acquired_at,
                    }
                )
                datasets.append(
                    DatasetStatus(
                        name=name,
                        status="failed",
                        detail=str(exc),
                        source_revision=rev,
                        knowledge_cutoff=knowledge_cutoff,
                        watermark=watermark,
                        source_request=json.dumps(request, sort_keys=True),
                    )
                )
                continue
            for row in rows:
                row["acquired_at"] = acquired_at
                for key in keys:
                    value = row.get(key)
                    if value is None:
                        continue
                    observed = str(value)
                    if observed_max is None or observed > observed_max:
                        observed_max = observed
                    break
            log_rows.append(
                {
                    "dataset": name,
                    "request": request,
                    "response_error_code": "0",
                    "response_error_msg": "ok",
                    "response_rows": len(rows),
                    "acquired_at": acquired_at,
                }
            )
            datasets.append(
                _seal_dataset(
                    name=name,
                    rows=rows,
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    source_request=json.dumps(request, sort_keys=True, default=str),
                    require_rows=False,
                )
            )
            continue
        request = {
            "provider": "local-event-sot",
            "dataset": name,
            "path": str(source),
            "window_start": start.isoformat(),
            "window_end": market_date.isoformat(),
            "date_keys": list(keys),
            "input_watermark": input_wm,
            "lookback_days": lookback,
            "note": (
                "ex_date/effective retained as business fields; "
                "filter uses disclose keys first"
            ),
        }
        acquired_at = _now_sh_iso()
        if not source.exists():
            log_rows.append(
                {
                    "dataset": name,
                    "request": request,
                    "response_error_code": "1",
                    "response_error_msg": "source_absent",
                    "acquired_at": acquired_at,
                }
            )
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="failed",
                    detail=f"mandatory source absent: {source.name}",
                    source_request=json.dumps(request, sort_keys=True),
                )
            )
            continue
        rows = _filter_rows_by_date(
            _read_parquet(source),
            market_date=market_date,
            date_keys=keys,
            lookback_days=lookback,
        )
        for row in rows:
            row["acquired_at"] = acquired_at
            row["source_request"] = name
            for key in (
                "announce_date",
                "notice_date",
                "publish_time",
                "publish_deadline",
            ):
                val = row.get(key)
                if val is None:
                    continue
                pub = str(val)
                if observed_max is None or pub > observed_max:
                    observed_max = pub
                break
        log_rows.append(
            {
                "dataset": name,
                "request": request,
                "response_error_code": "0",
                "response_error_msg": "ok",
                "response_rows": len(rows),
                "acquired_at": acquired_at,
            }
        )
        datasets.append(
            _seal_dataset(
                name=name,
                rows=rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                source_request=json.dumps(request, sort_keys=True),
                require_rows=False,
            )
        )

    datasets.append(
        _seal_json_dataset(
            name="acquisition_log",
            payload={
                "lane": "calendar-daily-events",
                "market_date": watermark,
                "acquire_mode": mode,
                "input_watermark": input_wm,
                "observed_maximum": observed_max,
                "lookback_days": lookback,
                "window_start": start.isoformat(),
                "requests": log_rows,
                "source_revision": rev,
            },
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
        )
    )
    # stash observed for caller via sidecar meta (committed only after READY)
    _atomic_json(
        landing / "_event_watermark_pending.json",
        {
            "input_watermark": input_wm,
            "observed_maximum": observed_max or market_date.isoformat(),
            "attempt_id": attempt_id,
            "market_date": market_date.isoformat(),
        },
    )
    return datasets


def _session_dates(calendar: Path, market_date: date, n: int) -> list[date]:
    if not calendar.is_file():
        return [market_date - timedelta(days=i) for i in range(n)]
    dates: list[date] = []
    for row in _read_parquet(calendar):
        if str(row.get("exchange") or "").upper() != "SSE":
            continue
        if int(row.get("is_open") or 0) != 1:
            continue
        value = row.get("trade_date") or row.get("date")
        if value is None:
            continue
        if hasattr(value, "date"):
            value = value.date()
        day = date.fromisoformat(str(value)[:10])
        if day <= market_date:
            dates.append(day)
    dates = sorted(set(dates), reverse=True)
    return dates[:n]


def _row_fingerprint(row: Mapping[str, Any], value_keys: Sequence[str]) -> str:
    parts = []
    for key in value_keys:
        parts.append(f"{key}={row.get(key)}")
    return "|".join(parts)


def _classify_keys(
    daily: dict[str, str],
    upstream: dict[str, str],
) -> list[dict[str, Any]]:
    keys = sorted(set(daily) | set(upstream))
    out: list[dict[str, Any]] = []
    for key in keys:
        d = daily.get(key)
        u = upstream.get(key)
        if d is None and u is not None:
            klass = "missing_in_daily"
        elif d is not None and u is None:
            # present only in daily: treat as conflict with vanished upstream
            klass = "conflict"
        elif d == u:
            klass = "same"
        else:
            # both present, values differ — changed_upstream unless both claim authority
            klass = "changed_upstream"
            if d is not None and u is not None and d.split("=", 1)[0] != u.split("=", 1)[0]:
                klass = "conflict"
        out.append({"key": key, "class": klass, "daily": d, "upstream": u})
    return out


def _build_weekly_fundamentals(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    source_revision: str,
) -> list[DatasetStatus]:
    watermark = market_date.isoformat()
    mode = _acquire_mode()
    datasets: list[DatasetStatus] = []
    receipt_rows: list[dict[str, Any]] = []

    if mode != "provider":
        local_sources = {
            "raw_f10": paths.home / "raw_f10.parquet",
            "financial_statements": paths.home / "financial_statements.parquet",
            "financial_quarterly": paths.home / "financial_quarterly.parquet",
            "industry_membership": paths.home / "ref_industry_map.parquet",
            "fundamentals_receipts": paths.home / "fundamentals_receipts.parquet",
        }
        for name, source in local_sources.items():
            datasets.append(
                _copy_dataset(
                    name=name,
                    source=source,
                    landing=landing,
                    source_revision=source_revision,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    relative_name=(
                        "industry_membership_revision.parquet"
                        if name == "industry_membership"
                        else f"{name}.parquet"
                    ),
                    source_request=f"copy:{source.name}",
                    require_rows=True,
                )
            )
        return datasets

    collector_meta: dict[str, dict[str, Any]] = {}
    for name in ("raw_f10", "financial_statements", "industry_membership"):
        output = landing / f"{name}.parquet"
        try:
            _rows, request = _run_configured_provider_collector(
                lane_id="weekly-corrections",
                dataset=name,
                output=output,
                paths=paths,
                market_date=market_date,
                window_start=market_date,
            )
            collector_meta[name] = request
            receipt_rows.extend(_read_parquet(Path(str(request["receipts"]))))
            datasets.append(
                _register_parquet_dataset(
                    name=name,
                    path=output,
                    source_revision=f"{name}@{source_revision}",
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                    source_request=json.dumps(request, sort_keys=True, default=str),
                    require_rows=True,
                )
            )
            if name == "financial_statements":
                datasets.append(
                    _register_parquet_dataset(
                        name="financial_quarterly",
                        path=landing / "financial_quarterly.parquet",
                        source_revision=f"financial_quarterly@{source_revision}",
                        knowledge_cutoff=knowledge_cutoff,
                        watermark=watermark,
                        source_request=(
                            "derived from complete income-statement provider payloads; "
                            f"statement_sha256={request['sha256']}"
                        ),
                        require_rows=True,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - each missing core asset blocks READY
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="failed",
                    detail=str(exc),
                    source_revision=source_revision,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                )
            )
            if name == "financial_statements":
                datasets.append(
                    DatasetStatus(
                        name="financial_quarterly",
                        status="failed",
                        detail="full financial statement acquisition failed",
                        source_revision=source_revision,
                        knowledge_cutoff=knowledge_cutoff,
                        watermark=watermark,
                    )
                )

    datasets.append(
        _seal_dataset(
            name="fundamentals_receipts",
            rows=receipt_rows,
            landing=landing,
            source_revision=f"per-security-receipts@{source_revision}",
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
            source_request=json.dumps(
                {
                    "contract": "per_security_v1",
                    "collectors": collector_meta,
                    "status_semantics": [
                        "success_nonempty",
                        "justified_empty",
                        "failed",
                    ],
                },
                sort_keys=True,
                default=str,
            ),
            require_rows=True,
        )
    )
    return datasets


def _build_weekly_corrections(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
) -> list[DatasetStatus]:
    rev = f"hermes-weekly-corrections-{market_date.strftime('%Y%m%d')}-v2"
    watermark = market_date.isoformat()
    datasets: list[DatasetStatus] = []
    mode = _acquire_mode()

    primary = [
        (
            "shareholder_count",
            paths.home / "ref_shareholder_count.parquet",
            ("hold_notice_date", "notice_date", "disclosure_date", "ann_date", "end_date"),
            90,
            "shareholder_count_revision.parquet",
            ("code", "end_date"),
            ("shareholder_count", "change_num"),
        ),
        (
            "corporate_actions",
            paths.home / "ref_corporate_actions.parquet",
            ("ex_date", "announce_date", "effective_date", "date"),
            365,
            "corporate_actions_revision.parquet",
            ("stock_code", "ex_date", "action_type"),
            ("cash_per_share", "bonus_shares", "transfer_shares"),
        ),
        (
            "dividends",
            paths.home / "dividend_history.parquet",
            ("ex_date", "announce_date", "ex_dividend_date", "date"),
            365,
            "dividends_revision.parquet",
            ("stock_code", "ex_dividend_date", "announce_date"),
            ("dividend_per_share", "bonus_shares"),
        ),
        (
            "adjustment_evidence",
            paths.home / "ref_adjustment_factors.parquet",
            ("effective_date", "ex_date", "date"),
            365,
            "adjustment_evidence_revision.parquet",
            ("stock_code", "effective_date"),
            ("fore_adjust_factor", "back_adjust_factor", "adjust_factor"),
        ),
    ]

    for name, source, keys, lookback, out_name, _id_keys, _value_keys in primary:
        request: dict[str, Any] | None = None
        if mode == "provider":
            raw_output = landing / "provider_raw" / f"{name}.parquet"
            try:
                selected, request = _run_configured_provider_collector(
                    lane_id="weekly-corrections",
                    dataset=name,
                    output=raw_output,
                    paths=paths,
                    market_date=market_date,
                    window_start=market_date - timedelta(days=lookback),
                )
                selected = _filter_rows_by_date(
                    selected,
                    market_date=market_date,
                    date_keys=keys,
                    lookback_days=lookback,
                )
                request["selected_row_count"] = len(selected)
            except Exception as exc:  # noqa: BLE001
                datasets.append(
                    DatasetStatus(
                        name=name,
                        status="failed",
                        detail=str(exc),
                        source_revision=rev,
                        knowledge_cutoff=knowledge_cutoff,
                        watermark=watermark,
                    )
                )
                continue
        elif source.is_file():
            selected = _filter_rows_by_date(
                _read_parquet(source),
                market_date=market_date,
                date_keys=keys,
                lookback_days=lookback,
            )
        else:
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="failed",
                    detail=f"missing {source.name}",
                )
            )
            continue
        for row in selected:
            row["revision_retained"] = True
            row["lookback_days"] = lookback
            row["watermark_date"] = watermark
            row["acquired_at"] = knowledge_cutoff
        datasets.append(
            _seal_dataset(
                name=name,
                rows=selected,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name=out_name,
                require_rows=False,
                source_request=(
                    json.dumps(request, sort_keys=True, default=str) if request else None
                ),
            )
        )

    st_request: dict[str, Any] | None = None
    st_path = paths.home / "ref_st_status_pit.parquet"
    if mode == "provider":
        try:
            selected, st_request = _run_configured_provider_collector(
                lane_id="weekly-corrections",
                dataset="st_announcement_trail",
                output=landing / "provider_raw" / "st_announcement_trail.parquet",
                paths=paths,
                market_date=market_date,
                window_start=market_date - timedelta(days=30),
            )
            selected = _filter_rows_by_date(
                selected,
                market_date=market_date,
                date_keys=("date", "effective_start", "announcement_date"),
                lookback_days=30,
            )
            st_request["selected_row_count"] = len(selected)
        except Exception as exc:  # noqa: BLE001
            datasets.append(
                DatasetStatus(
                    name="st_announcement_trail",
                    status="failed",
                    detail=str(exc),
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                )
            )
            selected = None
    else:
        if not st_path.is_file():
            st_path = (
                paths.home
                / "revisions"
                / "st_status_pit_v2_baostock_20260725"
                / "daily_isST.parquet"
            )
        selected = (
            _filter_rows_by_date(
                _read_parquet(st_path),
                market_date=market_date,
                date_keys=("date", "effective_start", "announcement_date"),
                lookback_days=30,
            )
            if st_path.is_file()
            else None
        )
    if selected is not None:
        datasets.append(
            _seal_dataset(
                name="st_announcement_trail",
                rows=selected,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name="st_status_observation_revision.parquet",
                source_request=(
                    json.dumps(st_request, sort_keys=True, default=str)
                    if st_request
                    else None
                ),
            )
        )
    elif not any(item.name == "st_announcement_trail" for item in datasets):
        datasets.append(
            DatasetStatus(
                name="st_announcement_trail",
                status="failed",
                detail="missing st status source",
            )
        )

    datasets.extend(
        _build_weekly_fundamentals(
            paths=paths,
            market_date=market_date,
            landing=landing,
            knowledge_cutoff=knowledge_cutoff,
            source_revision=rev,
        )
    )

    for name, source in (
        ("security_lifecycle", paths.home / "ref_security_master.parquet"),
    ):
        if source.is_file():
            datasets.append(
                _copy_dataset(
                    name=name,
                    source=source,
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                )
            )
        else:
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="failed",
                    detail=f"missing {source.name}",
                )
            )

    # Independent correction revision vs published daily revision (not same-file self-compare).
    session_days = _session_dates(paths.calendar, market_date, 20)
    none_path = paths.home / "daily_ohlcv_none.parquet"
    key_rows: list[dict[str, Any]] = []
    counts = {
        "same": 0,
        "missing_in_daily": 0,
        "changed_upstream": 0,
        "conflict": 0,
    }
    correction_rows: list[dict[str, Any]] = []
    correction_meta: dict[str, Any] = {
        "provider": None,
        "window_sessions": [day.isoformat() for day in session_days],
    }
    correction_error: str | None = None
    if none_path.is_file() and session_days:
        daily_map: dict[str, str] = {}
        upstream_map: dict[str, str] = {}
        max_keys_raw = os.environ.get("HERMES_ODS_WEEKLY_RECON_MAX_KEYS", "").strip()
        if mode == "provider" and max_keys_raw:
            correction_error = (
                "HERMES_ODS_WEEKLY_RECON_MAX_KEYS is smoke-only and forbidden "
                "for provider weekly publication"
            )
        max_keys = None if mode == "provider" else int(max_keys_raw or "20000")
        try:
            if correction_error:
                raise LaneRunError(correction_error)
            import pyarrow as pa
            import pyarrow.compute as pc
            import pyarrow.parquet as pq

            day_strs = [day.isoformat() for day in session_days]
            try:
                table = pq.read_table(
                    none_path,
                    columns=[
                        "stock_code",
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    ],
                    filters=[("date", "in", day_strs)],
                )
            except Exception:
                table = pq.read_table(
                    none_path,
                    columns=[
                        "stock_code",
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    ],
                )
                date_col = table.column("date")
                as_str = pc.utf8_slice_codeunits(pc.cast(date_col, pa.string()), 0, 10)
                mask = pc.is_in(as_str, value_set=pa.array(day_strs))
                table = table.filter(mask)

            if max_keys is not None and table.num_rows > max_keys:
                table = table.slice(0, max_keys)
            published_rows = table.to_pylist()
            for row in published_rows:
                code = _code6(row.get("stock_code"))
                value = row.get("date")
                if value is None:
                    continue
                if hasattr(value, "date"):
                    value = value.date()
                day = date.fromisoformat(str(value)[:10])
                if day not in session_days:
                    continue
                key = f"{code}|{day.isoformat()}"
                daily_map[key] = _row_fingerprint(
                    row, ("open", "high", "low", "close", "volume")
                )

            # Build independent upstream correction revision for same window.
            codes = sorted(
                {
                    _code6(r.get("stock_code"))
                    for r in published_rows
                    if r.get("stock_code")
                }
            )
            if not codes:
                codes = _universe_from_stock_list(paths.home)
            if mode == "provider" and not codes:
                raise LaneRunError("empty in-scope universe for weekly correction")
            start_day = min(session_days)
            end_day = max(session_days)
            correction_meta.update(
                {
                    "window_start": start_day.isoformat(),
                    "window_end": end_day.isoformat(),
                    "universe_size": len(codes),
                    "acquire_mode": mode,
                }
            )
            if mode == "provider":
                # Re-fetch baostock window as independent raw correction revision.
                import baostock as bs

                login = bs.login()
                if str(login.error_code) != "0":
                    raise LaneRunError(
                        "baostock login failed for weekly correction: "
                        f"{login.error_msg}"
                    )
                set_socket_timeout(30)
                fields = "date,code,open,high,low,close,volume,amount"
                acquired_at = _now_sh_iso()
                try:
                    for code in codes:
                        bs_code = _to_bs_code(code)
                        rs = bs.query_history_k_data_plus(
                            bs_code,
                            fields,
                            start_date=start_day.isoformat(),
                            end_date=end_day.isoformat(),
                            frequency="d",
                            adjustflag="3",
                        )
                        if str(rs.error_code) != "0":
                            raise LaneRunError(
                                f"baostock weekly correction failed for {code}: "
                                f"{rs.error_code} {rs.error_msg}"
                            )
                        while rs.next():
                            raw = rs.get_row_data()
                            day = str(raw[0])[:10]
                            correction_rows.append(
                                {
                                    "stock_code": code,
                                    "security_id": code,
                                    "date": day,
                                    "open": _fnum(raw[2]),
                                    "high": _fnum(raw[3]),
                                    "low": _fnum(raw[4]),
                                    "close": _fnum(raw[5]),
                                    "volume": _fnum(raw[6], 0.0) or 0.0,
                                    "source": "baostock.weekly_correction",
                                    "acquired_at": acquired_at,
                                    "revision": rev,
                                }
                            )
                finally:
                    bs.logout()
                if not correction_rows:
                    raise LaneRunError("weekly provider returned zero correction rows")
                correction_meta["provider"] = "baostock.query_history_k_data_plus"
            else:
                # local: independent correction revision derived from published snapshot
                # plus explicit mutation channel (tests) — never identical pointer compare.
                acquired_at = _now_sh_iso()
                for row in published_rows:
                    item = dict(row)
                    item["source"] = "local.weekly_correction_revision"
                    item["acquired_at"] = acquired_at
                    item["revision"] = rev
                    # normalize volume None -> 0 to create a distinct revision object
                    if item.get("volume") is None:
                        item["volume"] = 0.0
                    correction_rows.append(item)
                correction_meta["provider"] = "local.independent_correction_revision"

            for row in correction_rows:
                code = _code6(row.get("stock_code") or row.get("security_id"))
                value = row.get("date")
                if value is None:
                    continue
                if hasattr(value, "date"):
                    value = value.date()
                day = date.fromisoformat(str(value)[:10])
                if day not in session_days:
                    continue
                key = f"{code}|{day.isoformat()}"
                upstream_map[key] = _row_fingerprint(
                    row, ("open", "high", "low", "close", "volume")
                )

            # Optional demo inject for unit tests only (not Hermes acceptance evidence).
            inject = os.environ.get("HERMES_ODS_WEEKLY_INJECT", "").strip()
            if inject == "demo" and daily_map:
                sample_key = sorted(daily_map)[0]
                upstream_map[sample_key] = daily_map[sample_key] + "|mutated"
                upstream_map[f"999999|{market_date.isoformat()}"] = "open=1"
                correction_rows.append(
                    {
                        "stock_code": "999999",
                        "date": market_date.isoformat(),
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "source": "demo.inject",
                        "revision": rev,
                    }
                )

            classified = _classify_keys(daily_map, upstream_map)
            key_rows.extend(classified)
            for item in classified:
                counts[item["class"]] = counts.get(item["class"], 0) + 1
        except Exception as exc:  # noqa: BLE001
            correction_error = str(exc)
    else:
        correction_error = "none_path_or_sessions_missing"

    # Seal independent correction revision artifact (required evidence).
    if correction_error:
        correction_meta["error"] = correction_error
        datasets.append(
            DatasetStatus(
                name="weekly_correction_revision",
                status="failed",
                detail=correction_error,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                source_request=json.dumps(correction_meta, sort_keys=True, default=str),
            )
        )
    else:
        datasets.append(
            _seal_dataset(
                name="weekly_correction_revision",
                rows=correction_rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name="weekly_correction_revision.parquet",
                source_request=json.dumps(correction_meta, sort_keys=True, default=str),
                require_rows=True,
            )
        )
    datasets.append(
        _seal_json_dataset(
            name="weekly_correction_meta",
            payload=correction_meta,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
        )
    )

    recon = {
        "market_date": watermark,
        "classes": ["same", "missing_in_daily", "changed_upstream", "conflict"],
        "windows": {
            "daily_ohlcv_none": "20_sessions",
            "daily_status_observation": "90_days",
            "index_daily": "20_sessions",
            "trading_calendar": "full_diff",
            "security_master": "full_diff",
        },
        "session_days": [day.isoformat() for day in session_days],
        "may_overwrite_daily": False,
        "counts": counts,
        "key_count": len(key_rows),
        "source_revision": rev,
    }
    datasets.append(
        _seal_json_dataset(
            name="reconciliation_summary",
            payload=recon,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
        )
    )
    if correction_error:
        datasets.append(
            DatasetStatus(
                name="reconciliation_keys",
                status="failed",
                detail=f"acquisition failed: {correction_error}",
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
            )
        )
    else:
        datasets.append(
            _seal_dataset(
                name="reconciliation_keys",
                rows=key_rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name="reconciliation_keys.parquet",
                require_rows=True,
            )
        )
    return datasets


def _expected_last_bar(market_date: date, bar_size: str) -> str:
    # A-share close 15:00; 5m last bar 15:00, 1m last bar 15:00.
    return f"{market_date.isoformat()} 15:00:00"


def _expected_bar_timestamps(market_date: date, bar_size: str) -> set[str]:
    if bar_size == "1m":
        step = timedelta(minutes=1)
        sessions = (((9, 31), (11, 30)), ((13, 1), (15, 0)))
    elif bar_size == "5m":
        step = timedelta(minutes=5)
        sessions = (((9, 35), (11, 30)), ((13, 5), (15, 0)))
    else:
        raise LaneRunError(f"unsupported intraday bar size: {bar_size}")

    timestamps: set[str] = set()
    for (start_hour, start_minute), (end_hour, end_minute) in sessions:
        current = datetime.combine(
            market_date, datetime.min.time(), tzinfo=SH
        ).replace(hour=start_hour, minute=start_minute)
        end = current.replace(hour=end_hour, minute=end_minute)
        while current <= end:
            timestamps.add(current.strftime("%Y-%m-%d %H:%M:%S"))
            current += step
    return timestamps


def _latest_market_artifact(
    *,
    paths: LanePaths,
    market_date: date,
    available_at: datetime,
    artifact_name: str,
) -> tuple[Path, dict[str, Any]] | None:
    """Return same-day evidence from the latest verified market release."""

    if available_at.tzinfo is None or available_at.utcoffset() is None:
        return None

    release_root = (
        paths.shadow_root / "releases" / "daily" / market_date.isoformat()
    )
    if not release_root.is_dir():
        return None

    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for release_dir in release_root.iterdir():
        if not release_dir.is_dir():
            continue
        try:
            verified = verify_ods_release(release_dir)
        except OdsReleaseError:
            continue
        release = verified.release
        if (
            release.market_date != market_date
            or not release.release_id.startswith("hermes-market-daily-core-")
            or release.created_at > available_at
        ):
            continue
        artifact = next(
            (
                item
                for item in release.artifacts
                if item.name == artifact_name
            ),
            None,
        )
        if artifact is None or artifact.knowledge_cutoff > available_at:
            continue
        candidates.append(
            (
                release.created_at,
                release_dir.joinpath(*artifact.relative_path.parts),
                {
                    "release_id": release.release_id,
                    "release_dir": str(release_dir),
                    "artifact_sha256": artifact.sha256,
                    "knowledge_cutoff": artifact.knowledge_cutoff.isoformat(),
                },
            )
        )

    if not candidates:
        return None
    _, artifact_path, metadata = max(candidates, key=lambda item: item[0])
    return artifact_path, metadata


def _first_iso_date(*values: Any) -> str:
    """Return the first real ISO date, ignoring null-like temporal scalars."""

    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text.lower() in {"", "nat", "nan", "none", "null", "<na>"}:
            continue
        candidate = text[:10]
        try:
            date.fromisoformat(candidate)
        except ValueError:
            continue
        return candidate
    return ""


def _minute_no_data_evidence(
    *,
    paths: LanePaths,
    market_date: date,
    universe: Sequence[str],
    evidence_available_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Index auditable same-day reasons for an in-scope symbol to have no bars."""

    target = market_date.isoformat()
    wanted = {_code6(code) for code in universe}
    evidence: dict[str, dict[str, Any]] = {}

    master = paths.home / "ref_security_master.parquet"
    if master.is_file():
        for row in _read_parquet(master):
            code = _code6(row.get("security_id") or row.get("stock_code") or row.get("code"))
            if code not in wanted:
                continue
            list_date = _first_iso_date(row.get("list_date"), row.get("ipo_date"))
            delist_date = _first_iso_date(
                row.get("delist_date"), row.get("terminal_date")
            )
            if list_date and list_date > target:
                evidence[code] = {
                    "reason": "not_yet_listed",
                    "source": master.name,
                    "list_date": list_date,
                }
            elif delist_date and delist_date < target:
                evidence[code] = {
                    "reason": "already_delisted",
                    "source": master.name,
                    "delist_date": delist_date,
                }

    status_sources: list[tuple[Path, dict[str, Any]]] = []
    status_path = paths.home / "daily_status_observation.parquet"
    if status_path.is_file():
        status_sources.append((status_path, {}))
    market_status = _latest_market_artifact(
        paths=paths,
        market_date=market_date,
        available_at=evidence_available_at,
        artifact_name="daily_status_observation_delta",
    )
    if market_status is not None:
        status_sources.append(market_status)

    # Later sources take precedence, so a verified same-day market release wins
    # over a stale node-local snapshot without mutating either artifact.
    status_rows: dict[str, tuple[Mapping[str, Any], Path, dict[str, Any]]] = {}
    for candidate_path, metadata in status_sources:
        for row in _read_parquet(candidate_path):
            evidence_date = str(row.get("market_date") or row.get("date") or "")[:10]
            if evidence_date != target:
                continue
            code = _code6(row.get("security_id") or row.get("stock_code") or row.get("code"))
            if code not in wanted:
                continue
            status_rows[code] = (row, candidate_path, metadata)
    for code, (row, candidate_path, metadata) in status_rows.items():
        tradestatus = str(row.get("tradestatus") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        is_tradable = row.get("is_tradable")
        blocked = (
            tradestatus in {"0", "false", "suspended"}
            or is_tradable in {0, "0", "false"}
            or status in {"suspended", "not_executable", "delisted"}
        )
        if blocked:
            evidence[code] = {
                "reason": status or "daily_status_not_tradable",
                "source": candidate_path.name,
                "evidence_date": target,
                "tradestatus": row.get("tradestatus"),
                "is_tradable": is_tradable,
                **metadata,
            }

    daily_sources: list[tuple[Path, dict[str, Any]]] = []
    daily_path = paths.home / "daily_ohlcv_none.parquet"
    if daily_path.is_file():
        daily_sources.append((daily_path, {"source_sha256": _sha256(daily_path)}))
    market_daily = _latest_market_artifact(
        paths=paths,
        market_date=market_date,
        available_at=evidence_available_at,
        artifact_name="daily_ohlcv_none_delta",
    )
    if market_daily is not None:
        daily_sources.append(market_daily)

    # A same-day accepted daily row with exactly zero volume and amount is
    # structured evidence that no minute trades should exist. A verified
    # immutable market release takes precedence over the mutable local SoT.
    zero_turnover_rows: dict[str, tuple[Mapping[str, Any], Path, dict[str, Any]]] = {}
    for candidate_path, metadata in daily_sources:
        for row in _read_parquet_filter_date(candidate_path, market_date=market_date):
            code = _code6(row.get("security_id") or row.get("stock_code") or row.get("code"))
            if code not in wanted:
                continue
            volume = _fnum(row.get("volume"))
            amount = _fnum(row.get("amount"))
            if volume == 0.0 and amount == 0.0:
                zero_turnover_rows[code] = (row, candidate_path, metadata)
    for code, (row, candidate_path, metadata) in zero_turnover_rows.items():
        if code in evidence:
            continue
        evidence[code] = {
            "reason": "zero_turnover_daily_bar",
            "source": candidate_path.name,
            "evidence_date": target,
            "volume": row.get("volume"),
            "amount": row.get("amount"),
            "close": row.get("close"),
            **metadata,
        }

    suspension_path = paths.home / "ref_suspensions.parquet"
    if not suspension_path.is_file():
        suspension_path = paths.home / "suspensions.parquet"
    if suspension_path.is_file():
        for row in _read_parquet(suspension_path):
            code = _code6(row.get("security_id") or row.get("stock_code") or row.get("code"))
            if code not in wanted:
                continue
            start = _first_iso_date(
                row.get("suspend_start"), row.get("start_date"), row.get("date")
            )
            end = _first_iso_date(
                row.get("suspend_end"), row.get("resume_date"), row.get("end_date")
            )
            known = _first_iso_date(
                row.get("knowledge_cutoff"),
                row.get("announce_date"),
                row.get("notice_date"),
            )
            active_on_target = bool(start and start <= target and (not end or target <= end))
            if active_on_target and known and known <= target:
                evidence[code] = {
                    "reason": "suspended",
                    "source": suspension_path.name,
                    "suspend_start": start,
                    "suspend_end": end or None,
                    "knowledge_cutoff": known,
                }
    return evidence


def _minute_collector_fetch_mode(
    *,
    market_date: date,
    bar_size: str,
    as_of: date | None = None,
) -> tuple[str, int | None]:
    """Resolve minute_backfill ``--mode`` / ``--max-pages`` from env.

    Production default is always ``incr`` (single TDX page, PAGE=800 newest
    bars). Archive multi-day soaks that need older sessions must set both::

        HERMES_ODS_MINUTE_MODE=backfill   # or incr
        HERMES_ODS_MINUTE_MAX_PAGES=3     # required for backfill; ignored when empty on incr

    There is no automatic window-based mode switch: operators choose explicitly
    so canaries stay on ``incr`` and matrix soaks pin ``backfill`` + pages.
    ``market_date`` / ``bar_size`` / ``as_of`` are accepted for call-site
    compatibility and future validation only.
    """

    del market_date, as_of  # reserved; mode is env-explicit
    if bar_size not in {"1m", "5m"}:
        raise LaneRunError(f"unsupported intraday bar_size: {bar_size}")

    mode = (os.environ.get("HERMES_ODS_MINUTE_MODE") or "incr").strip().lower()
    if mode not in {"incr", "backfill"}:
        raise LaneRunError(
            "HERMES_ODS_MINUTE_MODE must be 'incr' or 'backfill' "
            f"(got {mode!r})"
        )

    pages_raw = (os.environ.get("HERMES_ODS_MINUTE_MAX_PAGES") or "").strip()
    if mode == "incr":
        # incr is always one page; a stale backfill page setting has no effect.
        return "incr", None

    if not pages_raw:
        raise LaneRunError(
            "HERMES_ODS_MINUTE_MAX_PAGES is required when "
            "HERMES_ODS_MINUTE_MODE=backfill"
        )
    try:
        max_pages = int(pages_raw)
    except ValueError as exc:
        raise LaneRunError(
            "HERMES_ODS_MINUTE_MAX_PAGES must be an integer when "
            "HERMES_ODS_MINUTE_MODE=backfill"
        ) from exc
    if max_pages < 1:
        raise LaneRunError(
            f"HERMES_ODS_MINUTE_MAX_PAGES must be >= 1 (got {max_pages})"
        )
    try:
        cap = int(os.environ.get("HERMES_ODS_MINUTE_MAX_PAGES_CAP", "50"))
    except ValueError as exc:
        raise LaneRunError("HERMES_ODS_MINUTE_MAX_PAGES_CAP must be an integer") from exc
    if cap < 1:
        raise LaneRunError(f"HERMES_ODS_MINUTE_MAX_PAGES_CAP must be >= 1 (got {cap})")
    if max_pages > cap:
        raise LaneRunError(
            "HERMES_ODS_MINUTE_MAX_PAGES exceeds the configured safety cap: "
            f"{max_pages} > {cap}"
        )
    return "backfill", max_pages


_MINUTE_COLLECTOR_DEFAULT_TIMEOUT_SECONDS = 1800
_MINUTE_COLLECTOR_MAX_TIMEOUT_SECONDS = 4 * 60 * 60


def _minute_collector_timeout_seconds() -> int:
    """Read a bounded child-process timeout that fits within the systemd unit."""

    raw = os.environ.get(
        "HERMES_ODS_MINUTE_TIMEOUT_SEC", str(_MINUTE_COLLECTOR_DEFAULT_TIMEOUT_SECONDS)
    )
    try:
        timeout_seconds = int(raw)
    except ValueError as exc:
        raise LaneRunError("HERMES_ODS_MINUTE_TIMEOUT_SEC must be an integer") from exc
    if timeout_seconds < 60:
        raise LaneRunError("HERMES_ODS_MINUTE_TIMEOUT_SEC must be at least 60")
    if timeout_seconds > _MINUTE_COLLECTOR_MAX_TIMEOUT_SECONDS:
        raise LaneRunError("HERMES_ODS_MINUTE_TIMEOUT_SEC exceeds the 4-hour service timeout")
    return timeout_seconds


def _run_minute_collector(
    *,
    paths: LanePaths,
    market_date: date,
    bar_size: str,
    symbols: Sequence[str],
    raw_home: Path,
) -> dict[str, Any]:
    """Invoke real minute collector (mootdx/TDX backfill) for this attempt's symbols."""

    minute_root = Path(
        os.environ.get("ASTOCK_MINUTE_ROOT") or (paths.home.parent / "minute")
    ).resolve()
    layer = "research" if bar_size == "1m" else "prod"
    py = (
        os.environ.get("ASTOCK_MINUTE_PYTHON")
        or os.environ.get("ASTOCK_PYTHON")
        or sys.executable
    )
    env = os.environ.copy()
    env["ASTOCK_ROOT"] = str(paths.home.parent)
    env["ASTOCK_HOME"] = str(paths.home)
    env["ASTOCK_MINUTE_ROOT"] = str(minute_root)
    fetch_mode, max_pages = _minute_collector_fetch_mode(
        market_date=market_date, bar_size=bar_size
    )
    seed_raw = os.environ.get("HERMES_ODS_MINUTE_SEED_HOME", "").strip()
    collector_override = os.environ.get("HERMES_ODS_MINUTE_COLLECTOR", "").strip()
    policy_raw = (
        os.environ.get("HERMES_ODS_MINUTE_1M_RECOVERY_POLICY", "").strip()
        if bar_size == "1m" else ""
    )
    recovery_policy = None
    policy_path = None
    if policy_raw:
        if seed_raw or collector_override or fetch_mode != "incr":
            raise LaneRunError("minute recovery policy requires repository 1m incr without seed")
        policy_path = Path(policy_raw).resolve()
        try:
            recovery_policy = load_minute_recovery_policy(policy_path)
        except (OSError, ValueError, TypeError) as exc:
            raise LaneRunError(f"invalid minute recovery policy: {exc}") from exc
        if recovery_policy.budget_seconds >= _minute_collector_timeout_seconds():
            raise LaneRunError("minute recovery budget must be below collector timeout")
    if seed_raw:
        seed_home = Path(seed_raw).resolve()
        try:
            status = json.loads((seed_home / "status.json").read_text(encoding="utf-8"))
            checkpoint = json.loads(
                (seed_home / "checkpoint.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError) as exc:
            raise LaneRunError(f"minute recovery seed is unreadable: {exc}") from exc
        expected = {_code6(code) for code in symbols}
        observed = checkpoint.get("symbols")
        if not isinstance(observed, dict):
            raise LaneRunError("minute recovery seed checkpoint has no symbols mapping")
        invalid: list[str] = []
        for code in sorted(expected):
            entry = observed.get(code)
            path = seed_home / f"{code}_{bar_size}.parquet"
            if not isinstance(entry, dict) or entry.get("status") != "done":
                invalid.append(code)
                continue
            try:
                rows_file = int(entry.get("rows_file", -1))
            except (TypeError, ValueError):
                invalid.append(code)
                continue
            if rows_file < 0 or (rows_file > 0 and not path.is_file()):
                invalid.append(code)
        extra = sorted(set(observed) - expected)
        if (
            status.get("state") != "done"
            or status.get("freq") != bar_size
            or int(status.get("total", -1)) != len(expected)
            or int(status.get("done", -1)) != len(expected)
            or int(status.get("error", -1)) != 0
            or invalid
            or extra
        ):
            raise LaneRunError(
                "minute recovery seed is not a complete exact-universe success: "
                f"invalid={invalid[:5]} extra={extra[:5]} status={status}"
            )
        started = time.monotonic()
        shutil.copytree(seed_home, raw_home)
        return {
            "provider": "quanthus.minute-recovery-seed",
            "api": "verified_attempt_seed_copy",
            "layer": layer,
            "bar_size": bar_size,
            "fetch_mode": fetch_mode,
            "max_pages": max_pages,
            "cmd": [],
            "exit_code": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": 0,
            "layer_dir": str(raw_home),
            "market_date": market_date.isoformat(),
            "universe_size": len(expected),
            "isolation": "attempt-local-seed-copy",
            "seed_home": str(seed_home),
        }
    if collector_override:
        script = Path(collector_override).resolve()
        if not script.is_file():
            raise LaneRunError(f"minute collector override missing: {script}")
        collector_argv = [py, str(script)]
        collector_identity = str(script)
    else:
        collector_argv = [py, "-m", "quanthus.providers.minute_tdx"]
        collector_identity = "quanthus.providers.minute_tdx"
    cmd = [
        *collector_argv,
        "--layer",
        layer,
        "--freq",
        bar_size,
        "--mode",
        fetch_mode,
        "--market-date",
        market_date.isoformat(),
        "--symbols",
        ",".join(symbols),
        "--no-lock",
        "--sleep",
        os.environ.get("HERMES_ODS_MINUTE_SLEEP", "0.01"),
    ]
    if max_pages is not None:
        cmd.extend(["--max-pages", str(max_pages)])
    if policy_path is not None:
        cmd.extend(["--recovery-policy", str(policy_path)])
    # Provider collection is always attempt-local. There is deliberately no
    # production fallback: shadow runs must never mutate shared legacy partitions.
    raw_home.mkdir(parents=True, exist_ok=False)
    cmd.extend(["--home", str(raw_home)])
    layer_dir = raw_home
    import subprocess

    timeout_seconds = _minute_collector_timeout_seconds()
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "provider": collector_identity,
        "api": (
            "baostock.query_history_k_data_plus.native_5m"
            if bar_size == "5m" and not collector_override
            else f"mootdx/tdx.{fetch_mode}"
        ),
        "layer": layer,
        "bar_size": bar_size,
        "fetch_mode": fetch_mode,
        "max_pages": max_pages,
        "cmd": cmd,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "duration_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": timeout_seconds,
        "layer_dir": str(layer_dir),
        "market_date": market_date.isoformat(),
        "universe_size": len(symbols),
        "isolation": "attempt-local",
        **(
            {"recovery_policy_id": recovery_policy.policy_id,
             "recovery_policy_sha256": recovery_policy.sha256,
             "budget_seconds": recovery_policy.budget_seconds}
            if recovery_policy else {}
        ),
    }


def _build_after_hours_fixed_price(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    source_revision: str,
) -> list[DatasetStatus]:
    watermark = market_date.isoformat()
    output = landing / "after_hours_fixed_price_trades.parquet"
    if _acquire_mode() == "provider":
        try:
            _rows, request = _run_configured_provider_collector(
                lane_id="intraday-archive",
                dataset="after_hours_fixed_price_trades",
                output=output,
                paths=paths,
                market_date=market_date,
                window_start=market_date,
            )
        except Exception as exc:  # noqa: BLE001 - missing sparse evidence blocks 1m READY
            return [
                DatasetStatus(
                    name="after_hours_fixed_price_trades",
                    status="failed",
                    detail=str(exc),
                    source_revision=source_revision,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                ),
                DatasetStatus(
                    name="after_hours_fixed_price_receipts",
                    status="failed",
                    detail="after-hours provider acquisition failed",
                    source_revision=source_revision,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                ),
            ]
        source_request = json.dumps(request, sort_keys=True, default=str)
        return [
            _register_parquet_dataset(
                name="after_hours_fixed_price_trades",
                path=output,
                source_revision=source_revision,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                source_request=source_request,
                require_rows=False,
            ),
            _register_parquet_dataset(
                name="after_hours_fixed_price_receipts",
                path=Path(str(request["receipts"])),
                source_revision=f"per-security-receipts@{source_revision}",
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                source_request=source_request,
                require_rows=True,
            ),
        ]

    return [
        _copy_dataset(
            name="after_hours_fixed_price_trades",
            source=paths.home / "after_hours_fixed_price_trades.parquet",
            landing=landing,
            source_revision=source_revision,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
            require_rows=False,
        ),
        _copy_dataset(
            name="after_hours_fixed_price_receipts",
            source=paths.home / "after_hours_fixed_price_receipts.parquet",
            landing=landing,
            source_revision=f"per-security-receipts@{source_revision}",
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
            require_rows=True,
        ),
    ]


_DIRECT_5M_PROVIDERS = frozenset(
    {"mootdx-native-kline", "sina-minute-jsonp", "baostock-native-5m"}
)


def _direct_5m_provenance_error(raw_home: Path, universe: Sequence[str]) -> str | None:
    """Fail closed if a formal 5m attempt lacks exact direct-source receipts."""
    try:
        checkpoint = json.loads((raw_home / "checkpoint.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return f"direct_5m_checkpoint_unreadable: {type(exc).__name__}"
    observed = checkpoint.get("symbols")
    if not isinstance(observed, dict):
        return "direct_5m_checkpoint_symbols_missing"
    expected = set(universe)
    if set(observed) != expected:
        return (
            "direct_5m_universe_mismatch: "
            f"missing={len(expected - set(observed))} extra={len(set(observed) - expected)}"
        )
    for code in sorted(expected):
        receipt = observed[code]
        if not isinstance(receipt, dict) or receipt.get("status") != "done":
            return f"direct_5m_receipt_incomplete: {code}"
        if (
            receipt.get("provider") not in _DIRECT_5M_PROVIDERS
            or receipt.get("native_frequency") != "5m"
            or receipt.get("derived_from") is not None
        ):
            return f"direct_5m_source_nonconforming: {code}"
    return None


def _build_intraday_stream(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    attempt_started_at: datetime,
    bar_size: str,
    minute_bar_contract: Mapping[str, Any],
    symbols: Sequence[str] | None = None,
) -> list[DatasetStatus]:
    if bar_size not in {"1m", "5m"}:
        raise LaneRunError(f"unsupported intraday bar_size: {bar_size}")
    layer = "research" if bar_size == "1m" else "prod"
    minute_root = Path(
        os.environ.get("ASTOCK_MINUTE_ROOT") or (paths.home.parent / "minute")
    ).resolve()
    layer_dir = minute_root / "data" / (
        "research_1m" if bar_size == "1m" else "prod_5m"
    )
    rev = f"intraday-{bar_size}-archive-{market_date.strftime('%Y%m%d')}-v3"
    watermark = market_date.isoformat()
    mode = _acquire_mode()
    smoke_only = mode == "provider" and _symbol_limit() is not None
    if symbols:
        universe = [_code6(code) for code in symbols]
    else:
        universe = _universe_from_stock_list(paths.home)
        if not universe:
            if mode == "provider":
                return [
                    DatasetStatus(
                        name="intraday_bars",
                        status="failed",
                        detail="empty in-scope universe from stock_list",
                        source_revision=rev,
                        watermark=watermark,
                        knowledge_cutoff=knowledge_cutoff,
                    )
                ]
            universe = ["000001", "600519"]

    collector_meta: dict[str, Any] | None = None
    if mode == "provider":
        # Real collector for this attempt's in-scope universe (bounded by SYMBOL_LIMIT).
        collector_meta = _run_minute_collector(
            paths=paths,
            market_date=market_date,
            bar_size=bar_size,
            symbols=universe,
            raw_home=landing / "provider_raw" / f"minute_{bar_size}",
        )
        if collector_meta.get("layer_dir"):
            layer_dir = Path(str(collector_meta["layer_dir"]))
        if int(collector_meta.get("exit_code", 1)) != 0:
            # hard fail the stream — do not READY on stale partitions alone
            return [
                DatasetStatus(
                    name="intraday_bars",
                    status="failed",
                    detail=(
                        f"minute collector failed exit={collector_meta.get('exit_code')}: "
                        f"{collector_meta.get('stderr_tail')}"
                    ),
                    source_request=json.dumps(collector_meta, sort_keys=True, default=str),
                ),
                _seal_json_dataset(
                    name="acquisition_log",
                    payload={
                        "lane": "intraday-archive",
                        "stream": bar_size,
                        "market_date": watermark,
                        "collector": collector_meta,
                        "source_revision": rev,
                    },
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=watermark,
                ),
            ]
        if bar_size == "5m":
            provenance_error = _direct_5m_provenance_error(layer_dir, universe)
            if provenance_error is not None:
                return [
                    DatasetStatus(
                        name="intraday_bars",
                        status="failed",
                        detail=provenance_error,
                        source_request=json.dumps(collector_meta, sort_keys=True, default=str),
                    ),
                    _seal_json_dataset(
                        name="acquisition_log",
                        payload={
                            "lane": "intraday-archive",
                            "stream": bar_size,
                            "market_date": watermark,
                            "collector": collector_meta,
                            "provenance_error": provenance_error,
                            "source_revision": rev,
                        },
                        landing=landing,
                        source_revision=rev,
                        knowledge_cutoff=knowledge_cutoff,
                        watermark=watermark,
                    ),
                ]

    rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    files_used: list[str] = []
    observed_max: str | None = None
    acquired_at = _now_sh_iso()
    expected_last = _expected_last_bar(market_date, bar_size)
    expected_timestamps = _expected_bar_timestamps(market_date, bar_size)
    unexplained_missing = 0
    ready_symbols = 0
    justified = 0
    no_data_evidence = _minute_no_data_evidence(
        paths=paths,
        market_date=market_date,
        universe=universe,
        evidence_available_at=attempt_started_at,
    )

    for code in universe:
        path = layer_dir / f"{code}_{bar_size}.parquet"
        request = {
            "provider": (
                "minute_backfill" if mode == "provider" else "minute-fixture"
            ),
            "api": "read_after_collect" if mode == "provider" else "read_symbol_partition",
            "code": code,
            "bar_size": bar_size,
            "path": str(path),
            "market_date": watermark,
            "layer": layer,
            "expected_last_bar": expected_last,
        }
        if not path.is_file():
            evidence = no_data_evidence.get(code)
            if evidence:
                justified += 1
                coverage_rows.append(
                    {
                        "security_id": code,
                        "status": "justified_no_data",
                        "row_count": 0,
                        "observed_maximum": None,
                        **evidence,
                    }
                )
                log_rows.append(
                    {
                        "security_id": code,
                        "request": request,
                        "response_error_code": "0",
                        "response_error_msg": "justified_no_data",
                        "response_rows": 0,
                        "evidence": evidence,
                        "acquired_at": acquired_at,
                    }
                )
                continue
            unexplained_missing += 1
            coverage_rows.append(
                {
                    "security_id": code,
                    "status": "failed",
                    "reason": "missing_partition",
                    "source": None,
                    "row_count": 0,
                    "observed_maximum": None,
                }
            )
            log_rows.append(
                {
                    "security_id": code,
                    "request": request,
                    "response_error_code": "1",
                    "response_error_msg": "missing_partition",
                    "response_rows": 0,
                    "acquired_at": acquired_at,
                }
            )
            continue
        files_used.append(str(path))
        rows_before_code = len(rows)
        matched = 0
        code_max: str | None = None
        code_timestamps: list[str] = []
        code_has_trades = False
        for row in _read_parquet(path):
            ts = (
                row.get("ts")
                or row.get("datetime")
                or row.get("date")
                or row.get("time")
            )
            if ts is None:
                continue
            day = str(ts)[:10]
            if day != watermark:
                continue
            item = dict(row)
            item["security_id"] = code
            item["bar_size"] = bar_size
            item["market_date"] = watermark
            item["acquired_at"] = acquired_at
            rows.append(item)
            volume = _fnum(item.get("volume"), 0.0) or 0.0
            amount = _fnum(item.get("amount"), 0.0) or 0.0
            code_has_trades = code_has_trades or volume > 1e-9 or amount > 1e-9
            matched += 1
            ts_s = str(ts)
            # normalize comparable timestamp
            ts_s = ts_s.replace("T", " ")[:19] if "T" in ts_s else ts_s[:19]
            code_timestamps.append(ts_s)
            if code_max is None or ts_s > code_max:
                code_max = ts_s
            if observed_max is None or ts_s > observed_max:
                observed_max = ts_s
        if matched <= 0:
            evidence = no_data_evidence.get(code)
            if evidence:
                justified += 1
                coverage_rows.append(
                    {
                        "security_id": code,
                        "status": "justified_no_data",
                        "row_count": 0,
                        "observed_maximum": None,
                        **evidence,
                    }
                )
                log_rows.append(
                    {
                        "security_id": code,
                        "request": request,
                        "response_error_code": "0",
                        "response_error_msg": "justified_no_data",
                        "response_rows": 0,
                        "evidence": evidence,
                        "acquired_at": acquired_at,
                    }
                )
            else:
                unexplained_missing += 1
                coverage_rows.append(
                    {
                        "security_id": code,
                        "status": "failed",
                        "reason": "zero_rows_for_session",
                        "source": None,
                        "row_count": 0,
                        "observed_maximum": None,
                    }
                )
                log_rows.append(
                    {
                        "security_id": code,
                        "request": request,
                        "response_error_code": "1",
                        "response_error_msg": "zero_rows_for_session",
                        "response_rows": 0,
                        "acquired_at": acquired_at,
                    }
                )
            continue

        unique_timestamps = set(code_timestamps)
        missing_timestamps = expected_timestamps - unique_timestamps
        unexpected_timestamps = unique_timestamps - expected_timestamps
        duplicate_count = matched - len(unique_timestamps)
        complete = (
            not missing_timestamps
            and not unexpected_timestamps
            and duplicate_count == 0
        )
        if not complete:
            evidence = no_data_evidence.get(code)
            if evidence and not code_has_trades:
                del rows[rows_before_code:]
                justified += 1
                coverage_rows.append(
                    {
                        "security_id": code,
                        "status": "justified_no_data",
                        "row_count": 0,
                        "observed_maximum": None,
                        "provider_rows_discarded": matched,
                        "missing_bar_count": len(missing_timestamps),
                        "unexpected_bar_count": len(unexpected_timestamps),
                        "duplicate_bar_count": duplicate_count,
                        **evidence,
                    }
                )
                log_rows.append(
                    {
                        "security_id": code,
                        "request": request,
                        "response_error_code": "0",
                        "response_error_msg": "justified_no_data",
                        "response_rows": 0,
                        "provider_rows_discarded": matched,
                        "missing_bar_count": len(missing_timestamps),
                        "unexpected_bar_count": len(unexpected_timestamps),
                        "duplicate_bar_count": duplicate_count,
                        "evidence": evidence,
                        "acquired_at": acquired_at,
                    }
                )
                continue
            unexplained_missing += 1
            coverage_rows.append(
                {
                    "security_id": code,
                    "status": "failed",
                    "reason": "incomplete_session",
                    "source": None,
                    "row_count": matched,
                    "observed_maximum": code_max,
                    "expected_last_bar": expected_last,
                    "expected_row_count": len(expected_timestamps),
                    "missing_bar_count": len(missing_timestamps),
                    "unexpected_bar_count": len(unexpected_timestamps),
                    "duplicate_bar_count": duplicate_count,
                }
            )
            log_rows.append(
                {
                    "security_id": code,
                    "request": request,
                    "response_error_code": "1",
                    "response_error_msg": "incomplete_session",
                    "response_rows": matched,
                    "observed_maximum": code_max,
                    "expected_row_count": len(expected_timestamps),
                    "missing_bar_count": len(missing_timestamps),
                    "unexpected_bar_count": len(unexpected_timestamps),
                    "duplicate_bar_count": duplicate_count,
                    "acquired_at": acquired_at,
                }
            )
            continue

        ready_symbols += 1
        coverage_rows.append(
            {
                "security_id": code,
                "status": "ready",
                "reason": "ok",
                "source": None,
                "row_count": matched,
                "observed_maximum": code_max,
            }
        )
        log_rows.append(
            {
                "security_id": code,
                "request": request,
                "response_error_code": "0",
                "response_error_msg": "ok",
                "response_rows": matched,
                "observed_maximum": code_max,
                "acquired_at": acquired_at,
            }
        )

    coverage_ok = (
        unexplained_missing == 0
        and ready_symbols + justified == len(universe)
    )
    coverage_columns = sorted(
        {column for row in coverage_rows for column in row}
    )
    coverage_rows = [
        {column: row.get(column) for column in coverage_columns}
        for row in coverage_rows
    ]
    auction_session_complete = bool(
        observed_max and observed_max[:16] >= expected_last[:16]
    )
    meta = {
        "bar_size": bar_size,
        "layer": layer,
        "acquisition": "collector" if mode == "provider" else "fixture",
        "acquire_mode": mode,
        "may_derive_from": None,
        "market_date": watermark,
        "universe_size": len(universe),
        "ready_symbols": ready_symbols,
        "justified_no_data": justified,
        "unexplained_missing": unexplained_missing,
        "coverage_ok": coverage_ok,
        "session_complete": auction_session_complete,
        "auction_session_complete": auction_session_complete,
        "session_phase": minute_bar_contract["session_phase"],
        "session_scope": minute_bar_contract["session_scope"],
        "session_policy_version": minute_bar_contract["policy_version"],
        "covers_after_hours_fixed_price": minute_bar_contract[
            "covers_after_hours_fixed_price"
        ],
        "after_hours_fixed_price_contract": minute_bar_contract[
            "after_hours_fixed_price_contract"
        ],
        "expected_last_bar": expected_last,
        "expected_rows_per_ready_symbol": len(expected_timestamps),
        "source_files": files_used,
        "row_count": len(rows),
        "observed_maximum": observed_max,
        "source_revision": rev,
        "collector": collector_meta,
        "scope_mode": "smoke_only" if smoke_only else "full_universe",
        "note": (
            "1m/5m completeness covers the continuous and closing-auction grid only; "
            "after-hours fixed-price trades require a separate sparse dataset; "
            "formal 5m is never synthesized from 1m; unexplained missing blocks READY"
        ),
    }
    datasets: list[DatasetStatus] = []
    if coverage_ok and auction_session_complete and len(rows) > 0:
        bars_dataset = _seal_dataset(
                name="intraday_bars",
                rows=rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name=f"intraday_{bar_size}_bars.parquet",
                require_rows=True,
                source_request=json.dumps(
                    {"universe_size": len(universe), "coverage_ok": True},
                    sort_keys=True,
                ),
            )
        if smoke_only:
            bars_dataset.status = "smoke_only"
            bars_dataset.detail = (
                "HERMES_ODS_SYMBOL_LIMIT is set; smoke evidence cannot publish READY"
            )
        datasets.append(bars_dataset)
    else:
        detail_parts = []
        if not coverage_ok:
            detail_parts.append(f"unexplained_missing={unexplained_missing}")
        if not auction_session_complete:
            detail_parts.append(
                f"session_incomplete observed={observed_max} "
                f"expected={expected_last}"
            )
        if len(rows) <= 0:
            detail_parts.append("zero_bars")
        datasets.append(
            DatasetStatus(
                name="intraday_bars",
                status="failed",
                row_count=len(rows),
                detail="; ".join(detail_parts) or "coverage gate failed",
                source_revision=rev,
                watermark=watermark,
                knowledge_cutoff=knowledge_cutoff,
                source_request=json.dumps(meta, sort_keys=True, default=str)[:2000],
            )
        )
    datasets.extend(
        [
            _seal_json_dataset(
                name="intraday_meta",
                payload=meta,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name=f"intraday_{bar_size}_meta.json",
            ),
            _seal_dataset(
                name="intraday_coverage",
                rows=coverage_rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name=f"intraday_{bar_size}_coverage.parquet",
                require_rows=True,
            ),
            _copy_dataset(
                name="trading_calendar",
                source=paths.calendar,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
                relative_name="ref_trading_calendar.parquet",
                source_request=_calendar_source_request(paths.calendar),
            ),
            _seal_json_dataset(
                name="acquisition_log",
                payload={
                    "lane": "intraday-archive",
                    "stream": bar_size,
                    "market_date": watermark,
                    "acquire_mode": mode,
                    "requests": log_rows,
                    "collector": collector_meta,
                    "source_revision": rev,
                },
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=watermark,
            ),
        ]
    )
    if bar_size == "1m":
        datasets.extend(
            _build_after_hours_fixed_price(
                paths=paths,
                market_date=market_date,
                landing=landing,
                knowledge_cutoff=knowledge_cutoff,
                source_revision=f"after-hours-fixed-price@{rev}",
            )
        )
    return datasets


def _mandatory_set(lane_id: str, *, closed: bool = False, stream: str | None = None) -> set[str]:
    if lane_id == "market-daily-core" and closed:
        return set(MANDATORY["market-daily-core-closed"])
    if lane_id == "intraday-archive":
        mandatory = set(MANDATORY["intraday-archive"])
        if stream == "1m":
            mandatory.update(
                {
                    "after_hours_fixed_price_trades",
                    "after_hours_fixed_price_receipts",
                }
            )
        return mandatory
    return set(MANDATORY.get(lane_id, set()))


def _evaluate_mandatory(
    datasets: Sequence[DatasetStatus],
    mandatory: set[str],
) -> tuple[str, str | None]:
    by_name = {item.name: item for item in datasets}
    missing = sorted(name for name in mandatory if name not in by_name)
    not_ready = sorted(
        name
        for name in mandatory
        if name in by_name and by_name[name].status != "ready"
    )
    if missing or not_ready:
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if not_ready:
            detail.append(f"not_ready={not_ready}")
        # if any ready optional/mandatory subset exists -> partial else failed
        any_ready = any(item.status == "ready" for item in datasets)
        return ("partial" if any_ready else "failed"), "; ".join(detail)
    return "ready", None


def _publish(
    *,
    schedule: str,
    market_date: date,
    attempt_id: str,
    landing: Path,
    datasets: Sequence[DatasetStatus],
    release_root: Path,
    lane_id: str,
    schedule_id: str,
    schedule_contract_revision: str,
    schedule_config_sha256: str,
    stream: str | None = None,
    closed: bool = False,
    failure_injection_stage: str | None = None,
) -> tuple[Path | None, str, str | None]:
    """Publish only when all mandatory datasets are ready. READY is last."""

    mandatory = _mandatory_set(lane_id, closed=closed, stream=stream)
    # lane_status artifact must exist before publish and be checksummed
    lane_status_payload = {
        "lane_id": lane_id,
        "attempt_id": attempt_id,
        "stream": stream,
        "market_date": market_date.isoformat(),
        "datasets": [item.to_dict() for item in datasets],
        "mandatory": sorted(mandatory),
        "producer": PRODUCER,
        "schedule_id": schedule_id,
        "schedule_contract_revision": schedule_contract_revision,
        "schedule_config_sha256": schedule_config_sha256,
    }
    datasets = list(datasets)
    # replace/add lane_status as sealed artifact in landing before publish
    status_ds = _seal_json_dataset(
        name="lane_status",
        payload=lane_status_payload,
        landing=landing,
        source_revision=f"lane_status@{attempt_id}",
        knowledge_cutoff=_now_sh_iso(),
        watermark=market_date.isoformat(),
        relative_name="lane_status.json",
    )
    # drop prior lane_status if any
    datasets = [item for item in datasets if item.name != "lane_status"]
    datasets.append(status_ds)

    if not closed and _acquire_mode() == "provider" and _symbol_limit() is not None:
        return (
            None,
            "partial",
            "HERMES_ODS_SYMBOL_LIMIT is smoke-only and cannot publish READY",
        )

    gate_status, gate_detail = _evaluate_mandatory(datasets, mandatory)
    if gate_status != "ready":
        return None, gate_status, gate_detail or "mandatory datasets not ready"

    artifacts = []
    for item in datasets:
        if item.status != "ready" or not item.path:
            continue
        source = Path(item.path)
        rel = f"artifacts/{source.name}"
        artifacts.append(
            {
                "name": item.name,
                "source_path": str(source),
                "relative_path": rel,
                "source_revision": item.source_revision or PRODUCER,
                "knowledge_cutoff": item.knowledge_cutoff or _now_sh_iso(),
            }
        )
    if not artifacts:
        return None, "failed", "no ready artifacts"

    suffix = f"-{stream}" if stream else ""
    release_id = (
        f"hermes-{lane_id}{suffix}-{market_date.strftime('%Y%m%d')}-"
        f"{attempt_id[-8:]}-v2"
    ).replace("/", "-")
    spec = {
        "schema_version": 1,
        "release_id": release_id,
        "schedule": schedule,
        "market_date": market_date.isoformat(),
        "producer": PRODUCER,
        "schedule_contract_id": schedule_id,
        "schedule_contract_revision": schedule_contract_revision,
        "schedule_config_sha256": schedule_config_sha256,
        "artifacts": artifacts,
    }
    if stream:
        release_dir = (
            release_root / schedule / stream / market_date.isoformat() / attempt_id
        )
    elif lane_id == "market-daily-core" or schedule != "daily":
        release_dir = release_root / schedule / market_date.isoformat() / attempt_id
    else:
        release_dir = (
            release_root
            / schedule
            / market_date.isoformat()
            / f"{lane_id}-{attempt_id}"
        )
    spec_path = landing / "release-spec.json"
    _atomic_json(spec_path, spec)
    try:
        publish_ods_release(
            spec_path=spec_path,
            output=release_dir,
            before_ready=(
                lambda: _inject_failure(failure_injection_stage, "transfer")
                if failure_injection_stage == "transfer"
                else None
            ),
        )
    except OdsReleaseError as exc:
        return None, "failed", str(exc)
    # READY is last inside publish_ods_release; do not write anything else here.
    if lane_id == "market-daily-core" and schedule == "daily" and stream is None:
        verified = verify_ods_release(release_dir)
        _atomic_json(
            release_root / schedule / market_date.isoformat() / "CURRENT.json",
            {
                "schema_version": 1,
                "schedule": schedule,
                "market_date": market_date.isoformat(),
                "attempt_id": attempt_id,
                "release_id": verified.release.release_id,
                "manifest_sha256": verified.manifest_sha256,
                "ready_at": verified.release.created_at.isoformat(),
            },
        )
    return release_dir, "ready", None


def _commit_watermark(
    *,
    paths: LanePaths,
    stream: str,
    market_date: date,
    attempt_id: str,
    observed_maximum: str | None,
    row_count: int,
) -> None:
    wm_path = paths.shadow_root / "watermarks" / f"intraday-{stream}.watermark.json"
    previous: dict[str, Any] = {}
    if wm_path.is_file():
        try:
            previous = json.loads(wm_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    prev_date = previous.get("committed_market_date")
    if prev_date:
        try:
            prev = date.fromisoformat(str(prev_date)[:10])
            if market_date < prev:
                raise LaneRunError(
                    f"watermark regression: {market_date} < committed {prev}"
                )
        except ValueError:
            pass
    if row_count <= 0 or not observed_maximum:
        raise LaneRunError("refusing watermark advance without rows/observed_maximum")
    payload = {
        "stream": stream,
        "input_watermark": previous.get("committed_market_date"),
        "observed_maximum": observed_maximum,
        "committed_market_date": market_date.isoformat(),
        "attempt_id": attempt_id,
        "row_count": row_count,
        "updated_at": _now_sh_iso(),
        "lock": f"intraday-{stream}.lock",
        "checkpoint": f"intraday-{stream}.watermark.json",
    }
    _atomic_json(wm_path, payload)


def run_lane(
    *,
    lane_id: str,
    market_date: date,
    schedule_config: Path,
    paths: LanePaths | None = None,
    stream: str | None = None,
    force_preflight: str | None = None,
    issue_ref: str | None = None,
    symbols: Sequence[str] | None = None,
    recovery_deadline_at: datetime | None = None,
) -> LaneRunResult:
    """Run one lane attempt under the schedule contract."""

    contract = load_schedule_contract(schedule_config)
    schedule_config_sha256 = _sha256(schedule_config)
    if lane_id not in contract.lanes:
        raise LaneRunError(f"unknown lane id: {lane_id}")
    payload = json.loads(schedule_config.read_text(encoding="utf-8"))
    lane = next(item for item in payload["lanes"] if item["id"] == lane_id)
    resolved = paths or resolve_paths()
    started = _now()
    if recovery_deadline_at is not None:
        if lane_id != "market-daily-core":
            raise LaneRunError("recovery deadline is only valid for market-daily-core")
        if recovery_deadline_at.tzinfo is None:
            raise LaneRunError("recovery deadline must be timezone-aware")
        if recovery_deadline_at.astimezone(UTC) <= started:
            raise LaneRunError("recovery deadline must be in the future")
        if market_date > started.astimezone(SH).date():
            raise LaneRunError("manual recovery cannot target a future market date")
    attempt_id = _attempt_id(market_date, started)
    if stream:
        attempt_dir = (
            resolved.shadow_root
            / "attempts"
            / lane_id
            / stream
            / market_date.isoformat()
            / attempt_id
        )
    else:
        attempt_dir = (
            resolved.shadow_root
            / "attempts"
            / lane_id
            / market_date.isoformat()
            / attempt_id
        )
    attempt_dir.mkdir(parents=True, exist_ok=False)
    landing = attempt_dir / "landing"
    landing.mkdir(parents=True, exist_ok=True)
    result = LaneRunResult(
        lane_id=lane_id,
        attempt_id=attempt_id,
        market_date=market_date,
        status="scheduled",
        attempt_dir=attempt_dir,
        release_dir=None,
        stream=stream,
        started_at=started,
        schedule_id=contract.schedule_id,
        schedule_contract_revision=contract.contract_revision,
        schedule_config_sha256=schedule_config_sha256,
        run_mode="manual_recovery" if recovery_deadline_at else "scheduled",
        recovery_deadline_at=recovery_deadline_at,
    )
    _atomic_json(attempt_dir / "attempt.json", result.to_dict())
    failure_injection_stage = _failure_injection_stage(
        shadow_root=resolved.shadow_root
    )

    lock_name = {
        "market-daily-core": "market-daily-core.lock",
        "calendar-daily-events": "calendar-daily-events.lock",
        "weekly-corrections": "weekly-corrections.lock",
        "bootstrap": "bootstrap.lock",
        "intraday-archive": f"intraday-{stream or 'all'}.lock",
    }.get(lane_id, f"{lane_id}.lock")
    handles = []
    try:
        handles.append(_lock(resolved.lock_root / lock_name))
        group = _resource_group_for(lane_id)
        if group:
            handles.append(_lock(resolved.lock_root / f"resource-group-{group}.lock"))

        if lane_id == "bootstrap":
            if not issue_ref:
                result.status = "failed"
                result.detail = "bootstrap requires --issue-ref and is non-recurring"
            else:
                result.status = "failed"
                result.detail = (
                    "bootstrap remains manual/issue-bound; "
                    "shadow runner refuses automatic execution"
                )
            result.finished_at = _now()
            _atomic_json(attempt_dir / "attempt.json", result.to_dict())
            return result

        result.status = "preflight"
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        preflight = force_preflight or "n/a"
        if lane_id in {"market-daily-core", "intraday-archive"}:
            acquire_mode = _acquire_mode()
            if acquire_mode == "provider" and force_preflight is None:
                calendar_output = (
                    landing / "preflight" / "ref_trading_calendar.parquet"
                )
                try:
                    refresh = refresh_calendar(
                        source=resolved.calendar,
                        output=calendar_output,
                        target_date=market_date,
                        lookback_days=int(
                            lane["session_preflight"]["calendar_refresh"][
                                "lookback_calendar_days"
                            ]
                        ),
                    )
                except CalendarRefreshError as exc:
                    result.status = "failed"
                    result.preflight = "calendar_refresh_failed"
                    result.preflight_evidence = {
                        "status": "failed",
                        "source": "baostock.query_trade_dates",
                        "target_date": market_date.isoformat(),
                        "error": str(exc),
                    }
                    result.detail = (
                        f"calendar refresh failed: {exc}; "
                        "no market or minute provider calls; no READY"
                    )
                    result.finished_at = _now()
                    _atomic_json(attempt_dir / "attempt.json", result.to_dict())
                    _atomic_json(attempt_dir / "datasets.json", {"datasets": []})
                    return result
                resolved = replace(resolved, calendar=refresh.output_path)
                result.preflight_evidence = {
                    "status": "ready",
                    **refresh.to_dict(),
                }
                _atomic_json(attempt_dir / "attempt.json", result.to_dict())
            elif force_preflight is not None:
                result.preflight_evidence = {
                    "status": "bypassed_by_explicit_force_preflight",
                    "target_date": market_date.isoformat(),
                }
            preflight = force_preflight or session_preflight(
                calendar=resolved.calendar, market_date=market_date
            )
            result.preflight = preflight
            if preflight == "missing_or_disagree":
                result.status = "failed"
                result.detail = (
                    "calendar missing or SSE/SZSE disagree; "
                    "no provider calls; no READY"
                )
                result.finished_at = _now()
                _atomic_json(attempt_dir / "attempt.json", result.to_dict())
                _atomic_json(attempt_dir / "datasets.json", {"datasets": []})
                return result

        knowledge_cutoff = _now_sh_iso()
        result.status = "acquiring"
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        _inject_failure(failure_injection_stage, "acquisition")

        if lane_id == "market-daily-core":
            if preflight == "both_closed":
                datasets = _build_market_closed(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                )
                result.datasets = datasets
                _inject_failure(failure_injection_stage, "pre_seal")
                result.status = "sealed"
                _atomic_json(
                    attempt_dir / "datasets.json",
                    {"datasets": [item.to_dict() for item in datasets]},
                )
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                    schedule_id=contract.schedule_id,
                    schedule_contract_revision=contract.contract_revision,
                    schedule_config_sha256=schedule_config_sha256,
                    closed=True,
                    failure_injection_stage=failure_injection_stage,
                )
                result.release_dir = release_dir
                result.status = "closed_session" if status == "ready" else status
                result.detail = detail or "calendar_only_release"
            elif preflight == "both_open":
                datasets = _build_market_open(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                    provider_readiness=lane.get("provider_readiness"),
                    completion_deadline=str(lane.get("completion_deadline") or ""),
                    collection_window_experiment=lane.get(
                        "collection_window_experiment"
                    ),
                    recovery_deadline_at=recovery_deadline_at,
                )
                result.datasets = datasets
                _inject_failure(failure_injection_stage, "pre_seal")
                result.status = "sealed"
                _atomic_json(
                    attempt_dir / "datasets.json",
                    {"datasets": [item.to_dict() for item in datasets]},
                )
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                    schedule_id=contract.schedule_id,
                    schedule_contract_revision=contract.contract_revision,
                    schedule_config_sha256=schedule_config_sha256,
                    failure_injection_stage=failure_injection_stage,
                )
                result.release_dir = release_dir
                result.status = status
                result.detail = detail
            else:
                raise LaneRunError(f"unexpected preflight state: {preflight}")

        elif lane_id == "calendar-daily-events":
            if lane.get("can_create_market_session") is not False:
                raise ScheduleContractError("event lane must not create market sessions")
            datasets = _build_calendar_events(
                paths=resolved,
                market_date=market_date,
                landing=landing,
                knowledge_cutoff=knowledge_cutoff,
                attempt_id=attempt_id,
            )
            result.datasets = datasets
            _inject_failure(failure_injection_stage, "pre_seal")
            result.status = "sealed"
            _atomic_json(
                attempt_dir / "datasets.json",
                {"datasets": [item.to_dict() for item in datasets]},
            )
            release_dir, status, detail = _publish(
                schedule="daily",
                market_date=market_date,
                attempt_id=attempt_id,
                landing=landing,
                datasets=datasets,
                release_root=resolved.shadow_root / "releases",
                lane_id=lane_id,
                schedule_id=contract.schedule_id,
                schedule_contract_revision=contract.contract_revision,
                schedule_config_sha256=schedule_config_sha256,
                failure_injection_stage=failure_injection_stage,
            )
            result.release_dir = release_dir
            result.status = status
            result.detail = detail
            if status == "ready" and release_dir is not None:
                pending_path = landing / "_event_watermark_pending.json"
                pending: dict[str, Any] = {}
                if pending_path.is_file():
                    try:
                        pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    except Exception:
                        pending = {}
                _commit_event_watermark(
                    paths=resolved,
                    market_date=market_date,
                    attempt_id=attempt_id,
                    input_watermark=pending.get("input_watermark"),
                    observed_maximum=pending.get("observed_maximum"),
                )

        elif lane_id == "weekly-corrections":
            if lane.get("may_overwrite_daily") is not False:
                raise ScheduleContractError("weekly must not overwrite daily")
            datasets = _build_weekly_corrections(
                paths=resolved,
                market_date=market_date,
                landing=landing,
                knowledge_cutoff=knowledge_cutoff,
            )
            result.datasets = datasets
            _inject_failure(failure_injection_stage, "pre_seal")
            result.status = "sealed"
            _atomic_json(
                attempt_dir / "datasets.json",
                {"datasets": [item.to_dict() for item in datasets]},
            )
            release_dir, status, detail = _publish(
                schedule="weekly",
                market_date=market_date,
                attempt_id=attempt_id,
                landing=landing,
                datasets=datasets,
                release_root=resolved.shadow_root / "releases",
                lane_id=lane_id,
                schedule_id=contract.schedule_id,
                schedule_contract_revision=contract.contract_revision,
                schedule_config_sha256=schedule_config_sha256,
                failure_injection_stage=failure_injection_stage,
            )
            result.release_dir = release_dir
            result.status = status
            result.detail = detail

        elif lane_id == "intraday-archive":
            if stream not in {"1m", "5m"}:
                raise LaneRunError("intraday-archive requires --stream 1m|5m")
            if lane.get("blocks_market_daily_core") is not False:
                raise ScheduleContractError("intraday must not block market-daily-core")
            if preflight == "both_closed":
                datasets = _build_market_closed(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                )
                # rename trading_calendar-only closed path uses closed mandatory
                result.datasets = datasets
                _inject_failure(failure_injection_stage, "pre_seal")
                result.status = "sealed"
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id="market-daily-core",
                    schedule_id=contract.schedule_id,
                    schedule_contract_revision=contract.contract_revision,
                    schedule_config_sha256=schedule_config_sha256,
                    stream=stream,
                    closed=True,
                    failure_injection_stage=failure_injection_stage,
                )
                # for intraday closed, use intraday mandatory override: calendar+lane_status
                if status != "ready":
                    # retry with explicit closed mandatory via market-daily-core-closed names
                    pass
                result.release_dir = release_dir
                result.status = "closed_session" if status == "ready" else status
                result.detail = detail or "intraday closed_session calendar-only"
            else:
                datasets = _build_intraday_stream(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                    attempt_started_at=started,
                    bar_size=stream,
                    minute_bar_contract=lane["minute_bar_contract"],
                    symbols=symbols,
                )
                result.datasets = datasets
                _inject_failure(failure_injection_stage, "pre_seal")
                result.status = "sealed"
                _atomic_json(
                    attempt_dir / "datasets.json",
                    {"datasets": [item.to_dict() for item in datasets]},
                )
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                    schedule_id=contract.schedule_id,
                    schedule_contract_revision=contract.contract_revision,
                    schedule_config_sha256=schedule_config_sha256,
                    stream=stream,
                    failure_injection_stage=failure_injection_stage,
                )
                result.release_dir = release_dir
                result.status = status
                result.detail = detail
                if status == "ready" and release_dir is not None:
                    bars = next(
                        (item for item in datasets if item.name == "intraday_bars"),
                        None,
                    )
                    meta = next(
                        (item for item in datasets if item.name == "intraday_meta"),
                        None,
                    )
                    observed = None
                    if meta and meta.path:
                        try:
                            observed = json.loads(
                                Path(meta.path).read_text(encoding="utf-8")
                            ).get("observed_maximum")
                        except Exception:
                            observed = None
                    _commit_watermark(
                        paths=resolved,
                        stream=stream,
                        market_date=market_date,
                        attempt_id=attempt_id,
                        observed_maximum=str(observed) if observed else None,
                        row_count=int(bars.row_count or 0) if bars else 0,
                    )
        else:
            raise LaneRunError(f"lane not implemented: {lane_id}")

        result.finished_at = _now()
        if result.release_dir is not None and result.status in {
            "ready",
            "closed_session",
        }:
            if (result.release_dir / "READY").is_file():
                verify_ods_release(result.release_dir)
            # ensure no untracked sidecar after READY
            if (result.release_dir / "lane_status.json").exists():
                # allowed only if it is a checksummed artifact under artifacts/
                pass
        # partial/failed must not leave READY
        if result.status in {"partial", "failed"} and result.release_dir is not None:
            if (result.release_dir / "READY").is_file():
                shutil.rmtree(result.release_dir, ignore_errors=True)
            result.release_dir = None
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        _atomic_json(
            attempt_dir / "datasets.json",
            {"datasets": [item.to_dict() for item in result.datasets]},
        )
        return result
    except Exception as exc:
        result.status = "failed"
        result.detail = f"{type(exc).__name__}: {exc}"
        result.finished_at = _now()
        if result.release_dir is not None:
            if (result.release_dir / "READY").is_file():
                shutil.rmtree(result.release_dir, ignore_errors=True)
            result.release_dir = None
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        _atomic_json(
            attempt_dir / "datasets.json",
            {"datasets": [item.to_dict() for item in result.datasets]},
        )
        raise
    finally:
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def expected_timer_oncalendar(
    schedule_config: Path, *, node_id: str = "hermes"
) -> dict[str, str]:
    """Derive systemd OnCalendar strings from the versioned schedule contract."""

    payload = json.loads(Path(schedule_config).read_text(encoding="utf-8"))
    tz = payload.get("timezone") or "Asia/Shanghai"
    out: dict[str, str] = {}
    for lane in payload["lanes"]:
        lane_id = lane["id"]
        if lane_id == "intraday-archive":
            for stream in lane.get("streams") or []:
                trigger = stream["trigger"]
                time_s = trigger["time"]
                # Mon..Fri
                out[f"intraday-{stream['bar_size']}"] = (
                    f"Mon..Fri *-*-* {time_s} {tz}"
                )
            continue
        trigger = lane.get("trigger") or {}
        if trigger.get("kind") != "timer":
            continue
        time_s = trigger["time"]
        if lane_id == "market-daily-core":
            experiment = lane.get("collection_window_experiment") or {}
            node = next(
                (
                    value
                    for value in experiment.get("nodes") or []
                    if value.get("node_id") == node_id
                ),
                None,
            )
            if node is not None:
                time_s = node["timer_start"]
        if trigger.get("days") == "every_natural_day":
            out[lane_id] = f"*-*-* {time_s} {tz}"
        elif trigger.get("weekdays") == [6]:
            out[lane_id] = f"Sat *-*-* {time_s} {tz}"
        elif trigger.get("weekdays") == [1, 2, 3, 4, 5]:
            out[lane_id] = f"Mon..Fri *-*-* {time_s} {tz}"
        else:
            out[lane_id] = f"*-*-* {time_s} {tz}"
    return out


def run_shadow_matrix(
    *,
    schedule_config: Path,
    open_date: date,
    closed_date: date,
    weekly_date: date,
    event_date: date,
    paths: LanePaths | None = None,
) -> dict[str, Any]:
    """Execute the minimum Phase B shadow matrix and return a summary."""

    resolved = paths or resolve_paths()
    summary: dict[str, Any] = {
        "schedule_id": "hermes-ods-schedule-v1",
        "producer": PRODUCER,
        "runs": [],
    }
    matrix = [
        ("market-daily-core", open_date, None, None),
        ("market-daily-core", closed_date, None, None),
        ("market-daily-core", open_date, None, "missing_or_disagree"),
        ("calendar-daily-events", event_date, None, None),
        ("weekly-corrections", weekly_date, None, None),
        ("intraday-archive", open_date, "1m", None),
        ("intraday-archive", open_date, "5m", None),
        ("bootstrap", open_date, None, None),
    ]
    for lane_id, market_date, stream, force in matrix:
        try:
            result = run_lane(
                lane_id=lane_id,
                market_date=market_date,
                schedule_config=schedule_config,
                paths=resolved,
                stream=stream,
                force_preflight=force,
                issue_ref=None,
            )
            summary["runs"].append(result.to_dict())
        except Exception as exc:  # noqa: BLE001
            summary["runs"].append(
                {
                    "lane_id": lane_id,
                    "market_date": market_date.isoformat(),
                    "stream": stream,
                    "status": "failed",
                    "detail": str(exc),
                }
            )
    out = resolved.shadow_root / "SHADOW_MATRIX_SUMMARY.json"
    _atomic_json(out, summary)
    summary["summary_path"] = str(out)
    return summary
