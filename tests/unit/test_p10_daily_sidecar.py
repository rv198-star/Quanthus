from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.p10_daily_sidecar import (
    SCHEMA,
    ConsumerConfig,
    InputVerification,
    SidecarError,
    SidecarStore,
    assess,
    input_identity,
    inventory_digest,
    record_id,
    select_as_of,
)


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


def specimen(cfg=None, *, grade="SOURCE_OBSERVED"):
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
            "m0_ref": cfg.identity,
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
    assert result.disposition == "CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW"
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
    record["binding"]["m0_ref"] = cfg.identity
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
    record, cfg, receipt = specimen()
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
    assert assess(record, cfg, receipt).disposition == "CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW"


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
    record, cfg, _ = specimen()
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
