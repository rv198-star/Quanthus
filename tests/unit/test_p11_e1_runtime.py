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
