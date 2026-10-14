import gzip
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines import p10_base23_realign as repair
from quanthus.pipelines import p10_complete_daily as complete
from quanthus.pipelines import p10_native_minute_delivery as minute
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, file_digest


def daily(tmp_path, monkeypatch, day="2026-10-08"):
    root = tmp_path / "daily-core"
    package = root / "packages" / day / "new"
    c = repair.contract()
    atomic(
        package / "manifest.json",
        {
            "kind": "cn01_daily_core_dwd_increment",
            "market_date": day,
            "base": {
                "manifest_sha256": c["base_manifest_sha256"],
                "content_digest": c["base_content_digest"],
            },
            "prior": None,
            "files": [],
            "acquisition_universe": {
                "policy_id": "p10-acquisition-prevention-v1",
                "market_date": day,
                "historical_pit_authorized": False,
                "universe_digest": digest(encoded(["000001"])),
                "codes": ["000001"],
                "population": 1,
                "provider": "baostock",
            },
        },
    )
    result = SimpleNamespace(
        market_date=date.fromisoformat(day),
        package_id="new",
        manifest_sha256=file_digest(package / "manifest.json"),
        content_digest="sha256:" + "e" * 64,
    )
    atomic(
        package / "source-universe.json",
        json.loads((package / "manifest.json").read_text())["acquisition_universe"],
    )
    monkeypatch.setattr(repair, "verify_daily_domain_increment", lambda _: result)
    old = {"market_date": day, "manifest_sha256": "sha256:" + "a" * 64}
    atomic(root / "date-pointers" / (day + ".json"), old)
    atomic(
        root / "CURRENT_DAILY_CORE.json",
        {"market_date": "2026-10-14", "manifest_sha256": "sha256:" + "b" * 64},
    )
    protected_file = tmp_path / "protected-12-13.json"
    protected_file.write_text("original")
    protected = {str(protected_file): hashlib.sha256(protected_file.read_bytes()).hexdigest()}
    return root, package, old, protected


@pytest.mark.parametrize("day", ["2026-10-12", "2026-10-13", "2026-10-15", "2026-09-30"])
def test_protected_and_future_dates_never_enter_recovery(day):
    with pytest.raises(ValueError, match="not authorized"):
        repair.check_day(day)


def test_historical_complete_needs_exact_extension(tmp_path, monkeypatch):
    _, package, _, _ = daily(tmp_path, monkeypatch)
    monkeypatch.setattr(complete, "verify_daily_domain_increment", lambda _: object())
    monkeypatch.setattr(complete, "daily_mapping", lambda *_: ({}, None))
    with pytest.raises(ValueError, match="one-off authority"):
        complete._check_daily(package)
    binding = repair.alignment_binding(package)
    assert complete._check_daily(package, binding)[0]["market_date"] == "2026-10-08"
    with pytest.raises(ValueError, match="exact authority"):
        complete._check_daily(package, {**binding, "daily_manifest_sha256": "sha256:" + "f" * 64})


def test_native_binding_is_mandatory(tmp_path, monkeypatch):
    _, package, _, _ = daily(tmp_path, monkeypatch)
    data = json.loads((package / "manifest.json").read_text())
    data["base"]["manifest_sha256"] = "sha256:" + "a" * 64
    atomic(package / "manifest.json", data)
    with pytest.raises(ValueError, match="actually bind"):
        repair.alignment_binding(package)


def test_historical_revision_preserves_current_and_old_descriptor(tmp_path, monkeypatch):
    root, package, old, protected = daily(tmp_path, monkeypatch)
    current = (root / "CURRENT_DAILY_CORE.json").read_bytes()
    outcome = repair.publish_daily_revision(
        root=root,
        package=package,
        expected_old=old["manifest_sha256"],
        protected=protected,
        receipt_root=tmp_path / "receipts",
    )
    assert outcome["status"] == "published"
    assert (root / "CURRENT_DAILY_CORE.json").read_bytes() == current
    assert (
        json.loads((root / "dated-revisions/2026-10-08" / ("a" * 64 + ".json")).read_text()) == old
    )
    assert outcome["supersedes"] == old and outcome["prior"] is None
    assert (
        repair.publish_daily_revision(
            root=root,
            package=package,
            expected_old=old["manifest_sha256"],
            protected=protected,
            receipt_root=tmp_path / "receipts",
        )["status"]
        == "already_published"
    )


def test_revision_cas_fails_before_writing(tmp_path, monkeypatch):
    root, package, _, protected = daily(tmp_path, monkeypatch)
    pointer = root / "date-pointers/2026-10-08.json"
    original = pointer.read_bytes()
    with pytest.raises(ValueError, match="CAS"):
        repair.publish_daily_revision(
            root=root,
            package=package,
            expected_old="sha256:" + "c" * 64,
            protected=protected,
            receipt_root=tmp_path / "receipts",
        )
    assert pointer.read_bytes() == original


def test_protected_change_blocks_revision(tmp_path, monkeypatch):
    root, package, old, protected = daily(tmp_path, monkeypatch)
    Path(next(iter(protected))).write_text("changed")
    with pytest.raises(ValueError, match="protected"):
        repair.publish_daily_revision(
            root=root,
            package=package,
            expected_old=old["manifest_sha256"],
            protected=protected,
            receipt_root=tmp_path / "receipts",
        )
    assert json.loads((root / "date-pointers/2026-10-08.json").read_text()) == old


def test_response_reprojection_preserves_original_clock_and_closure(tmp_path, monkeypatch):
    code = "000001"
    ref = {
        "kind": "p10_complete_daily_job_reference_v2",
        "market_date": "2026-10-09",
        "source_ods_manifest_sha256": "sha256:" + "a" * 64,
        "event_ods_manifest_sha256": "sha256:" + "b" * 64,
        "policy_id": "p10-trading-day-complete-daily-v1",
        "population": 1,
        "securities": {code: {"close": "1", "volume": "600", "tradestatus": "1"}},
        "universe_policy_id": "p10-acquisition-prevention-v1",
        "universe_digest": digest(encoded([code])),
        "universe_manifest_sha256": "sha256:" + "c" * 64,
    }
    ref["reference_digest"] = digest(encoded(ref))
    reference_file = tmp_path / "reference.json"
    atomic(reference_file, ref)
    raw = encoded(
        {
            "code": 0,
            "data": {
                "sz000001": {
                    "m1": [
                        ["202610091000", "1", "1", "1", "1", "1", "1"],
                        ["202610131000", "1", "1", "1", "1", "1", "1"],
                    ]
                }
            },
        }
    )
    from quanthus.providers.p10_native_m1_shard_capture import target_date_records

    shard = tmp_path / "original/shard-000"
    atomic(
        shard / "identity.json",
        {
            "kind": "p10_tencent_native_m1_raw_capture_shard",
            "market_date": "2026-10-13",
            "market_ods_manifest_sha256": "sha256:" + "d" * 64,
            "selected_symbols": [code],
        },
    )
    origin = {
        "stock_code": code,
        "status": "raw_saved",
        "market_date": "2026-10-13",
        "source_market_ods_sha256": "sha256:" + "d" * 64,
        "provider": "tencent-mkline-m1",
        "captured_at": "2026-10-14T03:00:00+00:00",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "target_date_observation_sha256": target_date_records(raw, code, date(2026, 10, 13))[1],
    }
    atomic(shard / "receipts" / (code + ".json"), origin)
    (shard / "raw").mkdir()
    (shard / "raw" / (code + ".json.gz")).write_bytes(gzip.compress(raw))
    monkeypatch.setattr(minute, "assess", lambda **_: ({"quality_grade": "R0"}, {}))
    result = repair.seed_reused_m1(
        origin_root=tmp_path / "original", reference_file=reference_file, output=tmp_path / "reuse"
    )
    assert result == {"reused": 1, "remaining": 0, "new_supplier_requests": 0}
    index = minute.source_index(tmp_path / "reuse", ref, "1m")
    assert index[code][1]["captured_at"] == origin["captured_at"]
    assert index[code][1]["reuse_origin_receipt"] == origin
    proof = index[code][1]
    with pytest.raises(ValueError, match="quality clock"):
        repair.verify_reprojection(
            {"provider": "tencent-mkline-m1"},
            proof,
            index[code][0],
            code,
            ref,
            quality_status={"source_acquired_at_or_after": "2026-10-09T00:00:00+00:00"},
        )
    with pytest.raises(ValueError, match="identity differs|actual clocks differ"):
        repair.verify_reprojection(
            {"provider": "tencent-mkline-m1"},
            {**proof, "captured_at": "2026-10-15T01:00:00+00:00"},
            index[code][0],
            code,
            ref,
        )


def test_independent_recovery_job_cannot_reset_on_changed_reference(tmp_path, monkeypatch):
    from quanthus.pipelines import p10_historical_tplus1_recovery as runtime

    monkeypatch.setattr(repair, "STATE", tmp_path)
    monkeypatch.setattr(runtime, "_run_operation", lambda **_: {"outcome": "success"})
    args = dict(
        day="2026-10-08",
        interval="1m",
        reference_digest="sha256:" + "a" * 64,
        revision="b" * 40,
        operation_id="seed",
        argv=["true"],
        publication=True,
    )
    args["argv"] = ["python", "-m", "quanthus.pipelines.p10_native_minute_delivery", "build"]
    assert repair.run_recovery_operation(**args)["outcome"] == "success"
    with pytest.raises(ValueError, match="budget cannot reset"):
        repair.run_recovery_operation(**{**args, "reference_digest": "sha256:" + "c" * 64})


def test_capture_cannot_be_classified_as_publication():
    with pytest.raises(ValueError, match="cannot bypass"):
        repair.run_recovery_operation(
            day="2026-10-08",
            interval="1m",
            reference_digest="sha256:" + "a" * 64,
            revision="b" * 40,
            operation_id="bad",
            argv=["python", "-m", "quanthus.providers.p10_native_m1_shard_capture"],
            publication=True,
        )
    with pytest.raises(ValueError, match="cannot bypass"):
        repair.run_recovery_operation(
            day="2026-10-08",
            interval="1m",
            reference_digest="sha256:" + "a" * 64,
            revision="b" * 40,
            operation_id="bad-native",
            argv=["python", "-m", "quanthus.pipelines.p10_native_minute_delivery", "capture"],
            publication=True,
        )


def test_universe_descriptor_cannot_differ_from_manifest(tmp_path, monkeypatch):
    _, package, _, _ = daily(tmp_path, monkeypatch)
    atomic(package / "source-universe.json", {"market_date": "2026-10-09"})
    with pytest.raises(ValueError, match="target-date universe"):
        repair.alignment_binding(package)


def test_complete_revision_persists_actual_verification_before_pointer(tmp_path, monkeypatch):
    from quanthus.pipelines import p10_delivery_common as common

    root, package, _, protected = daily(tmp_path, monkeypatch)
    actual = {
        "market_date": "2026-10-08",
        "package_id": "new",
        "manifest_sha256": file_digest(package / "manifest.json"),
        "content_digest": "sha256:" + "e" * 64,
        "base": repair.contract()["base_manifest_sha256"],
        "prior": None,
    }
    monkeypatch.setattr(complete, "verify", lambda _: actual)
    monkeypatch.setattr(common, "prepare_export", lambda *_: None)
    pointer = root / "date-pointers/2026-10-08.json"
    pointer.unlink()
    current = root / "CURRENT_COMPLETE_DAILY.json"
    atomic(current, {"market_date": "2026-10-13"})
    before = current.read_bytes()
    repair.publish_complete_revision(root=root, package=package, protected=protected)
    receipt = json.loads(
        (root / "verification" / (actual["manifest_sha256"][7:] + ".json")).read_text()
    )
    assert receipt["mode"] == "actual_complete_package_files_and_native_projection"
    assert receipt["manifest_sha256"] == actual["manifest_sha256"]
    assert current.read_bytes() == before


def test_dated_consumer_requires_exact_historical_extension(tmp_path, monkeypatch):
    from quanthus.pipelines.p10_delivery_common import BASE23_CONTENT, BASE23_MANIFEST
    from quanthus.platform import complete_daily_source as source

    sha = "sha256:" + "a" * 64
    manifest = {
        "kind": source.KIND,
        "policy_id": source.POLICY_ID,
        "policy_sha256": "policy",
        "base": {"manifest_sha256": BASE23_MANIFEST, "content_digest": BASE23_CONTENT},
        "consumer_purpose": "gap_aware_non_execution_development",
        "execution_authorized": False,
        "market_date": "2026-10-08",
        "content_digest": "content",
        "consumable_intervals": ["1d", "1m", "5m"],
        "minute_quality_grades": {"1m": {"R3": 0}, "5m": {"R3": 0}},
    }
    pointer = {
        "manifest_path": "packages/day/new/manifest.json",
        "market_date": "2026-10-08",
        "manifest_sha256": sha,
        "content_digest": "content",
    }
    package = tmp_path / "packages/day/new"
    package.mkdir(parents=True)
    (package / "READY").write_text(sha)
    visibility = {
        **pointer,
        "exact_source_match": True,
        "execution_authorized": False,
        "first_verified_visible_at": "2026-10-15T00:00:00+00:00",
    }
    monkeypatch.setattr(
        source,
        "object_at",
        lambda p: (
            manifest
            if p.name == "manifest.json"
            else visibility
            if p.parent.name == "visibility"
            else pointer
        ),
    )
    monkeypatch.setattr(source, "file_digest", lambda p: "policy" if p == source.CONFIG else sha)
    called = []

    def verify(binding, daily):
        called.append((binding, daily))
        if binding != {"authorized": True}:
            raise ValueError("historical extension differs")

    monkeypatch.setattr(repair, "verify_alignment", verify)
    consumer = source.CompleteDailySource(tmp_path, market_date="2026-10-08")
    with pytest.raises(ValueError, match="extension differs"):
        consumer.admission()
    manifest["historical_alignment"] = {"authorized": True}
    assert consumer.admission()["market_date"] == "2026-10-08"
    assert called[-1][1] == package / "members/1d"


def test_daily_mirror_reentry_keeps_full_predecessor_and_first_receipt(tmp_path, monkeypatch):
    import shutil

    from quanthus import warehouse_sync as sync

    remote, package, _, protected = daily(tmp_path / "cn01", monkeypatch)
    sha = file_digest(package / "manifest.json")
    pointer = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_current",
        "product_scope": "daily-core-v1",
        "market_date": "2026-10-08",
        "package_id": "new",
        "manifest_sha256": sha,
        "content_digest": "sha256:" + "e" * 64,
        "manifest_path": "packages/2026-10-08/new/manifest.json",
    }
    atomic(remote / "date-pointers/2026-10-08.json", pointer)
    target = tmp_path / "oci"
    old_manifest = target / "packages/2026-10-08/old/manifest.json"
    atomic(
        old_manifest,
        {"package_id": "old", "content_digest": "old-content", "market_date": "2026-10-08"},
    )
    old_sha = file_digest(old_manifest)
    old_pointer = {
        **pointer,
        "package_id": "old",
        "manifest_sha256": old_sha,
        "content_digest": "old-content",
        "manifest_path": "packages/2026-10-08/old/manifest.json",
    }
    atomic(target / "date-pointers/2026-10-08.json", old_pointer)
    atomic(
        target / "CURRENT_DAILY_CORE.json", {"market_date": "2026-10-14", "manifest_sha256": "tip"}
    )
    monkeypatch.setenv("QUANTHUS_CN01_DWD_REMOTE_ROOT", "products/daily-core")
    monkeypatch.setattr(sync, "_ssh_command", lambda: ("cn01", "22", "reader", "", "", "ssh"))
    monkeypatch.setattr(sync, "_validate_pointer", lambda *_: (Path("packages/2026-10-08/new"), {}))

    def transfer(*, source, destination, ssh):
        original = remote / source.split("products/daily-core/", 1)[1]
        if original.is_dir():
            shutil.copytree(original, destination, dirs_exist_ok=True)
        else:
            shutil.copyfile(original, destination)

    monkeypatch.setattr(sync, "_run_rsync", transfer)
    args = {
        "root": target,
        "day": "2026-10-08",
        "expected_old": old_sha,
        "expected_new": sha,
        "protected": protected,
    }
    first = repair.mirror_daily_revision(**args)
    second = repair.mirror_daily_revision(**args)
    assert first == second
    assert (
        json.loads((target / "dated-revisions/2026-10-08" / (old_sha[7:] + ".json")).read_text())
        == old_pointer
    )
    assert json.loads((target / "CURRENT_DAILY_CORE.json").read_text())["manifest_sha256"] == "tip"
