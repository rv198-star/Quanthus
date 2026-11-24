"""paper_replay_use: E5 strict loader for the adopted replay use receipt."""

from __future__ import annotations

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.paper_replay_use import (
    RECEIPT_ID,
    activate_use_receipt,
    load_use_receipt,
)


def _receipt(**over):
    doc = {
        "schema_version": "quanthus.p11.personal-paper-replay-use.v1",
        "receipt_id": "P11-PAPER-REPLAY-202610-v1",
        "adoption_state": "adopted",
        "purpose_mode": "historical_replay",
        "permitted_account_use": "personal_simulation",
        "replay_scope": {
            "market_date_from": "2026-10-01",
            "market_date_through": "2026-10-31",
            "venues": ["SSE", "SZSE"],
            "open_sessions_manifest_sha256": "sha256:" + "a" * 64,
            "protected_exclusions_manifest_sha256": "sha256:" + "b" * 64,
            "daily_bar_context_sessions": 61,
            "input_grade": "ENGINEERING_REPLAY_AS_OBSERVED",
            "strategy_performance_evaluation": False,
        },
        "source_binding": {
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "manifest_sha256": "sha256:" + "8" * 64,
            "member_set_sha256": "sha256:" + "c" * 64,
        },
        "rule_binding": {
            "code_commit": "abc123",
            "code_tree": "def456",
            "rule_card_sha256": "sha256:" + "d" * 64,
            "expert_topology_sha256": "sha256:" + "e" * 64,
            "feature_registry_sha256": "sha256:" + "f" * 64,
            "history_input_schema": "p11.raw-history-input.v1",
            "history_entry": (
                "quanthus.product_daily.recommendation."
                "history_input.run_daily_advice_from_history"
            ),
            "replay_entry": "quanthus.product_daily.recommendation.paper_replay.run_replay_request",
        },
        "consumer_binding": {
            "decision_set_schema": "p12.decision-set.v2",
            "loader_entry": "quanthus.p12_daily.decision_loader.load_decision_set",
            "source_commit": "aaa",
            "source_tree": "bbb",
            "pin_sha256": "sha256:" + "9" * 64,
        },
        "execution_binding": {
            "ledger_namespace_rule": "historical-replay:{scenario_id}",
            "model_id": "t1-next-open-v1",
            "model_version": "1.0.0",
            "logical_clock_separate_from_actual_time": True,
            "new_entry": "execute",
            "holding": "execute",
            "reduce": "display_only",
            "full_exit": "display_only",
        },
        "replay_run": {
            "scenario_id": "paper-replay-202610",
            "account_id": "replay-paper-202610",
            "ledger_namespace": "historical-replay:paper-replay-202610",
        },
        "activation_requires": ["adoption_state_adopted"],
        "daily_live_use": False,
        "real_account_use": False,
        "automatic_trading_use": False,
    }
    doc.update(over)
    return doc


def test_adopted_receipt_loads_and_permits():
    receipt = load_use_receipt(_receipt())
    assert receipt.receipt_id == RECEIPT_ID
    assert receipt.permits("new_entry") is True
    assert receipt.permits("holding") is True
    assert receipt.permits("reduce") is False  # display_only
    assert receipt.permits("full_exit") is False
    receipt.assert_replay_only()


def test_proposed_receipt_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(_receipt(adoption_state="proposed"))


def test_null_adoption_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(_receipt(adoption_state=None))


def test_wrong_schema_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(_receipt(schema_version="other"))


def test_wrong_purpose_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(_receipt(purpose_mode="daily_live"))


def test_real_account_use_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(_receipt(real_account_use=True))


def test_null_pin_refuses():
    doc = _receipt()
    doc["consumer_binding"]["pin_sha256"] = None
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)


def test_null_scope_manifest_refuses():
    doc = _receipt()
    doc["replay_scope"]["open_sessions_manifest_sha256"] = None
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)


def test_expected_pin_mismatch_refuses():
    with pytest.raises(ProductDailyError):
        load_use_receipt(
            _receipt(),
            expected_consumer={"pin_sha256": "sha256:" + "0" * 64},
        )


def test_expected_pin_match_passes():
    doc = _receipt()
    receipt = load_use_receipt(
        doc,
        expected_consumer={"pin_sha256": doc["consumer_binding"]["pin_sha256"]},
        expected_scope={"market_date_from": "2026-10-01"},
    )
    assert receipt.adoption_state == "adopted"


def test_unknown_action_state_refuses():
    doc = _receipt()
    doc["execution_binding"]["reduce"] = "execute_when_owner_looks_away"
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)


def test_unknown_action_permits_refuses():
    receipt = load_use_receipt(_receipt())
    with pytest.raises(ProductDailyError):
        receipt.permits("sentiment_buy")


def test_clock_binding_must_hold():
    doc = _receipt()
    doc["execution_binding"]["logical_clock_separate_from_actual_time"] = False
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)


def test_bar_window_must_be_61():
    doc = _receipt()
    doc["replay_scope"]["daily_bar_context_sessions"] = 120
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)


# --- activation: expected pins required ---------------------------------------


def _expected(doc):
    return {
        "expected_source": doc["source_binding"],
        "expected_rule": doc["rule_binding"],
        "expected_consumer": doc["consumer_binding"],
        "expected_execution": {
            k: v for k, v in doc["execution_binding"].items() if k != "reduce"
        },
        "expected_scope": doc["replay_scope"],
        "expected_replay_run": doc["replay_run"],
    }


def test_activate_requires_all_expected_blocks():
    doc = _receipt()
    exp = _expected(doc)
    exp["expected_consumer"] = None
    with pytest.raises(ProductDailyError):
        activate_use_receipt(doc, **exp)


def test_activate_missing_replay_run_expected_refuses():
    doc = _receipt()
    exp = _expected(doc)
    exp["expected_replay_run"] = None
    with pytest.raises(ProductDailyError):
        activate_use_receipt(doc, **exp)


def test_activate_all_empty_expected_refuses():
    # Root NORMAL-ENTRY finding: six EMPTY dicts must not activate.  An empty
    # mapping is not a verified pin set.
    doc = _receipt()
    with pytest.raises(ProductDailyError):
        activate_use_receipt(
            doc,
            expected_source={},
            expected_rule={},
            expected_consumer={},
            expected_execution={},
            expected_scope={},
            expected_replay_run={},
        )


def test_activate_one_empty_expected_refuses():
    doc = _receipt()
    exp = _expected(doc)
    exp["expected_execution"] = {}
    with pytest.raises(ProductDailyError):
        activate_use_receipt(doc, **exp)


def test_activate_full_expected_passes():
    doc = _receipt()
    receipt = activate_use_receipt(doc, **_expected(doc))
    assert receipt.adoption_state == "adopted"
    assert receipt.permits("new_entry") is True


def test_activate_mismatched_replay_run_refuses():
    doc = _receipt()
    exp = _expected(doc)
    exp["expected_replay_run"] = dict(doc["replay_run"], account_id="daily-live")
    with pytest.raises(ProductDailyError):
        activate_use_receipt(doc, **exp)


def test_activate_mismatched_scope_refuses():
    doc = _receipt()
    exp = _expected(doc)
    exp["expected_scope"] = dict(doc["replay_scope"], market_date_from="2026-11-01")
    with pytest.raises(ProductDailyError):
        activate_use_receipt(doc, **exp)


def test_reduce_execute_requires_e8_and_consumer_pin():
    doc = _receipt()
    doc["execution_binding"]["reduce"] = "execute"
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc, expected_consumer=doc["consumer_binding"])


def test_reduce_execute_passes_with_e8_and_verified_consumer():
    doc = _receipt()
    doc["execution_binding"]["reduce"] = "execute"
    doc["e8_binding"] = {"regression_receipt_sha256": "sha256:" + "7" * 64}
    receipt = load_use_receipt(doc, expected_consumer=doc["consumer_binding"])
    assert receipt.permits("reduce") is True


def test_missing_replay_run_block_refuses():
    doc = _receipt()
    del doc["replay_run"]
    with pytest.raises(ProductDailyError):
        load_use_receipt(doc)
