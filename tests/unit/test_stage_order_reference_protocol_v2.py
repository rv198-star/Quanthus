from __future__ import annotations

import copy

import pytest

from quanthus.stage_learning import order_reference_protocol_v2 as api
from quanthus.structure_directional_path_geometry import (
    RANGE_TRAVERSAL,
    build_directional_path_geometry_facts,
)
from quanthus.structure_v2_inputs import admit_daily, digest

from .test_stage_order_reference_protocol import make_evidence
from .test_structure_trend_evolution import sequence
from .test_structure_v2_inputs import policy


def packet_fixture():
    rows = sequence(80)
    evidence = make_evidence(rows)
    case = evidence["cases"][0]
    history = [evidence["source_rows"][ref] for ref in case["history_refs"]]
    admissions = tuple(admit_daily(item, policy()) for item in rows)
    facts = build_directional_path_geometry_facts(
        admissions,
        report_start=case_day(case),
        report_end=case_day(case),
    )
    measurements = [item for item in facts.measurements if item.market_date == case_day(case)]
    return case, api.build_primary_packet(case, history, measurements)


def case_day(case):
    from datetime import date

    return date.fromisoformat(case["market_date"])


def primary_raw(packet, role, primary):
    support = []
    if "TRUE" in primary.values():
        support = [packet["directional_path_facts"][RANGE_TRAVERSAL]["revision_id"]]
    unknowns = ["primary evidence remains insufficient"] if "UNKNOWN" in primary.values() else []
    return {
        "schema_version": api.PRIMARY_OPINION_SCHEMA,
        "case_id": packet["case_id"],
        "protocol_digest": api.PROTOCOL_DIGEST,
        "packet_digest": packet["packet_digest"],
        "reviewer_id": f"reviewer-{role}",
        "role": role,
        "provenance": {
            "kind": "self_simulated",
            "origin_id": "test-origin",
            "session_id": f"session-{role}",
        },
        "primary": primary,
        "support_refs": support,
        "contradiction_refs": [],
        "reason": "Synthetic protocol-v2 test opinion.",
        "unknowns": unknowns,
    }


def primary_commits(packet, primary_left, primary_right=None):
    right = primary_left if primary_right is None else primary_right
    return [
        api.commit_primary_opinion(packet, primary_raw(packet, "short_term", primary_left)),
        api.commit_primary_opinion(packet, primary_raw(packet, "fund", right)),
    ]


def recent_raw(packet, relation="CONTRADICTS"):
    refs = [
        item["revision_id"]
        for item in packet["recent_facts"].values()
        if item["revision_id"] is not None
    ]
    return {
        "schema_version": api.RECENT_MODIFIER_SCHEMA,
        "case_id": packet["case_id"],
        "protocol_digest": api.PROTOCOL_DIGEST,
        "packet_digest": packet["packet_digest"],
        "primary_opinion_digest": packet["primary_opinion_digest"],
        "reviewer_id": packet["reviewer_id"],
        "role": packet["role"],
        "recent": {"width": "EXPANDING", "center": "SHIFTING", "relation": relation},
        "support_refs": refs,
        "contradiction_refs": [],
        "reason": "Synthetic recent modifier.",
        "unknowns": [],
    }


def test_protocol_v2_binds_exact_fact_contracts_and_has_no_runtime_authority():
    protocol = api.load_protocol()
    assert digest(protocol) == api.PROTOCOL_DIGEST
    assert protocol["recent"]["may_modify_primary"] is False
    assert protocol["authority"]["runtime_job_submission_authorized"] is False
    assert protocol["authority"]["market_data_acquisition_authorized"] is False
    assert protocol["authority"]["parameter_search_authorized"] is False


def test_primary_packet_excludes_explicit_recent_facts_candidate_and_future_rows():
    _case, packet = packet_fixture()
    assert packet["primary_window_sessions"] == 20
    assert packet["explicit_10_session_derived_facts_visible"] is False
    assert packet["candidate_output_visible"] is False
    assert packet["future_rows_visible"] is False
    assert "recent_facts" not in packet
    assert len(packet["price_history"]) == 21
    assert set(packet["directional_path_facts"]) == {
        "structure.geometry.range-traversal",
        "structure.geometry.counter-direction-excursion-share",
    }


def test_recent_packet_requires_and_binds_exact_primary_commit():
    case, packet = packet_fixture()
    primary = {
        "structural_instability_20": "FALSE",
        "coherent_directional_relocation_20": "FALSE",
        "orderly_consolidation_20": "TRUE",
    }
    commit = primary_commits(packet, primary)[0]
    recent = api.build_recent_packet(case, commit)
    assert recent["primary_opinion_digest"] == commit["primary_opinion_digest"]
    assert recent["may_modify_primary"] is False
    broken = copy.deepcopy(commit)
    broken["primary_opinion_digest"] = "sha256:" + "0" * 64
    with pytest.raises(api.OrderReferenceV2Error, match="primary_commit_digest"):
        api.build_recent_packet(case, broken)


def test_primary_commit_cannot_reference_explicit_recent_fact():
    case, packet = packet_fixture()
    recent_ref = case["recent"]["width_change"]["revision_id"]
    raw = primary_raw(
        packet,
        "short_term",
        {
            "structural_instability_20": "FALSE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "TRUE",
        },
    )
    raw["support_refs"] = [recent_ref]
    with pytest.raises(api.OrderReferenceV2Error, match="outside_primary_packet"):
        api.commit_primary_opinion(packet, raw)


@pytest.mark.parametrize(
    "primary,status,reason",
    [
        (("TRUE", "FALSE", "FALSE"), "STAGE_0", "positive_direction_neutral_instability"),
        (("FALSE", "FALSE", "TRUE"), "STAGE_2", "positive_orderly_consolidation"),
        (("FALSE", "TRUE", "FALSE"), "NEITHER", "coherent_directional_relocation"),
        (("TRUE", "TRUE", "FALSE"), "NEITHER", "mixed_directional_instability"),
        (("FALSE", "FALSE", "FALSE"), "NEITHER", "no_positive_stage_0_or_stage_2"),
        (("TRUE", "UNKNOWN", "FALSE"), "AMBIGUOUS", "unknown_dependent"),
        (("TRUE", "FALSE", "TRUE"), "AMBIGUOUS_CONFLICT", "positive_order_conflicts"),
    ],
)
def test_atomic_primary_resolution_precedence(primary, status, reason):
    values = dict(zip(api.PRIMARY_PROPOSITIONS, primary, strict=True))
    resolved = api.project_atomic_primary(values)
    assert resolved["status"] == status
    assert any(reason in item for item in resolved["reason_codes"])


def test_cross_role_disagreement_stays_ambiguous_without_majority_or_adjudication():
    _case, packet = packet_fixture()
    stage0 = dict(zip(api.PRIMARY_PROPOSITIONS, ("TRUE", "FALSE", "FALSE"), strict=True))
    neither = dict(zip(api.PRIMARY_PROPOSITIONS, ("FALSE", "TRUE", "FALSE"), strict=True))
    resolved = api.resolve_primary_roles(primary_commits(packet, stage0, neither))
    assert resolved["primary_reference"] == "AMBIGUOUS"
    assert resolved["reason_codes"] == ["cross_role_primary_disagreement"]


def test_recent_contradiction_cannot_change_committed_stage2_primary():
    case, packet = packet_fixture()
    stage2 = dict(zip(api.PRIMARY_PROPOSITIONS, ("FALSE", "FALSE", "TRUE"), strict=True))
    primaries = primary_commits(packet, stage2)
    recents = []
    for commit in primaries:
        recent_packet = api.build_recent_packet(case, commit)
        recents.append(api.commit_recent_modifier(recent_packet, recent_raw(recent_packet)))
    resolved = api.resolve_case(primaries, recents)
    assert resolved["primary_reference"] == "STAGE_2"
    assert resolved["recent_can_modify_primary"] is False
    assert set(resolved["recent_relations"]) == {"short_term", "fund"}
    assert all(
        value["relation"] == "CONTRADICTS" for value in resolved["recent_relations"].values()
    )


def test_self_consistent_manual_commit_cannot_bypass_schema_or_packet_binding():
    _case, packet = packet_fixture()
    stage2 = dict(zip(api.PRIMARY_PROPOSITIONS, ("FALSE", "FALSE", "TRUE"), strict=True))
    commit = primary_commits(packet, stage2)[0]
    forged = copy.deepcopy(commit)
    forged["opinion"]["packet_digest"] = "sha256:" + "1" * 64
    forged["primary_opinion_digest"] = digest(forged["opinion"])
    second = primary_commits(packet, stage2)[1]
    with pytest.raises(api.OrderReferenceV2Error, match="packet_mismatch"):
        api.resolve_primary_roles([forged, second])

    forged = copy.deepcopy(commit)
    forged["opinion"]["primary"]["stage_0"] = "TRUE"
    forged["primary_opinion_digest"] = digest(forged["opinion"])
    with pytest.raises(api.OrderReferenceV2Error, match="atomic_primary_fields_differ"):
        api.validate_primary_commit(forged)


def test_recent_schema_has_no_primary_mutation_field():
    case, packet = packet_fixture()
    stage2 = dict(zip(api.PRIMARY_PROPOSITIONS, ("FALSE", "FALSE", "TRUE"), strict=True))
    commit = primary_commits(packet, stage2)[0]
    recent_packet = api.build_recent_packet(case, commit)
    raw = recent_raw(recent_packet)
    raw["primary"] = stage2
    with pytest.raises(api.OrderReferenceV2Error, match="recent_modifier_fields_differ"):
        api.commit_recent_modifier(recent_packet, raw)


def test_unavailable_primary_cannot_be_turned_into_stage0():
    _case, packet = packet_fixture()
    packet = {**packet, "primary_available": False}
    packet_body = {key: value for key, value in packet.items() if key != "packet_digest"}
    packet["packet_digest"] = digest(packet_body)
    stage0 = dict(zip(api.PRIMARY_PROPOSITIONS, ("TRUE", "FALSE", "FALSE"), strict=True))
    with pytest.raises(api.OrderReferenceV2Error, match="must_remain_unknown"):
        api.commit_primary_opinion(packet, primary_raw(packet, "short_term", stage0))
    unknown = dict(zip(api.PRIMARY_PROPOSITIONS, ("UNKNOWN", "UNKNOWN", "UNKNOWN"), strict=True))
    commits = primary_commits(packet, unknown)
    assert api.resolve_primary_roles(commits, primary_available=False)["primary_reference"] == (
        "INPUT_UNAVAILABLE"
    )
