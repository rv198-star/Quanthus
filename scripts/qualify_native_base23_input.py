#!/usr/bin/env python3
"""Qualify new native input bytes twice without running a strategy or publishing services."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quanthus.backtest import native_dwd_source
from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.backtest.direct_dwd_provider import open_direct_dwd_session
from quanthus.backtest.native_dwd_qualification import qualify_native_projection
from quanthus.policy import find_project_root


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def probe(root: Path) -> list[dict[str, object]]:
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit='256MB'")
        connection.execute("SET threads=1")
        rows = connection.execute(
            "SELECT security_id, CAST(date AS VARCHAR), CAST(dwd_close_decimal AS VARCHAR), "
            "CAST(dwd_bar_knowledge_from AS VARCHAR) FROM read_parquet(?) "
            "WHERE security_id IN ('000919', '600805', '600810') "
            "AND date BETWEEN DATE '2019-03-27' AND DATE '2019-03-29' "
            "ORDER BY security_id, date",
            [str(root / "tables/daily_ohlcv_none.parquet")],
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 9 or any(row[2] is None for row in rows):
        raise RuntimeError("native projection did not retain the nine qualified input fills")
    return [
        dict(zip(("security_id", "date", "close", "raw_knowledge_from"), row, strict=True))
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--temporary-parent", type=Path, default=Path("/dev/shm"))
    args = parser.parse_args()
    project = find_project_root()
    report_path = args.report.resolve()
    if report_path.exists():
        raise RuntimeError("qualification report already exists; resume its recorded task")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = project / native_dwd_source.CANONICAL_RECEIPT
    if receipt_path.exists():
        raise RuntimeError("canonical receipt already exists; never overwrite a sealed receipt")
    temporary_parent = args.temporary_parent.resolve(strict=True)
    disk_free = shutil.disk_usage(project).free
    memory_free = shutil.disk_usage(temporary_parent).free
    if disk_free < 4 * 1024**3 or memory_free < 9 * 1024**3:
        raise RuntimeError("qualification reserve is insufficient")
    config = load_replay_data_provider_config(
        project / "config/replay-data-provider.base23.v1.json"
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    report = {
        "schema_version": "quanthus.native-base23-input-qualification.v1",
        "started_at_beijing": now(),
        "status": "RUNNING",
        "code_revision": revision,
        "research_or_strategy_run": False,
        "production_changed": False,
        "provider_config_digest": config.digest,
        "source": dict(config.dwd_identity),
        "disk_free_at_start": disk_free,
        "tmp_free_at_start": memory_free,
        "stages": [],
    }
    atomic_json(report_path, report)
    started = time.monotonic()
    phase = "candidate"

    def progress(stage: str, details: object) -> None:
        event = {"at_beijing": now(), "phase": phase, "stage": stage, "details": details}
        report["stages"].append(event)
        atomic_json(report_path, report)
        print(json.dumps(event), flush=True)

    wrote_receipt = False
    original_tempdir = tempfile.tempdir
    try:
        with tempfile.TemporaryDirectory(
            prefix="quanthus-base23-qualification-", dir=temporary_parent
        ) as tmp:
            root = Path(tmp)
            progress("source-verification-started", {})
            receipt = qualify_native_projection(
                config,
                root=root,
                receipt_id="native-base23-development-20261012-v1",
                progress=progress,
            )
            report["known_input_delta_probe"] = probe(root)
            report["candidate_receipt"] = receipt
            with receipt_path.open("x") as output:
                output.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            wrote_receipt = True
            progress("candidate-measured", {"bundle_digest": receipt["canonical_bundle_digest"]})
        phase = "independent_rebuild"
        with tempfile.TemporaryDirectory(
            prefix="quanthus-base23-independent-", dir=temporary_parent
        ) as tmp:
            tempfile.tempdir = tmp
            progress("independent-source-verification-started", {"previous_tmp_removed": True})
            with open_direct_dwd_session(
                config,
                scope_start=config.scope.decision_start,
                scope_end=config.scope.decision_end,
                progress=progress,
            ) as session:
                identity = dict(session.identity)
                if identity["materialized_content_digest"] != receipt["canonical_bundle_digest"]:
                    raise RuntimeError("independent native projection identity differs")
                if identity["provider_lineage_digest"] != receipt["provider_lineage_digest"]:
                    raise RuntimeError("independent native provider lineage differs")
                report["independent_identity"] = identity
                progress(
                    "independent-input-accepted",
                    {"bundle_digest": identity["materialized_content_digest"]},
                )
        report.update(
            status="PASS",
            completed_at_beijing=now(),
            seconds=round(time.monotonic() - started, 3),
            canonical_receipt_sha256=native_dwd_source.digest(receipt_path),
            temporary_payloads_removed=True,
        )
        atomic_json(report_path, report)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "report": str(report_path),
                    "canonical_receipt_sha256": report["canonical_receipt_sha256"],
                    "seconds": report["seconds"],
                }
            ),
            flush=True,
        )
        return 0
    except BaseException as exc:
        if wrote_receipt:
            receipt_path.unlink()
        report.update(
            status="FAILED",
            completed_at_beijing=now(),
            error_type=type(exc).__name__,
            error=str(exc),
            unaccepted_candidate_receipt_removed=wrote_receipt,
        )
        atomic_json(report_path, report)
        raise
    finally:
        tempfile.tempdir = original_tempdir


if __name__ == "__main__":
    raise SystemExit(main())
