"""Reprocess a failed status-only attempt from preserved responses, without supplier calls."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
from dataclasses import fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines import lane_runner as lanes
from quanthus.pipelines.p10_delivery_common import atomic, file_digest, object_at, require_revision
from quanthus.providers.acquisition_evidence import POLICY, suspended_status, verify_capture


def recover(*, source: Path, output: Path, revision: str) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    require_revision(revision)
    if os.environ.get("HERMES_ODS_PREVENTION_POLICY") != POLICY or output.exists():
        raise ValueError("RECOVERY_REQUIRES_NEW_PREVENTION_ATTEMPT")
    landing = source / "landing"
    state = object_at(landing / "lane_status.json")
    day = date.fromisoformat(state["market_date"])
    if (
        output.parent.name != str(day)
        or output.parent.parent.name != "market-daily-core"
        or output.parents[2].name != "attempts"
        or output.parent != source.parent
    ):
        raise ValueError("RECOVERY_OUTPUT_SCOPE_DIFFERS")
    if state["lane_id"] != "market-daily-core" or state["stream"] is not None:
        raise ValueError("RECOVERY_LANE_DIFFERS")
    source_items = state["datasets"]
    failed = [x for x in source_items if x["status"] != "ready"]
    if len(failed) != 1 or failed[0]["name"] != "daily_status_observation_delta":
        raise ValueError("RECOVERY_ONLY_PROVEN_SUSPENDED_STATUS")
    identities = {}
    for item in source_items:
        path = Path(item["path"])
        if path.parent != landing or path.is_symlink() or file_digest(path) != item["sha256"]:
            raise ValueError("PRESERVED_DATASET_IDENTITY_DIFFERS")
        identities[path.name] = item["sha256"]
    universe = object_at(landing / "source_universe.json")
    quality = object_at(landing / "acquisition_quality.json")
    if (
        universe["market_date"] != str(day)
        or quality["universe_digest"] != universe["universe_digest"]
        or quality["original_responses_sha256"]
        != file_digest(landing / "original-responses.tar.gz")
    ):
        raise ValueError("RECOVERY_UNIVERSE_OR_RAW_DIFFERS")
    raw_inventory = {x["path"]: x for x in quality["raw_members"]}
    raw_paths = sorted((landing / "original-responses").glob("*.json.gz"))
    if {x.name for x in raw_paths} != set(raw_inventory):
        raise ValueError("RECOVERY_RAW_CLOSURE_DIFFERS")
    rows = pq.ParquetFile(landing / "daily_status_observation_delta.parquet").read().to_pylist()
    present = {x["security_id"] for x in rows}
    if len(present) != len(rows) or not present <= set(universe["codes"]):
        raise ValueError("RECOVERY_EXISTING_STATUS_KEYS_DIFFERS")
    missing = set(universe["codes"]) - present
    master = {
        x["security_id"]: x
        for x in pq.ParquetFile(landing / "ref_security_master.parquet").read().to_pylist()
    }
    bars = (
        pq.ParquetFile(landing / "daily_ohlcv_none_delta.parquet")
        .read(columns=["security_id", "market_date"])
        .to_pylist()
    )
    bar_keys = {x["security_id"] for x in bars}
    if (
        set(master) != set(universe["codes"])
        or len(bar_keys) != len(bars)
        or any(x["market_date"] != str(day) for x in bars)
        or any(
            x["market_date"] != str(day)
            or x["tradestatus"] not in {"0", "1"}
            or x["isST"] not in {"0", "1"}
            for x in rows
        )
        or any(x["tradestatus"] == "1" and x["security_id"] not in bar_keys for x in rows)
    ):
        raise ValueError("RECOVERY_EXISTING_DAILY_CLOSURE_DIFFERS")
    if not missing or any(master[x]["tradestatus"] != "0" for x in missing):
        raise ValueError("RECOVERY_MISSING_OPEN_OR_UNKNOWN_SECURITY")
    selected: dict[str, tuple[str, dict[str, Any], str]] = {}
    required_fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turn",
        "pctChg",
        "tradestatus",
        "isST",
        "peTTM",
        "pbMRQ",
        "psTTM",
        "pcfNcfTTM",
    ]
    for path in raw_paths:
        if file_digest(path) != raw_inventory[path.name]["sha256"]:
            raise ValueError("RECOVERY_CAPTURE_SHA_DIFFERS")
        capture = verify_capture(path)
        request = capture["request"]
        provider_code = request.get("code", "")
        code = provider_code.split(".")[-1]
        if code not in missing or capture["status"] != "0":
            continue
        if (
            request.get("api") != "query_history_k_data_plus"
            or request.get("frequency") != "d"
            or request.get("adjustflag") != "3"
            or request.get("fields", "").split(",") != required_fields
            or request.get("start_date", request.get("start")) != str(day)
            or request.get("end_date", request.get("end")) != str(day)
        ):
            raise ValueError("RECOVERY_REQUEST_DIFFERS")
        if provider_code != lanes._to_bs_code(code):
            raise ValueError("RECOVERY_VENUE_DIFFERS")
        raw = json.loads(base64.b64decode(capture["raw_base64"]))
        if raw.get("error_code") != "0" or raw.get("fields") != required_fields:
            raise ValueError("RECOVERY_RAW_SCHEMA_DIFFERS")
        for original in raw["rows"]:
            projected = suspended_status(
                original,
                code=code,
                provider_code=provider_code,
                day=str(day),
                observed_at=capture["warehouse_observed_at"],
            )
            if projected is None:
                raise ValueError("RECOVERY_NOT_SUSPENSION_WITH_MISSING_NUMERIC")
            prior = selected.get(code)
            if prior and prior[1]["isST"] != projected["isST"]:
                raise ValueError("RECOVERY_STATUS_CONFLICT")
            stamp = capture["warehouse_observed_at"]
            if prior is None or stamp > prior[0]:
                selected[code] = (stamp, projected, path.name)
    if set(selected) != missing:
        raise ValueError("RECOVERY_SUSPENSION_EVIDENCE_INCOMPLETE")
    if shutil.disk_usage(output.parent).free < 4 * 1024**3 + sum(
        x.stat().st_size for x in landing.rglob("*") if x.is_file()
    ):
        raise ValueError("RECOVERY_SCRATCH_RESERVE")
    output.mkdir()
    new = output / "landing"
    shutil.copytree(landing, new)
    # Only status rows change; source artifacts and raw clocks remain byte-identical.
    lanes._write_parquet(
        [*rows, *(selected[x][1] for x in sorted(missing))],
        new / "daily_status_observation_delta.parquet",
    )
    allowed = {f.name for f in fields(lanes.DatasetStatus)}
    datasets = []
    for original in source_items:
        name = original["name"]
        if name in {"original_responses", "source_universe", "acquisition_quality", "lane_status"}:
            continue
        item = lanes.DatasetStatus(**{k: v for k, v in original.items() if k in allowed})
        item.path = str(new / Path(original["path"]).name)
        if name.startswith("local_projection_evidence_"):
            item.name = name.removeprefix("local_projection_evidence_")
        if name == "daily_status_observation_delta":
            item.status = "ready"
            item.detail = None
            item.row_count = len(rows) + len(missing)
            item.sha256 = file_digest(Path(item.path))
            item.source_revision = "p10-suspended-response-reprocessing-v1@" + revision
            item.knowledge_cutoff = datetime.now(timezone.utc).isoformat()
        datasets.append(item)
    receipt = {
        "source_attempt": str(source),
        "source_files": identities,
        "recovered_status_count": len(missing),
        "unchanged_price_artifacts": True,
        "supplier_calls": 0,
        "code_revision": revision,
        "original_response_files": {x: selected[x][2] for x in sorted(missing)},
        "reprocessed_at": datetime.now(timezone.utc).isoformat(),
        "historical_pit_authorized": False,
    }
    atomic(new / "response-reprocessing.json", receipt)

    def frozen_source_check() -> None:
        for name, sha in identities.items():
            if file_digest(landing / name) != sha:
                raise ValueError("RECOVERY_SOURCE_CHANGED")
            if (
                name
                not in {
                    "daily_status_observation_delta.parquet",
                    "acquisition_quality.json",
                    "original-responses.tar.gz",
                }
                and file_digest(new / name) != sha
            ):
                raise ValueError("RECOVERY_COPIED_SOURCE_CHANGED")
        if object_at(new / "source_universe.json") != universe:
            raise ValueError("RECOVERY_FROZEN_UNIVERSE_CHANGED")
        for name, item in raw_inventory.items():
            if file_digest(landing / "original-responses" / name) != item["sha256"]:
                raise ValueError("RECOVERY_SOURCE_CAPTURE_CHANGED")
            if file_digest(new / "original-responses" / name) != item["sha256"]:
                raise ValueError("RECOVERY_COPIED_CAPTURE_CHANGED")

    datasets.append(
        lanes.DatasetStatus(
            name="response_reprocessing",
            status="ready",
            row_count=1,
            path=str(new / "response-reprocessing.json"),
            sha256=file_digest(new / "response-reprocessing.json"),
            source_revision=revision,
            knowledge_cutoff=str(receipt["reprocessed_at"]),
        )
    )
    release, status, detail = lanes._publish(
        schedule="daily",
        market_date=day,
        attempt_id=output.name,
        landing=new,
        datasets=datasets,
        release_root=output.parents[3] / "releases",
        lane_id="market-daily-core",
        schedule_id=state["schedule_id"],
        schedule_contract_revision=state["schedule_contract_revision"],
        schedule_config_sha256=state["schedule_config_sha256"],
        frozen_universe=universe,
        snapshot_check=frozen_source_check,
    )
    for name, sha in identities.items():
        if file_digest(landing / name) != sha:
            raise ValueError("RECOVERY_SOURCE_CHANGED")
    result = {**receipt, "status": status, "detail": detail, "release_dir": str(release)}
    atomic(output / "recovery-result.json", result)
    if status != "ready":
        raise ValueError("RECOVERY_PUBLISH_WITHHELD")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-attempt", type=Path, required=True)
    parser.add_argument("--output-attempt", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            recover(
                source=args.source_attempt, output=args.output_attempt, revision=args.code_revision
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
