"""Explicit October repair authority, separate from ordinary T/T+1 jobs."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import (
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    safe_path,
)
from quanthus.pipelines.securities_warehouse.daily_core_domain import verify_daily_domain_increment

CONFIG = (
    Path(__file__).resolve().parents[3] / "config/pipelines/p10-base23-october-realignment.v1.json"
)
ID = "p10-owner-october-base23-realignment-v1"
STATE = Path("/var/lib/quanthus/base23-october-realignment-20261015")
OCT12_CONFIG = CONFIG.with_name("p10-base23-oct12-unification.v1.json")
OCT12_STATE = Path("/var/lib/quanthus/base23-oct12-unification-20261015")


def contract() -> dict[str, Any]:
    c = object_at(CONFIG)
    if (
        c.get("schema_version") != 1
        or c.get("policy_id") != ID
        or c.get("business_dates") != ["2026-10-08", "2026-10-09", "2026-10-14"]
        or c.get("protected_dates") != ["2026-10-12", "2026-10-13"]
        or c.get("minimum_minute_grade") != "R2"
        or c.get("maximum_R3") != 0
        or c.get("execution_authorized") is not False
        or c.get("operation_budget_seconds_per_interval") != 3600
        or c.get("operation_timeout_seconds") != 600
    ):
        raise ValueError("October recovery contract differs")
    return c


def check_day(day: str) -> dict[str, Any]:
    if day == "2026-10-12":
        c = object_at(OCT12_CONFIG)
        if (
            c.get("schema_version") != 1
            or c.get("policy_id") != "p10-owner-oct12-unification-v1"
            or c.get("business_dates") != ["2026-10-12"]
            or c.get("protected_dates") != ["2026-10-13"]
            or c.get("minimum_minute_grade") != "R2"
            or c.get("maximum_R3") != 0
            or c.get("execution_authorized") is not False
            or c.get("operation_budget_seconds_per_interval") != 3600
            or c.get("operation_timeout_seconds") != 600
            or c.get("base_manifest_sha256") != contract()["base_manifest_sha256"]
            or c.get("base_content_digest") != contract()["base_content_digest"]
        ):
            raise ValueError("October12 successor recovery contract differs")
        return c
    c = contract()
    if day not in c["business_dates"]:
        raise ValueError("date is not authorized by this one-off recovery")
    return c


def alignment_binding(daily: Path) -> dict[str, str]:
    m = object_at(daily / "manifest.json")
    c = check_day(m["market_date"])
    if (
        m["base"].get("manifest_sha256") != c["base_manifest_sha256"]
        or m["base"].get("content_digest") != c["base_content_digest"]
    ):
        raise ValueError("October recovery must actually bind native Base 2.3")
    universe = m.get("acquisition_universe", {})
    codes = universe.get("codes", [])
    if (
        object_at(daily / "source-universe.json") != universe
        or universe.get("policy_id") != "p10-acquisition-prevention-v1"
        or universe.get("market_date") != m["market_date"]
        or universe.get("historical_pit_authorized") is not False
        or not universe.get("universe_digest")
        or universe.get("provider") != "baostock"
        or codes != sorted(set(codes))
        or universe.get("population") != len(codes)
        or universe.get("universe_digest") != digest(encoded(codes))
    ):
        raise ValueError("October recovery requires evidenced target-date universe")
    return {
        "policy_id": c["policy_id"],
        "contract_sha256": file_digest(
            OCT12_CONFIG if m["market_date"] == "2026-10-12" else CONFIG
        ),
        "market_date": m["market_date"],
        "daily_manifest_sha256": file_digest(daily / "manifest.json"),
        "universe_digest": universe["universe_digest"],
    }


def verify_alignment(binding: Any, daily: Path) -> None:
    if binding != alignment_binding(daily):
        raise ValueError("October recovery does not bind its exact authority/day/daily")


def check_protected(protected: dict[str, str], closure: dict[str, list[int]] | None = None) -> None:
    if not protected:
        raise ValueError("exact 12/13 protection is required")
    for raw, expected_digest in protected.items():
        p = Path(raw)
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected_digest:
            raise ValueError("protected 12/13 closure changed: " + raw)
    if closure is not None:
        for raw, old in closure.items():
            s = Path(raw).stat()
            if [s.st_ino, s.st_size, s.st_mtime_ns] != old:
                raise ValueError("protected sealed entity changed: " + raw)


def publish_daily_revision(
    *,
    root: Path,
    package: Path,
    expected_old: str,
    protected: dict[str, str],
    receipt_root: Path,
    protected_closure: dict[str, list[int]] | None = None,
    catalog: Path | None = None,
) -> dict[str, Any]:
    """Replace only an authorized dated projection, retain immutable originals."""
    result = verify_daily_domain_increment(package)
    m = object_at(package / "manifest.json")
    alignment = alignment_binding(package)
    if m.get("kind") != "cn01_daily_core_dwd_increment" or not package.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("revision package must be a native daily-core package inside root")
    day = result.market_date.isoformat()
    pointer = root / "date-pointers" / (day + ".json")
    current = root / "CURRENT_DAILY_CORE.json"
    descriptor = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_current",
        "product_scope": "daily-core-v1",
        "package_id": result.package_id,
        "market_date": day,
        "manifest_path": (package / "manifest.json").relative_to(root).as_posix(),
        "manifest_sha256": result.manifest_sha256,
        "content_digest": result.content_digest,
    }
    with (root / ".daily-core-current.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        check_protected(protected, protected_closure)
        old = object_at(pointer)
        tip = object_at(current)
        if old["manifest_sha256"] == result.manifest_sha256:
            if tip["market_date"] == day and tip["manifest_sha256"] != result.manifest_sha256:
                if tip["manifest_sha256"] != expected_old:
                    raise ValueError("partial current revision CAS differs")
                atomic(current, descriptor)
            return {"status": "already_published", **descriptor}
        if old["manifest_sha256"] != expected_old or tip["market_date"] < day:
            raise ValueError("dated revision CAS or CURRENT ordering differs")
        if tip["market_date"] == day and tip["manifest_sha256"] != expected_old:
            raise ValueError("current same-day revision CAS differs")
        if m.get("catalog_sha256") and (
            catalog is None or file_digest(catalog / "catalog.json") != m["catalog_sha256"]
        ):
            raise ValueError("revised catalog does not bind actual DWD")
        if day == "2026-10-14":
            job = root.parents[2] / "intraday/operations/complete-daily-v1/2026-10-14"
            if job.exists():
                raise ValueError(
                    "10/14 ordinary minute Job already frozen; explicit rebase required"
                )
            prior = object_at(root / "date-pointers/2026-10-13.json")
            if (m.get("prior") or {}).get("manifest_sha256") != prior["manifest_sha256"]:
                raise ValueError("14 revision must retain protected13 predecessor")
        archive = root / "dated-revisions" / day / (expected_old[7:] + ".json")
        if archive.exists() and object_at(archive) != old:
            raise ValueError("immutable old dated descriptor differs")
        if not archive.exists():
            atomic(archive, old)
        receipt = {
            "kind": "p10_base23_daily_revision_v1",
            "alignment": alignment,
            "market_date": day,
            "supersedes": old,
            "replacement": descriptor,
            "prior": m.get("prior"),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "protected_identity_digest": "sha256:"
            + hashlib.sha256(json.dumps(protected, sort_keys=True).encode()).hexdigest(),
        }
        receipt_path = receipt_root / day / (result.manifest_sha256[7:] + ".json")
        if receipt_path.exists() and object_at(receipt_path).get("replacement") != descriptor:
            raise ValueError("revision receipt collision")
        if not receipt_path.exists():
            atomic(receipt_path, receipt)
        if catalog is not None:
            target = root / "catalogs" / day
            for name in ["catalog.json", "READY"]:
                old_file = target / name
                if old_file.exists():
                    archive_catalog = (
                        target / "dated-revisions" / file_digest(target / "catalog.json")[7:]
                    )
                    archive_catalog.mkdir(parents=True, exist_ok=True)
                    backup = archive_catalog / name
                    if backup.exists() and backup.read_bytes() != old_file.read_bytes():
                        raise ValueError("immutable predecessor catalog differs")
                    if not backup.exists():
                        shutil.copy2(old_file, backup)
            target.mkdir(parents=True, exist_ok=True)
            for name in ["catalog.json", "READY"]:
                temporary = target / (name + ".realign-tmp")
                shutil.copy2(catalog / name, temporary)
                os.replace(temporary, target / name)
        atomic(pointer, descriptor)
        if tip["market_date"] == day:
            atomic(current, descriptor)
        check_protected(protected, protected_closure)
        return {"status": "published", **receipt}


def publish_complete_revision(
    *,
    root: Path,
    package: Path,
    protected: dict[str, str],
    protected_closure: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    from quanthus.pipelines.p10_complete_daily import verify

    result = verify(package)
    check_day(result["market_date"])
    if not package.resolve().is_relative_to(root.resolve()):
        raise ValueError("complete revision outside actual product root")
    day = result["market_date"]
    path = root / "date-pointers" / (day + ".json")
    current = root / "CURRENT_COMPLETE_DAILY.json"
    descriptor = {
        "schema_version": 1,
        "kind": "cn01_complete_daily_dwd_current",
        "market_date": day,
        "package_id": result["package_id"],
        "manifest_path": (package / "manifest.json").relative_to(root).as_posix(),
        "manifest_sha256": result["manifest_sha256"],
        "content_digest": result["content_digest"],
        "base": result["base"],
    }
    with (root / ".publish.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        check_protected(protected, protected_closure)
        if path.exists() and object_at(path)["manifest_sha256"] != result["manifest_sha256"]:
            raise ValueError("complete historical date already has another recovery version")
        from quanthus.pipelines.p10_delivery_common import prepare_export

        prepare_export(root, package)
        receipt = root / "verification" / (result["manifest_sha256"][7:] + ".json")
        if receipt.exists():
            existing = object_at(receipt)
            if any(existing.get(k) != v for k, v in result.items()):
                raise ValueError("complete verification receipt identity differs")
        else:
            atomic(
                receipt,
                {
                    **result,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "actual_complete_package_files_and_native_projection",
                },
            )
        atomic(path, descriptor)
        if not current.exists() or object_at(current)["market_date"] < day:
            atomic(current, descriptor)
        check_protected(protected, protected_closure)
    return descriptor


def mirror_daily_revision(
    *,
    root: Path,
    day: str,
    expected_old: str,
    expected_new: str,
    protected: dict[str, str],
    protected_closure: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    """Explicit old-date mirror, reusing ordinary receiver lock and SSH allowlist."""
    from quanthus.warehouse_sync import _run_rsync, _ssh_command, _validate_pointer

    check_day(day)
    if os.environ.get("QUANTHUS_CN01_DWD_REMOTE_ROOT") != "products/daily-core":
        raise ValueError("recovery mirror requires exact daily-core SSH jail")
    host, _, user, _, _, ssh = _ssh_command()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".realign-sync-", dir=root))
    source = f"{user}@{host}:products/daily-core/"
    try:
        _run_rsync(
            source=source + f"date-pointers/{day}.json", destination=stage / "pointer.json", ssh=ssh
        )
        pointer = object_at(stage / "pointer.json")
        relative, _ = _validate_pointer(pointer, date.fromisoformat(day), "daily-core-v1")
        if pointer["manifest_sha256"] != expected_new:
            raise ValueError("remote historical revision identity changed")
        _run_rsync(
            source=source + relative.as_posix() + "/manifest.json",
            destination=stage / "manifest.json",
            ssh=ssh,
        )
        remote_manifest = object_at(stage / "manifest.json")
        if file_digest(stage / "manifest.json") != expected_new or shutil.disk_usage(
            root
        ).free < 4 * 1024**3 + sum(x["size_bytes"] for x in remote_manifest["files"]):
            raise ValueError("daily mirror manifest or staging space budget differs")
        _run_rsync(
            source=source + relative.as_posix() + "/", destination=stage / "package", ssh=ssh
        )
        actual = verify_daily_domain_increment(stage / "package")
        if (
            actual.manifest_sha256 != expected_new
            or actual.content_digest != pointer["content_digest"]
        ):
            raise ValueError("actual dated mirror package differs")
        alignment_binding(stage / "package")
        destination = safe_path(root, relative.as_posix())
        with (root / ".sync.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            check_protected(protected, protected_closure)
            current = root / "CURRENT_DAILY_CORE.json"
            tip = object_at(current)
            dated = root / "date-pointers" / (day + ".json")
            if dated.exists() and object_at(dated)["manifest_sha256"] not in {
                expected_old,
                expected_new,
            }:
                raise ValueError("receiver dated CAS differs")
            old_packages = list((root / "packages" / day).glob("*/manifest.json"))
            if not any(file_digest(p) in {expected_old, expected_new} for p in old_packages):
                raise ValueError("receiver exact predecessor is absent")
            if tip["market_date"] == day and tip["manifest_sha256"] not in {
                expected_old,
                expected_new,
            }:
                raise ValueError("receiver current CAS differs")
            if tip["market_date"] < day:
                raise ValueError("historical mirror cannot skip receiver forward chain")
            if destination.exists():
                if file_digest(destination / "manifest.json") != expected_new:
                    raise ValueError("immutable receiver destination differs")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / "package", destination)
            archive = root / "dated-revisions" / day / (expected_old[7:] + ".json")
            if archive.exists():
                predecessor = object_at(archive)
                if (
                    predecessor.get("manifest_sha256") != expected_old
                    or file_digest(safe_path(root, predecessor["manifest_path"])) != expected_old
                ):
                    raise ValueError("receiver predecessor archive identity differs")
            elif dated.exists() and object_at(dated)["manifest_sha256"] == expected_old:
                predecessor = object_at(dated)
            else:
                old_manifest = next(p for p in old_packages if file_digest(p) == expected_old)
                old_data = object_at(old_manifest)
                predecessor = {
                    "market_date": day,
                    "manifest_sha256": expected_old,
                    "content_digest": old_data["content_digest"],
                    "manifest_path": old_manifest.relative_to(root).as_posix(),
                    "package_id": old_data["package_id"],
                }
            if archive.exists() and object_at(archive) != predecessor:
                raise ValueError("receiver predecessor archive differs")
            if not archive.exists():
                atomic(archive, predecessor)
            atomic(dated, pointer)
            if tip["market_date"] == day:
                atomic(current, pointer)
            receipt = {
                "kind": "oci_base23_dated_revision_v1",
                "market_date": day,
                "manifest_sha256": expected_new,
                "content_digest": actual.content_digest,
                "supersedes_manifest_sha256": expected_old,
                "exact_source_match": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "execution_authorized": False,
            }
            receipt_path = root / "receipts" / day / (actual.package_id + ".json")
            if receipt_path.exists():
                existing = object_at(receipt_path)
                if any(existing.get(k) != v for k, v in receipt.items() if k != "verified_at"):
                    raise ValueError("receiver immutable revision receipt differs")
                receipt = existing
            else:
                atomic(receipt_path, receipt)
            check_protected(protected, protected_closure)
            return receipt
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def mirror_complete_revision(
    *,
    root: Path,
    day: str,
    expected_new: str,
    protected: dict[str, str],
    protected_closure: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    """Mirror a qualified historical complete package without regressing CURRENT."""
    from quanthus.pipelines.p10_complete_daily_sync import (
        REMOTE,
        _copy_immutable,
        _validate_pointer,
    )
    from quanthus.warehouse_sync import _run_rsync, _ssh_command

    check_day(day)
    if os.environ.get("QUANTHUS_CN01_DWD_REMOTE_ROOT") != REMOTE:
        raise ValueError("complete historical mirror requires exact existing SSH jail")
    host, _, user, _, _, ssh = _ssh_command()
    source = f"{user}@{host}:{REMOTE}"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".mirror.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_protected(protected, protected_closure)
        stage = Path(tempfile.mkdtemp(prefix=".historical-complete-", dir=root))
        try:
            _run_rsync(
                source=source + f"/date-pointers/{day}.json",
                destination=stage / "pointer.json",
                ssh=ssh,
            )
            pointer = object_at(stage / "pointer.json")
            if pointer["manifest_sha256"] != expected_new:
                raise ValueError("complete historical source identity changed")
            dated = root / "date-pointers" / (day + ".json")
            if dated.exists() and object_at(dated)["manifest_sha256"] != expected_new:
                raise ValueError("complete historical receiver dated identity differs")
            package, actual = _copy_immutable(
                pointer=pointer, target=date.fromisoformat(day), stage=stage, source=source, ssh=ssh
            )
            destination = root / _validate_pointer(pointer, date.fromisoformat(day))
            if destination.exists():
                if file_digest(destination / "manifest.json") != expected_new:
                    raise ValueError("complete immutable receiver package differs")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(package, destination)
            publish_complete_revision(
                root=root,
                package=destination,
                protected=protected,
                protected_closure=protected_closure,
            )
            path = root / "visibility" / (day + "-" + expected_new[7:23] + ".json")
            if path.exists():
                receipt = object_at(path)
                if (
                    receipt.get("manifest_sha256") != expected_new
                    or receipt.get("content_digest") != actual["content_digest"]
                ):
                    raise ValueError("historical visibility immutable identity differs")
            else:
                receipt = {
                    "schema_version": 1,
                    "kind": "oci_complete_daily_visibility_v1",
                    "market_date": day,
                    "manifest_sha256": expected_new,
                    "content_digest": actual["content_digest"],
                    "package_id": actual["package_id"],
                    "intervals": ["1d", "1m", "5m"],
                    "first_verified_visible_at": datetime.now(timezone.utc).isoformat(),
                    "consumer_purpose": "gap_aware_non_execution_development",
                    "execution_authorized": False,
                    "exact_source_match": True,
                }
                atomic(path, receipt)
            check_protected(protected, protected_closure)
            return receipt
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def publish_domain_revision(
    *,
    root: Path,
    package: Path,
    expected_old: str | None,
    protected: dict[str, str],
    protected_closure: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    from quanthus.pipelines.p10_delivery_common import prepare_export
    from quanthus.pipelines.p10_domain_continuation import verify

    actual = verify(package)
    day = actual["market_date"]
    c = check_day(day) if day == "2026-10-12" else contract()
    if day not in c["business_dates"] + c["natural_dates"] or not package.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("domain historical publication outside recovery authority")
    with (root / ".publish.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        check_protected(protected, protected_closure)
        dated = root / "date-pointers" / (day + ".json")
        old = object_at(dated) if dated.exists() else None
        if (old or {}).get("manifest_sha256") not in {expected_old, actual["manifest_sha256"]}:
            raise ValueError("domain recovery dated CAS differs")
        if old and old["manifest_sha256"] != actual["manifest_sha256"]:
            archive = root / "dated-revisions" / day / (old["manifest_sha256"][7:] + ".json")
            if archive.exists() and object_at(archive) != old:
                raise ValueError("domain immutable predecessor differs")
            if not archive.exists():
                atomic(archive, old)
        prepare_export(root, package)
        pointer = {
            "kind": "p10_domain_continuation_current_v1",
            "domain": actual["domain"],
            "market_date": day,
            "manifest_path": (package / "manifest.json").relative_to(root).as_posix(),
            "manifest_sha256": actual["manifest_sha256"],
            "content_digest": actual["content_digest"],
            "prior": actual["prior"],
        }
        current = root / "CURRENT_DOMAIN.json"
        if current.exists():
            tip = object_at(current)
            if tip["market_date"] == day and tip["manifest_sha256"] not in {
                expected_old,
                actual["manifest_sha256"],
            }:
                raise ValueError("domain recovery current CAS differs")
        atomic(dated, pointer)
        if not current.exists() or object_at(current)["market_date"] <= day:
            atomic(current, pointer)
        check_protected(protected, protected_closure)
        return pointer


def run_recovery_operation(
    *,
    day: str,
    interval: str,
    reference_digest: str,
    revision: str,
    operation_id: str,
    argv: list[str],
    publication: bool = False,
    resume_authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Independent finite recovery ledger; never invoke/reset old T+1 budgets."""
    from quanthus.pipelines.p10_historical_tplus1_recovery import _run_operation

    c = check_day(day)
    if len(argv) < 3 or argv[1] != "-m":
        raise ValueError("recovery requires an explicit approved module action")
    capture_modules = {
        "quanthus.providers.p10_native_m1_shard_capture",
        "quanthus.providers.p10_native_m5_shard_capture",
    }
    grade_module = "quanthus.pipelines.p10_native_minute_delivery"
    if (
        publication
        and (
            argv[2] != grade_module
            or len(argv) < 4
            or argv[3] not in {"build", "verify", "seed-m1"}
        )
    ) or (
        not publication
        and argv[2] not in capture_modules
        and not (argv[2] == grade_module and len(argv) >= 4 and argv[3] == "capture")
    ):
        raise ValueError("capture cannot bypass source window as publication")
    if "-c" in argv:
        raise ValueError("inline commands cannot enter the recovery ledger")
    if interval not in {"1m", "5m"} or not reference_digest.startswith("sha256:"):
        raise ValueError("recovery interval/reference differs")
    state_root = OCT12_STATE if day == "2026-10-12" else STATE
    config_path = OCT12_CONFIG if day == "2026-10-12" else CONFIG
    root = state_root / "minute-jobs" / day / interval
    root.mkdir(parents=True, exist_ok=True)
    path = root / "ledger.json"
    identity = {
        "policy_id": c["policy_id"],
        "contract_sha256": file_digest(config_path),
        "market_date": day,
        "interval": interval,
        "reference_digest": reference_digest,
        "code_revision": revision,
        "phase": "owner_october_realign",
        "original_T_Tplus1_jobs_unchanged": True,
    }
    with (root / ".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            state = object_at(path)
            if any(state.get(k) != v for k, v in identity.items() if k != "code_revision"):
                raise ValueError(
                    "recovery Job reference/code identity changed; budget cannot reset"
                )
            effective_revision = state.get("processing_code_revision", state["code_revision"])
            if effective_revision != revision:
                expected_resume = {
                    "kind": "p10_october_realignment_processing_resume_v1",
                    "policy_id": c["policy_id"],
                    "contract_sha256": file_digest(config_path),
                    "market_date": day,
                    "interval": interval,
                    "reference_digest": reference_digest,
                    "from_code_revision": effective_revision,
                    "to_code_revision": revision,
                    "frozen_operations_digest": digest(encoded(state["operations"])),
                    "preserve_original_code_and_elapsed_budget": True,
                    "source_window_unchanged": True,
                }
                semantic_resume = False
                if (
                    publication
                    and day == "2026-10-08"
                    and interval == "1m"
                    and (resume_authorization or {}).get("kind")
                    == "p10_october_realignment_processing_resume_v2"
                ):
                    from quanthus.pipelines.p10_native_minute_delivery import reference

                    proof_path = STATE / "canary-shared-reference-bound-20261008.json"
                    proof = object_at(proof_path)
                    rows = proof.get("source_results", [])
                    frozen_ref = reference(STATE / "references/2026-10-08.json")
                    semantic_resume = len(rows) == 2 and all(
                        r.get("quality_grade") == "R3"
                        and r.get("quality_reason") == "invalid_native_source:ValueError"
                        and r.get("native_target_rows") == 241
                        for r in rows
                    )
                    semantic_resume = (
                        semantic_resume
                        and {r["stock_code"] for r in rows} == {"000001", "600000"}
                        and proof.get("reference_digest")
                        == reference_digest
                        == frozen_ref["reference_digest"]
                    )
                    if semantic_resume:
                        from quanthus.providers.p10_native_m1_shard_capture import (
                            target_date_records,
                        )

                        capture = STATE / "captures/2026-10-08/1m/canary-reference-v2"
                        identity_proof = object_at(capture / "identity.json")
                        semantic_resume = (
                            identity_proof.get("kind") == "p10_tencent_native_m1_raw_capture_shard"
                            and identity_proof.get("provider") == "tencent-mkline-m1"
                            and identity_proof.get("market_date") == "2026-10-08"
                            and identity_proof.get("market_ods_manifest_sha256")
                            == frozen_ref["source_ods_manifest_sha256"]
                            and set(identity_proof.get("selected_symbols", []))
                            == {"000001", "600000"}
                        )
                        for row in rows:
                            code = row["stock_code"]
                            if code not in {"000001", "600000"}:
                                semantic_resume = False
                                break
                            raw = gzip.decompress(
                                (capture / "raw" / (code + ".json.gz")).read_bytes()
                            )
                            receipt = object_at(capture / "receipts" / (code + ".json"))
                            acquired = datetime.fromisoformat(receipt["captured_at"])
                            target_rows, target_digest = target_date_records(
                                raw, code, date(2026, 10, 8)
                            )
                            if (
                                hashlib.sha256(raw).hexdigest() != row["raw_sha256"]
                                or receipt["captured_at"] != row["captured_at"]
                                or receipt.get("raw_sha256") != row["raw_sha256"]
                                or receipt.get("provider") != "tencent-mkline-m1"
                                or receipt.get("status") != "raw_saved"
                                or receipt.get("stock_code") != code
                                or receipt.get("market_date") != "2026-10-08"
                                or receipt.get("source_market_ods_sha256")
                                != frozen_ref["source_ods_manifest_sha256"]
                                or receipt.get("target_date_observation_sha256") != target_digest
                                or len(target_rows) != 241
                                or acquired.tzinfo is None
                                or not datetime.fromisoformat(c["source_window_start"])
                                <= acquired
                                < datetime.fromisoformat(c["source_window_end"])
                            ):
                                semantic_resume = False
                                break
                    if semantic_resume:
                        expected_resume.update(
                            {
                                "kind": "p10_october_realignment_processing_resume_v2",
                                "reason_code": "native_historical_scope_adaptation",
                                "evidence_sha256": file_digest(proof_path),
                            }
                        )
                if (
                    resume_authorization != expected_resume
                    or state.get("inflight_operation")
                    or not state["operations"]
                    or (state["operations"][-1].get("outcome") != "failed" and not semantic_resume)
                ):
                    raise ValueError(
                        "recovery processing code changed without exact failed-job resume; "
                        "budget cannot reset"
                    )
                state.setdefault("code_resumes", []).append(
                    {
                        **expected_resume,
                        "retained_cumulative_seconds": state["cumulative_processing_seconds"],
                        "applies_after_operation_count": len(state["operations"]),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                state["processing_code_revision"] = revision
                atomic(path, state)
        else:
            state = {
                **identity,
                "cumulative_processing_seconds": 0.0,
                "operations": [],
                "inflight_operation": None,
            }
            atomic(path, state)
        already = any(x["operation_id"] == operation_id for x in state["operations"])
        now = datetime.now(timezone.utc)
        if (
            not already
            and not publication
            and not (
                datetime.fromisoformat(c["source_window_start"])
                <= now
                < datetime.fromisoformat(c["source_window_end"])
            )
        ):
            return {
                "outcome": "cutoff_reached",
                "reason": "outside_explicit_recovery_source_window",
            }
        return _run_operation(
            state_path=path,
            state=state,
            operation_id=operation_id,
            command=argv,
            allow_publication=publication,
        )


def seed_reused_m1(
    *, origin_root: Path, reference_file: Path, output: Path, historical_daily: Path | None = None
) -> dict[str, int]:
    """Reproject unchanged Tencent responses with explicit original capture proof."""
    from quanthus.pipelines.p10_native_minute_delivery import assess, reference
    from quanthus.providers.p10_native_m1_shard_capture import target_date_records

    ref = reference(reference_file)
    check_day(ref["market_date"])
    historical = ref["market_date"] == "2026-10-08"
    if historical:
        from quanthus.pipelines.p10_native_minute_delivery import _historical_daily_binding

        if historical_daily is None:
            raise ValueError("Oct08 source reuse requires actual Base23 daily authority")
        _historical_daily_binding(historical_daily, ref)
    if output.exists():
        raise ValueError("reuse seed is immutable; use its existing receipts")
    origins = {}
    for shard in sorted(origin_root.glob("shard-*")):
        identity = object_at(shard / "identity.json")
        if identity.get("kind") != "p10_tencent_native_m1_raw_capture_shard":
            raise ValueError("wrong source kind for response reuse")
        for code in identity["selected_symbols"]:
            raw_path = shard / "raw" / (code + ".json.gz")
            receipt_path = shard / "receipts" / (code + ".json")
            if raw_path.exists() and receipt_path.exists():
                origins[code] = (identity, raw_path, object_at(receipt_path))
    selected = []
    for code, daily in ref["securities"].items():
        origin = origins.get(code)
        if origin is None:
            continue
        identity, raw_path, receipt = origin
        raw = gzip.decompress(raw_path.read_bytes())
        if hashlib.sha256(raw).hexdigest() != receipt["raw_sha256"]:
            raise ValueError("original reused source hash differs")
        try:
            records, target_digest = target_date_records(
                raw, code, date.fromisoformat(ref["market_date"])
            )
            quality, _ = assess(
                interval="1m",
                code=code,
                day=ref["market_date"],
                raw=raw,
                daily=daily,
                allow_historical=historical,
            )
        except (ValueError, KeyError, TypeError):
            continue
        if records and quality["quality_grade"] in {"R0", "R1", "R2"}:
            selected.append((code, identity, raw_path, receipt, target_digest))
    for index in range(0, len(selected), 50):
        chunk = selected[index : index + 50]
        shard = output / f"shard-{index // 50:03d}"
        shard.mkdir(parents=True)
        atomic(
            shard / "identity.json",
            {
                "schema_version": 1,
                "kind": "p10_tencent_native_m1_response_reprojection_v1",
                "market_date": ref["market_date"],
                "market_ods_manifest_sha256": ref["source_ods_manifest_sha256"],
                "selected_symbols": [r[0] for r in chunk],
                "provider": "tencent-mkline-m1",
                "new_supplier_requests": 0,
            },
        )
        for code, identity, source, old_receipt, target_digest in chunk:
            raw_dest = shard / "raw" / (code + ".json.gz")
            raw_dest.parent.mkdir(exist_ok=True)
            # Independent inode: later export/rsync metadata cannot touch the
            # protected original response through a shared hard link.
            shutil.copy2(source, raw_dest)
            atomic(
                shard / "receipts" / (code + ".json"),
                {
                    "stock_code": code,
                    "market_date": ref["market_date"],
                    "status": "raw_saved",
                    "provider": "tencent-mkline-m1",
                    "source_market_ods_sha256": ref["source_ods_manifest_sha256"],
                    "captured_at": old_receipt["captured_at"],
                    "raw_sha256": old_receipt["raw_sha256"],
                    "target_date_observation_sha256": target_digest,
                    "reuse_origin_identity": identity,
                    "reuse_origin_receipt": old_receipt,
                    "observed_projection_at": datetime.now(timezone.utc).isoformat(),
                },
            )
    return {
        "reused": len(selected),
        "remaining": len(ref["securities"]) - len(selected),
        "new_supplier_requests": 0,
    }


def verify_reprojection(
    meta: dict[str, Any],
    receipt: dict[str, Any],
    raw_path: Path,
    code: str,
    ref: dict[str, Any],
    quality_status: dict[str, Any] | None = None,
) -> None:
    from quanthus.providers.p10_native_m1_shard_capture import target_date_records

    check_day(ref["market_date"])
    original = receipt["reuse_origin_receipt"]
    identity = receipt["reuse_origin_identity"]
    raw = gzip.decompress(raw_path.read_bytes())
    captured = datetime.fromisoformat(receipt["captured_at"])
    projected = datetime.fromisoformat(receipt["observed_projection_at"])
    if captured.tzinfo is None or projected.tzinfo is None or projected < captured:
        raise ValueError("reused response actual clocks differ")
    if (
        quality_status is not None
        and quality_status.get("source_acquired_at_or_after") != receipt["captured_at"]
    ):
        raise ValueError("reused response quality clock differs from original capture")
    if (
        meta["provider"] != "tencent-mkline-m1"
        or original.get("provider") != "tencent-mkline-m1"
        or original.get("status") != "raw_saved"
        or original["stock_code"] != code
        or identity.get("kind") != "p10_tencent_native_m1_raw_capture_shard"
        or code not in identity["selected_symbols"]
        or original["market_date"] != identity["market_date"]
        or original["source_market_ods_sha256"] != identity["market_ods_manifest_sha256"]
        or original["captured_at"] != receipt["captured_at"]
        or original["raw_sha256"] != receipt["raw_sha256"]
        or hashlib.sha256(raw).hexdigest() != original["raw_sha256"]
        or target_date_records(raw, code, date.fromisoformat(original["market_date"]))[1]
        != original["target_date_observation_sha256"]
        or target_date_records(raw, code, date.fromisoformat(ref["market_date"]))[1]
        != receipt["target_date_observation_sha256"]
    ):
        raise ValueError("reused response original/time/target identity differs")
