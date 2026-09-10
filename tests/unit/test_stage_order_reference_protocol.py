from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from quanthus.stage_learning import order_reference_protocol as api
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

from .test_structure_trend_evolution import sequence
from .test_structure_v2_inputs import row

REVISION = "a" * 40


def make_evidence(values=None, indices=(-1,)):
    values = sequence(80) if values is None else values
    return api.build_evidence(
        values, [("000001", values[index].market_date) for index in indices],
        InputPolicy(api.PROTOCOL_DIGEST, ("000001",), "calendar-v1"),
        code_revision=REVISION, source_kind="synthetic_fixture",
    )


def opinion(case, role, *, primary=None, recent=None, origin="same-assistant"):
    return {
        "schema_version": api.REVIEW_SCHEMA, "case_id": case["case_id"],
        "protocol_digest": api.PROTOCOL_DIGEST, "reviewer_id": f"reviewer-{role}",
        "role": role,
        "provenance": {"kind": "self_simulated", "origin_id": origin, "session_id": role},
        "primary_window_sessions": 20, "recent_window_sessions": 10,
        "primary": primary or {"stage_0": "FALSE", "stage_2": "TRUE"},
        "recent": recent or {"width": "EXPANDING", "center": "STABLE"},
        "support_refs": [case["history_refs"][-1]], "contradiction_refs": [],
        "reason": "Synthetic authored opinion for protocol tests; not a market judgement.",
        "unknowns": ["synthetic_fixture_only"],
    }


def opinions(evidence):
    return [opinion(case, role) for case in evidence["cases"] for role in api.ROLES]


def test_frozen_protocol_and_fact_bindings():
    assert digest(api.load_protocol()) == api.PROTOCOL_DIGEST
    assert api.load_protocol()["authority"]["parameter_search"] is False


def test_batch_materializes_each_fact_family_once_and_views_reuse():
    with (
        patch.object(
            api, "build_trend_evolution_facts", wraps=api.build_trend_evolution_facts,
        ) as t,
        patch.object(
            api, "build_structure_order_facts", wraps=api.build_structure_order_facts,
        ) as o,
    ):
        evidence = make_evidence(indices=(65, 79))
        for case in evidence["cases"]:
            api.reviewer_view(evidence, case["case_id"])
            api.reviewer_view(evidence, case["case_id"])
        assert t.call_count == o.call_count == 1
    assert len(evidence["source_rows"]) == 80


def test_reviewer_view_is_prefix_only_and_identity_is_suffix_invariant():
    values = sequence(80)
    full = make_evidence(values, (65, 79))
    prefix = make_evidence(values[:66])
    case = full["cases"][0]
    assert case == prefix["cases"][0]
    view = api.reviewer_view(full, case["case_id"])
    assert len(view["price_history"]) == 61
    assert max(item["market_date"] for item in view["price_history"]) == case["market_date"]
    assert values[-1].market_date.isoformat() not in json.dumps(view)
    assert set(view) == {"case", "price_history", "profile"}
    assert {"prediction", "future_outcome", "score", "candidate_parameters"}.isdisjoint(case)


def test_input_use_clock_does_not_change_semantic_evidence():
    from dataclasses import replace

    rows = sequence(80)
    later = [replace(item, cutoff=item.cutoff + timedelta(days=20)) for item in rows]
    assert make_evidence(rows) == make_evidence(later)


@pytest.mark.parametrize("year", [2022, 2023, 2024, 2025, 2026])
def test_no_sealed_date_even_if_called_synthetic(year):
    from dataclasses import replace

    values = sequence(80)
    values[-1] = replace(values[-1], market_date=date(year, 1, 1))
    with pytest.raises(api.OrderReferenceError, match="case_outside_development"):
        make_evidence(values)


def test_source_after_last_case_or_duplicate_is_rejected():
    with pytest.raises(api.OrderReferenceError, match="time_scope"):
        make_evidence(indices=(65,))
    values = sequence(80)
    with pytest.raises(api.OrderReferenceError, match="duplicate_security_session"):
        make_evidence([*values, values[-1]])
    with pytest.raises(api.OrderReferenceError, match="duplicate_case"):
        make_evidence(indices=(79, 79))


def test_unknown_and_input_unavailable_are_not_market_stage_zero():
    values = sequence(80)
    values[-1] = row(79, close=None, quality_reason="missing_bar")
    evidence = make_evidence(values)
    case = evidence["cases"][0]
    assert not case["primary_available"]
    reviews = [opinion(case, role, primary={"stage_0": "UNKNOWN", "stage_2": "UNKNOWN"},
                       recent={"width": "UNAVAILABLE", "center": "UNAVAILABLE"})
               for role in api.ROLES]
    resolved, summary = api.resolve_opinions(evidence, reviews)
    assert resolved[0]["primary_reference"] == "INPUT_UNAVAILABLE"
    assert summary["primary_reference_counts"] == {"INPUT_UNAVAILABLE": 1}
    reviews[0]["primary"]["stage_0"] = "TRUE"
    with pytest.raises(api.OrderReferenceError, match="unavailable_primary"):
        api.resolve_opinions(evidence, reviews)


def test_insufficient_history_is_retained():
    case = make_evidence(sequence(30))["cases"][0]
    assert not case["primary_available"]
    assert len(case["history_refs"]) == 30
    assert set(case["availability_reasons"].values()) == {"insufficient_contiguous_history"}


@pytest.mark.parametrize("field,value", [("listing_id", "new-listing"),
                                         ("coordinate_epoch", "new-coordinate")])
def test_listing_and_coordinate_changes_reset_history(field, value):
    from dataclasses import replace

    rows = sequence(80)
    rows[-1] = replace(rows[-1], **{field: value})
    assert not make_evidence(rows)["cases"][0]["primary_available"]


def test_recent_disagreement_does_not_flip_primary():
    evidence = make_evidence()
    reviews = opinions(evidence)
    reviews[1]["recent"]["width"] = "CONTRACTING"
    resolved, summary = api.resolve_opinions(evidence, reviews)
    assert resolved[0]["primary_reference"] == "STAGE_2"
    assert not resolved[0]["recent_agreement"]
    assert summary["recent_disagreement_count"] == 1


@pytest.mark.parametrize("primary,status", [
    ({"stage_0": "FALSE", "stage_2": "FALSE"}, "NEITHER"),
    ({"stage_0": "UNKNOWN", "stage_2": "UNKNOWN"}, "AMBIGUOUS"),
    ({"stage_0": "TRUE", "stage_2": "TRUE"}, "RULE_CONFLICT"),
    ({"stage_0": "TRUE", "stage_2": "FALSE"}, "STAGE_0"),
])
def test_primary_statuses_are_distinct(primary, status):
    evidence = make_evidence()
    reviews = [opinion(evidence["cases"][0], role, primary=primary) for role in api.ROLES]
    resolved, _ = api.resolve_opinions(evidence, reviews)
    assert resolved[0]["primary_reference"] == status


def test_primary_disagreement_is_retained_without_forced_adjudication():
    evidence = make_evidence()
    reviews = opinions(evidence)
    reviews[1]["primary"] = {"stage_0": "TRUE", "stage_2": "FALSE"}
    resolved, _ = api.resolve_opinions(evidence, reviews)
    assert resolved[0]["primary_reference"] == "AMBIGUOUS"
    assert len(resolved[0]["opinion_digests"]) == 2


@pytest.mark.parametrize("kind", ["self_simulated", "deterministic_policy", "external_ai", "human"])
def test_claimed_role_or_source_never_certifies_independence_or_human_validation(kind):
    evidence = make_evidence()
    reviews = opinions(evidence)
    for item in reviews:
        item["provenance"]["kind"] = kind
    _, summary = api.resolve_opinions(evidence, reviews)
    assert summary["distinct_origin_count"] == 1
    for key in ("independent_validation", "human_expert_validated", "market_reference_validated",
                "parameter_search_authorized", "official_label_authorized"):
        assert summary[key] is False
    assert summary["primary_stage"] is None


@pytest.mark.parametrize("key,value", [
    ("candidate_prediction", True), ("future_outcome", 0.1), ("confidence", 0.9),
    ("primary_window_sessions", 10), ("case_id", "sha256:" + "0" * 64),
    ("support_refs", ["foreign-evidence"]), ("support_refs", []),
    ("reason", ""), ("role", "adjudicator"),
])
def test_unbound_or_leaking_or_incomplete_opinions_fail_closed(key, value):
    evidence = make_evidence()
    reviews = opinions(evidence)
    reviews[0][key] = value
    with pytest.raises(api.OrderReferenceError):
        api.resolve_opinions(evidence, reviews)


def test_duplicate_missing_and_alias_role_ledgers_rejected():
    evidence = make_evidence()
    reviews = opinions(evidence)
    for bad in (reviews[:1], [reviews[0], reviews[0]], [*reviews, reviews[0]]):
        with pytest.raises(api.OrderReferenceError):
            api.resolve_opinions(evidence, bad)
    reviews[1]["reviewer_id"] = reviews[0]["reviewer_id"]
    with pytest.raises(api.OrderReferenceError, match="impersonate"):
        api.resolve_opinions(evidence, reviews)


def test_bundle_cold_readback_keeps_all_evidence_and_clock_out_of_semantics(tmp_path):
    evidence = make_evidence(indices=(65, 79))
    reviews = opinions(evidence)
    t0 = datetime(2026, 9, 10, tzinfo=timezone.utc)
    for name, clock in (("a", t0), ("b", t0 + timedelta(hours=1))):
        api.publish_bundle(tmp_path / name, evidence, reviews, generated_at=clock)
        verified = api.verify_bundle(tmp_path / name)
        assert verified["verified"]
        assert verified["mode"] == "independent_reconstruction"
        assert verified["opinion_count"] == 4
    for name in api.FILES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert (tmp_path / "a" / "manifest.json").read_bytes() != (
        tmp_path / "b" / "manifest.json"
    ).read_bytes()
    with pytest.raises(api.OrderReferenceError, match="exists"):
        api.publish_bundle(tmp_path / "a", evidence, reviews)


@pytest.mark.parametrize("kind", ["missing_ready", "extra_file", "corrupt", "symlink"])
def test_incomplete_corrupt_and_symlink_artifact_rejected(tmp_path, kind):
    evidence = make_evidence()
    target = tmp_path / "bundle"
    api.publish_bundle(target, evidence, opinions(evidence))
    if kind == "missing_ready":
        (target / "READY").unlink()
    elif kind == "extra_file":
        (target / "hidden.json").write_text("{}")
    elif kind == "corrupt":
        (target / "reviews.json").write_text("[]")
    else:
        saved = tmp_path / "saved.json"
        (target / "reviews.json").rename(saved)
        (target / "reviews.json").symlink_to(saved)
    with pytest.raises(api.OrderReferenceError):
        api.verify_bundle(target)


def reseal(target: Path):
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["files"] = {name: api._sha((target / name).read_bytes()) for name in api.FILES}
    content = canonical(manifest)
    (target / "manifest.json").write_bytes(content)
    (target / "READY").write_text(api._sha(content) + "\n")


def test_rehashed_tampered_summary_still_fails_independent_reconstruction(tmp_path):
    evidence = make_evidence()
    api.publish_bundle(tmp_path / "bundle", evidence, opinions(evidence))
    file = tmp_path / "bundle" / "summary.json"
    summary = json.loads(file.read_text())
    summary["human_expert_validated"] = True
    file.write_bytes(canonical(summary))
    reseal(tmp_path / "bundle")
    with pytest.raises(api.OrderReferenceError, match="reconstruction"):
        api.verify_bundle(tmp_path / "bundle")


def test_rehashed_case_mutation_does_not_pass_source_reconstruction():
    evidence = make_evidence()
    corrupted = copy.deepcopy(evidence)
    case = corrupted["cases"][0]
    case["primary"]["range_width"]["value"] = "999"
    case["case_id"] = digest({key: value for key, value in case.items() if key != "case_id"})
    with pytest.raises(api.OrderReferenceError, match="reconstruction"):
        api.validate_evidence(corrupted)


def test_fixed_synthetic_paths_keep_primary_geometry_and_recent_difference():
    packets = []
    for contraction in (True, False):
        values = []
        for index in range(80):
            half_width = 8 if index < 60 else 6 if (index < 70) == contraction else 4
            values.append(row(index, str(99 if index % 2 == 0 else 101),
                              high=Decimal(100 + half_width), low=Decimal(100 - half_width)))
        packets.append(make_evidence(values)["cases"][0])
    assert {key: item["value"] for key, item in packets[0]["primary"].items()} == {
        key: item["value"] for key, item in packets[1]["primary"].items()
    }
    assert Decimal(packets[0]["recent"]["width_change"]["value"]) < 0
    assert Decimal(packets[1]["recent"]["width_change"]["value"]) > 0


def test_progress_is_observational_and_sampling_strata_keep_separate_denominators():
    rows = sequence(80)
    keys = [("000001", rows[i].market_date) for i in (65, 79)]
    strata = dict(zip(keys, ("representative", "enriched"), strict=True))
    policy = InputPolicy(api.PROTOCOL_DIGEST, ("000001",), "calendar-v1")
    timings = []
    kwargs = {"code_revision": REVISION, "source_kind": "synthetic_fixture",
              "sampling_strata": strata}
    evidence = api.build_evidence(rows, keys, policy, **kwargs, progress=timings.append)
    assert evidence == api.build_evidence(rows, keys, policy, **kwargs)
    assert [item["stage"] for item in timings] == [
        "admission", "trend_facts", "order_facts", "case_packets",
    ]
    assert all(item["disposition"] == "built_once" for item in timings)
    assert timings[-1]["bytes"] == len(canonical(evidence))
    reviews = opinions(evidence)
    for item in reviews:
        item["provenance"]["session_id"] = item["case_id"]
    _, summary = api.resolve_opinions(evidence, reviews)
    assert summary["sampling_stratum_counts"] == {"enriched": 1, "representative": 1}
    assert summary["reference_counts_by_stratum"]["enriched"] == {"STAGE_2": 1}
    assert "precision" not in summary
    api.validate_evidence(evidence)


def test_naive_or_before_observation_cutoff_is_rejected():
    from dataclasses import replace

    values = sequence(80)
    for bad_cutoff in (datetime(2020, 3, 21), values[-1].result_as_of - timedelta(seconds=1)):
        bad = [*values[:-1], replace(values[-1], cutoff=bad_cutoff)]
        with pytest.raises(api.OrderReferenceError, match="cutoff"):
            make_evidence(bad)


def test_profile_and_code_or_source_identity_changes_are_not_silent():
    from dataclasses import replace

    values = sequence(80)
    first = make_evidence(values)
    values[-1] = replace(values[-1], source_revision="revision-2")
    assert first["cases"][0]["case_id"] != make_evidence(values)["cases"][0]["case_id"]
    with pytest.raises(api.OrderReferenceError, match="code_revision"):
        api.build_evidence(values, [("000001", values[-1].market_date)],
                           InputPolicy(api.PROTOCOL_DIGEST, ("000001",), "calendar-v1"),
                           code_revision="main", source_kind="synthetic_fixture")
    broken = copy.deepcopy(first)
    broken["protocol"]["profile"]["primary_window_sessions"] = 10
    with pytest.raises(api.OrderReferenceError, match="protocol"):
        api.validate_evidence(broken)


def test_rehashed_raw_source_change_is_rejected():
    evidence = make_evidence()
    broken = copy.deepcopy(evidence)
    ref = next(iter(broken["source_rows"]))
    broken["source_rows"][ref]["close"] = "123"
    with pytest.raises(api.OrderReferenceError, match="source_member_digest"):
        api.validate_evidence(broken)


def test_all_original_reviews_are_retained_in_bundle(tmp_path):
    evidence = make_evidence()
    reviews = opinions(evidence)
    reviews[0]["primary"] = {"stage_0": "TRUE", "stage_2": "FALSE"}
    api.publish_bundle(tmp_path / "bundle", evidence, reviews)
    retained = json.loads((tmp_path / "bundle" / "reviews.json").read_text())
    assert sorted(retained, key=lambda r: r["role"]) == sorted(reviews, key=lambda r: r["role"])
    assert api.verify_bundle(tmp_path / "bundle")["primary_reference_counts"] == {"AMBIGUOUS": 1}


def test_generation_clock_must_be_timezone_aware(tmp_path):
    evidence = make_evidence()
    with pytest.raises(api.OrderReferenceError, match="generation_clock"):
        api.publish_bundle(tmp_path / "bundle", evidence, opinions(evidence),
                           generated_at=datetime(2026, 9, 10))
    assert not (tmp_path / "bundle").exists()


def test_cli_missing_artifact_is_a_structured_failure(tmp_path, capsys):
    assert api.main([str(tmp_path / "missing")]) == 2
    assert json.loads(capsys.readouterr().out)["verified"] is False
