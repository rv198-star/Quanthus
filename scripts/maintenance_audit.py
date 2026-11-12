"""Evidence-based eight-check verifier for the bounded maintenance bootstrap."""

from __future__ import annotations

import datetime as dt
import re

from maintenance_store import digest


def runtime_satisfies(actual, required):
    if not required:
        return True
    version = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", actual or "")
    policy = re.fullmatch(r"(\d+)\.(\d+)(\+)?", required)
    if not version or not policy:
        return False
    observed = tuple(map(int, version.group(1, 2)))
    expected = tuple(map(int, policy.group(1, 2)))
    return observed >= expected if policy[3] else observed == expected


def signed(value, field):
    unsigned = dict(value)
    expected = unsigned.pop(field, None)
    return bool(expected) and digest(unsigned) == expected


def valid_chain(events):
    previous = None
    for event in events:
        if not signed(event, "event_digest") or event["previous_digest"] != previous:
            return False
        previous = event["event_digest"]
    return bool(events)


def verify(receipt, delivery, requests, config, expected, checked_at):
    """Caller obtains these exact documents by GET from their formal GitHub outlet."""
    checks = {}
    snap, result = receipt["snapshot"], receipt["result"]
    ledger = delivery["original_ledger"]
    events = ledger["events"]
    by_kind = {}
    for e in events:
        by_kind.setdefault(e["kind"], []).append(e)
    checks["1_context"] = (
        signed(receipt, "receipt_digest")
        and signed(delivery, "evidence_digest")
        and receipt["repository"] == config["repository"] == delivery["repository"]
        and receipt["environment_id"] == snap["environment_id"] == delivery["environment_id"]
        and all(
            receipt[k] == delivery[k] == requests[0]["request"][k]
            for k in ("batch_id", "challenge", "request_id")
        )
        and receipt["contract_version"] == config["contract_version"]
        and snap["adapter_source_sha"] == expected["source_sha"]
        and snap["operational_fingerprint"] == expected["operational_fingerprint"]
    )

    def parsed(value):
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

    age = (parsed(checked_at) - parsed(snap["collected_at"])).total_seconds()
    role = next(e for e in config["environments"] if e["id"] == receipt["environment_id"])
    sources = snap.get("source_status", {})
    required = {"github_work", "native_tasks", "executor", "verification", "maintenance_executions"}
    checks["2_environment"] = (
        0 <= age <= role["max_snapshot_age_seconds"]
        and parsed(snap["collected_at"]) >= parsed(receipt["received_at"])
        and not snap["missing_sources"]
        and required <= sources.keys()
        and all(sources[k]["status"] == "ok" for k in required)
        and sources["maintenance_executions"]["receiver"]["environment_id"]
        == receipt["environment_id"]
        and sources["verification"]["sources"]
        and all(
            x["evidence"]["operational_fingerprint"] == expected["operational_fingerprint"]
            and x["evidence"]["exit_code"] == 0
            and runtime_satisfies(x["evidence"].get("runtime"), config.get("verification_python"))
            for x in sources["verification"]["sources"]
        )
    )
    attempt = receipt.get("native_attempt") or {}
    starts, ends = by_kind.get("execution_started", []), by_kind.get("execution_completed", [])
    received = by_kind.get("request_received", [])
    checks["3_execution"] = (
        valid_chain(events)
        and all(e["receiver_run_id"] in ledger["receiver_runs"] for e in events)
        and all(
            r["adapter_source_sha"] == expected["source_sha"]
            and r["environment_id"] == receipt["environment_id"]
            for r in ledger["receiver_runs"].values()
        )
        and len(received) == len(starts) == len(ends) == 1
        and attempt == ledger["native_attempt"]
        and attempt.get("execution_model") == "in_process_probe"
        and received[0]["data"]["received_at"] == receipt["received_at"]
        and starts[0]["data"]["attempt_id"]
        == ends[0]["data"]["attempt_id"]
        == attempt.get("attempt_id")
        and parsed(receipt["received_at"])
        <= parsed(attempt["started_at"])
        <= parsed(attempt["completed_at"])
        <= parsed(receipt["recorded_at"])
    )
    deliveries = [e for e in events if e["kind"] in {"delivery_received", "duplicate_delivery"}]
    checks["4_duplicate"] = (
        len({x["id"] for x in requests}) >= 2
        and all(x["request"] == requests[0]["request"] for x in requests)
        and digest(requests[0]["request"]) == receipt["request_digest"] == ledger["request_digest"]
        and len({e["data"]["comment_id"] for e in deliveries}) >= 2
        and len(starts) == 1
    )
    sequence = [
        "result_persisted",
        "publication_deferred",
        "reconciliation",
        "recovery_started",
        "publication_attempt_started",
        "receipt_published",
        "receipt_readback",
    ]
    positions = [next((i for i, e in enumerate(events) if e["kind"] == k), -1) for k in sequence]
    checks["5_recovery"] = (
        all(i >= 0 for i in positions)
        and positions == sorted(positions)
        and all(
            e["data"].get(
                "receipt_digest",
                e["data"].get("original_receipt_digest", receipt["receipt_digest"]),
            )
            == receipt["receipt_digest"]
            for e in events
            if e["kind"] in set(sequence)
        )
        and ledger["ledger_handle"] == receipt["ledger_handle"]
        and delivery["receipt_digest"] == ledger["receipt_digest"] == receipt["receipt_digest"]
        and by_kind.get("publication_deferred", [{}])[0].get("receiver_run_id")
        != by_kind.get("recovery_started", [{}])[0].get("receiver_run_id")
    )
    child = result.get("consumer", {})
    child_ledger = delivery.get("consumer_ledger") or {}
    ce = child_ledger.get("events", [])
    consumed = [e for e in ce if e["kind"] == "rule_consumed"]
    loads = [e for e in ce if e["kind"] == "rule_loaded"]
    behaviors = child.get("behaviors", [])
    checks["6_rule_consumer"] = (
        valid_chain(ce)
        and len(consumed) == 1
        and loads
        and child_ledger["identity"]["parent_request_id"] == receipt["request_id"]
        and child["native_attempt"]["execution_model"] == "queued_rule_consumer_fixture"
        and child["native_attempt"] == child_ledger["native_attempt"]
        and child["native_attempt"]["receiver_run_id"] != child_ledger["identity"]["queued_run_id"]
        and attempt.get("started_at")
        and parsed(child["native_attempt"]["started_at"]) >= parsed(attempt["started_at"])
        and len(behaviors) == 2
        and {x["case"] for x in behaviors} == {"bad", "good"}
        and all(
            x["actual"]["accepted"] == x["expected_accept"]
            and digest(x["input"]) == x["input_digest"]
            for x in behaviors
        )
        and next(x for x in behaviors if x["case"] == "good")["actual"]["accepted"]
        and not next(x for x in behaviors if x["case"] == "bad")["actual"]["accepted"]
        and child["rule"] == expected["activated_rule"]
        and child["rule"]["consumed_rule_digest"]
        == consumed[0]["data"]["consumed_rule_digest"]
        == loads[0]["data"]["consumed_rule_digest"]
    )
    effect = result.get("effect", {})
    checks["7_effect"] = (
        effect.get("baseline_ref")
        and effect.get("eligibility")
        and effect.get("evidence_refs")
        and effect.get("observed_uses") == len(consumed) == child.get("observed_uses") == 1
        and effect.get("decision") == "keep"
        and effect.get("consumer_run_id") == child.get("consumer_run_id")
        and any(e["kind"] == "consumer_readback" and e["data"]["matched"] for e in events)
    )
    checks["8_readback"] = (
        delivery["receipt_url"] == ledger["publication"]
        and any(
            e["kind"] == "receipt_readback"
            and e["data"].get("original_store_match")
            and e["data"]["receipt_digest"] == receipt["receipt_digest"]
            for e in events
        )
        and signed(delivery, "evidence_digest")
        and signed(receipt, "receipt_digest")
    )
    checks = {k: bool(v) for k, v in checks.items()}
    return {
        "schema": "maintenance.acceptance.v1",
        "repository": receipt["repository"],
        "environment_id": receipt["environment_id"],
        "batch_id": receipt["batch_id"],
        "challenge": receipt["challenge"],
        "request_id": receipt["request_id"],
        "checked_at": checked_at,
        "checks": checks,
        "status": "READY" if all(checks.values()) else "BLOCKED",
        "scope": "controlled-maintenance-bootstrap",
        "receipt_digest": receipt["receipt_digest"],
        "delivery_evidence_digest": delivery["evidence_digest"],
    }
