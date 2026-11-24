"""e1_runtime: production entry -- frozen assets + same-job panel + P12 path.

Synthetic fixtures only; never reads the physical Base.  The panel report is
asserted as the SAME job's actual computation (its row/feature_ready counts
and content digest come from the run's real features_by_security, not a
fixture label).
"""

from __future__ import annotations

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.e1_runtime import (
    E1RuleAssets,
    load_e1_rule_assets,
    run_e1_from_history,
)
from tests.unit.test_p11_history_input import (
    _run_kwargs,
    _trend_envelope,
)

ROOT = find_project_root()


@pytest.fixture(scope="module")
def assets() -> E1RuleAssets:
    return load_e1_rule_assets(ROOT)


# --- asset loading ------------------------------------------------------------


def test_load_assets_returns_frozen_identities(assets):
    assert assets.card_digest.startswith("sha256:")
    assert assets.registry_digest.startswith("sha256:")
    assert assets.topology_digest.startswith("sha256:")
    assert type(assets.runner).__name__ == "FormalStrategyRunner"


def test_load_assets_pin_match_passes(assets):
    again = load_e1_rule_assets(
        ROOT,
        expected_card_digest=assets.card_digest,
        expected_registry_digest=assets.registry_digest,
    )
    assert again.card_digest == assets.card_digest


def test_load_assets_pin_mismatch_refuses(assets):
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets(ROOT, expected_card_digest="sha256:" + "0" * 64)


def test_load_assets_missing_root_refuses():
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets("/nonexistent-e1-root")


# --- same-job panel + run ----------------------------------------------------


def test_run_e1_from_history_produces_real_panel(assets):
    kw = _run_kwargs()
    output, report = run_e1_from_history(
        _trend_envelope(),
        assets=assets,
        facts=kw["facts"],
        policy=kw["policy"],
        account=kw["account"],
        run_id="e1-test-1",
        snapshot_id="snap-e1",
        snapshot_manifest_digest=sha256_digest({"e1": 1}),
        split_policy=kw["split_policy"],
        source_policy=kw["source_policy"],
        clock=kw["clock"],
    )
    # The panel is the actual same-job computation: rows/ready/eligible come
    # from the run's features_by_security, and the content digest binds the
    # real feature bytes.
    assert report.row_count == 1
    assert report.feature_ready_count == 1
    assert report.entry_eligible_count == 1
    assert report.data_content_digest.startswith("sha256:")
    assert report.digest.startswith("sha256:")
    # The run produced a real entry through the genuine chain.
    assert output.receipt is not None
    assert output.result.disposition.value == "publishable"
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries


def test_run_e1_rejects_empty_universe(assets):
    kw = _run_kwargs()
    env = _trend_envelope()
    env["securities"] = []
    with pytest.raises(ProductDailyError):
        run_e1_from_history(
            env,
            assets=assets,
            facts=kw["facts"],
            policy=kw["policy"],
            account=kw["account"],
            run_id="e1-test-2",
            snapshot_id="snap-e1",
            snapshot_manifest_digest=sha256_digest({"e1": 2}),
            split_policy=kw["split_policy"],
            source_policy=kw["source_policy"],
            clock=kw["clock"],
        )


# --- R2: real panel on the replay path + verified asset identity ---------------


def test_r2_replay_day_binds_real_panel_not_placeholder(assets):
    # Root R2 probe: run_e1_replay_day must sign the actual same-job panel,
    # not the zero-row placeholder.  Two inputs that change real features get
    # two different real reports.
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    out1 = e_run_e1_replay(req, f.T1, assets)
    other = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    other["securities"][0]["bars"][-10]["volume"] = "91234"
    out2 = e_run_e1_replay(other, f.T1, assets)
    placeholder = _ph(assets)
    d1 = out1["source_closure"].get("feature_panel_report_digest")
    d2 = out2["source_closure"].get("feature_panel_report_digest")
    assert d1 != placeholder.digest
    assert d1 != d2
    assert out1.get("publishable") is True


def e_run_e1_replay(req, day, assets):
    from quanthus.product_daily.recommendation.e1_runtime import run_e1_replay_day

    return run_e1_replay_day(
        req, day, assets=assets, snapshot_id="s", snapshot_manifest_digest="sha256:" + "a" * 64
    )


def _ph(assets):
    from quanthus.product_daily.recommendation.e1_runtime import _replay_panel_placeholder

    return _replay_panel_placeholder(assets)


def test_r2_wrong_code_commit_refused(assets):
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets(
            ROOT,
            expected_code_commit="1" * 40,
            expected_code_tree="2" * 40,
        )


def test_r2_default_call_no_clock_not_crash(assets):
    kw = _run_kwargs()
    output, report = run_e1_from_history(
        _trend_envelope(),
        assets=assets,
        facts=kw["facts"],
        policy=kw["policy"],
        account=kw["account"],
        run_id="e1-noclock",
        snapshot_id="s",
        snapshot_manifest_digest=sha256_digest({"n": 1}),
        source_policy=kw["source_policy"],
        # clock omitted -> default logical decision instant, never a TypeError
    )
    assert output.receipt is not None
    assert report.row_count == 1


def test_r3_replay_day_injects_equivalent_day_anchor(assets):
    """run_e1_replay_day carries anchor_mode='equivalent_day': an open_at=null
    session on an old continuously-listed security resolves by the day-bound
    proof, named on the receipt's membership_proof -- not a strict isolation."""
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    # Null out open_at on a run of mid sessions (the real October Base shape):
    # the listing predates the bound calendar so every such session resolves
    # wholly inside the listing interval.
    for row in req["calendar_rows"]:
        if row["session_date"] in {d.isoformat() for d in days[40:60]}:
            row["open_at"] = None
    out = e_run_e1_replay(req, f.T1, assets)
    receipts = out["source_closure"].get("history_receipts") or []
    assert receipts
    proof = receipts[0].get("membership_proof") or {}
    assert proof.get("anchor_mode") == "equivalent_day"
    assert proof.get("equivalent_day_sessions")
