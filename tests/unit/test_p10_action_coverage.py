"""Offline action coverage never derives certainty from the presence of a bar."""

import hashlib
import json
from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse import p10_action_coverage as coverage
from tests.unit.test_p3_retrospective_bridge import INFO


def fixture():
    def dump(x):
        return json.dumps(x, sort_keys=True) + "\n"

    plan = dump(
        {
            "no_action_claim": coverage.CLAIM,
            "historical_pit_granted": False,
            "information_cutoff": INFO.isoformat(),
            "trading_dates": ["2025-01-01", "2026-09-18"],
        }
    )
    exceptions = dump([{"security_id": "unknown", "reason": "INCOMPLETE"}])
    actions = dump([])
    files = {
        name: {
            "sha256": "sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
            "bytes": len(raw.encode()),
        }
        for name, raw in [
            ("plan.json", plan),
            ("action-exceptions.json", exceptions),
            ("actions.json", actions),
        ]
    }
    manifest = dump({"files": files})
    args = dict(
        support_sha256="sha256:" + hashlib.sha256(manifest.encode()).hexdigest(),
        actions_sha256=files["actions.json"]["sha256"],
        information_cutoff=INFO,
    )
    value = coverage.freeze(
        manifest_json=manifest,
        plan_json=plan,
        exceptions_json=exceptions,
        securities=iter(["unknown", "covered"]),
        **args,
    )
    return value, args


def test_exact_coverage_preserves_exceptions_and_needs_no_source_paths():
    value, args = fixture()
    assert coverage.validate(value, securities=["covered", "unknown"], **args) == value
    assert value["unqualified_security_ids"] == ["unknown"]


@pytest.mark.parametrize(
    "change",
    [
        "manifest",
        "plan",
        "exceptions",
        "universe",
        "duplicate",
        "exception_clear",
        "scope",
        "schema",
    ],
)
def test_repackaging_cannot_change_source_claim_or_census(change):
    value, args = fixture()
    bad = deepcopy(value)
    if change in ("manifest", "plan", "exceptions"):
        key = {
            "manifest": "support_manifest_json",
            "plan": "support_plan_json",
            "exceptions": "exceptions_json",
        }[change]
        bad[key] += " "
    elif change == "universe":
        bad["security_ids"] = ["covered"]
    elif change == "duplicate":
        bad["security_ids"] = ["covered", "covered", "unknown"]
    elif change == "exception_clear":
        bad["unqualified_security_ids"] = []
    elif change == "scope":
        bad["business_through"] = "2026-09-30"
    else:
        bad["schema_version"] = "wrong"
    with pytest.raises(ValueError, match="ACTION_COVERAGE"):
        coverage.validate(bad, securities=["covered", "unknown"], **args)
