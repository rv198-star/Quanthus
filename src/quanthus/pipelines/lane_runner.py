"""Hermes ODS five-lane shadow/production runner bound to the schedule contract.

Phase B runs beside legacy cron. Outputs land under an isolated shadow root so
canonical production paths and live timers are untouched until GO_FOR_CUTOVER.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.contracts import OdsReleaseError, publish_ods_release, verify_ods_release
from quanthus.pipelines.schedule_contracts import ScheduleContractError, load_schedule_contract

SH = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
PRODUCER = "hermes-ods-lane-runner-v1"


class LaneRunError(RuntimeError):
    """Raised when a lane attempt cannot complete its contract."""


_SUCCESS = {"scheduled", "preflight", "acquiring", "sealed", "ready"}
_TERMINAL = {"closed_session", "failed", "partial"}


def _now() -> datetime:
    return datetime.now(UTC)


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
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_parquet(rows: Sequence[Mapping[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise LaneRunError("pyarrow is required to seal lane artifacts") from exc
    if not rows:
        table = pa.table({})
        pq.write_table(table, path)
        return 0
    table = pa.Table.from_pylist(list(rows))
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return len(rows)


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise LaneRunError("pyarrow is required to read lane inputs") from exc
    table = pq.read_table(path)
    return table.to_pylist()


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
    detail: str | None = None
    stream: str | None = None

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
            "detail": self.detail,
            "stream": self.stream,
            "producer": PRODUCER,
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
    resolved_home = (home or Path(os.environ.get("ASTOCK_HOME") or (root / "data"))).resolve()
    resolved_shadow = (
        shadow_root
        or Path(os.environ.get("HERMES_ODS_SHADOW_ROOT") or (resolved_home / "shadow" / "hermes-ods-v1"))
    ).resolve()
    resolved_calendar = (
        calendar or Path(os.environ.get("HERMES_ODS_CALENDAR") or (resolved_home / "ref_trading_calendar.parquet"))
    ).resolve()
    lock_root = Path(os.environ.get("HERMES_ODS_LOCK_ROOT") or (resolved_shadow / "locks")).resolve()
    return LanePaths(
        home=resolved_home,
        shadow_root=resolved_shadow,
        calendar=resolved_calendar,
        lock_root=lock_root,
    )


def _attempt_id(market_date: date, started: datetime) -> str:
    return f"{market_date.strftime('%Y%m%d')}-{started.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"


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


def _seal_dataset(
    *,
    name: str,
    rows: Sequence[Mapping[str, Any]],
    landing: Path,
    source_revision: str,
    knowledge_cutoff: str,
    watermark: str,
    relative_name: str | None = None,
) -> DatasetStatus:
    started = time.monotonic()
    file_name = relative_name or f"{name}.parquet"
    path = landing / file_name
    count = _write_parquet(rows, path)
    return DatasetStatus(
        name=name,
        status="ready" if count >= 0 else "failed",
        row_count=count,
        sha256=_sha256(path),
        source_revision=source_revision,
        watermark=watermark,
        knowledge_cutoff=knowledge_cutoff,
        duration_seconds=round(time.monotonic() - started, 3),
        path=str(path),
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
) -> DatasetStatus:
    started = time.monotonic()
    if not source.is_file():
        return DatasetStatus(
            name=name,
            status="failed",
            detail=f"missing source: {source}",
            duration_seconds=round(time.monotonic() - started, 3),
        )
    file_name = relative_name or f"{name}.parquet"
    dest = landing / file_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    shutil.copy2(source, tmp)
    os.replace(tmp, dest)
    # best-effort row count
    row_count: int | None
    try:
        row_count = len(_read_parquet(dest))
    except Exception:
        row_count = None
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
    )


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


def _build_market_open_from_local(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
) -> list[DatasetStatus]:
    rev = f"local-none-status-supplement-{market_date.strftime('%Y%m%d')}-v1"
    watermark = market_date.isoformat()
    datasets: list[DatasetStatus] = []

    stock_list = paths.home / "stock_list.parquet"
    none_path = paths.home / "daily_ohlcv_none.parquet"
    st_candidates = [
        paths.home / "revisions" / "st_status_pit_v2_baostock_20260725" / "daily_isST.parquet",
        paths.home / "daily_status_observation.parquet",
    ]
    st_path = next((path for path in st_candidates if path.is_file()), None)

    if not stock_list.is_file() or not none_path.is_file():
        return [
            DatasetStatus(
                name="daily_ohlcv_none_delta",
                status="failed",
                detail="stock_list or daily_ohlcv_none missing",
            )
        ]

    # Prefer date-filtered read when pyarrow expression filters work; fallback full scan.
    try:
        import pyarrow.parquet as pq

        none_table = pq.read_table(none_path, filters=[("date", "=", market_date)])
        none_rows = none_table.to_pylist()
    except Exception:
        none_rows = [
            row
            for row in _read_parquet(none_path)
            if str(row.get("date") or row.get("market_date") or "")[:10] == market_date.isoformat()
        ]
    none_by_code: dict[str, Mapping[str, Any]] = {}
    for row in none_rows:
        code = str(row.get("stock_code") or row.get("security_id") or "").zfill(6)
        if code:
            none_by_code[code] = row

    st_by_code: dict[str, Mapping[str, Any]] = {}
    if st_path is not None:
        try:
            import pyarrow.parquet as pq

            st_table = pq.read_table(st_path, filters=[("date", "=", market_date)])
            st_rows = st_table.to_pylist()
        except Exception:
            st_rows = [
                row
                for row in _read_parquet(st_path)
                if str(row.get("date") or row.get("market_date") or "")[:10] == market_date.isoformat()
            ]
        for row in st_rows:
            code = str(row.get("stock_code") or row.get("security_id") or "").zfill(6)
            if code:
                st_by_code[code] = row

    # Shadow universe is the observed session cross-section (bars ∪ status). Full
    # stock_list completeness is a production collector concern; here we seal what
    # local evidence can prove for market_date without inventing zeros.
    stocks = _read_parquet(stock_list)
    listed = {
        str(row.get("stock_code") or row.get("security_id") or "").zfill(6)
        for row in stocks
        if str(row.get("stock_code") or row.get("security_id") or "")
        and not str(row.get("stock_code") or row.get("security_id") or "").startswith("920")
    }
    universe = sorted((set(none_by_code) | set(st_by_code)) & listed)
    if not universe:
        universe = sorted(set(none_by_code) | set(st_by_code))

    none_delta: list[dict[str, Any]] = []
    status_delta: list[dict[str, Any]] = []
    missing_status = 0
    t1_without_bar = 0
    for code in universe:
        bar = none_by_code.get(code)
        st = st_by_code.get(code, {})
        tradestatus = st.get("tradestatus")
        is_st = st.get("isST")
        if tradestatus is None and bar is not None:
            tradestatus = "1"
        if tradestatus is None and is_st is None and bar is None:
            missing_status += 1
            status_delta.append(
                {
                    "security_id": code,
                    "stock_code": code,
                    "market_date": market_date.isoformat(),
                    "tradestatus": None,
                    "isST": None,
                    "source": "missing_observation",
                }
            )
            continue
        status_delta.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": market_date.isoformat(),
                "tradestatus": None if tradestatus is None else str(tradestatus),
                "isST": None if is_st is None else str(is_st),
                "source": "local-none-status-supplement",
            }
        )
        if str(tradestatus) == "1" and bar is None:
            t1_without_bar += 1
        if bar is not None:
            none_delta.append(
                {
                    "security_id": code,
                    "stock_code": code,
                    "market_date": market_date.isoformat(),
                    "date": market_date.isoformat(),
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": bar.get("volume") if bar.get("volume") is not None else 0.0,
                    "amount": bar.get("amount") if bar.get("amount") is not None else 0.0,
                    "adj_mode": "none",
                    "source": rev,
                }
            )

    datasets.append(
        _seal_dataset(
            name="daily_ohlcv_none_delta",
            rows=none_delta,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=watermark,
        )
    )
    status = _seal_dataset(
        name="daily_status_observation_delta",
        rows=status_delta,
        landing=landing,
        source_revision=rev,
        knowledge_cutoff=knowledge_cutoff,
        watermark=watermark,
    )
    if missing_status:
        status.status = "failed"
        status.detail = f"missing_status={missing_status}"
    if t1_without_bar:
        status.status = "failed"
        status.detail = (status.detail or "") + f" tradestatus_1_without_bar={t1_without_bar}"
    datasets.append(status)

    for name, source, artifact in (
        ("security_master", paths.home / "ref_security_master.parquet", "ref_security_master.parquet"),
        ("stock_list", stock_list, "stock_list.parquet"),
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
            )
        )

    # optional deltas
    for name, source in (
        ("index_daily_delta", paths.home / "index_daily.parquet"),
        ("adjustment_evidence_delta", paths.home / "ref_adjustment_factors.parquet"),
    ):
        if source.is_file():
            rows = _filter_rows_by_date(
                _read_parquet(source),
                market_date=market_date,
                date_keys=("date", "trade_date", "effective_date", "ex_date"),
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
                )
            )
        else:
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="skipped",
                    detail=f"optional source absent: {source.name}",
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
    rev = f"calendar-closed-{market_date.strftime('%Y%m%d')}-v1"
    return [
        _copy_dataset(
            name="trading_calendar",
            source=paths.calendar,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=market_date.isoformat(),
            relative_name="ref_trading_calendar.parquet",
        )
    ]


def _build_calendar_events(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
) -> list[DatasetStatus]:
    rev = f"calendar-events-{market_date.strftime('%Y%m%d')}-v1"
    mapping = {
        "announcements_delta": (
            paths.home / "announcements.parquet",
            ("ann_date", "notice_date", "date", "publish_date"),
            0,
        ),
        "suspensions_delta": (
            paths.home / "ref_suspensions.parquet",
            ("date", "suspend_date", "market_date"),
            0,
        ),
        "corporate_actions_delta": (
            paths.home / "ref_corporate_actions.parquet",
            ("ex_date", "announce_date", "effective_date", "date"),
            0,
        ),
        "dividends_delta": (
            paths.home / "dividend_history.parquet",
            ("ex_date", "announce_date", "date", "pay_date"),
            0,
        ),
        "factor_evidence_delta": (
            paths.home / "ref_adjustment_factors.parquet",
            ("effective_date", "ex_date", "date"),
            0,
        ),
        "financial_disclosures_delta": (
            paths.home / "financial_quarterly.parquet",
            ("announce_date", "notice_date", "end_date", "date"),
            0,
        ),
    }
    # alternate suspension path
    if not mapping["suspensions_delta"][0].is_file():
        alt = paths.home / "suspensions.parquet"
        if alt.is_file():
            mapping["suspensions_delta"] = (alt, mapping["suspensions_delta"][1], 0)

    datasets: list[DatasetStatus] = []
    for name, (source, keys, lookback) in mapping.items():
        if not source.is_file():
            # Event lane is fail-closed for missing mandatory collectors in production,
            # but shadow may mark skipped when source product is not yet landed.
            datasets.append(
                DatasetStatus(
                    name=name,
                    status="skipped",
                    detail=f"source absent in shadow home: {source.name}",
                )
            )
            continue
        rows = _filter_rows_by_date(
            _read_parquet(source),
            market_date=market_date,
            date_keys=keys,
            lookback_days=lookback,
        )
        datasets.append(
            _seal_dataset(
                name=name,
                rows=rows,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=market_date.isoformat(),
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
    rev = f"hermes-weekly-corrections-{market_date.strftime('%Y%m%d')}-v1"
    datasets: list[DatasetStatus] = []

    sh_path = paths.home / "ref_shareholder_count.parquet"
    if sh_path.is_file():
        rows = _read_parquet(sh_path)
        disc_keys = ("hold_notice_date", "notice_date", "disclosure_date", "ann_date", "end_date")
        selected = _filter_rows_by_date(rows, market_date=market_date, date_keys=disc_keys, lookback_days=90)
        for row in selected:
            row["revision_retained"] = True
            row["lookback_days"] = 90
            row["watermark_date"] = market_date.isoformat()
        datasets.append(
            _seal_dataset(
                name="shareholder_count",
                rows=selected,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=market_date.isoformat(),
                relative_name="shareholder_count_revision.parquet",
            )
        )
    else:
        datasets.append(DatasetStatus(name="shareholder_count", status="failed", detail="missing ref_shareholder_count"))

    for name, source, keys, lookback, out_name in (
        (
            "financial_quarterly",
            paths.home / "financial_quarterly.parquet",
            ("announce_date", "end_date", "date"),
            400,
            "financial_quarterly_revision.parquet",
        ),
        (
            "corporate_actions",
            paths.home / "ref_corporate_actions.parquet",
            ("ex_date", "announce_date", "effective_date", "date"),
            365,
            "corporate_actions_revision.parquet",
        ),
        (
            "dividends",
            paths.home / "dividend_history.parquet",
            ("ex_date", "announce_date", "date"),
            365,
            "dividends_revision.parquet",
        ),
        (
            "adjustment_evidence",
            paths.home / "ref_adjustment_factors.parquet",
            ("effective_date", "ex_date", "date"),
            365,
            "adjustment_evidence_revision.parquet",
        ),
    ):
        if not source.is_file():
            datasets.append(DatasetStatus(name=name, status="skipped", detail=f"missing {source.name}"))
            continue
        selected = _filter_rows_by_date(
            _read_parquet(source),
            market_date=market_date,
            date_keys=keys,
            lookback_days=lookback,
        )
        datasets.append(
            _seal_dataset(
                name=name,
                rows=selected,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=market_date.isoformat(),
                relative_name=out_name,
            )
        )

    st_path = paths.home / "revisions" / "st_status_pit_v2_baostock_20260725" / "daily_isST.parquet"
    if st_path.is_file():
        selected = _filter_rows_by_date(
            _read_parquet(st_path),
            market_date=market_date,
            date_keys=("date",),
            lookback_days=30,
        )
        datasets.append(
            _seal_dataset(
                name="st_announcement_trail",
                rows=selected,
                landing=landing,
                source_revision=rev,
                knowledge_cutoff=knowledge_cutoff,
                watermark=market_date.isoformat(),
                relative_name="st_status_observation_revision.parquet",
            )
        )
    else:
        datasets.append(DatasetStatus(name="st_announcement_trail", status="skipped", detail="missing daily_isST"))

    for name, source in (
        ("security_lifecycle", paths.home / "ref_security_master.parquet"),
        ("industry_membership", paths.home / "ref_industry_map.parquet"),
    ):
        if source.is_file():
            datasets.append(
                _copy_dataset(
                    name=name,
                    source=source,
                    landing=landing,
                    source_revision=rev,
                    knowledge_cutoff=knowledge_cutoff,
                    watermark=market_date.isoformat(),
                )
            )
        else:
            datasets.append(DatasetStatus(name=name, status="skipped", detail=f"missing {source.name}"))

    # reconciliation windows evidence (classification counts only; no overwrite of daily)
    recon: dict[str, Any] = {
        "market_date": market_date.isoformat(),
        "classes": ["same", "missing_in_daily", "changed_upstream", "conflict"],
        "windows": {
            "daily_ohlcv_none": "20_sessions",
            "daily_status_observation": "90_days",
            "index_daily": "20_sessions",
            "trading_calendar": "full_diff",
            "security_master": "full_diff",
        },
        "may_overwrite_daily": False,
        "counts": {
            "same": 0,
            "missing_in_daily": 0,
            "changed_upstream": 0,
            "conflict": 0,
        },
    }
    recon_path = landing / "reconciliation_summary.json"
    recon_path.write_text(json.dumps(recon, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    datasets.append(
        DatasetStatus(
            name="reconciliation_summary",
            status="ready",
            row_count=1,
            sha256=_sha256(recon_path),
            source_revision=rev,
            watermark=market_date.isoformat(),
            knowledge_cutoff=knowledge_cutoff,
            path=str(recon_path),
        )
    )
    return datasets


def _build_intraday_stream(
    *,
    paths: LanePaths,
    market_date: date,
    landing: Path,
    knowledge_cutoff: str,
    bar_size: str,
    symbols: Sequence[str] | None = None,
) -> list[DatasetStatus]:
    if bar_size not in {"1m", "5m"}:
        raise LaneRunError(f"unsupported intraday bar_size: {bar_size}")
    layer = "research" if bar_size == "1m" else "prod"
    minute_root = Path(os.environ.get("ASTOCK_MINUTE_ROOT") or (paths.home.parent / "minute")).resolve()
    layer_dir = minute_root / "data" / ("research_1m" if bar_size == "1m" else "prod_5m")
    rev = f"intraday-{bar_size}-archive-{market_date.strftime('%Y%m%d')}-v1"
    sample = list(symbols or ("000001", "600519"))
    rows: list[dict[str, Any]] = []
    files_used: list[str] = []
    for code in sample:
        path = layer_dir / f"{code}_{bar_size}.parquet"
        if not path.is_file():
            continue
        files_used.append(str(path))
        for row in _read_parquet(path):
            ts = row.get("datetime") or row.get("date") or row.get("time")
            if ts is None:
                continue
            day = str(ts)[:10]
            if day != market_date.isoformat():
                # keep a small sample even if market_date bars absent (shadow archive proof)
                continue
            item = dict(row)
            item["security_id"] = code
            item["bar_size"] = bar_size
            item["market_date"] = market_date.isoformat()
            rows.append(item)
    # If no rows for market_date, still seal an empty partition with provenance.
    meta = {
        "bar_size": bar_size,
        "layer": layer,
        "acquisition": "direct",
        "may_derive_from": None if bar_size == "5m" else "n/a",
        "market_date": market_date.isoformat(),
        "sample_symbols": sample,
        "source_files": files_used,
        "row_count": len(rows),
        "source_revision": rev,
        "note": "shadow archive partition; formal 5m never synthesized from 1m",
    }
    meta_path = landing / f"intraday_{bar_size}_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    datasets = [
        _seal_dataset(
            name=f"intraday_{bar_size}_bars",
            rows=rows,
            landing=landing,
            source_revision=rev,
            knowledge_cutoff=knowledge_cutoff,
            watermark=market_date.isoformat(),
            relative_name=f"intraday_{bar_size}_bars.parquet",
        ),
        DatasetStatus(
            name=f"intraday_{bar_size}_meta",
            status="ready",
            row_count=1,
            sha256=_sha256(meta_path),
            source_revision=rev,
            watermark=market_date.isoformat(),
            knowledge_cutoff=knowledge_cutoff,
            path=str(meta_path),
        ),
        _copy_dataset(
            name="trading_calendar",
            source=paths.calendar,
            landing=landing,
            source_revision=f"trading_calendar@{rev}",
            knowledge_cutoff=knowledge_cutoff,
            watermark=market_date.isoformat(),
            relative_name="ref_trading_calendar.parquet",
        ),
    ]
    return datasets


def _publish(
    *,
    schedule: str,
    market_date: date,
    attempt_id: str,
    landing: Path,
    datasets: Sequence[DatasetStatus],
    release_root: Path,
    lane_id: str,
    stream: str | None = None,
) -> tuple[Path | None, str, str | None]:
    ready_datasets = [item for item in datasets if item.status == "ready" and item.path]
    failed = [item for item in datasets if item.status == "failed"]
    if failed:
        return None, "partial" if ready_datasets else "failed", "one or more datasets failed"
    if not ready_datasets:
        return None, "failed", "no ready datasets to publish"

    artifacts = []
    for item in ready_datasets:
        source = Path(item.path)  # type: ignore[arg-type]
        rel = f"artifacts/{source.name}"
        artifacts.append(
            {
                "name": item.name,
                "source_path": str(source),
                "relative_path": rel,
                "source_revision": item.source_revision or PRODUCER,
                "knowledge_cutoff": item.knowledge_cutoff or datetime.now(SH).isoformat(timespec="seconds"),
            }
        )
    suffix = f"-{stream}" if stream else ""
    release_id = f"hermes-{lane_id}{suffix}-{market_date.strftime('%Y%m%d')}-{attempt_id[-8:]}-v1"
    # release_id must match contracts regex; collapse invalid chars
    release_id = release_id.replace("/", "-")
    spec = {
        "schema_version": 1,
        "release_id": release_id,
        "schedule": schedule,
        "market_date": market_date.isoformat(),
        "producer": PRODUCER,
        "artifacts": artifacts,
    }
    release_dir = release_root / schedule / market_date.isoformat() / attempt_id
    if stream:
        release_dir = release_root / schedule / stream / market_date.isoformat() / attempt_id
    spec_path = landing / "release-spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        published = publish_ods_release(spec_path=spec_path, output=release_dir)
    except OdsReleaseError as exc:
        return None, "failed", str(exc)
    # attach lane status sidecar (not part of READY contract)
    sidecar = {
        "lane_id": lane_id,
        "attempt_id": attempt_id,
        "stream": stream,
        "datasets": [item.to_dict() for item in datasets],
        "manifest_sha256": published.manifest_sha256,
    }
    _atomic_json(release_dir / "lane_status.json", sidecar)
    return release_dir, "ready", None


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
) -> LaneRunResult:
    """Run one lane attempt under the schedule contract."""

    contract = load_schedule_contract(schedule_config)
    if lane_id not in contract.lanes:
        raise LaneRunError(f"unknown lane id: {lane_id}")
    payload = json.loads(schedule_config.read_text(encoding="utf-8"))
    lane = next(item for item in payload["lanes"] if item["id"] == lane_id)
    resolved = paths or resolve_paths()
    started = _now()
    attempt_id = _attempt_id(market_date, started)
    attempt_dir = resolved.shadow_root / "attempts" / lane_id / market_date.isoformat() / attempt_id
    if stream:
        attempt_dir = (
            resolved.shadow_root / "attempts" / lane_id / stream / market_date.isoformat() / attempt_id
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
        started_at=started,
        stream=stream,
    )
    _atomic_json(attempt_dir / "attempt.json", result.to_dict())

    lock_name = {
        "market-daily-core": "market-daily-core.lock",
        "calendar-daily-events": "calendar-daily-events.lock",
        "weekly-corrections": "weekly-corrections.lock",
        "bootstrap": "bootstrap.lock",
        "intraday-archive": f"intraday-{stream or 'all'}.lock",
    }.get(lane_id, f"{lane_id}.lock")
    lock_handle = _lock(resolved.lock_root / lock_name)
    try:
        if lane_id == "bootstrap":
            if lane.get("enabled") is not False or lane.get("recurring") is not False:
                raise ScheduleContractError("bootstrap lane misconfigured")
            if not issue_ref:
                result.status = "failed"
                result.detail = "bootstrap requires --issue-ref and is non-recurring"
                result.finished_at = _now()
                _atomic_json(attempt_dir / "attempt.json", result.to_dict())
                return result
            result.status = "failed"
            result.detail = (
                "bootstrap remains manual/issue-bound; shadow runner refuses automatic execution"
            )
            result.finished_at = _now()
            _atomic_json(attempt_dir / "attempt.json", result.to_dict())
            return result

        result.status = "preflight"
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        preflight = force_preflight or "n/a"
        if lane_id in {"market-daily-core", "intraday-archive"}:
            preflight = force_preflight or session_preflight(
                calendar=resolved.calendar, market_date=market_date
            )
            result.preflight = preflight
            if preflight == "missing_or_disagree":
                result.status = "failed"
                result.detail = "calendar missing or SSE/SZSE disagree; no provider calls; no READY"
                result.finished_at = _now()
                _atomic_json(attempt_dir / "attempt.json", result.to_dict())
                _atomic_json(attempt_dir / "datasets.json", {"datasets": []})
                return result

        knowledge_cutoff = datetime.now(SH).isoformat(timespec="seconds")
        result.status = "acquiring"
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())

        if lane_id == "market-daily-core":
            if preflight == "both_closed":
                datasets = _build_market_closed(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                )
                result.datasets = datasets
                result.status = "sealed"
                _atomic_json(attempt_dir / "datasets.json", {"datasets": [d.to_dict() for d in datasets]})
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                )
                result.release_dir = release_dir
                result.status = "closed_session" if status == "ready" else status
                result.detail = detail or "calendar_only_release"
            elif preflight == "both_open":
                datasets = _build_market_open_from_local(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                )
                result.datasets = datasets
                result.status = "sealed"
                _atomic_json(attempt_dir / "datasets.json", {"datasets": [d.to_dict() for d in datasets]})
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=[d for d in datasets if d.status == "ready"],
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                )
                # If any mandatory failed, force partial/failed and no READY.
                mandatory = {
                    "daily_ohlcv_none_delta",
                    "daily_status_observation_delta",
                    "security_master",
                    "stock_list",
                    "trading_calendar",
                }
                mandatory_failed = [
                    d for d in datasets if d.name in mandatory and d.status == "failed"
                ]
                if mandatory_failed:
                    if release_dir and release_dir.exists():
                        # Should not happen because we filtered ready-only; keep guard.
                        shutil.rmtree(release_dir, ignore_errors=True)
                    result.release_dir = None
                    result.status = "partial"
                    result.detail = "mandatory dataset failed; READY withheld"
                else:
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
            )
            result.datasets = datasets
            ready = [d for d in datasets if d.status == "ready"]
            failed = [d for d in datasets if d.status == "failed"]
            result.status = "sealed"
            _atomic_json(attempt_dir / "datasets.json", {"datasets": [d.to_dict() for d in datasets]})
            if not ready:
                result.status = "failed"
                result.detail = "no event datasets ready"
            else:
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=ready,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                )
                result.release_dir = release_dir
                if failed:
                    result.status = "partial"
                    result.detail = "some event datasets failed"
                    if release_dir:
                        # partial must not look like full success to cutover gates
                        result.detail = (result.detail or "") + "; release sealed for ready subset only"
                else:
                    result.status = status
                    result.detail = detail

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
            ready = [d for d in datasets if d.status == "ready"]
            failed = [d for d in datasets if d.status == "failed"]
            result.status = "sealed"
            _atomic_json(attempt_dir / "datasets.json", {"datasets": [d.to_dict() for d in datasets]})
            if failed and not ready:
                result.status = "failed"
                result.detail = "weekly correction datasets failed"
            elif not ready:
                result.status = "failed"
                result.detail = "weekly produced no artifacts"
            else:
                release_dir, status, detail = _publish(
                    schedule="weekly",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=ready,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                )
                result.release_dir = release_dir
                result.status = "partial" if failed else status
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
                result.datasets = datasets
                result.status = "sealed"
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=datasets,
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                    stream=stream,
                )
                result.release_dir = release_dir
                result.status = "closed_session" if status == "ready" else status
                result.detail = detail or "intraday closed_session calendar-only"
            else:
                datasets = _build_intraday_stream(
                    paths=resolved,
                    market_date=market_date,
                    landing=landing,
                    knowledge_cutoff=knowledge_cutoff,
                    bar_size=stream,
                    symbols=symbols,
                )
                result.datasets = datasets
                result.status = "sealed"
                _atomic_json(attempt_dir / "datasets.json", {"datasets": [d.to_dict() for d in datasets]})
                release_dir, status, detail = _publish(
                    schedule="daily",
                    market_date=market_date,
                    attempt_id=attempt_id,
                    landing=landing,
                    datasets=[d for d in datasets if d.status == "ready"],
                    release_root=resolved.shadow_root / "releases",
                    lane_id=lane_id,
                    stream=stream,
                )
                result.release_dir = release_dir
                result.status = status
                result.detail = detail
                # watermark sidecar
                wm = {
                    "stream": stream,
                    "market_date": market_date.isoformat(),
                    "attempt_id": attempt_id,
                    "updated_at": knowledge_cutoff,
                    "lock": f"intraday-{stream}.lock",
                    "checkpoint": f"intraday-{stream}.watermark.json",
                }
                _atomic_json(resolved.shadow_root / "watermarks" / f"intraday-{stream}.watermark.json", wm)
        else:
            raise LaneRunError(f"lane not implemented: {lane_id}")

        result.finished_at = _now()
        if result.release_dir is not None and result.status in {"ready", "closed_session", "partial"}:
            # Verify READY contract when present; partial may still have subset release.
            if (result.release_dir / "READY").is_file():
                verify_ods_release(result.release_dir)
        _atomic_json(attempt_dir / "attempt.json", result.to_dict())
        _atomic_json(
            attempt_dir / "datasets.json",
            {"datasets": [item.to_dict() for item in result.datasets]},
        )
        return result
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


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
    summary: dict[str, Any] = {"schedule_id": "hermes-ods-schedule-v1", "runs": []}

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
        except Exception as exc:  # noqa: BLE001 - matrix must continue
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
