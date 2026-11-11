"""Expose verified alignment results to the scheduled producer using a self-contained index."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .base_build_inputs import file_sha
from .daily_base24_cycle import publish
from .daily_base_alignment import load, save, verify_result
from .daily_core_ods_catalog import verify_daily_ods_catalog
from .standard_daily import inspect


def copy_tree(source: Path, target: Path) -> None:
    """Preserve catalog-relative paths and source bytes without source hardlinks."""
    if source.resolve() != source or target.resolve() != target:
        raise ValueError("ALIGNED_RUNTIME_PHYSICAL_PATH_REQUIRED")
    members = sorted(source.rglob("*"))
    if source.is_symlink() or any(p.is_symlink() for p in members):
        raise ValueError("ALIGNED_RUNTIME_SOURCE_LINK")
    expected = {p.relative_to(source) for p in members if p.is_file()}
    if target.exists():
        actual = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}
        if actual - expected:
            raise ValueError("ALIGNED_RUNTIME_EXTRA_MEMBER")
    for relative in sorted(expected):
        src, dst = source / relative, target / relative
        if dst.is_symlink():
            raise ValueError("ALIGNED_RUNTIME_DESTINATION_LINK")
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as stream, dst.open("xb") as out:
                shutil.copyfileobj(stream, out, 1024 * 1024)
        if dst.stat().st_size != src.stat().st_size or file_sha(dst) != file_sha(src):
            raise ValueError("ALIGNED_RUNTIME_COPY_CHANGED")


def delivery_source(delivery: Path, body: dict[str, Any], role: str) -> Path:
    manifest = delivery / body["sources"][role]["path"]
    if manifest.name != "release.json" or not manifest.is_file():
        raise ValueError("ALIGNED_RUNTIME_SOURCE_MANIFEST_REQUIRED")
    return manifest.parent


def _publish_aligned(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    receipt, plan = load(root / "VERIFIED.json"), load(root / "PLAN.json")
    if (
        receipt.get("status") != "VERIFIED"
        or receipt["request_identity"] != plan["identity"]
        or receipt["base"] != plan["base"]
        or [r["market_date"] for r in receipt["days"]]
        != [r["market_date"] for r in plan["entries"]]
    ):
        raise ValueError("ALIGNED_RUNTIME_VERIFIED_CLOSURE_REQUIRED")
    original = {r["market_date"]: r for r in plan["entries"]}
    prepared = []
    for result in receipt["days"]:
        verify_result(result)
        day = result["market_date"]
        delivery = Path(result["delivery"])
        body = inspect(delivery)
        if (
            body["market_date"] != day
            or body["base"]["manifest_sha256"] != plan["base"]["manifest_sha256"]
        ):
            raise ValueError("ALIGNED_RUNTIME_DELIVERY_SCOPE_CHANGED")
        value = {
            "market_date": day,
            "base_manifest_sha256": result["base_manifest_sha256"],
            "pipeline_revision": receipt["code_revision"],
            "source_manifest_sha256": result["source_manifests"],
            "standard_delivery": str(delivery),
            "standard_delivery_manifest_sha256": result["delivery_manifest_sha256"],
            "standard_delivery_identity": result["delivery_identity"],
            "config_sha256": file_sha(root / "governance.json"),
            "completed_at": result["completed_at"],
            "consumer_admission": "NOT_GRANTED",
            "execution_authorized": False,
        }
        if result["day_kind"] == "market":
            catalog = root / "source-catalogs" / day
            copy_tree(Path(original[day]["catalog"]), catalog)
            verify_daily_ods_catalog(catalog)
            releases = root / "collector-home/shadow/releases"
            for row in load(catalog / "catalog.json")["lanes"]:
                relative = Path(row["release_path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("ALIGNED_RUNTIME_UNSAFE_RELEASE")
                role = row["lane"]
                source = delivery_source(delivery, body, role)
                if file_sha(source / "release.json") != row["release_manifest_sha256"]:
                    raise ValueError("ALIGNED_RUNTIME_SOURCE_MANIFEST_CHANGED")
                copy_tree(source, releases / relative)
            product = Path(result["product"])
            native = product / "members/native5m"
            if file_sha(native / "manifest.json") != result["native_manifest_sha256"]:
                raise ValueError("ALIGNED_RUNTIME_NATIVE_CHANGED")
            value.update(
                status="QUALIFIED_1D_NATIVE5M",
                core=result["core"],
                core_manifest_sha256=result["core_manifest_sha256"],
                product=str(product),
                product_manifest_sha256=result["product_manifest_sha256"],
                native_source=str(native),
                native_manifest_sha256=result["native_manifest_sha256"],
                source_catalog=str(catalog),
                source_release_root=str(releases),
            )
        else:
            value.update(
                status="AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY",
                event_source=str(delivery_source(delivery, body, "calendar_daily_events")),
                event_manifest_sha256=result["source_manifests"]["calendar_daily_events"],
                calendar_sha256=original[day]["calendar_observation_sha256"],
            )
            calendar = root / (day + "-aligned") / "calendar"
            copy_tree(Path(original[day]["calendar_observation"]).parent, calendar)
            if file_sha(calendar / "calendar.json") != value["calendar_sha256"]:
                raise ValueError("ALIGNED_RUNTIME_CALENDAR_CHANGED")
        prepared.append(value)
    if prepared:
        latest = prepared[-1]
        last_delivery = Path(latest["standard_delivery"])
        last_body = inspect(last_delivery)
        role = (
            "market_daily_core"
            if "market_daily_core" in last_body["sources"]
            else "calendar_reference"
        )
        fallback = delivery_source(last_delivery, last_body, role)
        fallback_sha = latest["source_manifest_sha256"][role]
    else:
        recorded = plan.get("calendar_reference")
        if not recorded:
            raise ValueError("ALIGNED_RUNTIME_EMPTY_ROLLOVER_REFERENCE_REQUIRED")
        fallback = Path(recorded["path"])
        fallback_sha = recorded["manifest_sha256"]
        if file_sha(fallback / "release.json") != fallback_sha:
            raise ValueError("ALIGNED_RUNTIME_CALENDAR_REFERENCE_CHANGED")
        retained = root / "calendar-reference"
        copy_tree(fallback, retained)
        fallback = retained
    if file_sha(fallback / "release.json") != fallback_sha:
        raise ValueError("ALIGNED_RUNTIME_CALENDAR_REFERENCE_CHANGED")
    existing = load(root / "DATES.json")["dates"] if (root / "DATES.json").exists() else {}
    for value in prepared:
        old = existing.get(value["market_date"])
        if old is not None and old != value:
            raise ValueError("ALIGNED_RUNTIME_EXISTING_AUTHORITY_CHANGED")
    for value in prepared:
        publish({"root": str(root)}, value)
    result = {
        "kind": "p10-aligned-daily-runtime-v1",
        "status": "VERIFIED",
        "base": plan["base"],
        "through": receipt["through"],
        "days": len(prepared),
        "index_sha256": file_sha(root / "DATES.json") if prepared else None,
        "alignment_receipt_sha256": file_sha(root / "VERIFIED.json"),
        "calendar_reference": {"path": str(fallback), "manifest_sha256": fallback_sha},
        "execution_authorized": False,
        "consumer_admission": "NOT_GRANTED",
    }
    save(root / "RUNTIME-READY.json", result)
    return result


def publish_aligned(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    with (root / "ALIGN.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _publish_aligned(root)


def cycle_configuration(
    root: Path, template: dict[str, Any], revision: str, governance: Path
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    receipt, plan = load(root / "RUNTIME-READY.json"), load(root / "PLAN.json")
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("ALIGNED_RUNTIME_FULL_REVISION_REQUIRED")
    if receipt["status"] != "VERIFIED" or receipt["base"] != plan["base"]:
        raise ValueError("ALIGNED_RUNTIME_PUBLICATION_REQUIRED")
    if load(governance) != plan["governance"]:
        raise ValueError("ALIGNED_RUNTIME_GOVERNANCE_CHANGED")
    base = plan["base_root"]
    value = dict(template)
    value.update(
        base=base,
        base_manifest_sha256=plan["base"]["manifest_sha256"],
        root=str(root),
        bootstrap_cores=[],
        pipeline_revision=revision,
        release_config=str(governance),
        release_config_sha256=file_sha(governance),
        prior_anchor=os.path.commonpath([base, str(root), str(governance.parent)]),
        standard_delivery=True,
        execution_authorized=False,
        consumer_admission="NOT_GRANTED",
        activation_authority="Owner 2026-11-10 standard Daily/Base rollover",
    )
    reference = receipt["calendar_reference"]
    if file_sha(Path(reference["path"]) / "release.json") != reference["manifest_sha256"]:
        raise ValueError("ALIGNED_RUNTIME_CALENDAR_REFERENCE_CHANGED")
    value["calendar_reference_release"] = reference["path"]
    value["calendar_reference_manifest_sha256"] = reference["manifest_sha256"]
    return value


def main() -> None:
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--cycle-template", type=Path)
    parser.add_argument("--cycle-output", type=Path)
    parser.add_argument("--pipeline-revision")
    parser.add_argument("--governance", type=Path)
    args = parser.parse_args()
    result = publish_aligned(args.alignment)
    if args.cycle_template:
        if not all([args.cycle_output, args.pipeline_revision, args.governance]):
            parser.error("cycle output, full revision and governance are required")
        config = cycle_configuration(
            args.alignment, load(args.cycle_template), args.pipeline_revision, args.governance
        )
        if args.cycle_output.exists() and load(args.cycle_output) != config:
            raise ValueError("ALIGNED_RUNTIME_EXISTING_CONFIGURATION_CHANGED")
        save(args.cycle_output, config)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
