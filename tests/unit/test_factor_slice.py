"""Real consumer semantics, causal arithmetic and the retrospective authority boundary."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path

import pytest

from quanthus.stage_learning.factor_slice import (
    FACTOR,
    FactorSliceError,
    audit_arithmetic,
    compare_queues,
    consume_queue,
    load_catalog,
    project_facts,
    run,
)
from quanthus.structure_v2_inputs import admit_daily
from tests.unit.test_structure_v2_inputs import SHA, TZ, policy, row


def values(count=23):
    output = []
    for i in range(count):
        day = date(2021, 1, 4)+timedelta(days=i)
        output.append(replace(row(i), market_date=day,
                              cutoff=datetime.combine(day, datetime.min.time(), TZ)
                              + timedelta(hours=15)))
    return output


def build(rows):
    admitted = tuple(admit_daily(r, policy()) for r in rows)
    facts = project_facts(admitted, spec_digest=SHA, calendar_ref=SHA, definition_digest=SHA)
    return admitted, facts


def fact(i, volume="1", margin="0.01", day="2021-06-01", admitted=True):
    return {"security_id": f"s{i}", "listing_id": f"l{i}", "market_date": day,
            "admitted": admitted, "resistance_margin20": margin, "volume_ratio20": volume,
            "historical_knowledge_cutoff": None, "source_member_digest": SHA,
            "price_fact_revision": SHA, "factor_revision": SHA,
            "feature_id": FACTOR, "feature_version": "1.0.0", "definition_digest": SHA}


def test_catalog_reuses_strict_eight_definitions_without_duplicate_alias_asset():
    repo = Path(__file__).resolve().parents[2]
    legacy = repo/"config/p3-baseline-features.research.v1.json"
    before = legacy.read_bytes()
    catalog = load_catalog(repo)
    assert len(catalog["assets"]) == 8
    assert len({a["asset_id"] for a in catalog["assets"]}) == 8
    selected = [a for a in catalog["assets"] if a["computed_in_this_slice"]]
    assert len(selected) == 1
    assert selected[0]["asset_id"] == FACTOR+"@1.0.0"
    assert selected[0]["business_domains"] == ["liquidity", "structure_activity"]
    assert not catalog["legacy_PIT_feature_fact"]
    assert legacy.read_bytes() == before


def test_prior20_excludes_T_and_all_semantics_have_exact_independent_oracle():
    rows = values(21)
    rows[-1] = replace(rows[-1], high=Decimal("120"), close=Decimal("110"),
                       volume=Decimal("2500"))
    admitted, facts = build(rows)
    assert facts[-1]["resistance_margin20"] == "0.1"
    assert facts[-1]["volume_ratio20"] == "2.5"
    assert audit_arithmetic(admitted, facts)["volume_values_checked"] == 1
    # An included-T denominator or high would produce a different result.
    assert facts[-1]["source_dwd_known_from"] == rows[-1].dwd_known_from
    assert facts[-1]["historical_knowledge_cutoff"] is None


def test_current_rolling_high_cannot_be_replaced_by_frozen_episode_target():
    rows = values(45)
    for i in range(20, len(rows)):
        level = Decimal(105+(i-20)*5)
        rows[i] = replace(rows[i], high=level+2, close=level)
    admitted, facts = build(rows)
    # The second breakout row is above the latest high107, not just the old target100.
    with localcontext(Context(prec=28)):
        expected = Decimal(110)/Decimal(107)-1
    assert facts[21]["resistance_margin20"] == format(expected, "f")
    assert audit_arithmetic(admitted, facts)["price_values_checked"] == 25


@pytest.mark.parametrize("volume,reason", [(Decimal(0), "available"),
                                         (None, "current_volume_unavailable")])
def test_zero_current_volume_is_not_null(volume, reason):
    rows = values(21)
    rows[-1] = replace(rows[-1], volume=volume)
    admitted, facts = build(rows)
    assert facts[-1]["volume_ratio20"] == ("0" if volume == 0 else None)
    assert facts[-1]["volume_reason"] == reason
    assert audit_arithmetic(admitted, facts)["status"] == "PASS_EXACT_ARITHMETIC"


def test_zero_prior_baseline_is_unknown_not_infinite_or_a_filled_volume():
    rows = [replace(r, volume=Decimal(0)) for r in values(21)]
    admitted, facts = build(rows)
    assert facts[-1]["volume_ratio20"] is None
    assert facts[-1]["volume_reason"] == "nonpositive_prior_volume_baseline"
    assert audit_arithmetic(admitted, facts)["unknown_volume_rows"] == 21


def test_unknown_prior_volume_stays_unknown_through_its_full_causal_window():
    rows = values(43)
    rows[21] = replace(rows[21], volume=None)
    admitted, facts = build(rows)
    assert all(f["volume_ratio20"] is None for f in facts[21:42])
    assert facts[42]["volume_ratio20"] == "1"
    audit_arithmetic(admitted, facts)


@pytest.mark.parametrize("kind", ["coordinate", "reset", "session_gap", "suspended"])
def test_gap_or_action_rewarms_both_facts_without_creating_a_sell(kind):
    rows = values(44)
    if kind == "coordinate":
        for i in range(22, len(rows)):
            rows[i] = replace(rows[i], coordinate_epoch="new")
    elif kind == "reset":
        rows[22] = replace(rows[22], reset_reason="action_boundary")
    elif kind == "session_gap":
        for i in range(22, len(rows)):
            rows[i] = replace(rows[i], session_index=i+1)
    else:
        rows[22] = replace(rows[22], quality_reason="suspended")
    admitted, facts = build(rows)
    assert facts[22]["resistance_margin20"] is None
    assert facts[22]["volume_ratio20"] is None
    assert facts[41]["volume_ratio20"] is None
    assert facts[43]["volume_ratio20"] is not None
    audit_arithmetic(admitted, facts)
    q = consume_queue(facts, volume_gate=True)
    assert not q["entry_or_exit_authorized"]


def test_appended_future_suffix_cannot_change_earlier_factor_or_provenance():
    rows = values(42)
    _, first = build(rows[:24])
    _, all_rows = build(rows)
    assert all_rows[:24] == first


@pytest.mark.parametrize("field", ["volume_ratio20", "resistance_margin20"])
def test_oracle_rejects_a_tampered_value(field):
    admitted, facts = build(values())
    facts[-1][field] = "987"
    with pytest.raises(FactorSliceError, match="ARITHMETIC_MISMATCH"):
        audit_arithmetic(admitted, facts)


def test_same_price_pool_factor_really_changes_selections_with_actual_costs():
    rows = [fact(1, "0.2"), fact(2, "2"), fact(3, None), fact(4, "1", margin="0")]
    base = consume_queue(rows, volume_gate=False)
    candidate = consume_queue(rows, volume_gate=True)
    result = compare_queues(base, candidate)
    assert result["price_pool_units"] == 3
    assert result["volume_gate_pool_units"] == 1
    assert result["baseline_review_slots"] == 3
    assert result["candidate_review_slots"] == 1
    assert result["review_slots_saved"] == 2
    assert result["unknown_factor_in_price_pool"] == 1
    assert not result["same_actual_cost"]
    assert result["selected_removed"] == 2
    assert result["human_minutes_saved"] is None
    assert result["predictive_or_economic_value"] == "NOT_ASSESSED"
    assert not result["trading_or_P6_admission"]


def test_top5_replacement_uses_same_frozen_rank_not_an_extra_factor_ranking():
    rows = [fact(i, "2") for i in range(8)]
    base = consume_queue(rows, volume_gate=False)
    first_selected = base["days"][0]["selected"][0]["security_id"]
    for r in rows:
        if r["security_id"] == first_selected:
            r["volume_ratio20"] = "0.1"
    base = consume_queue(rows, volume_gate=False)
    candidate = consume_queue(rows, volume_gate=True)
    result = compare_queues(base, candidate)
    assert result["review_slots_saved"] == 0
    assert result["selected_removed"] == result["selected_replacements"] == 1
    assert result["selected_retained"] == 4
    assert consume_queue(rows[::-1], volume_gate=True) == candidate


def test_empty_days_unknowns_and_rejections_stay_in_full_denominator():
    rows = [fact(1, None), fact(2, margin=None),
            fact(3, day="2021-06-02", admitted=False)]
    base = consume_queue(rows, volume_gate=False)
    candidate = consume_queue(rows, volume_gate=True)
    assert candidate["input_rows"] == 3
    assert len(candidate["days"]) == 2
    assert candidate["maximum_review_slots"] == 10
    assert candidate["actual_review_slots"] == 0
    assert all(d["unused_slots"] == 5 for d in candidate["days"])
    assert compare_queues(base, candidate)["unknown_factor_in_price_pool"] == 1
    other = consume_queue(rows[:-1], volume_gate=True)
    with pytest.raises(FactorSliceError, match="DENOMINATOR"):
        compare_queues(base, other)


@pytest.mark.parametrize("mutation", ["duplicate", "future", "knowledge", "nonfinite"])
def test_invalid_scope_or_values_rejected(mutation):
    rows = [fact(1)]
    if mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "future":
        rows[0]["market_date"] = "2025-01-01"
    elif mutation == "knowledge":
        rows[0]["historical_knowledge_cutoff"] = "2021-06-01T15:00:00+08:00"
    else:
        rows[0]["volume_ratio20"] = "NaN"
    with pytest.raises(FactorSliceError):
        consume_queue(rows, volume_gate=True)


def test_protected_year_rejected_before_facts_engines_run():
    rows = values(1)
    rows[0] = replace(rows[0], market_date=date(2025, 1, 1))
    with pytest.raises(FactorSliceError, match="OUT_OF_SCOPE"):
        build(rows)


@pytest.mark.parametrize("field,value", [("feature_id", "different.factor"),
                                        ("feature_version", "2.0.0"),
                                        ("definition_digest", "unbound"),
                                        ("source_member_digest", "unknown")])
def test_wrong_factor_or_unbound_source_is_not_accepted(field, value):
    r = fact(1)
    r[field] = value
    with pytest.raises(FactorSliceError, match="DEFINITION_OR_SOURCE_BINDING"):
        consume_queue([r], volume_gate=True)


@pytest.mark.parametrize("failure", ["deadline", "space"])
def test_preflight_budget_failure_writes_no_output(tmp_path, failure):
    import json

    repo = Path(__file__).resolve().parents[2]
    source = tmp_path/"source"
    source.mkdir()
    output = tmp_path/"output"
    budget = {"source_root": str(source), "output": str(output),
              "purpose": "RETROSPECTIVE_RESEARCH_REVIEW_QUEUE_ONLY",
              "new_source_requests": 0, "code_and_config_members": [],
              "deadline": "2000-01-01T00:00:00+00:00" if failure == "deadline"
              else "2100-01-01T00:00:00+00:00", "maximum_seconds": 60,
              "rss_bytes": 10**15, "minimum_free_bytes": 0 if failure == "deadline"
              else 10**30, "maximum_output_bytes": 1024}
    path = tmp_path/"budget.json"
    path.write_text(json.dumps(budget))
    with pytest.raises(FactorSliceError, match="EXECUTION_BUDGET"):
        run(source=source, repo=repo, output=output, budget_path=path)
    assert not output.exists()
