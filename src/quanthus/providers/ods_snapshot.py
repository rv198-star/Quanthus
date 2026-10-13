"""Window canonical ODS sources into an immutable lane attempt.

This adapter does not acquire data from an upstream vendor. It reads the
node-local canonical ODS/Base files and writes only the output path supplied by
the lane runner. Missing or unreadable sources fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

DATASET_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "corporate-actions": (
        "ref_corporate_actions.parquet",
        ("announce_date", "ex_date", "effective_date", "date"),
    ),
    "dividends": (
        "dividend_history.parquet",
        ("announce_date", "ex_dividend_date", "ex_date", "date"),
    ),
    "adjustment-evidence": (
        "ref_adjustment_factors.parquet",
        ("knowledge_cutoff", "effective_date", "ex_date", "date"),
    ),
    "financial-disclosures": (
        "financial_quarterly.parquet",
        ("publish_deadline", "report_date", "announce_date", "end_date", "date"),
    ),
    "financial-quarterly": (
        "financial_quarterly.parquet",
        ("publish_deadline", "report_date", "announce_date", "end_date", "date"),
    ),
    "suspensions": (
        "ref_suspensions.parquet",
        (
            "knowledge_cutoff",
            "as_of_query_date",
            "suspend_start",
            "announce_date",
            "date",
        ),
    ),
    "shareholder-count": (
        "ref_shareholder_count.parquet",
        ("hold_notice_date", "end_date", "knowledge_cutoff", "date"),
    ),
    "st-status": (
        "ref_st_status_pit.parquet",
        ("announcement_date", "effective_start", "knowledge_cutoff", "date"),
    ),
}


def _parse_day(raw: str) -> date:
    return date.fromisoformat(raw[:10])


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nat", "nan"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _row_in_window(row: dict[str, Any], keys: tuple[str, ...], start: date, end: date) -> bool:
    for key in keys:
        day = _as_date(row.get(key))
        if day is not None and start <= day <= end:
            return True
    return False


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


def _write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    normalized = [{key: _jsonable(value) for key, value in row.items()} for row in rows]
    if not normalized:
        normalized = [{"_empty": True}]

    keys = sorted({key for row in normalized for key in row})
    arrays = {
        key: pa.array(
            [None if row.get(key) is None else str(row[key]) for row in normalized],
            type=pa.string(),
        )
        for key in keys
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(arrays), path)


def collect(
    dataset: str,
    start: date,
    end: date,
    output: Path,
    home: Path,
) -> dict[str, Any]:
    source_config = DATASET_SOURCES.get(dataset)
    if source_config is None:
        raise ValueError(f"unknown dataset: {dataset}")
    source_name, date_keys = source_config
    source = home / source_name
    if not source.is_file():
        raise FileNotFoundError(f"source missing for {dataset}: {source}")

    raw_rows = _read_rows(source)
    selected = [dict(row) for row in raw_rows if _row_in_window(dict(row), date_keys, start, end)]
    acquired_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in selected:
        row.update(
            provider_dataset=dataset,
            provider_source_path=str(source),
            provider_window_start=start.isoformat(),
            provider_window_end=end.isoformat(),
            provider_acquired_at=acquired_at,
        )
    _write_parquet(selected, output)

    result = {
        "dataset": dataset,
        "source": str(source),
        "output": str(output),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "raw_row_count": len(raw_rows),
        "selected_row_count": len(selected),
        "acquired_at": acquired_at,
        "provider": f"quanthus.ods-snapshot/{dataset}",
        "acquisition_kind": "local_projection",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_refreshed": False,
        "source_snapshot_as_of": None,
        "source_freshness_status": "unknown",
        "reason_code": "SOURCE_NOT_REFRESHED",
        "current_no_change_proven": False,
        "projection_observed_at": acquired_at,
        "provider_acquisition_at": None,
    }
    output.with_suffix(output.suffix + ".meta.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-ods-snapshot")
    parser.add_argument("dataset", choices=sorted(DATASET_SOURCES))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--home", default="")
    args = parser.parse_args(argv)

    start = _parse_day(args.start)
    end = _parse_day(args.end)
    if end < start:
        print(f"invalid window: {start} > {end}", file=sys.stderr)
        return 2
    home = Path(args.home or os.environ.get("ASTOCK_HOME") or "/root/work/astock/data").resolve()
    try:
        result = collect(args.dataset, start, end, Path(args.output).resolve(), home)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"collector failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
