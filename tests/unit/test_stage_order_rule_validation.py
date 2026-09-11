from __future__ import annotations

import copy
import random
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext
from unittest.mock import patch

import pytest

from quanthus.stage_learning import order_reference_protocol as ref
from quanthus.stage_learning import order_rule_validation as api
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

from .test_stage_order_reference_protocol import opinion
from .test_structure_trend_evolution import sequence


def sketch(identity="i000", *, day=date(2021, 1, 15), available=True, kind="order_like"):
    values = {
        "order_like": (D("0.2"), D("0.12"), D("-0.3"), D("0.01")),
        "instability_like": (D("-0.2"), D("0.25"), D("0.5"), D("0.1")),
        "boundary": (D("0.2"), D("0.2"), D("0.01"), D("0.01")),
        "directional": (D("0.8"), D("0.2"), D("0.8"), D("0.1")),
    }[kind]
    return api.SamplingObservation(identity, identity.replace("i", "s"), day, available,
                                   *values if available else (None,) * 4)


def synthetic_pool(count=200):
    return {f"i{i:03}": f"s{i:03}" for i in range(count)}


def sample_rows(pool):
    for identity in pool:
        for quarter in range(4):
            for offset, kind in enumerate(("order_like", "instability_like", "boundary")):
                yield sketch(identity, day=date(2021, 1 + 3 * quarter, 10 + offset), kind=kind)


def test_validation_contract_is_digest_frozen():
    assert digest(api.load_validation()) == api.VALIDATION_DIGEST


def test_selection_is_order_invariant_and_never_calls_candidate():
    pool = synthetic_pool()
    rows = list(sample_rows(pool))
    expected = api.select_cases(pool, rows)
    random.Random(7).shuffle(rows)
    with patch.object(api, "evaluate_case", side_effect=AssertionError("no candidate in sampling")):
        actual = api.select_cases(dict(reversed(list(pool.items()))), iter(rows))
    assert actual == expected
    assert len(actual["selected"]) == 160
    assert len({v["security_id"] for v in actual["selected"]}) == 160
    assert actual["shortfalls"] == dict.fromkeys(
        ("coverage", "order_like", "instability_like", "boundary"), 0,
    )
    coverage = [v for v in actual["selected"] if v["panel"] == "coverage"]
    assert [sum(v["quarter"] == q for v in coverage) for q in range(1, 5)] == [16] * 4
    assert not actual["source_isolation_verified"]
    assert not actual["runtime_activation_authorized"]


def test_coverage_keeps_missing_and_unavailable_without_replacement():
    pool = synthetic_pool()
    rows = list(sample_rows(pool))
    slots = api.select_cases(pool, rows)["coverage_slots"]
    first, second = list(slots)[:2]
    rows = [replace(obs, available=False, efficiency=None, width=None,
                    width_change=None, center_shift=None) if obs.security_id == first else obs
            for obs in rows if obs.security_id != second]
    result = api.select_cases(pool, rows)
    chosen = [v for v in result["selected"] if v["security_id"] == first]
    assert len(chosen) == 1 and chosen[0]["panel"] == "coverage"
    assert result["shortfalls"]["coverage"] == 1
    assert second not in {v["security_id"] for v in result["selected"]}
    assert all(v["security_id"] not in slots for v in result["selected"]
               if v["panel"] == "challenge")


def test_challenge_shortages_do_not_cross_bucket_backfill():
    pool = synthetic_pool()
    rows = [sketch(identity, day=date(2021, month, 15))
            for identity in pool for month in (1, 4, 7, 10)]
    result = api.select_cases(pool, rows)
    assert len(result["selected"]) == 64 + 48
    assert result["shortfalls"]["instability_like"] == 32
    assert result["shortfalls"]["boundary"] == 16


@pytest.mark.parametrize("change", ["duplicate", "outside_pool", "2022", "2025", "bad_code",
                                   "nan", "infinite", "missing", "bool", "efficiency"])
def test_invalid_or_foreign_observations_fail_before_selection(change):
    pool = synthetic_pool(1)
    obs = sketch()
    rows = [obs]
    if change == "duplicate":
        rows.append(obs)
    elif change == "outside_pool":
        rows = [replace(obs, security_id="not-in-pool")]
    elif change.isdigit():
        rows = [replace(obs, market_date=date(int(change), 1, 15))]
    elif change == "bad_code":
        rows = [replace(obs, security_code="other-code")]
    elif change in {"nan", "infinite"}:
        rows = [replace(obs, width=D("NaN" if change == "nan" else "Infinity"))]
    elif change == "missing":
        rows = [replace(obs, width=None)]
    elif change == "bool":
        rows = [replace(obs, available=1)]
    else:
        rows = [replace(obs, efficiency=D("1.01"))]
    with pytest.raises(api.RuleValidationError):
        api.select_cases(pool, rows)


def test_pool_limits_and_scan_limit_are_fail_closed():
    with pytest.raises(api.RuleValidationError, match="pool"):
        api.select_cases(synthetic_pool(513), [])
    with (patch.object(api, "MAX_SCAN_ROWS", 1),
          pytest.raises(api.RuleValidationError, match="scan")):
        api.select_cases(synthetic_pool(1), [sketch(), sketch(day=date(2021, 1, 16))])


def test_sampling_is_independent_of_ambient_decimal_precision():
    pool = synthetic_pool()
    rows = list(sample_rows(pool))
    expected = api.select_cases(pool, rows)
    with localcontext() as context:
        context.prec = 2
        assert api.select_cases(pool, rows) == expected


def test_case_reconstruction_uses_same_absolute_sampling_coordinates():
    case = {
        "security_id": "i000",
        "security_code": "s000",
        "market_date": "2021-01-15",
        "primary_available": True,
        "primary": {
            "directional_efficiency": {"value": "-0.2"},
            "range_width": {"value": "0.25"},
            "width_change": {"value": "0.5"},
            "center_shift": {"value": "-0.1"},
        },
    }
    rebuilt = api.SamplingObservation.from_case(case)
    expected = api.SamplingObservation(
        "i000", "s000", date(2021, 1, 15), True,
        D("0.2"), D("0.25"), D("0.5"), D("0.1"),
    )
    assert rebuilt == expected
    assert digest(rebuilt) == digest(expected)


def old_metadata():
    codes = tuple(f"{i:06}" for i in range(512))
    summary = canonical({"code_revision": api.OLD_REVISION, "cohort_size": 512,
                         "cohort_digest": digest(codes)})
    manifest = canonical({"code_revision": api.OLD_REVISION,
                          "files": {"summary.json": api._sha(summary)}})
    return codes, summary, manifest


def test_old_cohort_must_match_accepted_manifest_and_actual_cohort_digest():
    codes, summary, manifest = old_metadata()
    # Synthetic test identities must not weaken the production pin.
    with pytest.raises(api.RuleValidationError, match="accepted_run"):
        api.verify_old_cohort(manifest, summary, codes)
    with patch.object(api, "OLD_MANIFEST", api._sha(manifest)):
        assert api.verify_old_cohort(manifest, summary, tuple(reversed(codes))) == codes
        with pytest.raises(api.RuleValidationError, match="summary_binding"):
            api.verify_old_cohort(manifest, summary + b" ", codes)
        with pytest.raises(api.RuleValidationError, match="cohort_reconstruction"):
            api.verify_old_cohort(manifest, summary, (*codes[:-1], "999999"))
        with pytest.raises(api.RuleValidationError, match="cohort_reconstruction"):
            api.verify_old_cohort(manifest, summary, (*codes[:-1], codes[0]))


def identity_members():
    codes = tuple(f"{i:06}" for i in range(512))
    members = [{"security_code": code, "security_id": "old-" + code} for code in codes]
    members += [{"security_code": code, "security_id": "old-" + code}
                for code in ("300750", "600519")]
    members += [{"security_code": f"{100000+i:06}", "security_id": f"new-{i}"}
                for i in range(600)]
    return codes, members


def test_economic_aliases_and_historical_code_reuse_cannot_evade_exclusion():
    codes, members = identity_members()
    members += [{"security_code": "777777", "security_id": "old-000100"},
                {"security_code": "000100", "security_id": "old-reused-identity"},
                {"security_code": "888888", "security_id": "old-reused-identity"}]
    result = api.isolated_pool(codes, members)
    assert len(result["pool"]) == 512
    assert "old-reused-identity" in result["excluded_economic_ids"]
    assert all(key.startswith("new-") for key in result["pool"])
    assert result == api.isolated_pool(tuple(reversed(codes)), list(reversed(members)))
    assert not result["source_membership_verified"]


@pytest.mark.parametrize("change", ["missing006", "missing_old", "duplicate"])
def test_incomplete_identity_mapping_is_not_a_valid_exclusion(change):
    codes, members = identity_members()
    if change == "duplicate":
        members.append(members[0])
    else:
        code = "600519" if change == "missing006" else "000100"
        members = [item for item in members if item["security_code"] != code]
    with pytest.raises(api.RuleValidationError):
        api.isolated_pool(codes, members)


def pair(reference, prediction, count=1):
    return [{"reference": reference, "prediction": prediction} for _ in range(count)]


def good_pairs():
    return (pair("STAGE_0", "STAGE_0", 30) + pair("STAGE_2", "STAGE_2", 30)
            + pair("NEITHER", "NO_ASSERTION", 50) + pair("AMBIGUOUS", "NO_ASSERTION", 10))


def assess(rows, coverage=None):
    return api.assess_gates(api.metrics(rows), api.metrics([] if coverage is None else coverage))


def test_no_assertion_is_a_false_negative_on_definite_positive_not_a_correct_neither():
    report = api.metrics(pair("STAGE_2", "NO_ASSERTION", 5) + pair("NEITHER", "NO_ASSERTION", 3)
                         + pair("AMBIGUOUS", "STAGE_2", 2))
    result = report["classes"]["STAGE_2"]
    assert result["false_negative"] == 5
    assert result["precision"] is None  # ambiguous predictions do not invent known labels
    assert report["ambiguous_assertion_rate"] == "1"
    assert report["matrix"]["NEITHER"]["NO_ASSERTION"] == 3


def test_promising_calculation_never_claims_independent_validation():
    assert assess(good_pairs())["disposition"] == "PROMISING_EXPLORATORY_ONLY"
    assert "independent_semantic_evidence_not_established" in assess(good_pairs())["reasons"]


@pytest.mark.parametrize("kind", ["reference", "prediction", "ambiguous", "complete"])
def test_scarcity_stops_without_supplement(kind):
    rows = good_pairs()
    if kind == "reference":
        rows[:15] = pair("NEITHER", "NO_ASSERTION", 15)
    elif kind == "prediction":
        rows[:15] = pair("STAGE_0", "NO_ASSERTION", 15)
    elif kind == "ambiguous":
        rows[-5:] = pair("NEITHER", "NO_ASSERTION", 5)
    else:
        rows = rows[:-1]
    assert assess(rows)["disposition"] == "INCONCLUSIVE_NO_SUPPLEMENT"


@pytest.mark.parametrize("kind", ["precision", "false_positive", "coverage", "ambiguous"])
def test_bad_performance_with_sufficient_counts_requires_revision(kind):
    rows = good_pairs()
    if kind == "precision":
        rows[:10] = pair("STAGE_2", "STAGE_0", 10)
    elif kind == "false_positive":
        rows[60:70] = pair("NEITHER", "STAGE_0", 10)
    elif kind == "coverage":
        rows += pair("STAGE_0", "NO_ASSERTION", 30)
    else:
        rows[-3:] = pair("AMBIGUOUS", "STAGE_2", 3)
    assert assess(rows)["disposition"] == "REVISE_TARGET_OR_PARK"


def test_challenge_panel_cannot_hide_coverage_panel_veto():
    coverage = pair("STAGE_0", "STAGE_0", 6) + pair("NEITHER", "STAGE_0", 4)
    assert assess(good_pairs() + coverage, coverage)["disposition"] == "REVISE_TARGET_OR_PARK"
    assert "STAGE_0:coverage_panel_veto" in assess(good_pairs() + coverage, coverage)["reasons"]


@pytest.mark.parametrize("row", [pair("RULE_CONFLICT", "NO_ASSERTION")[0],
                                 pair("STAGE_0", "RULE_CONFLICT")[0],
                                 pair("INPUT_UNAVAILABLE", "STAGE_0")[0]])
def test_evidence_failures_take_precedence_over_counts(row):
    assert assess([row])["disposition"] == "BLOCKED_EVIDENCE"


def test_empty_metrics_keep_every_matrix_cell_and_zero_denominators():
    value = api.metrics([])
    assert len(value["matrix"]) == len(api.REFERENCES)
    assert all(set(row) == set(api.PREDICTIONS) for row in value["matrix"].values())
    assert value["classes"]["STAGE_0"]["precision"] is None
    assert assess([])["disposition"] == "INCONCLUSIVE_NO_SUPPLEMENT"


def make_bundle(tmp_path):
    rows = [replace(row, market_date=row.market_date + timedelta(days=366),
                    cutoff=row.cutoff + timedelta(days=366)) for row in sequence(80)]
    evidence = ref.build_evidence(
        rows, [(rows[-1].security_code, rows[-1].market_date)],
        InputPolicy(ref.PROTOCOL_DIGEST, (rows[-1].security_code,), "calendar-v1"),
        code_revision="a" * 40, source_kind="synthetic_fixture",
    )
    case = evidence["cases"][0]
    obs = api.SamplingObservation.from_case(case)
    selected = api.select_cases({obs.security_id: obs.security_code}, [obs])
    opinions = [opinion(case, role, primary={"stage_0": "FALSE", "stage_2": "FALSE"})
                for role in ref.ROLES]
    target = tmp_path / "sealed"
    receipt = ref.publish_bundle(target, evidence, opinions)
    return target, selected, receipt, evidence


def test_scoring_reads_sealed_opinions_and_binds_the_exact_selection(tmp_path):
    target, selected, receipt, _ = make_bundle(tmp_path)
    result = api.score_sealed_bundle(target, selected,
                                    expected_manifest=receipt["manifest_sha256"],
                                    expected_selection=selected["selection_digest"])
    assert result["benchmark"]["case_count"] == 1
    assert result["cases"][0]["reference"] == "NEITHER"
    assert set(result["quarters"]) == {"1", "2", "3", "4"}
    assert set(result["panels"]) == {"coverage", "challenge"}
    assert result["status"] == "SCORING_COMPONENT_ONLY"
    assert result["gate_calculation"]["disposition"] == "INCONCLUSIVE_NO_SUPPLEMENT"
    assert not result["source_isolation_verified"]
    assert not result["runtime_activation_authorized"]
    assert result["distinct_origin_count"] == 1


@pytest.mark.parametrize("change", [
    "bundle_binding", "selection_binding", "observation", "reviews",
])
def test_sealed_scoring_rejects_wrong_or_tampered_inputs(tmp_path, change):
    target, selected, receipt, _ = make_bundle(tmp_path)
    expected_manifest = receipt["manifest_sha256"]
    expected_selection = selected["selection_digest"]
    if change == "bundle_binding":
        expected_manifest = "sha256:" + "0" * 64
    elif change == "selection_binding":
        selected["scanned_attempts"] += 1
    elif change == "observation":
        selected["selected"][0]["observation_digest"] = "sha256:" + "0" * 64
        expected_selection = digest({k: v for k, v in selected.items() if k != "selection_digest"})
        selected["selection_digest"] = expected_selection
    else:
        (target / "reviews.json").write_bytes(b"[]")
    with pytest.raises((api.RuleValidationError, ref.OrderReferenceError)):
        api.score_sealed_bundle(target, selected, expected_manifest=expected_manifest,
                               expected_selection=expected_selection)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_duplicate_or_nonfinite_metadata_is_rejected(raw):
    with pytest.raises(api.RuleValidationError):
        api._json(raw)


def test_threshold_equalities_and_metric_precision_are_stable():
    # Precision=0.8, coverage=0.8, FPR=0.0625; ambiguous assertions=0.2.
    rows = (pair("STAGE_0", "STAGE_0", 20) + pair("STAGE_0", "NO_ASSERTION", 5)
            + pair("STAGE_2", "STAGE_2", 20) + pair("STAGE_2", "NO_ASSERTION", 5)
            + pair("NEITHER", "STAGE_0", 5) + pair("NEITHER", "STAGE_2", 5)
            + pair("NEITHER", "NO_ASSERTION", 50)
            + pair("AMBIGUOUS", "STAGE_0", 2) + pair("AMBIGUOUS", "NO_ASSERTION", 8))
    expected = api.metrics(rows)
    assert expected["classes"]["STAGE_0"]["precision"] == "0.8"
    assert assess(rows)["disposition"] == "PROMISING_EXPLORATORY_ONLY"
    with localcontext() as context:
        context.prec = 2
        assert api.metrics(rows) == expected
        assert assess(rows)["disposition"] == "PROMISING_EXPLORATORY_ONLY"


def test_rule_and_old_experiment_contracts_are_not_mutated_by_components():
    cfg = copy.deepcopy(api.load_validation())
    api.select_cases(synthetic_pool(1), [sketch()])
    assess(good_pairs())
    assert api.load_validation() == cfg
