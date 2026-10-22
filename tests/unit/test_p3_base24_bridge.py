from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.contracts import OpenExecutionState
from quanthus.backtest.inputs import market_session_from_p1
from quanthus.contracts import SnapshotRef
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy import p3_base24_bridge as bridge
from quanthus.expert_strategy import p3_base24_native as native

ROOT = Path(__file__).resolve().parents[2]
SHA = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
TZ = native.TZ
DAY = date(2025, 2, 20)


def clock(day=DAY, hour=15):
    return datetime.combine(day, datetime.min.time().replace(hour=hour), TZ)


def row(day=DAY):
    return {
        "security_id": "security-native",
        "company_id": "company-native",
        "listing_id": "listing-native",
        "listing_code": "000001",
        "venue_id": "venue-native",
        "mic_code": "XSHE",
        "instrument_type": "A_SHARE",
        "trade_date": day,
        "calendar_session_id": "session-" + day.isoformat(),
        "observation_start": clock(day, 0),
        "observation_end": clock(day),
        "open_at": clock(day, 9),
        "close_at": clock(day),
        "interval_size": "1d",
        "price_basis": "none",
        "open": Decimal("10.0000000001"),
        "high": Decimal("11.0000000001"),
        "low": Decimal("9.0000000001"),
        "close": Decimal("10.5000000001"),
        "volume": Decimal("20.0000000000"),
        "turnover": Decimal("100.0000000000"),
        "bar_knowledge_from": clock(day),
        "knowledge_to": None,
        "listing_knowledge_from": clock(day),
        "listing_knowledge_to": None,
        "security_knowledge_from": clock(day),
        "security_knowledge_to": None,
        "calendar_knowledge_from": clock(day),
        "calendar_knowledge_to": None,
        "venue_knowledge_from": clock(day),
        "venue_knowledge_to": None,
        "state_knowledge_from": clock(day),
        "state_knowledge_to": None,
        "is_tradable": True,
        "can_buy": True,
        "can_sell": True,
    }


def support(r):
    t = clock(r["trade_date"])
    return bridge.DailySupport(
        SHA,
        bridge.row_digest(r),
        OTHER,
        r["listing_id"],
        r["trade_date"],
        listing_sessions=200,
        listing_sessions_known_at=t,
        adjustment_effective=False,
        adjustment_known_at=t,
        execution_known_at=t,
        entry_policy_allows=True,
        is_executable=True,
        upper_limit_price=Decimal("12.0000000000"),
        lower_limit_price=Decimal("8.0000000000"),
    )


def port(r=None, *, qualified=True, cutoff=None, **kwargs):
    r = row() if r is None else r
    return bridge.decision_port(
        r,
        cutoff=cutoff or clock(r["trade_date"]),
        source_manifest_sha256=SHA,
        support=support(r) if qualified else None,
        static_industry_id="static-L1",
        **kwargs,
    )


def feature(day=DAY):
    d = Decimal("1.0000000000")
    return BaselineFeatureRow(
        day,
        "security-native",
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        d,
        listing_sessions=200,
        feature_ready=True,
        entry_eligible=True,
    )


def window():
    days = tuple(DAY - timedelta(days=21 - i) for i in range(22))
    # Deliberately synthetic sessions; no real exchange membership is inferred.
    return days, tuple(port(row(d), cutoff=clock()) for d in days)


def mask(ports=None, *, f=None, known=None):
    days, default = window()
    return bridge.common_entry_mask(
        default if ports is None else ports,
        expected_dates=days,
        card=load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
        ),
        feature=feature() if f is None else f,
        feature_known_at=clock() if known is None else known,
        feature_evidence_digest=SHA,
    )


def test_native_ports_preserve_decimal_scale_and_identity_without_changing_input():
    r = row()
    before = bridge.row_digest(r)
    p = port(r)
    assert p.candidate is not None
    assert p.candidate.security_id == p.bar.security_code == r["security_id"]
    assert p.candidate.venue_id == "SZSE"
    assert p.candidate.instrument_type == "common_equity"
    assert p.candidate.static_level1_industry_id == "static-L1"
    assert p.candidate.close_at == r["close_at"]
    for src, dest in (
        ("open", "open_price"),
        ("high", "high_price"),
        ("low", "low_price"),
        ("close", "close_price"),
        ("turnover", "amount"),
    ):
        assert getattr(p.bar, dest).as_tuple() == r[src].as_tuple()
    assert p.bar.is_executable and p.bar.entry_policy_allows
    assert bridge.row_digest(r) == before


@pytest.mark.parametrize("key", bridge._CLOCKS)
@pytest.mark.parametrize("value", [None, clock() + timedelta(microseconds=1)])
def test_unknown_or_late_required_native_clock_is_never_projected(key, value):
    r = row()
    r[key] = value
    with pytest.raises(native.InputError, match="BRIDGE_NOT_VISIBLE"):
        port(r)


@pytest.mark.parametrize(
    "key",
    [
        "knowledge_to",
        "listing_knowledge_to",
        "security_knowledge_to",
        "calendar_knowledge_to",
        "venue_knowledge_to",
    ],
)
def test_native_half_open_expiry_is_preserved(key):
    r = row()
    r[key] = clock()
    with pytest.raises(native.InputError, match="BRIDGE_NOT_VISIBLE"):
        port(r)


def test_unknown_support_does_not_make_native_flags_p2_authority():
    p = port(qualified=False)
    assert p.candidate is not None
    assert p.candidate.listing_sessions is None and p.candidate.adjustment_effective is None
    assert not p.bar.is_executable and not p.bar.entry_policy_allows
    assert "LISTING_SESSION_UNAVAILABLE" in p.unavailable_reasons
    assert "ADJUSTMENT_BOUNDARY_UNAVAILABLE" in p.unavailable_reasons
    assert "EXECUTION_QUALIFICATION_UNAVAILABLE" in p.unavailable_reasons


@pytest.mark.parametrize("field", ["state_knowledge_from", "state_knowledge_to"])
def test_unavailable_or_expired_state_is_not_a_suspension_or_new_entry(field):
    r = row()
    r[field] = None if field.endswith("from") else clock()
    p = port(r)
    assert p.bar.coverage_status.value == "missing"
    assert not p.bar.is_executable and not p.bar.entry_policy_allows
    assert p.candidate is not None and p.candidate.is_tradable is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_manifest_sha256", OTHER),
        ("row_sha256", SHA),
        ("listing_id", "other"),
        ("market_date", DAY - timedelta(days=1)),
    ],
)
def test_support_cannot_be_reused_for_another_source_key_or_version(field, value):
    r = row()
    with pytest.raises(native.InputError, match="BRIDGE_SUPPORT_BINDING"):
        bridge.decision_port(
            r,
            cutoff=clock(),
            source_manifest_sha256=SHA,
            support=replace(support(r), **{field: value}),
            static_industry_id=None,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("close", 10.5),
        ("interval_size", "5m"),
        ("price_basis", "QFQ"),
        ("instrument_type", "FUND"),
        ("is_tradable", "true"),
        ("trade_date", DAY - timedelta(days=1)),
    ],
)
def test_invalid_native_contract_fails_closed(field, value):
    r = row()
    r[field] = value
    with pytest.raises(native.InputError):
        port(r)


def test_one_complete_case_mask_is_shared_by_all_four_variants():
    m = mask()
    assert m.reasons == ()
    assert {m.for_variant(v) for v in bridge.VARIANTS} == {True}
    with pytest.raises(native.InputError, match="BRIDGE_VARIANT"):
        m.for_variant("other")


@pytest.mark.parametrize("field", bridge.REQUIRED_FEATURES)
def test_either_experts_missing_feature_blocks_every_variant_including_baseline(field):
    m = mask(f=replace(feature(), **{field: None}))
    assert "FEATURE_MISSING:" + field in m.reasons
    assert {m.for_variant(v) for v in bridge.VARIANTS} == {False}


def test_late_feature_proof_cannot_enter_the_common_mask():
    m = mask(known=clock() + timedelta(microseconds=1))
    assert "FEATURE_CLOCK_UNAVAILABLE" in m.reasons


def test_unknown_adjustment_boundary_is_not_no_action():
    days, ports = window()
    r = row()
    unknown = bridge.decision_port(
        r,
        cutoff=clock(),
        source_manifest_sha256=SHA,
        support=replace(support(r), adjustment_known_at=None),
        static_industry_id="static-L1",
    )
    m = mask((*ports[:-1], unknown))
    assert "corporate_action_boundary" in m.reasons


def test_mask_rejects_mixed_security_features_and_mixed_source_snapshots():
    with pytest.raises(native.InputError, match="BRIDGE_FEATURE_KEY"):
        mask(f=replace(feature(), security_id="other"))
    days, ports = window()
    with pytest.raises(native.InputError, match="BRIDGE_MIXED_SOURCE"):
        mask((replace(ports[0], source_manifest_sha256=OTHER), *ports[1:]))


def test_missing_historical_execution_support_does_not_block_candidate_price_history():
    days, ports = window()
    r = row(days[0])
    old = bridge.decision_port(
        r,
        cutoff=clock(),
        source_manifest_sha256=SHA,
        support=replace(support(r), execution_known_at=None),
        static_industry_id="static-L1",
    )
    assert not old.bar.is_executable
    assert mask((old, *ports[1:])).eligible


@pytest.mark.parametrize("index", [0, 20, 21])
def test_only_current_listing_age_is_required_without_filling_unknown_history(index):
    days, ports = window()
    r = row(days[index])
    unknown = bridge.decision_port(
        r,
        cutoff=clock(),
        source_manifest_sha256=SHA,
        support=replace(support(r), listing_sessions=None, listing_sessions_known_at=None),
        static_industry_id="static-L1",
    )
    assert unknown.candidate is not None
    assert unknown.candidate.listing_sessions is None
    m = mask((*ports[:index], unknown, *ports[index + 1 :]))
    if index == 21:
        assert "missing_input" in m.reasons
        assert not any(m.for_variant(v) for v in bridge.VARIANTS)
    else:
        assert m.eligible


@pytest.mark.parametrize("industry", [None, "", " \t ", 42])
def test_invalid_static_industry_blocks_all_variants_locally(industry):
    days, ports = window()
    r = row()
    unknown = bridge.decision_port(
        r,
        cutoff=clock(),
        source_manifest_sha256=SHA,
        support=support(r),
        static_industry_id=industry,
    )
    m = mask((*ports[:-1], unknown))
    assert "static_industry_missing" in m.reasons
    assert not any(m.for_variant(v) for v in bridge.VARIANTS)
    assert mask().eligible


def test_p2_adapter_uses_existing_accepted_open_state_mapping():
    p = port(execution_only=True)
    assert p.candidate is None  # execution observations never become decision candidates
    e = bridge.execution_evidence(
        p,
        snapshot=SnapshotRef(DAY, clock(), SHA, "synthetic-input"),
        open_at=clock(hour=9),
        close_at=clock(),
        decision_date=DAY - timedelta(days=1),
        next_session_date=DAY,
    )
    session = market_session_from_p1(e)
    assert session.open_execution_state is OpenExecutionState.EXECUTABLE
    assert session.open_price.as_tuple() == row()["open"].as_tuple()


def test_p2_unknown_st_ipo_action_authority_is_missing_evidence_not_sell():
    p = port(qualified=False, execution_only=True)
    e = bridge.execution_evidence(
        p,
        snapshot=SnapshotRef(DAY, clock(), SHA, "synthetic-input"),
        open_at=clock(hour=9),
        close_at=clock(),
        decision_date=DAY - timedelta(days=1),
        next_session_date=DAY,
    )
    session = market_session_from_p1(e)
    assert session.open_execution_state is OpenExecutionState.MISSING_EVIDENCE
    assert session.open_price is None


@pytest.mark.parametrize("change", ["source", "open", "date", "knowledge"])
def test_p2_snapshot_and_session_cannot_be_substituted(change):
    p = port(execution_only=True)
    kw = dict(
        snapshot=SnapshotRef(DAY, clock(), SHA, "synthetic-input"),
        open_at=clock(hour=9),
        close_at=clock(),
        decision_date=DAY - timedelta(days=1),
        next_session_date=DAY,
    )
    if change == "source":
        kw["snapshot"] = SnapshotRef(DAY, clock(), OTHER, "synthetic-input")
    if change == "open":
        kw["open_at"] = clock(hour=10)
    if change == "date":
        kw["next_session_date"] = DAY + timedelta(days=1)
    if change == "knowledge":
        kw["snapshot"] = SnapshotRef(DAY, clock(hour=14), SHA, "synthetic-input")
    with pytest.raises(native.InputError, match="BRIDGE_EXECUTION_SCOPE"):
        bridge.execution_evidence(p, **kw)


def test_zero_and_null_values_are_preserved_while_nontradable_is_not_missing():
    r = row()
    r["volume"] = Decimal("0.0000000000")
    r["turnover"] = None
    r["is_tradable"] = False
    p = port(r)
    assert p.bar.volume.as_tuple() == r["volume"].as_tuple()
    assert p.bar.amount is None and not p.bar.is_executable
    assert p.bar.coverage_status.value == "suspended"


def test_warmup_or_post_window_rows_need_exact_authority():
    with pytest.raises(native.InputError, match="WARMUP_CALENDAR_AUTHORITY_REQUIRED"):
        port(row(date(2024, 12, 31)), cutoff=clock())
    with pytest.raises(native.InputError, match="BRIDGE_FROZEN_CUTOFF"):
        port(row(date(2026, 9, 18)))


def test_bridge_identity_keeps_scale_and_unknown_separate():
    assert bridge.row_digest({"v": Decimal("1.0")}) != bridge.row_digest({"v": Decimal("1.00")})
    assert bridge.row_digest({"v": None}) != bridge.row_digest({"v": Decimal("0")})


def test_domain_port_contract_preserves_frozen_rule_and_no_admission():
    c = json.loads((ROOT / "config/research/p3-strat-003/base24-domain-ports.v1.json").read_text())
    assert c["schema_version"] == bridge.VERSION
    assert c["request_digest"] == native.REQUEST
    assert c["candidate_rule_digest"] == bridge.RULE
    assert c["common_mask"]["required_feature_fields"] == list(bridge.REQUIRED_FEATURES)
    assert c["common_mask"]["variants"] == list(bridge.VARIANTS)
    assert c["historical_clock_override"] is False
    assert c["B_accepted"] is c["D_activated"] is c["execution_authorized"] is False


def test_changed_p4_rules_cannot_be_hidden_behind_old_semantic_digest():
    days, ports = window()
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    altered = replace(card, eligibility=replace(card.eligibility, minimum_listing_sessions=1))
    with pytest.raises(native.InputError, match="BRIDGE_FROZEN_RULE"):
        bridge.common_entry_mask(
            ports,
            expected_dates=days,
            card=altered,
            feature=feature(),
            feature_known_at=clock(),
            feature_evidence_digest=SHA,
        )


@pytest.mark.parametrize("remove", [0, 10, 21, "all"])
def test_missing_native_observation_blocks_entry_without_stopping_other_keys(remove):
    days, ports = window()
    kept = () if remove == "all" else tuple(p for i, p in enumerate(ports) if i != remove)
    m = mask(kept)
    assert "observation_gap" in m.reasons
    assert not any(m.for_variant(v) for v in bridge.VARIANTS)
    assert mask().eligible  # Independent complete key still proceeds.


def test_decision_port_cannot_be_relabelled_as_t_plus_one_execution():
    p = port()
    with pytest.raises(native.InputError, match="BRIDGE_EXECUTION_SCOPE"):
        bridge.execution_evidence(
            p,
            snapshot=SnapshotRef(DAY, clock(), SHA, "synthetic-input"),
            open_at=clock(hour=9),
            close_at=clock(),
            decision_date=DAY - timedelta(days=1),
            next_session_date=DAY,
        )
