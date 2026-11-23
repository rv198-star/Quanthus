from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.p10_daily_sidecar import (
    SCHEMA,
    SESSION_SCHEMA,
    ConsumerConfig,
    InputVerification,
    MeasureRequirement,
    ResourceLimits,
    SidecarError,
    SidecarStore,
    assess,
    canonical,
    input_identity,
    inventory_digest,
    record_id,
    select_as_of,
)


def test_audit_source_fixture_never_receives_source_observed_review():
    record, cfg, receipt = specimen(grade="SOURCE_OBSERVED")
    assert assess(record, cfg, receipt).disposition != "CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW"


def test_audit_corrupt_committed_parent_prevents_append(tmp_path):
    old, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "audit-parent", cfg, protected_roots=[])
    store.append(old, receipt)
    old["publication"]["publisher"] = ""
    seal(old)
    path = next((store.root / "records").glob("*.json"))
    stored = json.loads(path.read_bytes())
    if "record" in stored:
        stored["record"] = old
    else:
        stored = old
    path.unlink()
    path.with_name(f"00000001-{old['record_id'][7:]}.json").write_bytes(canonical(stored))
    new = withdraw(old)
    new["publication"]["publisher"] = "fixture-publisher"
    seal(new)
    with pytest.raises(SidecarError):
        store.append(new, verification(new, cfg))
    assert len(list((store.root / "records").glob("*.json"))) == 1


def test_audit_withdrawal_cannot_replace_payload_members(tmp_path):
    old, cfg, receipt = specimen()
    changed = withdraw(old)
    changed["members"][0]["sha256"] = sha("9")
    changed["publication"]["covered_inventory_sha256"] = inventory_digest(changed["members"])
    seal(changed)
    store = SidecarStore(tmp_path / "audit-withdrawal", cfg, protected_roots=[])
    store.append(old, receipt)
    with pytest.raises(SidecarError, match="WITHDRAWAL_INVENTORY_MISMATCH"):
        store.append(changed, verification(changed, cfg))


def test_observed_looking_uri_does_not_replace_source_qualification():
    record, cfg, _ = specimen(grade="SOURCE_OBSERVED")
    for ref in record["source"].values():
        ref["uri"] = "cn02:/qualified-looking/" + ref["uri"].split(":")[-1]
    for proof in record["evidence"]:
        proof["uri"] = "cn02:/qualified-looking/" + proof["id"]
    seal(record)
    receipt = replace(verification(record, cfg), uri="cn02:/qualified-looking/verified")
    result = assess(record, cfg, receipt)
    assert result.disposition == "INPUT_OR_CLOCK_BLOCKED"
    assert "SOURCE_QUALIFICATION_UNBOUND" in result.blockers


@pytest.mark.parametrize(
    "field", ["sha256", "path", "size_bytes", "source_version", "available_at", "state"]
)
def test_whole_version_withdrawal_preserves_exact_original_members(field):
    old, cfg, _ = specimen()
    new = withdraw(old)
    member = new["members"][0]
    values = {
        "sha256": sha("9"),
        "path": "different.parquet",
        "size_bytes": 11,
        "source_version": "changed",
        "available_at": clock("19:02:00", "consumer_channel_visible", "members-visible"),
        "state": "WITHDRAWN",
    }
    member[field] = values[field]
    if field == "state":
        member["unavailable_reason"] = "claimed-partial-withdrawal"
    new["publication"]["covered_inventory_sha256"] = inventory_digest(new["members"])
    seal(new)
    with pytest.raises(SidecarError, match="WITHDRAWAL_INVENTORY_MISMATCH"):
        assess(new, cfg, verification(new, cfg), previous=old)


def test_persisted_receipt_is_rechecked_before_another_append(tmp_path):
    old, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "receipt", cfg, protected_roots=[])
    store.append(old, receipt)
    path = next((store.root / "records").glob("*.json"))
    stored = json.loads(path.read_bytes())
    assert stored["verification"]["input_identity"] == receipt.input_identity
    stored["verification"]["sha256"] = sha("9")
    path.write_bytes(canonical(stored))
    with pytest.raises(SidecarError, match="VERIFICATION_EVIDENCE_MISMATCH"):
        store.append(withdraw(old), receipt)
    assert len(list((store.root / "records").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("max_evidence", 4, "MAX_EVIDENCE_EXCEEDED"),
        ("max_path_bytes", 4, "MAX_PATH_BYTES_EXCEEDED"),
        ("max_record_bytes", 100, "MAX_RECORD_BYTES_EXCEEDED"),
    ],
)
def test_injected_consumer_limits_bound_metadata_admission(field, value, reason):
    cfg = replace(config(), resource_limits=ResourceLimits("bounded-test-v1", **{field: value}))
    record, _, receipt = specimen(cfg)
    with pytest.raises(SidecarError, match=reason):
        assess(record, cfg, receipt)
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg


def test_member_limit_is_consumer_specific_without_a_global_thousand_cap():
    large = replace(
        config(),
        requirements=config().requirements + tuple((f"optional-{i}", False) for i in range(1000)),
    )
    record, cfg, receipt = specimen(large)
    result = assess(record, cfg, receipt)
    assert result.disposition == "TEST_ONLY"
    assert "UNCONFIGURED_RESOURCE_BOUND:max_members" in result.boundary_notes
    with pytest.raises(SidecarError, match="MAX_MEMBERS_EXCEEDED"):
        replace(large, resource_limits=ResourceLimits("specific-two-member-use-v1", max_members=2))


@pytest.mark.parametrize(
    "field,reason", [("max_events", "MAX_EVENTS_EXCEEDED"), ("max_files", "MAX_FILES_EXCEEDED")]
)
def test_consumer_event_and_file_cap_preserves_retry_and_rejects_new_append(
    tmp_path, field, reason
):
    cfg = replace(config(), resource_limits=ResourceLimits("single-event-test-v1", **{field: 1}))
    old, _, receipt = specimen(cfg)
    store = SidecarStore(tmp_path / field, cfg, protected_roots=[])
    assert store.append(old, receipt)
    assert not store.append(old, receipt)
    with pytest.raises(SidecarError, match=reason):
        store.append(withdraw(old), receipt)
    assert store.read() == [old]


def test_store_bytes_budget_does_not_change_payload_size_policy(tmp_path):
    cfg = replace(
        config(), resource_limits=ResourceLimits("small-metadata-store-test-v1", max_store_bytes=1)
    )
    record, _, receipt = specimen(cfg)
    record["members"][0]["size_bytes"] = 10**12
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    seal(record)
    receipt = verification(record, cfg)
    assert not assess(record, cfg, receipt).blockers
    store = SidecarStore(tmp_path / "tiny-budget", cfg, protected_roots=[])
    with pytest.raises(SidecarError, match="MAX_STORE_BYTES_EXCEEDED"):
        store.append(record, receipt)
    assert not list((store.root / "records").glob("*.json"))


def test_record_file_is_size_checked_before_reading_or_parsing(tmp_path):
    cfg = replace(
        config(), resource_limits=ResourceLimits("file-read-budget-test-v1", max_record_bytes=20000)
    )
    record, _, receipt = specimen(cfg)
    store = SidecarStore(tmp_path / "read-limit", cfg, protected_roots=[])
    store.append(record, receipt)
    path = next((store.root / "records").glob("*.json"))
    path.write_bytes(b" " * 20001)
    with pytest.raises(SidecarError, match="MAX_RECORD_BYTES_EXCEEDED"):
        store.read()


def sha(letter):
    return "sha256:" + letter * 64


def config(*, pending=False, optional=False):
    return ConsumerConfig(
        "test-consumer-v1",
        "p11-test",
        "test-only",
        None if pending else sha("a"),
        None if pending else sha("b"),
        sha("c"),
        "d" * 40,
        (("bars", True), ("calendar", not optional)),
        m0_ref=None if pending else sha("0"),
    )


def clock(time, semantic, proof):
    return {
        "start": f"2026-11-24T{time}+08:00",
        "end": f"2026-11-24T{time}+08:00",
        "evidence_ref": proof,
        "semantic": semantic,
    }


def seal(record):
    record["record_id"] = record_id(record)
    return record


def verification(record, cfg):
    return InputVerification(
        input_identity(record, cfg),
        "verified",
        "fixture:verified-input-receipt",
        sha("e"),
        "2026-11-24T18:30:00+08:00",
        "2026-11-24T18:30:00+08:00",
    )


def specimen(cfg=None, *, grade="SYNTHETIC"):
    cfg = cfg or config()
    members = [
        {
            "id": name,
            "path": name + ".parquet",
            "sha256": sha("f"),
            "size_bytes": 10,
            "required": required,
            "source_version": "fixture-source-v1",
            "state": "PRESENT",
            "unavailable_reason": None,
            "source_available_at": clock("18:00:00", "source_artifact_known", "source"),
            "validated_at": clock("18:30:00", "validation_complete", "verified"),
            "available_at": clock("19:01:00", "consumer_channel_visible", "members-visible"),
        }
        for name, required in cfg.requirements
    ]
    evidence = [
        {
            "id": name,
            "kind": "SYNTHETIC_FIXTURE" if grade == "SYNTHETIC" else "OBSERVED_ATTESTATION",
            "semantic": semantic,
            "uri": "fixture:" + name,
            "sha256": sha("e"),
            "descriptor_sha256": sha("1"),
            "covered_member_ids": [m["id"] for m in members],
        }
        for name, semantic in (
            ("source", "source_artifact_known"),
            ("verified", "validation_complete"),
            ("published", "publication_commit"),
            ("members-visible", "consumer_channel_visible"),
            ("event-visible", "consumer_channel_visible"),
        )
    ]
    evidence[1]["uri"] = "fixture:verified-input-receipt"
    record = {
        "schema": SCHEMA,
        "record_id": "",
        "evidence_grade": grade,
        "event": "PUBLISH",
        "binding": {
            "delivery_id": "fixture-v1",
            "purpose_id": cfg.purpose_id,
            "market_date": "2026-11-24",
            "observation_end": "2026-11-24T15:00:00+08:00",
            "decision_cutoff": "2026-11-24T22:30:00+08:00",
            "consumer_id": cfg.consumer_id,
            "m0_ref": cfg.m0_ref,
        },
        "scope": {"intervals": ["1d"], "references": "required_only"},
        "source": {
            "descriptor": {"uri": "fixture:descriptor", "sha256": sha("1")},
            "manifest": {"uri": "fixture:manifest", "sha256": sha("2")},
        },
        "members": members,
        "publication": {
            "publisher": "fixture-publisher",
            "channel": "fixture-channel",
            "published_at": clock("19:00:00", "publication_commit", "published"),
            "visible_at": clock("19:02:00", "consumer_channel_visible", "event-visible"),
            "covered_inventory_sha256": inventory_digest(members),
        },
        "evidence": evidence,
        "revision": {
            "state": "COMPLETE",
            "source_chain_state": "COMPLETE",
            "previous_record_id": None,
            "reason": None,
            "replaces_descriptor_sha256": None,
        },
        "consumer_receipt": None,
    }
    return seal(record), cfg, verification(record, cfg)


def withdraw(old, *, visible="21:01:00"):
    record = copy.deepcopy(old)
    record["event"] = "WITHDRAW"
    record["publication"]["published_at"] = clock("21:00:00", "publication_commit", "published")
    record["publication"]["visible_at"] = clock(
        visible, "consumer_channel_visible", "event-visible"
    )
    record["revision"].update(
        previous_record_id=old["record_id"],
        reason="fixture-withdrawal",
        replaces_descriptor_sha256=old["source"]["descriptor"]["sha256"],
    )
    return seal(record)


def revise(old):
    record = copy.deepcopy(old)
    record["event"] = "REVISE"
    record["binding"]["delivery_id"] = "fixture-v2"
    record["source"]["descriptor"]["sha256"] = sha("3")
    record["source"]["manifest"]["sha256"] = sha("4")
    for evidence in record["evidence"]:
        evidence["descriptor_sha256"] = sha("3")
    record["publication"]["published_at"] = clock("20:00:00", "publication_commit", "published")
    record["publication"]["visible_at"] = clock(
        "20:02:00", "consumer_channel_visible", "event-visible"
    )
    for member in record["members"]:
        member["source_version"] = "fixture-source-v2"
        member["available_at"] = clock("20:01:00", "consumer_channel_visible", "members-visible")
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    record["revision"].update(
        previous_record_id=old["record_id"],
        reason="fixture-revision",
        replaces_descriptor_sha256=old["source"]["descriptor"]["sha256"],
    )
    return seal(record)


def select(records, cfg, receipts, cutoff="2026-11-24T22:30:00+08:00"):
    return select_as_of(records, cfg, receipts, market_date="2026-11-24", cutoff=cutoff)


def test_complete_metadata_is_for_consumer_review_only():
    record, cfg, receipt = specimen()
    result = assess(record, cfg, receipt)
    assert result.disposition == "TEST_ONLY"
    assert not result.blockers
    assert record["consumer_receipt"] is None
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg


@pytest.mark.parametrize("grade", ["SYNTHETIC", "HISTORICAL_METADATA_ONLY"])
def test_non_actual_grades_do_not_receive_consumer_readiness(grade):
    record, cfg, receipt = specimen(grade=grade)
    assert assess(record, cfg, receipt).disposition == (
        "TEST_ONLY" if grade == "SYNTHETIC" else "INPUT_OR_CLOCK_BLOCKED"
    )


def test_pending_pool_is_configurable_and_blocked():
    record, cfg, receipt = specimen(config(pending=True))
    assert "CONSUMER_CLOSURE_PENDING" in assess(record, cfg, receipt).blockers


def test_unknown_available_at_stays_null_with_stable_input_verification():
    record, cfg, receipt = specimen()
    record["members"][0]["available_at"] = None
    seal(record)
    assert record["members"][0]["available_at"] is None
    assert "HARD_MEMBER_BLOCKED:bars" in assess(record, cfg, receipt).blockers


def test_missing_hard_member_is_not_silently_made_optional():
    record, cfg, _ = specimen()
    record["members"].pop()
    for evidence in record["evidence"]:
        evidence["covered_member_ids"] = ["bars"]
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert "HARD_MEMBER_BLOCKED:calendar" in result.blockers


def test_missing_optional_member_is_isolated():
    record, cfg, _ = specimen(config(optional=True))
    record["members"].pop()
    for evidence in record["evidence"]:
        evidence["covered_member_ids"] = ["bars"]
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert not result.blockers
    assert result.member_blockers == (("calendar", ("MEMBER_MISSING",)),)


@pytest.mark.parametrize("field", ["source_version", "sha256", "source_available_at"])
def test_old_verification_cannot_cover_changed_source_identity(field):
    record, cfg, receipt = specimen()
    record["members"][0][field] = {
        "source_version": "changed",
        "sha256": sha("9"),
        "source_available_at": clock("18:01:00", "source_artifact_known", "source"),
    }[field]
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    seal(record)
    with pytest.raises(SidecarError, match="VERIFICATION_IDENTITY_MISMATCH"):
        assess(record, cfg, receipt)


def test_old_verification_cannot_cover_changed_pool():
    record, cfg, receipt = specimen()
    cfg = replace(cfg, universe_sha256=sha("9"))
    record["binding"]["m0_ref"] = cfg.m0_ref
    seal(record)
    with pytest.raises(SidecarError, match="VERIFICATION_IDENTITY_MISMATCH"):
        assess(record, cfg, receipt)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda r: r.update(schema="other"), "UNSUPPORTED_SCHEMA"),
        (lambda r: r.update(consumer_receipt={"received_at": "invented"}), "CONSUMER_RECEIPT"),
        (lambda r: r["members"][0].update(required=False), "MEMBER_REQUIREMENT_CHANGED"),
        (lambda r: r["members"][0].update(path="../escape"), "UNSAFE_MEMBER_PATH"),
        (lambda r: r["members"][0].update(size_bytes=True), "INVALID_MEMBER_SIZE"),
        (lambda r: r["scope"].update(intervals=["1d", "5m"]), "INVALID_SCOPE"),
        (
            lambda r: r["members"][0]["available_at"].update(semantic="manifest_built_at"),
            "CLOCK_SEMANTIC",
        ),
        (
            lambda r: r["members"][0]["available_at"].update(start="2026-11-24T19:01:00"),
            "TIMEZONE_REQUIRED",
        ),
    ],
)
def test_schema_and_clock_errors_are_explicit(change, reason):
    record, cfg, receipt = specimen()
    change(record)
    seal(record)
    with pytest.raises(SidecarError, match=reason):
        assess(record, cfg, receipt)


def test_record_id_rejects_unsealed_changes():
    record, cfg, receipt = specimen()
    record["publication"]["publisher"] = "changed"
    with pytest.raises(SidecarError, match="RECORD_ID_MISMATCH"):
        assess(record, cfg, receipt)


def test_validation_proof_and_exact_member_coverage_are_bound():
    record, cfg, receipt = specimen()
    record["evidence"][1]["covered_member_ids"] = ["bars"]
    seal(record)
    with pytest.raises(SidecarError, match="VERIFICATION_EVIDENCE_MISMATCH"):
        assess(record, cfg, receipt)


def test_non_source_batch_proof_cannot_skip_a_member():
    record, cfg, receipt = specimen()
    record["evidence"][3]["covered_member_ids"] = ["calendar"]
    seal(record)
    with pytest.raises(SidecarError, match="CLOCK_MEMBER_COVERAGE_MISSING"):
        assess(record, cfg, receipt)


def test_synthetic_proof_cannot_be_promoted():
    record, cfg, receipt = specimen(grade="SOURCE_OBSERVED")
    record["evidence"][3]["kind"] = "SYNTHETIC_FIXTURE"
    seal(record)
    with pytest.raises(SidecarError, match="SYNTHETIC_EVIDENCE_NOT_REAL"):
        assess(record, cfg, receipt)


def test_visibility_before_publication_is_rejected():
    record, cfg, receipt = specimen()
    record["publication"]["visible_at"] = clock(
        "18:59:00", "consumer_channel_visible", "event-visible"
    )
    seal(record)
    with pytest.raises(SidecarError, match="EVENT_CAUSALITY"):
        assess(record, cfg, receipt)


def test_late_withdrawal_does_not_rewrite_cutoff_snapshot():
    old, cfg, receipt = specimen()
    late = withdraw(old, visible="22:31:00")
    refs = {old["record_id"]: receipt, late["record_id"]: receipt}
    assert select([old, late], cfg, refs).record_id == old["record_id"]
    later = select([old, late], cfg, refs, "2026-11-24T22:32:00+08:00")
    assert later.disposition == "INPUT_OR_CLOCK_BLOCKED"
    assert "WITHDRAWN_VERSION" in later.blockers


def test_cross_cutoff_withdrawal_interval_is_unknown_not_old_ready():
    old, cfg, receipt = specimen()
    revised = withdraw(old)
    revised["publication"]["visible_at"]["start"] = "2026-11-24T22:29:59+08:00"
    revised["publication"]["visible_at"]["end"] = "2026-11-24T22:30:01+08:00"
    seal(revised)
    result = select([old, revised], cfg, {old["record_id"]: receipt, revised["record_id"]: receipt})
    assert result.disposition == "INPUT_OR_CLOCK_BLOCKED"


def test_unknown_visibility_does_not_default_to_library_call_time():
    old, cfg, receipt = specimen()
    revised = withdraw(old)
    revised["publication"]["visible_at"] = None
    seal(revised)
    result = select([old, revised], cfg, {old["record_id"]: receipt, revised["record_id"]: receipt})
    assert result.blockers == ("EVENT_VISIBILITY_UNKNOWN",)
    assert revised["publication"]["visible_at"] is None


def test_revision_selects_new_identity_only_after_its_visibility():
    old, cfg, receipt = specimen()
    new = revise(old)
    new_receipt = verification(new, cfg)
    refs = {old["record_id"]: receipt, new["record_id"]: new_receipt}
    assert select([old, new], cfg, refs, "2026-11-24T19:30:00+08:00").record_id == old["record_id"]
    assert select([old, new], cfg, refs).record_id == new["record_id"]


def test_revision_requires_new_delivery_descriptor_and_manifest():
    old, cfg, receipt = specimen()
    new = copy.deepcopy(old)
    new["event"] = "REVISE"
    new["revision"].update(
        previous_record_id=old["record_id"],
        reason="retry",
        replaces_descriptor_sha256=old["source"]["descriptor"]["sha256"],
    )
    seal(new)
    with pytest.raises(SidecarError, match="REVISION_REQUIRES_NEW_IDENTITY"):
        assess(new, cfg, receipt, previous=old)


def test_asof_checks_member_time_even_before_registered_cutoff():
    record, cfg, receipt = specimen()
    result = select([record], cfg, {record["record_id"]: receipt}, "2026-11-24T18:59:00+08:00")
    assert result.disposition == "INPUT_OR_CLOCK_BLOCKED"


def test_store_exact_retry_and_new_process_reuse(tmp_path):
    record, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[tmp_path / "source"])
    assert store.append(record, receipt)
    original = list((tmp_path / "metadata" / "records").iterdir())[0].read_bytes()
    assert not SidecarStore(store.root, cfg, protected_roots=[]).append(record, receipt)
    assert store.read() == [record]
    assert list((store.root / "records").iterdir())[0].read_bytes() == original


@pytest.mark.parametrize("after_link", [False, True])
def test_crash_before_or_after_commit_has_one_recoverable_event(tmp_path, monkeypatch, after_link):
    record, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.read()  # initialize the dedicated metadata namespace
    original = store._sync

    def crash(path):
        if path.name == "records":
            raise OSError("simulated post-link crash")
        return original(path)

    if after_link:
        monkeypatch.setattr(store, "_sync", crash)
    else:
        monkeypatch.setattr(
            "quanthus.p10_daily_sidecar.os.link",
            lambda *_: (_ for _ in ()).throw(OSError("simulated pre-link crash")),
        )
    with pytest.raises(OSError, match="simulated"):
        store.append(record, receipt)
    monkeypatch.undo()
    retry = SidecarStore(store.root, cfg, protected_roots=[])
    assert retry.append(record, receipt) is (not after_link)
    assert retry.read() == [record]


def test_orphan_temp_file_is_not_a_committed_event(tmp_path):
    record, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.read()
    (store.root / "records" / ".pending-crash").write_bytes(b"partial")
    assert store.append(record, receipt)
    assert store.read() == [record]


def test_concurrent_exact_retry_writes_once(tmp_path):
    record, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.append(record, receipt), range(2)))
    assert sorted(results) == [False, True]
    assert store.read() == [record]


def test_withdrawal_and_stale_predecessor_append(tmp_path):
    old, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.append(old, receipt)
    withdrawn = withdraw(old)
    assert store.append(withdrawn, receipt)
    competing = revise(old)
    with pytest.raises(SidecarError, match="PREDECESSOR_ID_MISMATCH"):
        store.append(competing, verification(competing, cfg))


def test_source_roots_and_symlinks_are_write_protected(tmp_path):
    cfg = config()
    with pytest.raises(SidecarError, match="STORE_SOURCE_OVERLAP"):
        SidecarStore(tmp_path / "source" / "metadata", cfg, protected_roots=[tmp_path / "source"])
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SidecarError, match="STORE_SYMLINK"):
        SidecarStore(link, cfg, protected_roots=[])


def test_configuration_rebind_cannot_overwrite_store(tmp_path):
    old, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.append(old, receipt)
    with pytest.raises(SidecarError, match="IMMUTABLE_EVENT_CONFLICT"):
        SidecarStore(store.root, replace(cfg, universe_sha256=sha("9")), protected_roots=[]).read()


def test_corrupt_or_duplicate_json_committed_event_fails_closed(tmp_path):
    record, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.append(record, receipt)
    p = next((store.root / "records").glob("*.json"))
    p.write_text('{"schema": 1, "schema": 2}')
    with pytest.raises(SidecarError, match="DUPLICATE_JSON_KEY"):
        store.read()


def test_no_payload_reads_are_needed_for_same_identity_verification(tmp_path, monkeypatch):
    record, cfg, receipt = specimen()
    protected = tmp_path / "source"
    protected.mkdir()
    payload = protected / "bars.parquet"
    payload.write_bytes(b"not opened by metadata validation")
    read_bytes = Path.read_bytes

    def guarded(path):
        if path.is_relative_to(protected):
            raise AssertionError("metadata library tried to read payload")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[protected])
    assert store.append(record, receipt)
    assert assess(record, cfg, receipt).disposition == "TEST_ONLY"


def test_known_late_publication_with_unknown_visibility_cannot_revoke_past_snapshot():
    old, cfg, receipt = specimen()
    late = withdraw(old)
    late["publication"]["published_at"] = clock("23:00:00", "publication_commit", "published")
    late["publication"]["visible_at"] = None
    seal(late)
    result = select([old, late], cfg, {old["record_id"]: receipt, late["record_id"]: receipt})
    assert result.record_id == old["record_id"]


def test_source_after_observation_close_can_meet_explicit_evening_deadline():
    record, cfg, receipt = specimen()
    assert record["binding"]["observation_end"] == "2026-11-24T15:00:00+08:00"
    assert record["members"][0]["source_available_at"]["start"].endswith("18:00:00+08:00")
    assert not assess(record, cfg, receipt).blockers


def test_historical_close_cutoff_is_not_rolled_forward():
    record, cfg, _ = specimen(grade="HISTORICAL_METADATA_ONLY")
    record["binding"]["decision_cutoff"] = "2026-11-24T15:00:00+08:00"
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert result.disposition == "INPUT_OR_CLOCK_BLOCKED"
    assert "EVENT_AFTER_CUTOFF" in result.blockers


def test_next_day_observation_is_not_a_T_signal():
    record, cfg, receipt = specimen()
    record["binding"]["observation_end"] = "2026-11-25T15:00:00+08:00"
    seal(record)
    with pytest.raises(SidecarError, match="OBSERVATION_WINDOW_MISMATCH"):
        assess(record, cfg, receipt)


def test_other_day_unknown_visibility_does_not_poison_requested_day():
    old, cfg, receipt = specimen()
    tomorrow = copy.deepcopy(old)
    tomorrow["binding"].update(
        market_date="2026-11-25",
        decision_cutoff="2026-11-25T22:30:00+08:00",
        delivery_id="fixture-next-day",
    )
    tomorrow["publication"]["visible_at"] = None
    seal(tomorrow)
    result = select([old, tomorrow], cfg, {old["record_id"]: receipt})
    assert result.record_id == old["record_id"]


def test_delivery_id_cannot_be_rebound_on_other_day(tmp_path):
    old, cfg, receipt = specimen()
    store = SidecarStore(tmp_path / "metadata", cfg, protected_roots=[])
    store.append(old, receipt)
    new = copy.deepcopy(old)
    new["binding"].update(market_date="2026-11-25", decision_cutoff="2026-11-25T22:30:00+08:00")
    seal(new)
    with pytest.raises(SidecarError, match="DELIVERY_ID_REBOUND"):
        store.append(new, verification(new, cfg))


def test_partial_clock_still_rejects_known_impossible_causality():
    record, cfg, receipt = specimen()
    record["members"][0]["available_at"] = None
    record["publication"]["published_at"] = clock("18:01:00", "publication_commit", "published")
    seal(record)
    with pytest.raises(SidecarError, match="MEMBER_CAUSALITY"):
        assess(record, cfg, receipt)


def test_policy_identity_cannot_be_null_in_constructor_or_parser():
    with pytest.raises(SidecarError):
        replace(config(), policy_sha256=None)
    raw = config().to_dict()
    raw["policy_sha256"] = None
    with pytest.raises(SidecarError):
        ConsumerConfig.from_dict(raw)


def test_honest_pending_null_m0_is_reported_as_closure_blocker():
    cfg = config(pending=True)
    record, _, receipt = specimen(cfg)
    record["binding"]["m0_ref"] = None
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert "M0_NOT_BOUND" in result.blockers
    assert "CONSUMER_CLOSURE_PENDING" in result.blockers


def test_exact_retry_cannot_replace_original_verification_time(tmp_path):
    record, cfg, _ = specimen()
    for member in record["members"]:
        member["validated_at"] = None
    seal(record)
    receipt = verification(record, cfg)
    store = SidecarStore(tmp_path / "strict-retry", cfg, protected_roots=[])
    store.append(record, receipt)
    changed = replace(receipt, start="2026-11-24T18:31:00+08:00", end="2026-11-24T18:31:00+08:00")
    with pytest.raises(SidecarError, match="IMMUTABLE_VERIFICATION_CONFLICT"):
        store.append(record, changed)


def test_orphan_pending_file_does_not_consume_committed_file_cap(tmp_path):
    cfg = replace(config(), resource_limits=ResourceLimits("single-commit", max_files=1))
    record, _, receipt = specimen(cfg)
    store = SidecarStore(tmp_path / "pending-orphan", cfg, protected_roots=[])
    store.read()
    (store.root / "records" / ".pending-crash").write_bytes(b"incomplete")
    assert store.append(record, receipt)
    assert not store.append(record, receipt)
    assert store.read() == [record]


def test_selection_retains_unconfigured_resource_boundary_notes():
    record, cfg, receipt = specimen()
    result = select_as_of(
        [record],
        cfg,
        {record["record_id"]: receipt},
        market_date="2026-11-24",
        cutoff="2026-11-24T22:30:00+08:00",
    )
    assert "UNCONFIGURED_RESOURCE_BOUND:max_members" in result.boundary_notes


@pytest.mark.parametrize("raw", [None, [], True, 1, "string"])
def test_nonmapping_consumer_config_has_stable_error(raw):
    with pytest.raises(SidecarError, match="INVALID_CONSUMER_CONFIG"):
        ConsumerConfig.from_dict(raw)


def test_external_content_addressed_m0_is_independent_from_config_digest():
    cfg = replace(config(), m0_ref=sha("9"))
    assert cfg.m0_ref != cfg.identity and cfg.binding_complete
    assert cfg.to_dict()["schema"].endswith(".v1.2")
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg
    record, _, receipt = specimen(cfg)
    assert record["binding"]["m0_ref"] == sha("9")
    assert not assess(record, cfg, receipt).blockers


def test_complete_config_with_pending_record_stays_blocked():
    record, cfg, _ = specimen()
    record["binding"]["m0_ref"] = None
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert "M0_NOT_BOUND" in result.blockers and "CONSUMER_CLOSURE_PENDING" in result.blockers


def test_config_digest_alias_cannot_qualify_unconfigured_m0():
    cfg = replace(config(), m0_ref=None)
    record, _, _ = specimen(cfg)
    record["binding"]["m0_ref"] = cfg.identity
    seal(record)
    result = assess(record, cfg, verification(record, cfg))
    assert not cfg.binding_complete
    assert "M0_REFERENCE_UNCONFIGURED" in result.blockers


def test_wrong_external_m0_is_rejected_even_with_new_verification():
    record, cfg, _ = specimen()
    record["binding"]["m0_ref"] = sha("9")
    seal(record)
    with pytest.raises(SidecarError, match="M0_REFERENCE_MISMATCH"):
        assess(record, cfg, verification(record, cfg))


def test_policy_change_invalidates_verification_without_changing_m0():
    record, cfg, receipt = specimen()
    updated = replace(cfg, policy_sha256=sha("9"))
    assert updated.m0_ref == cfg.m0_ref and updated.identity != cfg.identity
    with pytest.raises(SidecarError, match="VERIFICATION_IDENTITY_MISMATCH"):
        assess(record, updated, receipt)


def test_legacy_config_without_m0_remains_parseable_pending_and_byte_compatible():
    cfg = replace(config(), m0_ref=None)
    raw = cfg.to_dict()
    assert raw["schema"].endswith(".v1") and "m0_ref" not in raw
    assert ConsumerConfig.from_dict(raw).to_dict() == raw
    assert not ConsumerConfig.from_dict(raw).binding_complete
    wrong = {**raw, "schema": "quanthus.p10.daily-consumer-config.v1.2", "m0_ref": None}
    with pytest.raises(SidecarError):
        ConsumerConfig.from_dict(wrong)


def test_selection_without_visible_records_still_retains_resource_notes():
    result = select_as_of(
        [], config(), {}, market_date="2026-11-24", cutoff="2026-11-24T22:30:00+08:00"
    )
    assert "NO_VISIBLE_VERSION" in result.blockers
    assert "UNCONFIGURED_RESOURCE_BOUND:max_members" in result.boundary_notes


def test_bounded_session_config_roundtrip_and_store_share_full_identity(tmp_path):
    record, cfg, _ = session_specimen()
    cfg = replace(
        cfg,
        resource_limits=ResourceLimits(
            "session-day1-test-v1", max_members=2, max_record_bytes=262144
        ),
    )
    record["binding"]["m0_ref"] = cfg.m0_ref
    record, cfg, receipt = reseal_session(record, cfg)
    assert cfg.to_dict()["schema"].endswith(".v2.2")
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg
    store = SidecarStore(tmp_path / "bounded-session", cfg, protected_roots=[])
    assert store.append(record, receipt)
    assert not assess(record, cfg, receipt).blockers


def test_whole_session_withdrawal_cannot_change_applicability_identity():
    record, cfg, _ = session_specimen()
    new = withdraw(record)
    session_meta(new)["applicability"]["rule_sha256"] = sha("5")
    new, cfg, receipt = reseal_session(new, cfg)
    with pytest.raises(SidecarError, match="WITHDRAWAL_INVENTORY_MISMATCH"):
        assess(new, cfg, receipt, previous=record)


def session_specimen(*, price_only=False):
    req = MeasureRequirement(
        "bars",
        ("close",) if price_only else ("close", "turnover", "volume"),
        "NOT_REQUESTED" if price_only else "FULL_DAY",
    )
    cfg = replace(config(), config_id="test-session-v2", measure_requirements=(req,))
    record, _, _ = specimen(cfg)
    record["schema"] = SESSION_SCHEMA
    for member in record["members"]:
        member["session"] = None
    record["members"][0]["session"] = {
        "session_scope": "REGULAR_AND_APPLICABLE_AFTER_HOURS",
        "volume_basis": "FULL_DAY",
        "official_price_close_anchor": "2026-11-24T15:00:00+08:00",
        "full_day_volume_amount_observation_end": "2026-11-24T15:30:00+08:00",
        "vendor_scope_evidence": {"uri": "fixture:vendor-definition", "sha256": sha("9")},
        "applicability": {
            "venue_mic": "XSHG",
            "board": "SSE_main",
            "security_scope_sha256": sha("b"),
            "effective_start": "2026-07-06",
            "effective_end": None,
            "after_hours": "APPLIES",
            "calendar_sha256": sha("7"),
            "rule_sha256": sha("8"),
            "evidence": {"uri": "fixture:scoped-rule", "sha256": sha("6")},
        },
    }
    return reseal_session(record, cfg)


def reseal_session(record, cfg):
    record["publication"]["covered_inventory_sha256"] = inventory_digest(record["members"])
    return seal(record), cfg, verification(record, cfg)


def session_meta(record):
    return record["members"][0]["session"]


def test_session_v2_accepts_explicit_complete_volume_and_keeps_v1_shape():
    record, cfg, receipt = session_specimen()
    assert assess(record, cfg, receipt).disposition == "TEST_ONLY"
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg
    assert "measure_requirements" not in config().to_dict()
    assert config().to_dict()["schema"].endswith(".v1.2")
    assert replace(config(), m0_ref=None).to_dict()["schema"].endswith(".v1")


@pytest.mark.parametrize(
    "field", ["full_day_volume_amount_observation_end", "applicability", "vendor_scope_evidence"]
)
def test_missing_quantity_metadata_stays_unknown(field):
    record, cfg, _ = session_specimen()
    session_meta(record)[field] = None
    record, cfg, receipt = reseal_session(record, cfg)
    result = assess(record, cfg, receipt)
    assert "HARD_MEMBER_BLOCKED:bars" in result.blockers
    assert any("UNKNOWN" in reason for _, reasons in result.member_blockers for reason in reasons)


@pytest.mark.parametrize(
    "basis,scope",
    [
        ("UNKNOWN", "UNKNOWN"),
        ("REGULAR_ONLY", "REGULAR_ONLY"),
        ("INDEPENDENT_AFTER_HOURS", "AFTER_HOURS_ONLY"),
    ],
)
def test_non_full_day_quantity_cannot_satisfy_full_day_config(basis, scope):
    record, cfg, _ = session_specimen()
    session_meta(record).update(volume_basis=basis, session_scope=scope)
    record, cfg, receipt = reseal_session(record, cfg)
    result = assess(record, cfg, receipt)
    assert result.disposition == "TEST_ONLY"
    assert "VOLUME_BASIS_NOT_FULL_DAY" in dict(result.member_blockers)["bars"]


def test_full_day_volume_claim_at_price_close_is_incomplete():
    record, cfg, _ = session_specimen()
    session_meta(record)["full_day_volume_amount_observation_end"] = "2026-11-24T15:00:00+08:00"
    record, cfg, receipt = reseal_session(record, cfg)
    assert (
        "FULL_DAY_VOLUME_WINDOW_INCOMPLETE"
        in dict(assess(record, cfg, receipt).member_blockers)["bars"]
    )


def test_source_clock_must_follow_full_volume_observation():
    record, cfg, _ = session_specimen()
    record["members"][0]["source_available_at"] = clock(
        "15:00:00", "source_artifact_known", "source"
    )
    record, cfg, receipt = reseal_session(record, cfg)
    assert (
        "SOURCE_PRECEDES_VOLUME_COMPLETENESS"
        in dict(assess(record, cfg, receipt).member_blockers)["bars"]
    )


def test_full_volume_publication_and_visibility_cannot_precede_completion():
    record, cfg, _ = session_specimen()
    for member in record["members"]:
        member["source_available_at"] = clock("15:00:00", "source_artifact_known", "source")
        member["validated_at"] = clock("15:01:00", "validation_complete", "verified")
        member["available_at"] = clock("15:03:00", "consumer_channel_visible", "members-visible")
    record["publication"].update(
        published_at=clock("15:02:00", "publication_commit", "published"),
        visible_at=clock("15:04:00", "consumer_channel_visible", "event-visible"),
    )
    record, cfg, receipt = reseal_session(record, cfg)
    receipt = replace(receipt, start="2026-11-24T15:01:00+08:00", end="2026-11-24T15:01:00+08:00")
    assert {
        "PUBLICATION_PRECEDES_VOLUME_COMPLETENESS",
        "AVAILABLE_PRECEDES_VOLUME_COMPLETENESS",
    } <= set(dict(assess(record, cfg, receipt).member_blockers)["bars"])


def test_early_calendar_and_price_only_use_are_independent_of_quantity_floor():
    record, cfg, _ = session_specimen(price_only=True)
    session_meta(record).update(
        session_scope="UNKNOWN",
        volume_basis="UNKNOWN",
        applicability=None,
        full_day_volume_amount_observation_end=None,
        vendor_scope_evidence=None,
    )
    record["members"][1]["source_available_at"] = clock(
        "08:00:00", "source_artifact_known", "source"
    )
    record, cfg, receipt = reseal_session(record, cfg)
    assert not assess(record, cfg, receipt).blockers


def test_not_applicable_needs_named_scope_evidence_and_explicit_volume_end():
    record, cfg, _ = session_specimen()
    session_meta(record)["applicability"]["after_hours"] = "NOT_APPLICABLE"
    session_meta(record)["full_day_volume_amount_observation_end"] = "2026-11-24T15:00:00+08:00"
    record, cfg, receipt = reseal_session(record, cfg)
    assert not assess(record, cfg, receipt).blockers
    session_meta(record)["applicability"]["evidence"] = None
    record, cfg, receipt = reseal_session(record, cfg)
    with pytest.raises(SidecarError, match="INVALID_SESSION_EVIDENCE"):
        assess(record, cfg, receipt)


def test_session_scope_changes_invalidate_verified_input_and_inventory_identity():
    record, cfg, old_receipt = session_specimen()
    old_inventory = record["publication"]["covered_inventory_sha256"]
    session_meta(record)["applicability"]["rule_sha256"] = sha("5")
    record, cfg, _ = reseal_session(record, cfg)
    assert old_inventory != record["publication"]["covered_inventory_sha256"]
    with pytest.raises(SidecarError, match="VERIFICATION_IDENTITY_MISMATCH"):
        assess(record, cfg, old_receipt)


def test_publisher_cannot_downgrade_v2_consumer_to_v1():
    record, cfg, receipt = session_specimen()
    record["schema"] = SCHEMA
    seal(record)
    with pytest.raises(SidecarError, match="SESSION_CONFIG_VERSION_MISMATCH"):
        assess(record, cfg, receipt)


def test_session_v2_append_retry_and_asof_reuse_the_same_store(tmp_path):
    record, cfg, receipt = session_specimen()
    store = SidecarStore(tmp_path / "v2-store", cfg, protected_roots=[tmp_path / "sources"])
    assert store.append(record, receipt)
    assert not store.append(record, receipt)
    assert (
        select(store.read(), cfg, {record["record_id"]: receipt}).record_id == record["record_id"]
    )


def test_exact_volume_end_is_accepted_without_an_invented_latency_buffer():
    record, cfg, _ = session_specimen()
    for member in record["members"]:
        member["source_available_at"] = clock("15:30:00", "source_artifact_known", "source")
        member["validated_at"] = clock("15:30:00", "validation_complete", "verified")
        member["available_at"] = clock("15:30:00", "consumer_channel_visible", "members-visible")
    record["publication"].update(
        published_at=clock("15:30:00", "publication_commit", "published"),
        visible_at=clock("15:30:00", "consumer_channel_visible", "event-visible"),
    )
    record, cfg, receipt = reseal_session(record, cfg)
    receipt = replace(receipt, start="2026-11-24T15:30:00+08:00", end="2026-11-24T15:30:00+08:00")
    assert not assess(record, cfg, receipt).blockers


def test_pre_expansion_day_requires_explicit_applicability_instead_of_global_date_rule():
    record, cfg, _ = session_specimen()
    record = json.loads(json.dumps(record).replace("2026-11-24", "2026-07-03"))
    meta = session_meta(record)
    meta["full_day_volume_amount_observation_end"] = "2026-07-03T15:00:00+08:00"
    meta["applicability"].update(
        effective_start="1900-01-01", effective_end="2026-07-05", after_hours="NOT_APPLICABLE"
    )
    record, cfg, receipt = reseal_session(record, cfg)
    receipt = replace(receipt, start="2026-07-03T18:30:00+08:00", end="2026-07-03T18:30:00+08:00")
    assert not assess(record, cfg, receipt).blockers
    meta["applicability"] = None
    record, cfg, receipt = reseal_session(record, cfg)
    receipt = replace(receipt, start="2026-07-03T18:30:00+08:00", end="2026-07-03T18:30:00+08:00")
    assert (
        "AFTER_HOURS_APPLICABILITY_UNKNOWN"
        in dict(assess(record, cfg, receipt).member_blockers)["bars"]
    )


def test_named_applicability_outside_effective_scope_stays_blocked():
    record, cfg, _ = session_specimen()
    session_meta(record)["applicability"]["effective_end"] = "2026-11-23"
    record, cfg, receipt = reseal_session(record, cfg)
    assert (
        "SESSION_EFFECTIVE_SCOPE_MISMATCH"
        in dict(assess(record, cfg, receipt).member_blockers)["bars"]
    )


def test_required_volume_member_cannot_hide_its_session_annotation():
    record, cfg, _ = session_specimen()
    record["members"][0]["session"] = None
    record, cfg, receipt = reseal_session(record, cfg)
    assert "SESSION_METADATA_UNKNOWN" in dict(assess(record, cfg, receipt).member_blockers)["bars"]


@pytest.mark.parametrize(
    "board,day,expected",
    [
        ("SSE_main", "2026-07-03", "5.0"),
        ("SSE_main", "2026-07-06", "10.0"),
        ("SZ_main", "2026-07-03", "5.0"),
        ("SZ_main", "2026-07-06", "10.0"),
        ("ChiNext", "2026-07-03", "20.0"),
        ("ChiNext", "2026-07-06", "20.0"),
        ("STAR", "2026-07-03", "20.0"),
        ("STAR", "2026-07-06", "20.0"),
    ],
)
def test_existing_price_limit_v2_scopes_main_st_change_without_rewriting_other_boards(
    board, day, expected
):
    from decimal import Decimal

    from quanthus.price_limits import resolve_price_limit_policy

    policy = json.loads(
        (Path(__file__).resolve().parents[2] / "config/price-limit.v2.json").read_text()
    )
    decision = resolve_price_limit_policy(
        board=board,
        market_date=day,
        status_known=True,
        is_st_class=True,
        listing_window_rule=None,
        eligible_session_index=200,
        ipo_window_sessions=5,
        observed_tradestatus="1",
        prior_trading_close=Decimal("10"),
        policy=policy,
    )
    assert decision.upper_limit_pct == Decimal(expected)
    unknown = resolve_price_limit_policy(
        board=board,
        market_date=day,
        status_known=False,
        is_st_class=True,
        listing_window_rule=None,
        eligible_session_index=200,
        ipo_window_sessions=5,
        observed_tradestatus="1",
        prior_trading_close=Decimal("10"),
        policy=policy,
    )
    assert not unknown.entry_policy_allows


def test_session_external_m0_and_honest_pending_keep_full_member_identity():
    record, cfg, _ = session_specimen()
    cfg = replace(cfg, m0_ref=sha("8"))
    record["binding"]["m0_ref"] = cfg.m0_ref
    record, cfg, receipt = reseal_session(record, cfg)
    assert cfg.to_dict()["schema"].endswith(".v2.2")
    assert ConsumerConfig.from_dict(cfg.to_dict()) == cfg
    assert not assess(record, cfg, receipt).blockers
    record["binding"]["m0_ref"] = None
    record, cfg, receipt = reseal_session(record, cfg)
    assert "M0_NOT_BOUND" in assess(record, cfg, receipt).blockers


def test_legacy_bounded_session_without_m0_keeps_v21_wire_but_stays_pending():
    _, cfg, _ = session_specimen()
    cfg = replace(cfg, m0_ref=None, resource_limits=ResourceLimits("legacy-session", max_members=2))
    raw = cfg.to_dict()
    assert raw["schema"].endswith(".v2.1") and "m0_ref" not in raw
    assert ConsumerConfig.from_dict(raw).to_dict() == raw
    assert not cfg.binding_complete
