"""E5 strict loader for the personal paper-replay use receipt.

Binds config/research/r11/d1b-20261124/personal-paper-replay-use-receipt.v1.json.
This validator is the ONLY path E5 may use to decide whether the first-stage
historical replay actions are activated.  It refuses activation fail-closed on:

* adoption_state != "adopted" (proposed / null / missing),
* schema, receipt_id, purpose_mode or account-use mismatch,
* a missing or unmatched caller-supplied expected pin in ANY binding block
  (source / rule / consumer / execution / replay_scope / replay_run), or
* an action state outside the declared set, a reduce/full_exit marked
  executable without a bound E8 + real P12 v2 pin, or the replay scope flags
  (daily_live / real_account / automatic_trading) not all False.

The caller (E5) MUST supply the versioned expected pins it independently
verified for the current run; an adopted receipt is never trusted on its own
claims.  Missing expected keys are treated as unverifiable, not optional.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

from quanthus.product_daily.contracts import ProductDailyError

USE_RECEIPT_SCHEMA = "quanthus.p11.personal-paper-replay-use.v1"
RECEIPT_ID = "P11-PAPER-REPLAY-202610-v1"
PURPOSE_MODE = "historical_replay"
PERMITTED_ACCOUNT_USE = "personal_simulation"
INPUT_GRADE = "ENGINEERING_REPLAY_AS_OBSERVED"
ADOPTED = "adopted"

ACTIONS = ("new_entry", "holding", "reduce", "full_exit")
ACTION_STATES = ("execute", "display_only", "pending_validation", "refused")

REQUIRED_SOURCE_PINS = ("package_id", "manifest_sha256", "member_set_sha256")
REQUIRED_RULE_PINS = (
    "code_commit",
    "code_tree",
    "rule_card_sha256",
    "expert_topology_sha256",
    "feature_registry_sha256",
    "history_input_schema",
    "history_entry",
    "replay_entry",
)
REQUIRED_CONSUMER_PINS = (
    "decision_set_schema",
    "loader_entry",
    "source_commit",
    "source_tree",
    "pin_sha256",
)
REQUIRED_EXECUTION_PINS = (
    "ledger_namespace_rule",
    "model_id",
    "model_version",
    "logical_clock_separate_from_actual_time",
)
# Scope keys that must match the caller-verified replay batch exactly.
REQUIRED_SCOPE_KEYS = (
    "market_date_from",
    "market_date_through",
    "daily_bar_context_sessions",
    "open_sessions_manifest_sha256",
    "protected_exclusions_manifest_sha256",
)


def _fail(code: str, message: str) -> NoReturn:
    raise ProductDailyError(code, message)


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_use_receipt", f"{label} must be an object")
    return dict(value)


@dataclass(frozen=True)
class ReplayUseReceipt:
    """A strictly validated adopted replay use receipt."""

    raw: Mapping[str, Any]

    @property
    def receipt_id(self) -> str:
        return str(self.raw["receipt_id"])

    @property
    def adoption_state(self) -> str:
        return str(self.raw["adoption_state"])

    def action_state(self, action: str) -> str:
        return str(self.raw["execution_binding"][action])

    def permits(self, action: str) -> bool:
        if action not in ACTIONS:
            _fail("unknown_action", action)
        return self.action_state(action) == "execute"

    def assert_replay_only(self) -> None:
        for flag in ("daily_live_use", "real_account_use", "automatic_trading_use"):
            if self.raw.get(flag) is not False:
                _fail(
                    "replay_scope_violation",
                    f"{flag} must be false for the historical_replay use",
                )


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_use_receipt", f"{label} must be a non-empty string")
    return value


def _verify_pin_block(
    raw: Mapping[str, Any],
    section: str,
    required_keys: tuple[str, ...],
    expected: Mapping[str, Any] | None,
) -> dict[str, Any]:
    block = _obj(raw.get(section), section)
    for key in required_keys:
        value = block.get(key)
        if value is None or (isinstance(value, str) and not value):
            _fail(
                "use_receipt_pin_missing",
                f"{section}.{key} is null/empty; an adopted receipt binds a real pin",
            )
        if expected is not None and expected.get(key) is not None and value != expected[key]:
            _fail(
                "use_receipt_pin_mismatch",
                f"{section}.{key}={value!r} differs from the verified pin "
                f"{expected[key]!r}",
            )
    return block


def load_use_receipt(
    raw: Mapping[str, Any],
    *,
    expected_source: Mapping[str, Any] | None = None,
    expected_rule: Mapping[str, Any] | None = None,
    expected_consumer: Mapping[str, Any] | None = None,
    expected_execution: Mapping[str, Any] | None = None,
    expected_scope: Mapping[str, Any] | None = None,
) -> ReplayUseReceipt:
    """Strict-validate and return a ReplayUseReceipt; raise on any breach.

    Every supplied expected_* mapping is caller-verified truth for the current
    run; a non-None expected value must equal the receipt's value exactly.
    Activation is refused unless the caller supplies the complete expected pin
    set for ALL binding blocks AND each one matches.
    """
    doc = _obj(raw, "use_receipt")
    if doc.get("schema_version") != USE_RECEIPT_SCHEMA:
        _fail("use_receipt_schema_mismatch", f"schema_version must be {USE_RECEIPT_SCHEMA}")
    if doc.get("receipt_id") != RECEIPT_ID:
        _fail("use_receipt_id_mismatch", f"receipt_id must be {RECEIPT_ID}")
    if doc.get("adoption_state") != ADOPTED:
        _fail(
            "use_receipt_not_adopted",
            "adoption_state={!r}; proposed/null receipts refuse activation".format(
                doc.get("adoption_state")
            ),
        )
    if doc.get("purpose_mode") != PURPOSE_MODE:
        _fail("use_receipt_purpose_mismatch", f"purpose_mode must be {PURPOSE_MODE}")
    if doc.get("permitted_account_use") != PERMITTED_ACCOUNT_USE:
        _fail(
            "use_receipt_account_mismatch",
            f"permitted_account_use must be {PERMITTED_ACCOUNT_USE}",
        )

    scope = _obj(doc.get("replay_scope"), "replay_scope")
    if scope.get("input_grade") != INPUT_GRADE:
        _fail(
            "use_receipt_grade_mismatch",
            "input_grade must be ENGINEERING_REPLAY_AS_OBSERVED",
        )
    if scope.get("strategy_performance_evaluation") is not False:
        _fail("use_receipt_scope_violation", "strategy_performance_evaluation must be false")
    for date_key in ("market_date_from", "market_date_through"):
        try:
            date.fromisoformat(_require_text(scope.get(date_key), f"replay_scope.{date_key}"))
        except ValueError:
            _fail("invalid_use_receipt", f"replay_scope.{date_key} must be an ISO date")
    if scope.get("daily_bar_context_sessions") != 61:
        _fail("use_receipt_scope_mismatch", "daily_bar_context_sessions must be 61")
    if not isinstance(scope.get("venues"), list) or not scope["venues"]:
        _fail("invalid_use_receipt", "replay_scope.venues must be a non-empty list")
    for key in ("open_sessions_manifest_sha256", "protected_exclusions_manifest_sha256"):
        if scope.get(key) is None:
            _fail("use_receipt_pin_missing", f"replay_scope.{key} is null")

    _verify_pin_block(doc, "source_binding", REQUIRED_SOURCE_PINS, expected_source)
    _verify_pin_block(doc, "rule_binding", REQUIRED_RULE_PINS, expected_rule)
    _verify_pin_block(doc, "consumer_binding", REQUIRED_CONSUMER_PINS, expected_consumer)
    _verify_pin_block(doc, "execution_binding", REQUIRED_EXECUTION_PINS, expected_execution)
    # The replay scope's identity pins (date range, bar context, manifests) are
    # verified like the other blocks: a mismatched expected value fails closed.
    if expected_scope is not None:
        for key in REQUIRED_SCOPE_KEYS:
            if expected_scope.get(key) is not None and scope.get(key) != expected_scope[key]:
                _fail(
                    "use_receipt_pin_mismatch",
                    f"replay_scope.{key}={scope.get(key)!r} differs from the "
                    f"verified pin {expected_scope[key]!r}",
                )

    # The replay run binding (account, scenario, ledger) is verified only when
    # the caller supplies it; an adopted receipt must still carry the block.
    run_binding = _obj(doc.get("replay_run"), "replay_run")
    for key in ("scenario_id", "account_id", "ledger_namespace"):
        if run_binding.get(key) is None:
            _fail("use_receipt_pin_missing", f"replay_run.{key} is null")

    execution = _obj(doc.get("execution_binding"), "execution_binding")
    if execution.get("logical_clock_separate_from_actual_time") is not True:
        _fail(
            "use_receipt_clock_violation",
            "logical_clock_separate_from_actual_time must be true",
        )
    for action in ACTIONS:
        state = execution.get(action)
        if state not in ACTION_STATES:
            _fail(
                "use_receipt_action_state",
                f"execution_binding.{action}={state!r} is not a declared action state",
            )
    # A reduce / full_exit may not be executable unless the receipt binds the
    # accepted E8 regression AND a real P12 v2 consumer pin.
    for risky in ("reduce", "full_exit"):
        if execution.get(risky) == "execute":
            e8 = doc.get("e8_binding") or {}
            if e8.get("regression_receipt_sha256") is None:
                _fail(
                    "use_receipt_unverified_exit",
                    f"{risky}=execute requires a bound E8 regression receipt",
                )
            consumer = doc.get("consumer_binding") or {}
            if consumer.get("pin_sha256") is None or (
                expected_consumer is not None
                and expected_consumer.get("pin_sha256") is not None
                and consumer.get("pin_sha256") != expected_consumer["pin_sha256"]
            ):
                _fail(
                    "use_receipt_unverified_exit",
                    f"{risky}=execute requires a verified P12 v2 consumer pin",
                )

    receipt = ReplayUseReceipt(raw=doc)
    receipt.assert_replay_only()
    return receipt


def activate_use_receipt(
    raw: Mapping[str, Any],
    *,
    expected_source: Mapping[str, Any],
    expected_rule: Mapping[str, Any],
    expected_consumer: Mapping[str, Any],
    expected_execution: Mapping[str, Any],
    expected_scope: Mapping[str, Any],
    expected_replay_run: Mapping[str, Any],
) -> ReplayUseReceipt:
    """Activation entry: every expected binding block is required and verified.

    E5 calls this (not load_use_receipt) when deciding whether actions may run.
    A None expected_* mapping is itself a refusal: there is no adopted replay
    use without the caller's independently verified pins for every block.
    """
    for name, value in (
        ("expected_source", expected_source),
        ("expected_rule", expected_rule),
        ("expected_consumer", expected_consumer),
        ("expected_execution", expected_execution),
        ("expected_scope", expected_scope),
        ("expected_replay_run", expected_replay_run),
    ):
        if value is None:
            _fail(
                "use_receipt_expected_missing",
                f"{name} is required to activate a replay use receipt",
            )
    receipt = load_use_receipt(
        raw,
        expected_source=expected_source,
        expected_rule=expected_rule,
        expected_consumer=expected_consumer,
        expected_execution=expected_execution,
        expected_scope=expected_scope,
    )
    run_binding = receipt.raw["replay_run"]
    for key, expected in expected_replay_run.items():
        if expected is not None and run_binding.get(key) != expected:
            _fail(
                "use_receipt_run_mismatch",
                f"replay_run.{key}={run_binding.get(key)!r} != {expected!r}",
            )
    return receipt


def load_use_receipt_file(path: str | Path, **kwargs: Any) -> ReplayUseReceipt:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _fail("use_receipt_unreadable", f"{path}: {exc}")
    return load_use_receipt(raw, **kwargs)
