"""Source-only worker, executed with a separately pinned provider runtime.

The controller invokes this file, not a legacy Daily/DWD cycle. Provider modules
come exclusively from the published collector checkout. No legacy environment,
CURRENT, one-minute acquisition or consumer activation is involved.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

COLLECTOR = "f839af0033ab4aa8cbe6d9fb933f29db88792e91"


def runtime(path: Path) -> None:
    if path.is_symlink() or not path.is_absolute():
        raise ValueError("DAILY_COLLECTOR_PHYSICAL_ABSOLUTE_PATH_REQUIRED")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    if sha != COLLECTOR or subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=path
    ):
        raise ValueError("DAILY_COLLECTOR_REVISION_OR_TRACKED_STATE_DIFFERS")
    # Verify the imported namespace too: PYTHONPATH alone is not proof of origin.
    import quanthus.providers.security_universe as universe  # type: ignore[import-untyped]

    if Path(universe.__file__).resolve() != path / "src/quanthus/providers/security_universe.py":
        raise ValueError("DAILY_COLLECTOR_IMPORT_ORIGIN_DIFFERS")


def calendar(start: date, end: date, output: Path, home: Path | None = None) -> dict[str, Any]:
    """Retain original calendar response and require every requested date."""
    import baostock as bs  # type: ignore[import-untyped]

    from quanthus.pipelines.p10_delivery_common import (  # type: ignore[import-untyped]
        atomic,
        encoded,
        file_digest,
    )
    from quanthus.providers.acquisition_evidence import (  # type: ignore[import-untyped]
        capture,
        clock,
    )
    from quanthus.providers.baostock_transport import set_socket_timeout

    if output.exists() or end < start or (end - start).days > 90:
        raise ValueError("DAILY_CALENDAR_NEW_BOUNDED_OUTPUT_REQUIRED")
    output.mkdir(parents=True)
    with contextlib.redirect_stdout(io.StringIO()):
        login = bs.login()
    if str(login.error_code) != "0":
        raise ValueError("DAILY_CALENDAR_LOGIN_FAILED")
    try:
        if not set_socket_timeout(30):
            raise ValueError("DAILY_CALENDAR_FINITE_TIMEOUT_REQUIRED")
        began = clock()
        response = bs.query_trade_dates(start_date=str(start), end_date=str(end))
        rows = []
        while str(response.error_code) == "0" and response.next():
            rows.append(response.get_row_data())
        raw = encoded(
            {
                "fields": response.fields,
                "rows": rows,
                "error_code": response.error_code,
                "representation": "lossless_sdk_strings",
            }
        )
        capture(
            root=output,
            request={
                "provider": "baostock",
                "api": "query_trade_dates",
                "start_date": str(start),
                "end_date": str(end),
            },
            raw=raw,
            started_at=began,
            completed_at=clock(),
            status=str(response.error_code),
        )
        if str(response.error_code) != "0":
            raise ValueError("DAILY_CALENDAR_PROVIDER_FAILED")
        flags = {}
        for row in rows:
            item = dict(zip(response.fields, row, strict=True))
            key = date.fromisoformat(item["calendar_date"])
            if key in flags or item["is_trading_day"] not in {"0", "1"}:
                raise ValueError("DAILY_CALENDAR_DUPLICATE_OR_UNKNOWN_FLAG")
            flags[key] = item["is_trading_day"] == "1"
        expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
        if set(flags) != expected:
            raise ValueError("DAILY_CALENDAR_SCOPE_INCOMPLETE")
        result = {
            "kind": "cn02_actual_provider_calendar_v1",
            "observed_at": clock(),
            "start": str(start),
            "end": str(end),
            "dates": {str(k): v for k, v in sorted(flags.items())},
            "original_files": {p.name: file_digest(p) for p in sorted(output.iterdir())},
        }
        atomic(output / "calendar.json", result)
        if home is not None:
            import pyarrow as pa  # type: ignore[import-untyped]
            import pyarrow.parquet as pq  # type: ignore[import-untyped]

            home.mkdir(parents=True, exist_ok=True)
            baseline = home / "calendar-baseline.parquet"
            if not baseline.exists():
                with baseline.open("xb") as stream:
                    pq.write_table(
                        pa.Table.from_pylist(
                            [
                                {"exchange": venue, "trade_date": day, "is_open": int(flag)}
                                for day, flag in sorted(flags.items())
                                for venue in ["SSE", "SZSE", "BSE"]
                            ]
                        ),
                        stream,
                    )
        return result
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def lane(
    action: str,
    day: date,
    home: Path,
    runtime_root: Path,
    budget: int,
    seed_inventory: Path | None = None,
    seed_sha256: str | None = None,
) -> dict[str, Any]:
    from quanthus.pipelines import lane_runner

    seed = None
    producer = COLLECTOR
    original = lane_runner._baostock_fetch_day
    if seed_inventory is not None:
        if action != "market" or seed_sha256 is None:
            raise ValueError("DAILY_MARKET_ONLY_FROZEN_SEED_REQUIRED")
        # This file runs against the separate published collector namespace. Load
        # the additive adapter only from the same versioned pipeline checkout.
        spec = importlib.util.spec_from_file_location(
            "daily_base24_market_resume", Path(__file__).with_name("daily_base24_market_resume.py")
        )
        assert spec is not None and spec.loader is not None
        resume = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resume)
        seed = resume.load_seed(seed_inventory, seed_sha256, day)
        if seed["collector_revision"] != COLLECTOR:
            raise ValueError("DAILY_SEED_COLLECTOR_REVISION_DIFFERS")
        producer = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[4], text=True
        ).strip()
        if subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).parents[4],
            text=True,
        ):
            raise ValueError("DAILY_RESUME_ADAPTER_CLEAN_REVISION_REQUIRED")
        lane_runner._baostock_fetch_day = resume.adapter(original, seed, producer)

    home.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        ASTOCK_HOME=str(home),
        ASTOCK_ROOT=str(home.parent),
        HERMES_ODS_NODE_ID="cn02",
        HERMES_ODS_ACQUIRE_MODE="provider",
        HERMES_ODS_PREVENTION_POLICY="p10-acquisition-prevention-v1",
        QUANTHUS_COLLECTOR_SHA=producer,
    )
    try:
        result = lane_runner.run_lane(
            lane_id={"market": "market-daily-core", "events": "calendar-daily-events"}[action],
            market_date=day,
            schedule_config=runtime_root / "config/schedules/hermes-ods-schedule.v1.json",
            paths=lane_runner.resolve_paths(
                home=home, shadow_root=home / "shadow", calendar=home / "calendar-baseline.parquet"
            ),
            recovery_deadline_at=(
                datetime.now(timezone.utc) + timedelta(seconds=budget)
                if action == "market"
                else None
            ),
        )
    finally:
        lane_runner._baostock_fetch_day = original
    safe = {
        k: result.to_dict()[k]
        for k in [
            "lane_id",
            "market_date",
            "status",
            "release_dir",
            "attempt_dir",
            "started_at",
            "finished_at",
            "preflight",
        ]
    }
    safe["actual_provider_acquisition"] = True
    safe["collector_revision"] = COLLECTOR
    safe["adapter_revision"] = producer
    if seed is not None:
        safe["seed_inventory_sha256"] = seed_sha256
    if result.status not in {"ready", "closed_session"}:
        safe["qualified"] = False
    return safe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["calendar", "universe", "market", "events", "reference", "capture", "native"],
    )
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--universe-day", type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--market", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--budget", type=int, default=3600)
    parser.add_argument("--market-seed-inventory", type=Path)
    parser.add_argument("--market-seed-sha256")
    args = parser.parse_args()
    runtime(args.runtime)
    if not 30 <= args.budget <= 7200:
        raise ValueError("DAILY_PROVIDER_BOUNDED_BUDGET_REQUIRED")
    if (args.market_seed_inventory is not None or args.market_seed_sha256 is not None) and (
        args.action != "market" or not args.market_seed_inventory or not args.market_seed_sha256
    ):
        raise ValueError("DAILY_MARKET_ONLY_FROZEN_SEED_REQUIRED")
    # SDK chatter is retained in the private service log, never mixed into JSON.
    with contextlib.redirect_stdout(io.StringIO()):
        if args.action == "calendar":
            result = calendar(args.start, args.day, args.output, args.home)
        elif args.action == "universe":
            from quanthus.providers.security_universe import refresh

            os.environ["HERMES_ODS_PREVENTION_POLICY"] = "p10-acquisition-prevention-v1"
            source, value = refresh(home=args.home, day=args.day)
            result = {
                "market_date": value["market_date"],
                "population": value["population"],
                "universe_digest": value["universe_digest"],
                "root": str(source),
                "warehouse_observed_at": value["warehouse_observed_at"],
            }
            args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
        elif args.action in {"market", "events"}:
            if args.universe_day:
                if args.action != "events" or args.universe_day > args.day:
                    raise ValueError("DAILY_EVENT_UNIVERSE_DATE_INVALID")
                os.environ["HERMES_ODS_UNIVERSE_SOURCE_DATE"] = str(args.universe_day)
            result = lane(
                args.action,
                args.day,
                args.home,
                args.runtime,
                args.budget,
                args.market_seed_inventory,
                args.market_seed_sha256,
            )
            args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
        else:
            from quanthus.pipelines.p10_delivery_common import file_digest
            from quanthus.pipelines.p10_native_minute_delivery import (  # type: ignore[import-untyped]
                build,
                freeze_reference,
                reference,
            )
            from quanthus.providers.p10_native_m5_shard_capture import (  # type: ignore[import-untyped]
                capture_shard,
            )

            os.environ["HERMES_ODS_PREVENTION_POLICY"] = "p10-acquisition-prevention-v1"
            if args.action == "reference":
                value = freeze_reference(
                    args.market,
                    args.day.isoformat(),
                    args.output,
                    events_sha=file_digest(args.events / "release.json"),
                )
                result = {
                    "reference_digest": value["reference_digest"],
                    "market_date": value["market_date"],
                    "population": value["population"],
                }
            elif args.action == "capture":
                value = reference(args.reference)
                result = capture_shard(
                    codes=sorted(value["securities"]),
                    market_date=args.day,
                    market_manifest_sha=value["source_ods_manifest_sha256"],
                    output=args.output,
                    shard_index=args.shard_index,
                )
            else:
                result = build(
                    reference_file=args.reference,
                    capture_root=args.capture_root,
                    interval="5m",
                    product_root=args.output,
                    code_revision=COLLECTOR,
                )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status", "ready") in {"ready", "closed_session"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
