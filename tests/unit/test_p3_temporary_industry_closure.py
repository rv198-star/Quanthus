"""Synthetic source composition contracts; no market or strategy values."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_temporary_industry_closure as module
from quanthus.industry_mapping import load_static_industry_mapping


def identity(security, code="123456"):
    return {
        "security_id": security,
        "company_id": "company_" + security,
        "listing_bindings": [
            {
                "listing_id": "listing_" + security,
                "listing_code": code,
                "venue_id": "SSE",
                "first_session": "2024-10-09",
                "last_session": "2026-06-10",
            }
        ],
    }


def sources():
    identities = [identity("native"), identity("sealed"), identity("temporary", "688287")]
    temporary = {
        "target": {
            "security_id": "temporary",
            "company_id": "company_temporary",
            "listing_id": "listing_temporary",
            "security_code": "688287",
            "exchange": "SSE",
        },
        "request": {"query_date": "2024-10-09"},
    }
    return (
        identities,
        {"native": "industry_1"},
        {"sealed": ("industry_2", "123456", "SSE")},
        temporary,
    )


def test_composition_preserves_existing_sources_and_dated_aliases(tmp_path):
    identities, dwd, p4, temporary = sources()
    identities[0]["listing_bindings"][0].update(listing_code="300114", last_session="2025-01-01")
    later = {
        **identities[0]["listing_bindings"][0],
        "listing_id": "new_listing",
        "listing_code": "302132",
        "first_session": "2025-01-02",
        "last_session": "2026-06-10",
    }
    identities[0]["listing_bindings"].append(later)
    original = copy.deepcopy(identities)
    members, ledger, counts = module.compose(identities, dwd, p4, temporary, "industry_3")
    assert identities == original
    assert ledger[0]["listing_bindings"] == original[0]["listing_bindings"]
    assert counts == {"dwd_native": 1, "p4_sealed_reference": 1, "temporary_static_source": 1}
    unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": "static-level1-concentration-research-v1",
        "usage": "static_non_pit_concentration_only",
        "source": {
            "provider": "synthetic",
            "endpoint": "synthetic",
            "retrieved_at": "2026-10-03T00:00:00+00:00",
            "source_revision": "test-v1",
        },
        "memberships": members,
    }
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({**unsigned, "artifact_digest": digest_value(unsigned)}))
    loaded = load_static_industry_mapping(
        path,
        expected_version=unsigned["mapping_version"],
        required_security_ids=[r["security_id"] for r in identities],
    )
    assert {k: v.level1_industry_id for k, v in loaded.by_security_id.items()} == {
        "native": "industry_1",
        "sealed": "industry_2",
        "temporary": "industry_3",
    }


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("duplicate", "DUPLICATE_SECURITY_IDENTITY"),
        ("conflict", "DWD_P4_CONFLICT"),
        ("missing", "UNRESOLVED_SECURITY_IDENTITY"),
        ("company", "TEMPORARY_IDENTITY_MISMATCH"),
        ("code", "TEMPORARY_IDENTITY_MISMATCH"),
        ("venue", "TEMPORARY_IDENTITY_MISMATCH"),
        ("date", "TEMPORARY_IDENTITY_MISMATCH"),
        ("already_covered", "EXACT_TEMPORARY_RESIDUAL_REQUIRED"),
    ],
)
def test_conflicts_or_unbound_residual_fail_closed(mutation, reason):
    identities, dwd, p4, temporary = sources()
    if mutation == "duplicate":
        identities.append(identities[0])
    elif mutation == "conflict":
        p4["native"] = ("conflicting_industry", "123456", "SSE")
    elif mutation == "missing":
        identities.append(identity("unknown"))
    elif mutation == "company":
        identities[2]["company_id"] = "wrong_company"
    elif mutation == "code":
        identities[2]["listing_bindings"][0]["listing_code"] = "wrong_code"
    elif mutation == "venue":
        identities[2]["listing_bindings"][0]["venue_id"] = "SZSE"
    elif mutation == "date":
        temporary["request"]["query_date"] = "2024-10-08"
    else:
        dwd["temporary"] = "already_bound"
    with pytest.raises(ValueError, match=reason):
        module.compose(identities, dwd, p4, temporary, "industry_3")


def test_request_and_total_output_budget_are_frozen():
    root = Path(__file__).resolve().parents[2]
    request = module.checked_json(
        root / "config/research/p3-strat-003/temporary-industry-closure.v1.json", module.REQUEST_SHA
    )
    assert request["expected_sources"] == {
        "dwd_native": 5201,
        "p4_sealed_reference": 49,
        "temporary_static_source": 1,
    }
    one = module.checked_output_payloads({"one": "value"}, 100)
    with pytest.raises(ValueError, match="OUTPUT_BUDGET_EXCEEDED"):
        module.checked_output_payloads({"one": "value", "two": "value"}, len(one["one"]))


def test_code_receipt_binds_revision_and_imported_helper_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(module, "validate_private_root", lambda: None)
    repo = tmp_path / "repo"
    relative = "src/quanthus/expert_strategy/p3_temporary_industry_closure.py"
    implementation = repo / relative
    implementation.parent.mkdir(parents=True)
    implementation.write_bytes(Path(module.__file__).read_bytes())
    helper = repo / "src/quanthus/helper.py"
    helper.write_text("# synthetic dependency\n")
    (tmp_path / "code-receipt.json").write_text(
        json.dumps(
            {
                "code_revision": "a" * 40,
                "files": {
                    relative: module._sha256(implementation),
                    "src/quanthus/helper.py": module._sha256(helper),
                },
            }
        )
    )
    assert module.verify_code_receipt(repo, "a" * 40).startswith("sha256:")
    with pytest.raises(ValueError, match="IMPLEMENTATION_RECEIPT_MISMATCH"):
        module.verify_code_receipt(repo, "b" * 40)
    helper.write_text("# uncommitted change\n")
    with pytest.raises(ValueError, match="IMPLEMENTATION_BYTES_MISMATCH"):
        module.verify_code_receipt(repo, "a" * 40)


def test_arbitrary_output_is_rejected_before_input_access(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "verify_code_receipt", lambda *_: "sha256:" + "a" * 64)
    with pytest.raises(ValueError, match="FIXED_PRIVATE_OUTPUT_REQUIRED"):
        module.build(
            tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, tmp_path / "formal", "a" * 40
        )
    assert not (tmp_path / "formal").exists()
