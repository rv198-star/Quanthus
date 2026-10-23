"""Once-only rolling input, exact P4 rules, and shared R2 entry eligibility."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import duckdb
import pyarrow.parquet as pq
import pytest

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy import p3_base24_bridge as strict
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as bridge
from quanthus.expert_strategy import p3_retrospective_features as features
from tests.unit.test_p3_base24_bridge import support
from tests.unit.test_p3_retrospective_bridge import INFO, ROOT, SOURCE, observation

DAYS = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(65))


def ports(*, missing=None, unknown_action=None, missing_clock=None):
    for i, day in enumerate(DAYS):
        r = observation(day)
        ratio = Decimal("0.005") if i == 60 else Decimal("0.02") if i == 61 else Decimal("0.01")
        amount = (
            Decimal("5000000")
            if i == 60
            else Decimal("20000000")
            if i == 61
            else Decimal("10000000")
        )
        r.update(
            open=Decimal("100.0000000000"),
            high=Decimal("100") * (1 + ratio),
            low=Decimal("100.0000000000"),
            close=Decimal("100.0000000000"),
            turnover=amount,
            volume=Decimal("100000.0000000000"),
        )
        if i == missing_clock:
            r.update(open_at=None, close_at=None)
        s = replace(
            support(r),
            execution_known_at=INFO,
            upper_limit_price=Decimal("110"),
            lower_limit_price=Decimal("90"),
        )
        if i == unknown_action:
            s = replace(s, adjustment_effective=None)
        yield bridge.decision_port(
            r,
            information_cutoff=INFO,
            source_manifest_sha256=SOURCE,
            support=None if i == missing else s,
            static_industry_id="static-L1",
        )


def panel(tmp_path, rows, *, end=DAYS[-1], name="one"):
    history, output = tmp_path / (name + "-TMP.parquet"), tmp_path / (name + ".parquet")
    report = features.write_history(
        rows, history, information_cutoff=INFO, source_manifest_sha256=SOURCE
    )
    with duckdb.connect(":memory:") as c:
        features.feature_panel(
            c,
            history=history,
            trading_dates=DAYS,
            scope_start=DAYS[0],
            scope_end=end,
            output=output,
        )
    return output, report, history


def test_one_panel_across_months_matches_prefix_at_T_and_shared_mask(tmp_path):
    all_ports = tuple(ports())
    output, report, history = panel(tmp_path, all_ports)
    table = pq.read_table(output)
    rows = {v["market_date"]: v for v in table.to_pylist()}
    assert report["rows"] == 65 and report["unavailable_observations"] == 0
    raw = pq.read_table(history).to_pylist()
    assert raw[0]["close"] == "100.0000000000"
    assert raw[0]["information_cutoff"] == INFO
    # A single whole-scope SQL must give the same values as each historical
    # prefix, including the Jan->Feb and Feb->Mar boundaries.
    for i in (30, 31, 59, 60, 61, 64):
        prior, _, _ = panel(tmp_path, all_ports[: i + 1], end=DAYS[i], name=f"prefix-{i}")
        assert pq.read_table(prior).to_pylist()[-1] == rows[DAYS[i]]
    feature = BaselineFeatureRow.from_mapping(rows[DAYS[61]])
    assert feature.feature_ready and feature.entry_eligible
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    mask = bridge.common_entry_mask(
        all_ports[40:62],
        expected_dates=DAYS[40:62],
        card=card,
        feature=feature,
        feature_known_at=INFO,
        feature_evidence_digest=SOURCE,
    )
    assert mask.eligible and all(mask.for_variant(v) for v in strict.VARIANTS)
    selected = features.candidate_observations(
        iter(all_ports),
        trading_dates=DAYS,
        repository=ROOT,
        source_manifest_sha256=SOURCE,
        information_cutoff=INFO,
        support_manifest_sha256=SOURCE,
    )
    assert selected["raw_triggers"] == 1 and len(selected["candidates"]) == 1
    candidate = selected["candidates"][0]
    assert candidate["signal_date"] == DAYS[61].isoformat()
    assert candidate["source_knowledge_cutoff"] > candidate["business_close"]
    assert selected["is_strict_EventRecord"] is selected["D_activated"] is False


def test_future_unknown_action_does_not_change_earlier_features(tmp_path):
    healthy, _, _ = panel(tmp_path, ports(), name="healthy")
    late, _, _ = panel(tmp_path, ports(unknown_action=63), name="late")
    old, new = pq.read_table(healthy).to_pylist(), pq.read_table(late).to_pylist()
    assert old[:63] == new[:63]
    assert new[63]["exception_active"] and not new[63]["entry_eligible"]
    assert not new[63]["feature_ready"]
    assert new[64]["scope_continuity_excluded"]


@pytest.mark.parametrize("field", ["missing", "unknown_action", "missing_clock"])
def test_unavailable_support_or_clock_does_not_qualify_affected_key(tmp_path, field):
    out, report, _ = panel(tmp_path, ports(**{field: 61}), name=field)
    r = pq.read_table(out).to_pylist()[61]
    assert not r["entry_eligible"]
    if field == "missing_clock":
        assert report["unavailable_observations"] == 1
        assert r["close_price"] is None  # no zero bar or clock was manufactured
    selected = features.candidate_observations(
        ports(**{field: 61}),
        trading_dates=DAYS,
        repository=ROOT,
        source_manifest_sha256=SOURCE,
        information_cutoff=INFO,
        support_manifest_sha256=SOURCE,
    )
    assert not selected["candidates"]


def test_duplicate_or_out_of_order_ports_and_changed_snapshot_rejected(tmp_path):
    values = tuple(ports())
    for changed in (
        (values[0], values[0]),
        (values[1], values[0]),
        (replace(values[0], information_cutoff=INFO + timedelta(seconds=1)),),
    ):
        with pytest.raises(native.InputError):
            features.candidate_observations(
                changed,
                trading_dates=DAYS,
                repository=ROOT,
                source_manifest_sha256=SOURCE,
                information_cutoff=INFO,
                support_manifest_sha256=SOURCE,
            )
    path = tmp_path / "frozen.parquet"
    path.write_bytes(b"sealed")
    with pytest.raises(native.InputError, match="RETRO_HISTORY_OUTPUT_EXISTS"):
        features.write_history(values, path, information_cutoff=INFO, source_manifest_sha256=SOURCE)
    assert path.read_bytes() == b"sealed"


@pytest.mark.parametrize("field", ["listing_id", "listing_code", "company_id"])
def test_dated_identity_segments_do_not_abort_or_bridge_a_transition(field):
    values = tuple(ports())

    def changed_at(start):
        output = []
        for i, p in enumerate(values):
            assert p.selected is not None and p.selected.candidate is not None
            candidate = p.selected.candidate
            if i >= start:
                candidate = replace(candidate, **{field: "new-identity"})
            output.append(replace(p, selected=replace(p.selected, candidate=candidate)))
        return output

    def scan(rows):
        return features.candidate_observations(
            rows, trading_dates=DAYS, repository=ROOT,
            source_manifest_sha256=SOURCE, information_cutoff=INFO,
            support_manifest_sha256=SOURCE,
        )

    # Enough same-identity history after a legitimate dated transition qualifies.
    assert len(scan(changed_at(20))["candidates"]) == 1
    # A signal whose 22-session window crosses the identity change stays blocked.
    assert not scan(changed_at(50))["candidates"]
    # A later identity change cannot erase the earlier eligible signal.
    assert len(scan(changed_at(63))["candidates"]) == 1
