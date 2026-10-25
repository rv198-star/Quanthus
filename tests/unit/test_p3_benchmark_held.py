"""BH dispatch is a fixed component comparison, not a new risk policy."""

import copy
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_benchmark_held as bh

R = Path(__file__).resolve().parents[2]


def test_bh_request_keeps_source_scope_and_rights():
    request = bh.load_request(R)
    assert request["components"] == bh.COMPONENTS
    assert request["optional_paid_rights"] == "DO_NOT_SUBSCRIBE"
    assert request["scope"] == ["2012-01-04", "2021-12-30", "2021-12-31"]
    assert (
        request["conditional_low_capital_control"]["status"]
        == "PROPOSAL_ONLY_NOT_ACTIVATED_BY_THIS_REQUEST"
    )


def test_bh_dispatch_is_exact_b_entry_and_th_held_policy(monkeypatch):
    runtime = bh.BenchmarkHeldRuntime.__new__(bh.BenchmarkHeldRuntime)
    calls = []
    monkeypatch.setattr(
        bh.controls.parent.RestructuringRuntime,
        "_evaluate_entry_decision",
        lambda self, runner, **kw: calls.append(kw) or "same-decision",
    )
    assert (
        runtime._evaluate_entry_decision(None, intervention_id="BH", sentinel=17) == "same-decision"
    )
    assert calls == [{"intervention_id": "B", "sentinel": 17}]
    assert runtime._held_intervention_id("BH") is None
    for old_arm in ("B", "T", "R", "TH", "TCH", None):
        with pytest.raises(ValueError, match="CONTROL_ARM"):
            runtime._held_intervention_id(old_arm)


def test_paired_interval_keeps_pairing_and_omits_only_incomplete_tail():
    left = [0.01, -0.01] * 22 + [0.5]
    right = [0.01, -0.01] * 22 + [-0.5]
    intervals = bh.paired_intervals(left, right, 17)
    assert all(float(limit) == 0 for item in intervals.values() for limit in item.values())
    with pytest.raises(ValueError, match="BH_INTERVAL_PAIRING"):
        bh.paired_intervals(left, right[:-1], 17)


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_reference", "T"),
        ("held_reference", "R"),
        ("candidate_keys", 100),
        ("components", {"BH": {"trend_entry": False, "entry_cap": True, "held_risk": True}}),
        ("historical_PIT_claim", True),
        ("new_source_collection", True),
        ("source_manifest_sha256", "sha256:" + "0" * 64),
    ],
)
def test_repacking_does_not_change_bh_policy(monkeypatch, field, value):
    request = copy.deepcopy(bh.load_request(R))
    request[field] = value
    monkeypatch.setattr(bh, "read_json", lambda *a, **kw: (request, "ignored"))
    with pytest.raises(ValueError, match="BH_REQUEST"):
        bh.load_request(R)
