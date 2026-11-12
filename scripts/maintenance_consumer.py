"""Formal maintenance fixture consumer; loads the activated rule itself."""

from __future__ import annotations

import hashlib
import json

from maintenance_store import digest


def load_rule(root):
    entry = root / "config/maintenance-consumer-rule.v1.json"
    rule = json.loads(entry.read_text())
    path = (root / rule["authority_path"]).resolve()
    if (
        rule.get("schema") != "maintenance.consumer-rule.v1"
        or not rule.get("active")
        or not path.is_relative_to(root.resolve())
        or not path.is_file()
        or path.is_symlink()
    ):
        raise ValueError("activated maintenance rule unavailable")
    return {
        "rule_id": rule["rule_id"],
        "version": rule["version"],
        "rule_path": rule["authority_path"],
        "consumed_rule_digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        "entry_path": str(entry.relative_to(root)),
        "entry_digest": hashlib.sha256(entry.read_bytes()).hexdigest(),
        "loader": "scripts/maintenance_consumer.py:load_rule",
    }


def evaluate(candidate, loaded):
    # Both fixture and ordinary consumers enter through this version-matching decision.
    if candidate.get("rule_id") != loaded["rule_id"]:
        return {"accepted": False, "reason": "rule_identity_mismatch"}
    if candidate.get("version") != loaded["version"]:
        return {"accepted": False, "reason": "rule_version_mismatch"}
    if candidate.get("consumed_rule_digest") != loaded["consumed_rule_digest"]:
        return {"accepted": False, "reason": "rule_digest_mismatch"}
    return {"accepted": True, "reason": "activated_rule_matched"}


def consume(root, req, store):
    key = req["request_id"]
    rule = load_rule(root)
    load_event = store.event(key, "rule_loaded", **rule)
    behaviors = []
    for case in req["payload"]["cases"]:
        actual = evaluate(case["input"], rule)
        event = store.event(
            key,
            "behavior_evaluated",
            case=case["name"],
            input_digest=digest(case["input"]),
            expected_accept=case["expected_accept"],
            actual=actual,
            rule_load_event_id=load_event["event_id"],
        )
        behaviors.append(
            {
                "case": case["name"],
                "input": case["input"],
                "input_digest": event["data"]["input_digest"],
                "expected_accept": case["expected_accept"],
                "actual": actual,
                "event_id": event["event_id"],
            }
        )
    matched = (
        len(behaviors) == 2
        and {x["case"] for x in behaviors} == {"bad", "good"}
        and all(x["actual"]["accepted"] == x["expected_accept"] for x in behaviors)
        and next(x for x in behaviors if x["case"] == "good")["actual"]["accepted"]
        and not next(x for x in behaviors if x["case"] == "bad")["actual"]["accepted"]
    )
    if matched and not store.consumption_count(key):
        store.event(
            key,
            "rule_consumed",
            rule_id=rule["rule_id"],
            version=rule["version"],
            consumed_rule_digest=rule["consumed_rule_digest"],
            rule_load_event_id=load_event["event_id"],
            behavior_event_ids=[x["event_id"] for x in behaviors],
            attempt_id=store.attempt(key)["attempt_id"],
        )
    return {
        "status": "pass" if matched else "failed",
        "rule": rule,
        "behaviors": behaviors,
        "observed_uses": store.consumption_count(key),
        "consumer_run_id": store.run()["receiver_run_id"],
        "parent_request_id": req["payload"]["parent_request_id"],
    }
