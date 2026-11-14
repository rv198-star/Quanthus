"""Bounded successor using qualified references and the existing sealed 2021 source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from quanthus import r9_calibration as cal
from quanthus.r7_native import verified_members
from quanthus.r9_stratified import order
from quanthus.structure_v2_inputs import canonical

SOURCE_SCHEMA = "quanthus.r9-reference-assets-source.v1"
ASSET_SCHEMA = "quanthus.r9-reference-assets-study.v1"
LAYOUT = (
    ("train", "2020-11-30", "import", 12),
    ("train", "2020-12-31", "import", 12),
    ("guard", "2021-05-31", "narrow", 4),
    ("guard", "2021-05-31", "boundary", 2),
    ("guard", "2021-05-31", "expanding", 2),
    ("update", "2021-06-30", "hash", 8),
    ("evaluation", "2021-11-30", "narrow", 4),
    ("evaluation", "2021-11-30", "boundary", 4),
    ("evaluation", "2021-11-30", "expanding", 4),
    ("evaluation", "2021-12-31", "narrow", 4),
    ("evaluation", "2021-12-31", "boundary", 4),
    ("evaluation", "2021-12-31", "expanding", 4),
)


def prepare_source(request: Path) -> dict[str, Any]:
    import duckdb

    from quanthus.r9_qualification import qualify_asset_pool
    from quanthus.r9_study import bound, check_deadline, read

    c = read(request)
    if c["schema_version"] != SOURCE_SCHEMA:
        raise ValueError("R9_ASSETS_SOURCE_CONTRACT")
    project = Path(__file__).resolve().parents[2]
    for name, expected in c["implementation_bindings"].items():
        if cal.sha(project / name) != expected:
            raise ValueError("R9_ASSETS_CODE_BINDING")
    check_deadline(c)
    root = Path(c["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "pool"
    if destination.exists():
        raise ValueError("R9_ASSETS_FRESH_POOL")
    origin = bound(Path(c["origin_selected"]["path"]), c["origin_selected"]["sha256"])["rows"]
    training = [r for r in origin if r["identity"]["role"] == "evaluation"]
    if len(training) != 24:
        raise ValueError("R9_ASSETS_IMPORTED_DENOMINATOR")
    identities = [{**r["identity"], "role": "train"} for r in training]
    excluded: set[str] = set()
    for entry in c["exclusions"]:
        data = bound(Path(entry["path"]), entry["sha256"])
        excluded.update(r.get("source_row", r)["identity"]["company_id"] for r in data["rows"])
    pool = bound(Path(c["identity_source"]["path"]), c["identity_source"]["sha256"])["rows"]
    candidates = sorted(
        (r["identity"] for r in pool if r["identity"]["company_id"] not in excluded), key=order
    )[:1024]
    if len(candidates) != 1024 or len({r["company_id"] for r in candidates}) != 1024:
        raise ValueError("R9_ASSETS_IDENTITY_CAPACITY")
    source = Path(c["source"]["cache"])
    base = read(project / "config/research/research-r7/request.v2.json")
    members = verified_members(source, project, base)
    db = duckdb.connect()
    db.execute("SET threads=2")
    db.execute("SET memory_limit='1GB'")
    db.execute("SET TimeZone='Asia/Shanghai'")
    paths = [
        str(source / "DWD" / m["path"])
        for m in members
        if m["path"].startswith("payload/dwd_trading_calendar/")
    ]
    db.read_parquet(paths, hive_partitioning=True).create_view("calendar")
    groups = (
        ("guard", "2021-05-31"),
        ("update", "2021-06-30"),
        ("evaluation", "2021-11-30"),
        ("evaluation", "2021-12-31"),
    )
    calendar_cache: dict[tuple[str, str], Any] = {}
    for group, (role, anchor) in enumerate(groups):
        for identity in candidates[group * 256 : (group + 1) * 256]:
            key = (identity["venue_id"], anchor)
            if key not in calendar_cache:
                calendar_cache[key] = db.execute(
                    "SELECT trade_date,calendar_session_id FROM calendar WHERE venue_id=? "
                    "AND is_open AND trade_date<=?::DATE AND knowledge_from<=?::TIMESTAMPTZ "
                    "AND knowledge_to IS NULL ORDER BY trade_date DESC LIMIT 40",
                    [identity["venue_id"], anchor, c["source"]["knowledge_cutoff"]],
                ).fetchall()[::-1]
            days = calendar_cache[key]
            token = hashlib.sha256(
                ("R9-ASSETS-001|" + identity["company_id"] + "|" + anchor).encode()
            ).hexdigest()
            identities.append(
                {
                    **identity,
                    "role": role,
                    "anchor": anchor,
                    "sample_id": "r9asset-" + token[:24],
                    "episode_id": "r9asset-episode-" + token,
                    "sessions": [
                        {"date": str(day), "session_id": session} for day, session in days
                    ],
                }
            )
    db.close()
    manifest = root / "candidate-identities.private.json"
    with manifest.open("xb") as stream:
        stream.write(canonical({"rows": identities}) + b"\n")
    # Exact identity/window commitment precedes the first new OHLC value read.
    native_request = root / "native-source-request.json"
    cal.write(
        native_request,
        {
            "schema_version": SOURCE_SCHEMA,
            "source": c["source"],
            "fixed_role_identity": {"sha256": cal.sha(manifest)},
        },
    )
    qualified = qualify_asset_pool(project, source, manifest, native_request, destination)
    rebuilt = read(destination / "qualified-cases.private.json")["rows"]
    by_id = {r["identity"]["sample_id"]: r for r in rebuilt}
    for original in training:
        other = by_id[original["identity"]["sample_id"]]
        if (
            not other["available"]
            or other["features"] != original["features"]
            or other["packet"] != original["packet"]
        ):
            raise ValueError("R9_ASSETS_IMPORTED_FEATURE_MISMATCH")
    check_deadline(c)
    result = {
        "status": "ASSET_SOURCE_POOL_QUALIFIED",
        "source_request_sha256": cal.sha(request),
        "subjects": 1048,
        "identity_sha256": cal.sha(manifest),
        "pool_sha256": cal.sha(destination / "qualified-cases.private.json"),
        "imported_train": 24,
        "qualification": qualified,
        "new_reference_calls": 0,
    }
    cal.write(root / "source.safe.json", result)
    return result


def import_train(request: Path) -> dict[str, Any]:
    from quanthus.r9_learning import support
    from quanthus.r9_study import bound, read, root_for, selected_rows

    c, root = read(request), root_for(request)
    origin = c["imported_reference"]
    assets = {
        key: bound(Path(entry["path"]), entry["sha256"]) for key, entry in origin["files"].items()
    }
    raw, qa, resolved = assets["references"], assets["qa"], assets["resolved"]
    old_selection, old_request = assets["selection"], assets["role_request"]
    if (
        raw["request_sha256"] != origin["files"]["role_request"]["sha256"]
        or raw["selected_sha256"] != origin["files"]["selection"]["sha256"]
        or qa["opinions_sha256"] != origin["files"]["references"]["sha256"]
        or qa["request_sha256"] != origin["files"]["role_request"]["sha256"]
        or qa["selected_sha256"] != origin["files"]["selection"]["sha256"]
        or resolved["summary"]["opinions_sha256"] != origin["files"]["references"]["sha256"]
        or resolved["summary"]["qa_sha256"] != origin["files"]["qa"]["sha256"]
        or resolved["role"] != "evaluation"
        or qa["excluded"]
        or old_request["role"] != "evaluation"
        or raw["rubric_sha256"] != hashlib.sha256(cal.RUBRIC.encode()).hexdigest()
    ):
        raise ValueError("R9_ASSETS_ORIGIN_REFERENCE_BINDING")
    old_by_id = {
        r["source_row"]["identity"]["sample_id"]: r["source_row"] for r in old_selection["rows"]
    }
    new_train = [r for r in selected_rows(request) if r["identity"]["role"] == "train"]
    if len(new_train) != 24 or {r["identity"]["sample_id"] for r in new_train} != set(old_by_id):
        raise ValueError("R9_ASSETS_TRAIN_SET_DRIFT")
    for row in new_train:
        previous = old_by_id[row["identity"]["sample_id"]]
        if (
            row["identity"] != {**previous["identity"], "role": "train"}
            or row["features"] != previous["features"]
            or row["packet"] != previous["packet"]
        ):
            raise ValueError("R9_ASSETS_TRAIN_ROW_DRIFT")
    rows = resolved["resolved"]
    if {r["case_id"] for r in rows} != set(old_by_id):
        raise ValueError("R9_ASSETS_ORIGIN_LABEL_COVERAGE")
    summary = support("train", rows)
    output = root / "qa-train"
    output.mkdir()
    private = output / "resolved.private.json"
    cal.write(
        private,
        {
            "request_sha256": cal.sha(request),
            "role": "train",
            "resolved": rows,
            "origin": origin,
            "disposition": "exposed training asset; retired from evaluation in this lineage",
        },
    )
    safe = {
        **summary,
        "request_sha256": cal.sha(request),
        "resolved_sha256": cal.sha(private),
        "role_request_sha256": origin["files"]["role_request"]["sha256"],
        "references_sha256": origin["files"]["references"]["sha256"],
        "qa_sha256": origin["files"]["qa"]["sha256"],
        "imported_reference": True,
        "new_reference_calls": 0,
        "fit_runs": 0,
        "update_runs": 0,
    }
    cal.write(root / "support-train.safe.json", safe)
    return safe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("source",))
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        result = prepare_source(args.request)
    else:
        from quanthus.r9_study import run_limited

        result = run_limited(args.request, "source", source=True)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
