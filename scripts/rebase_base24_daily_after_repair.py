"""Continue Path A Daily rebasing through the existing, pinned producer APIs.

The first completed Daily is an immutable seed product. Remaining inputs come
from the frozen selection and source packet. Restored ODS READY markers are
derived only after the frozen binding, original Daily and all artifact hashes
agree. No acquisition, business clock revision or consumer publication occurs.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def continue_rebase(args: argparse.Namespace) -> None:
    import pyarrow.parquet as pq

    from quanthus.pipelines.contracts import verify_ods_release
    from quanthus.pipelines.securities_warehouse.daily_base24 import build, verify
    from quanthus.pipelines.securities_warehouse.daily_core_domain import (
        advance_daily_current,
        build_daily_domain_increment,
    )
    from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
        build_daily_ods_catalog,
    )

    selection, roots = load(args.selection), load(args.roots)
    packages = sorted(
        (row for row in selection["packages"] if row["role"] == "daily"),
        key=lambda row: row["day"],
    )
    source = Path(roots["daily-sources"])
    bindings = load(source / "BINDINGS.json")
    manifest = load(args.base / "manifest.json")
    base_sha = sha(args.base / "manifest.json")
    config = load(args.governance)
    seed = load(args.first_day_receipt)
    require((args.base / "READY").read_text().strip() == base_sha, "BASE_READY_CHANGED")
    require(config["layers"]["dwd"]["manifest_sha256"] == base_sha, "CONFIG_BASE_CHANGED")
    require(seed["base_manifest_sha256"] == base_sha, "SEED_BASE_CHANGED")
    require(seed["day"] == packages[0]["day"], "SEED_FIRST_DAY_CHANGED")
    require(seed["source_replay_verification"] == "PASS", "SEED_NOT_VERIFIED")
    first = args.work_root / "daily" / seed["day"]
    require(sha(first / "manifest.json") == seed["daily_manifest_sha256"], "SEED_CHANGED")
    pointer = args.work_root / "core/CURRENT_DAILY_CORE.json"
    require(
        load(pointer)["manifest_sha256"] == seed["core_manifest_sha256"], "SEED_POINTER_CHANGED"
    )
    require(len(args.pipeline_revision) == 40, "PIPELINE_REVISION_REQUIRED")
    require(len(args.driver_revision) == 40, "DRIVER_REVISION_REQUIRED")
    require(manifest["business_through"] == selection["predecessor_through"], "BASE_CUTOFF_CHANGED")
    require(not (args.work_root / "source-evidence").exists(), "PACKET_ALREADY_EXISTS")
    reserve = 8 * 1024**3
    import os

    fs = os.statvfs(args.work_root)
    require(fs.f_bavail * fs.f_frsize >= args.free_floor_bytes + reserve, "FREE_SPACE_FLOOR")
    plan = {
        "kind": "base24r1-daily-rebase-continuation-v1",
        "base_manifest_sha256": base_sha,
        "selection_sha256": sha(args.selection),
        "roots_sha256": sha(args.roots),
        "source_bindings_sha256": sha(source / "BINDINGS.json"),
        "pipeline_revision": args.pipeline_revision,
        "driver_revision": args.driver_revision,
        "seed_daily_manifest_sha256": seed["daily_manifest_sha256"],
        "days": [row["day"] for row in packages],
        "free_floor_bytes": args.free_floor_bytes,
    }
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False), flush=True)
        return
    args.control_root.mkdir(exist_ok=False)
    save(args.control_root / "PLAN.json", plan)
    with args.build_lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipts = {seed["day"]: seed}
        try:
            verify(first)
            for package in packages[1:]:
                day = package["day"]
                old = Path(roots[package["id"]])
                old_core = load(old / "members/core/manifest.json")
                print(json.dumps({"day": day, "status": "STARTED"}), flush=True)
                restored = []
                for lane in ("market_daily_core", "calendar_daily_events"):
                    selected = [
                        b for b in bindings["bindings"] if b["day"] == day and b["role"] == lane
                    ]
                    require(len(selected) == 1, "SOURCE_BINDING_NOT_UNIQUE")
                    bound = selected[0]
                    original = source / day / lane
                    require(
                        sha(original / "release.json")
                        == bound["manifest_sha256"]
                        == old_core["selected_releases"][lane]["manifest_sha256"],
                        "SOURCE_MANIFEST_CHANGED",
                    )
                    require(
                        sha(old / "manifest.json") == bound["parent_daily_manifest_sha256"],
                        "OLD_DAILY_CHANGED",
                    )
                    require(
                        sha(old / "members/core/manifest.json") == bound["core_manifest_sha256"],
                        "OLD_CORE_CHANGED",
                    )
                    for item in bound["files"]:
                        relative = Path(item["path"])
                        require(
                            not relative.is_absolute() and ".." not in relative.parts,
                            "SOURCE_PATH_INVALID",
                        )
                        member = source / relative
                        require(
                            member.stat().st_size == item["size_bytes"]
                            and sha(member) == item["sha256"],
                            "SOURCE_ARTIFACT_CHANGED",
                        )
                    output = args.work_root / "releases/daily" / day / bound["release_id"]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(original, output)
                    ready = output / "READY"
                    if ready.exists():
                        require(
                            ready.read_text().strip() == bound["manifest_sha256"],
                            "SOURCE_READY_CHANGED",
                        )
                    else:
                        with ready.open("x") as stream:
                            stream.write(bound["manifest_sha256"] + "\n")
                    verified = verify_ods_release(output)
                    require(
                        verified.manifest_sha256 == bound["manifest_sha256"],
                        "RESTORED_RELEASE_CHANGED",
                    )
                    restored.append(
                        {
                            "lane": lane,
                            "manifest_sha256": verified.manifest_sha256,
                            "READY_sha256": sha(ready),
                        }
                    )
                catalog = build_daily_ods_catalog(
                    release_root=args.work_root / "releases",
                    market_date=date.fromisoformat(day),
                    output=args.work_root / "catalogs" / day,
                    product_scope="daily-core-v1",
                )
                core = build_daily_domain_increment(
                    catalog_root=catalog.root,
                    release_root=args.work_root / "releases",
                    base_root=args.base,
                    release_config=args.governance,
                    output=args.work_root / "core" / day,
                    code_revision=args.pipeline_revision,
                    prior_pointer=pointer,
                )
                daily = args.work_root / "daily" / day
                daily.parent.mkdir(parents=True, exist_ok=True)
                build(
                    core=core.root,
                    native5m=old / "members/native5m",
                    config=args.governance,
                    output=daily,
                    code_revision=args.pipeline_revision,
                )
                verify(daily)
                for relative in ("payload/1d.parquet", "payload/5m.parquet"):
                    require(
                        pq.ParquetFile(old / relative)
                        .read()
                        .equals(pq.ParquetFile(daily / relative).read(), check_metadata=True),
                        "PERIOD_VALUES_CLOCKS_OR_SCHEMA_CHANGED",
                    )
                advance_daily_current(package=core.root, pointer=pointer)
                receipt = {
                    "day": day,
                    "base_manifest_sha256": base_sha,
                    "core_manifest_sha256": sha(core.root / "manifest.json"),
                    "daily_manifest_sha256": sha(daily / "manifest.json"),
                    "source_replay_verification": "PASS",
                    "actual_1d_and_5m_values_clocks_schemas_equal_old_daily": True,
                    "source_packaging": restored,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                save(args.control_root / (day + ".json"), receipt)
                receipts[day] = receipt
                print(
                    json.dumps(
                        {
                            "day": day,
                            "status": "COMPLETED",
                            "completed": len(receipts),
                            "total": len(packages),
                        }
                    ),
                    flush=True,
                )
            packet = args.work_root / "source-evidence"
            shutil.copytree(
                source,
                packet,
                ignore=lambda directory, names: (
                    ["BINDINGS.json"] if Path(directory) == source else []
                ),
            )
            for bound in bindings["bindings"]:
                require(bound["day"] in receipts, "UNSELECTED_SOURCE_BINDING")
                bound["parent_daily_manifest_sha256"] = receipts[bound["day"]][
                    "daily_manifest_sha256"
                ]
                bound["core_manifest_sha256"] = receipts[bound["day"]]["core_manifest_sha256"]
            save(packet / "BINDINGS.json", bindings)
            save(
                args.control_root / "SUCCESS.json",
                {
                    "plan": plan,
                    "daily": receipts,
                    "new_source_bindings_sha256": sha(packet / "BINDINGS.json"),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as error:
            save(
                args.control_root / "FAILURE.json",
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "completed_days": sorted(receipts),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "selection",
        "roots",
        "base",
        "work-root",
        "control-root",
        "governance",
        "first-day-receipt",
        "build-lock",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--pipeline-revision", required=True)
    parser.add_argument("--driver-revision", required=True)
    parser.add_argument("--free-floor-bytes", type=int, default=16 * 1024**3)
    parser.add_argument("--plan-only", action="store_true")
    continue_rebase(parser.parse_args())


if __name__ == "__main__":
    main()
