"""Dual-revision publication contract: audit code and materialization are distinct."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base_build import validate_audit_job
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
from quanthus.pipelines.securities_warehouse.base_build_publication import (
    CONTRACT,
    DUAL_REVISION_CONTRACT,
    _require_audit,
    pair_gate,
    require_gate_revisions,
)
from quanthus.pipelines.securities_warehouse.base_build_quality import EXPECTED, POLICY

MATERIALIZED = "a" * 40
AUDIT = "f" * 40
GOVERNED = {
    "SZSE-300114-20250217-volume-coverage": "ACCEPTED_MINUTE_LOT_GRANULARITY",
    "SZSE-002336-20200617-tradability": "REPAIRED_IN_PREDECESSOR",
    "SZSE-002500-20200617-tradability": "REPAIRED_IN_PREDECESSOR",
    "SSE-688065-20230615-tradability": "ACCEPTED_PERMANENT_HISTORICAL_LIMITATION",
}


def contract():
    return delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.5.1",
        predecessor_version="2.4",
        through="2026-10-31",
    )


def historical(*, pending: str | None = None, policy_sha: str | None = None):
    cases = []
    for cid, venue, code, day in sorted(EXPECTED):
        cases.append(
            {
                "id": cid,
                "venue": venue,
                "stock_code": code,
                "market_date": day,
                "observations_present": True,
                "scope_registered": True,
                "status": "PENDING_GOVERNED_DISPOSITION"
                if cid == pending
                else GOVERNED[cid],
                "facts_or_grades_changed": False,
                "held_added": 0,
            }
        )
    gate = {
        "contract": "p10-standard-base-historical-gates-v2",
        "cases": cases,
        "publication_eligible": pending is None,
        "facts_or_grades_changed": False,
        "held_added": 0,
        "policy_sha256": policy_sha or file_sha(POLICY),
        "historical_disposition": (
            "PASS_ALL_REGISTERED_CASES_GOVERNED"
            if pending is None
            else "PENDING_REGISTERED_CASE_DISPOSITION"
        ),
    }
    gate["identity"] = identity(gate)
    return gate


def receipt(*, pending: str | None = None, policy_sha: str | None = None):
    frozen = "sha256:" + "b" * 64
    ods = {
        "quality_held_added": 0,
        "period_admission_identity": "sha256:" + "c" * 64,
        "input_identity": frozen,
        "materialization_sha256": "sha256:" + "d" * 64,
    }
    ods["identity"] = identity(ods)
    dwd = {
        "assessment": {"build_eligible": True},
        "global_keys_versions_references_lineage": "PASS",
        "materialization_sha256": "sha256:" + "e" * 64,
    }
    dwd["identity"] = digest(dwd)
    value = {
        "contract": "p10-standard-base-pair-audit-v1",
        "binding": {
            "code_revision": MATERIALIZED,
            "policy_identity": identity(contract()),
            "target_version": contract()["base_version"],
            "through_date": contract()["business_through"],
            "frozen_input_identity": frozen,
        },
        "input_identity": frozen,
        "source_replay": "PASS_ALL_PERIOD_SOURCES_AND_TYPED_DELTA",
        "replay_identity": ods["period_admission_identity"],
        "ODS": ods,
        "DWD": dwd,
        "closure": {"table_counts": {f"table-{i}": 1 for i in range(33)}},
        "publication_eligible": pending is None,
        "historical_gate": historical(pending=pending, policy_sha=policy_sha),
    }
    value["identity"] = identity(value)
    return value


def test_governed_registered_cases_no_longer_block_publication():
    # Regression for BUILD_PUBLICATION_HISTORICAL_REGISTRATION_UNRESOLVED: gate v2
    # deliberately registers all four governed cases with scope_registered true.
    _require_audit(receipt(), contract(), AUDIT, materialization_revision=MATERIALIZED)


def forged_pending_case():
    """Governed receipt whose gate claims PASS while one case is still pending."""
    value = receipt()
    gate = value["historical_gate"]
    gate["cases"][1]["status"] = "PENDING_GOVERNED_DISPOSITION"
    gate["identity"] = identity({k: v for k, v in gate.items() if k != "identity"})
    value["identity"] = identity({k: v for k, v in value.items() if k != "identity"})
    return value


def test_pending_registered_case_still_blocks_publication():
    with pytest.raises(BoundedError, match="HISTORICAL_REGISTRATION_UNRESOLVED"):
        _require_audit(
            forged_pending_case(),
            contract(),
            AUDIT,
            materialization_revision=MATERIALIZED,
        )


def test_pending_gate_verdict_is_rejected_before_the_case_scan():
    value = receipt(pending="SZSE-002336-20200617-tradability")
    with pytest.raises(BoundedError, match="FULL_SOURCE_AND_QUALITY_AUDIT_REQUIRED"):
        _require_audit(value, contract(), AUDIT, materialization_revision=MATERIALIZED)


def test_policy_hash_mismatch_still_blocks_publication():
    with pytest.raises(BoundedError, match="HISTORICAL_REGISTRATION_UNRESOLVED"):
        _require_audit(
            receipt(policy_sha="sha256:" + "0" * 64),
            contract(),
            AUDIT,
            materialization_revision=MATERIALIZED,
        )


def test_audit_receipt_must_bind_the_materialization_revision():
    with pytest.raises(BoundedError, match="FULL_SOURCE_AND_QUALITY_AUDIT_REQUIRED"):
        _require_audit(receipt(), contract(), AUDIT, materialization_revision="9" * 40)


def test_pair_gate_records_both_revisions_only_when_they_differ():
    dual = pair_gate(receipt(), revision=AUDIT, materialization_revision=MATERIALIZED)
    assert dual["contract"] == DUAL_REVISION_CONTRACT
    assert dual["audit_revision"] == AUDIT
    assert dual["materialization_revision"] == MATERIALIZED
    assert dual["identity"] == identity(
        {k: v for k, v in dual.items() if k != "identity"}
    )
    single = pair_gate(receipt(), revision=MATERIALIZED)
    assert single["contract"] == CONTRACT
    assert "audit_revision" not in single and "materialization_revision" not in single
    with pytest.raises(BoundedError, match="MATERIALIZATION_REVISION_CHANGED"):
        pair_gate(receipt(), revision=AUDIT, materialization_revision="9" * 40)


def test_gate_revision_binding_rejects_mismatched_and_degenerate_pairs():
    dual = pair_gate(receipt(), revision=AUDIT, materialization_revision=MATERIALIZED)
    assert require_gate_revisions(dual, AUDIT) == MATERIALIZED
    with pytest.raises(BoundedError, match="DUAL_REVISION_BINDING_CHANGED"):
        require_gate_revisions(dual, MATERIALIZED)
    degenerate = pair_gate(
        receipt(), revision=AUDIT, materialization_revision=MATERIALIZED
    )
    degenerate["audit_revision"] = MATERIALIZED
    degenerate["identity"] = identity(
        {k: v for k, v in degenerate.items() if k != "identity"}
    )
    with pytest.raises(BoundedError, match="DUAL_REVISION_BINDING_CHANGED"):
        require_gate_revisions(degenerate, MATERIALIZED)
    single = pair_gate(receipt(), revision=MATERIALIZED)
    with pytest.raises(BoundedError, match="SINGLE_REVISION_BINDING_CHANGED"):
        require_gate_revisions(single, AUDIT)


def test_audit_job_accepts_only_a_full_optional_materialization_revision():
    job = {
        "contract": "p10-standard-base-pair-audit-job-v1",
        "builder_revision": AUDIT,
        "ods": "/data/ODS",
        "dwd": "/data/DWD",
        "work_root": "/var/tmp/audit/output",
        "frozen_input_identity": "sha256:" + "b" * 64,
        "environment_lock": "/opt/environment.json",
        "wheelhouse": "/data/wheelhouse",
        "limits": {"allocated_bytes": 1024, "free_floor_bytes": 1},
        "denied_input_roots": ["/data/original"],
    }
    validate_audit_job(job)
    validate_audit_job({**job, "materialization_revision": MATERIALIZED})
    with pytest.raises(BoundedError, match="AUDIT_JOB_SCHEMA_OR_IDENTITY_INVALID"):
        validate_audit_job({**job, "materialization_revision": "not-a-sha"})
    with pytest.raises(BoundedError, match="AUDIT_JOB_SCHEMA_OR_IDENTITY_INVALID"):
        validate_audit_job({**job, "extra": 1})
