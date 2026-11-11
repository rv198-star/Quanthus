"""Publish complete standard Daily from the scheduled cycle after native verification."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .base22_bounded import Budget, Limits
from .base_build_inputs import file_sha
from .daily_base_alignment import load
from .standard_daily import publish


def finish(
    config: dict[str, Any],
    run: Path,
    day: date,
    value: dict[str, Any],
    *,
    market: Path | None = None,
    events: Path,
) -> None:
    if not config.get("standard_delivery"):
        return
    governance = load(Path(config["release_config"]))
    if governance.get("layers", {}).get("dwd", {}).get("format") != "native_standard_base":
        return
    if market is not None:
        sources = {"market_daily_core": market, "calendar_daily_events": events}
        projection = Path(value["product"])
        observation = None
    else:
        reference = config.get("calendar_reference_release")
        reference_sha = config.get("calendar_reference_manifest_sha256")
        index = Path(config["root"]) / "DATES.json"
        if index.exists():
            candidates = [
                (d, e)
                for d, e in load(index)["dates"].items()
                if d < day.isoformat() and e.get("standard_delivery") and e.get("product")
            ]
            if candidates:
                _, latest = max(candidates, key=lambda pair: pair[0])
                reference = str(Path(latest["standard_delivery"]) / "sources/market_daily_core")
                reference_sha = latest["source_manifest_sha256"]["market_daily_core"]
        if not reference or file_sha(Path(reference) / "release.json") != reference_sha:
            raise ValueError("STANDARD_DAILY_LIVE_EXACT_REFERENCE_REQUIRED")
        sources = {"calendar_reference": Path(reference), "calendar_daily_events": events}
        projection = None
        observation = run / "calendar/calendar.json"
        if file_sha(observation) != value["calendar_sha256"]:
            raise ValueError("STANDARD_DAILY_LIVE_CALENDAR_CHANGED")
    with Budget(
        run / "standard-delivery-work",
        [Path(config["base"]), Path(config["release_config"]), *sources.values()],
        Limits(allocated_bytes=12 * 1024**3, free_floor_bytes=16 * 1024**3),
    ) as budget:
        delivery = run / "standard-delivery"
        body = publish(
            projection=projection,
            sources=sources,
            config=Path(config["release_config"]),
            base_root=Path(config["base"]),
            output=delivery,
            code_revision=config["pipeline_revision"],
            budget=budget,
            calendar_observation=observation,
        )
    value.update(
        standard_delivery=str(delivery),
        standard_delivery_manifest_sha256=file_sha(delivery / "manifest.json"),
        standard_delivery_identity=body["request_identity"],
        base_manifest_sha256=config["base_manifest_sha256"],
    )
