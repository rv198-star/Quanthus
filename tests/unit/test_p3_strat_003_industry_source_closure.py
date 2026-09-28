"""Synthetic source closure and independent coverage counts for B-R1 S1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy import p3_strat_003_industry_inspection as inspection

TABLES = (
    "dwd_company_industry_membership",
    "dwd_industry_node",
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(
    label: str,
    code: str = "600001",
    *,
    decision: bool = True,
) -> dict[str, Any]:
    return {
        "security_id": f"sec-{label}",
        "company_id": f"co-{label}",
        "listing_code": code,
        "venue_id": "SSE",
        "first_session": "2024-12-30",
        "last_session": "2026-09-18" if decision else "2024-12-31",
        "in_decision_scope": decision,
    }


def _bind_manifest(source: SimpleNamespace) -> None:
    root = source.runtime.historical_base_root
    manifest = root / "manifest.json"
    manifest.write_bytes(canonical_json_bytes(source.manifest) + b"\n")
    source.runtime.historical_base_manifest_sha256 = _sha(manifest)
    (root / "READY").write_text(_sha(manifest) + "\n", encoding="ascii")


@pytest.fixture
def dwd_source(tmp_path: Path) -> SimpleNamespace:
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "dwd-base"
    root.mkdir()
    paths = {table: root / "payload" / f"{table}.parquet" for table in TABLES}
    (root / "payload").mkdir()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE membership(company_id VARCHAR, industry_node_id VARCHAR, "
            "membership_type VARCHAR, knowledge_to DATE)"
        )
        connection.executemany(
            "INSERT INTO membership VALUES (?, ?, ?, ?)",
            [
                ("co-direct", "industry-a", "PRIMARY_CLASSIFICATION", None),
                ("co-direct", "industry-b", "PRIMARY_CLASSIFICATION", "2024-01-01"),
                ("co-direct", "industry-b", "SECONDARY_CLASSIFICATION", None),
                ("co-child", "child-a", "PRIMARY_CLASSIFICATION", None),
                ("co-ambiguous", "industry-a", "PRIMARY_CLASSIFICATION", None),
                ("co-ambiguous", "industry-b", "PRIMARY_CLASSIFICATION", None),
                ("co-node-missing", "missing-node", "PRIMARY_CLASSIFICATION", None),
                ("co-parent-unresolved", "orphan", "PRIMARY_CLASSIFICATION", None),
                ("co-expired", "industry-a", "PRIMARY_CLASSIFICATION", "2024-01-01"),
            ],
        )
        connection.execute(
            "CREATE TABLE nodes(industry_node_id VARCHAR, parent_node_id VARCHAR, "
            "node_level INTEGER, knowledge_to DATE)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?)",
            [
                ("industry-a", None, 1, None),
                ("industry-b", None, 1, None),
                ("child-a", "industry-a", 2, None),
                ("orphan", "absent-parent", 2, None),
            ],
        )
        for table, relation in zip(TABLES, ("membership", "nodes"), strict=True):
            escaped = str(paths[table]).replace("'", "''")
            connection.execute(f"COPY {relation} TO '{escaped}' (FORMAT PARQUET)")
    finally:
        connection.close()

    content_digest = digest_value({"fixture": "synthetic industry source"})
    manifest = {
        "schema_version": 1,
        "kind": "dwd_domain_base_package",
        "package_id": "dwd-base-202607-v2.1",
        "content_digest": content_digest,
        "files": [
            {
                "path": paths[table].relative_to(root).as_posix(),
                "size_bytes": paths[table].stat().st_size,
                "sha256": _sha(paths[table]),
            }
            for table in TABLES
        ],
    }
    labels = (
        "direct",
        "child",
        "ambiguous",
        "no-membership",
        "node-missing",
        "parent-unresolved",
        "expired",
    )
    source = SimpleNamespace(
        runtime=SimpleNamespace(
            historical_base_root=root,
            historical_base_content_digest=content_digest,
        ),
        manifest=manifest,
        paths=paths,
        identities=[_identity(label, f"60000{index}") for index, label in enumerate(labels)],
    )
    _bind_manifest(source)
    return source


def test_dwd_resolution_preserves_source_closure_receipt(
    dwd_source: SimpleNamespace,
) -> None:
    mapping, reasons, receipt = inspection._dwd_reference(dwd_source.runtime, dwd_source.identities)
    assert mapping == {"sec-direct": "industry-a", "sec-child": "industry-a"}
    assert reasons == {
        "sec-direct": "covered",
        "sec-child": "covered",
        "sec-ambiguous": "ambiguous_level1_membership",
        "sec-no-membership": "no_company_industry_membership",
        "sec-node-missing": "industry_node_missing",
        "sec-parent-unresolved": "level1_parent_unresolved",
        "sec-expired": "no_company_industry_membership",
    }
    root = dwd_source.runtime.historical_base_root
    assert receipt["path"] == str(root)
    assert receipt["package_id"] == "dwd-base-202607-v2.1"
    assert receipt["manifest_sha256"] == _sha(root / "manifest.json")
    assert receipt["ready_sha256"] == _sha(root / "READY")
    assert receipt["base_content_digest"] == dwd_source.runtime.historical_base_content_digest
    assert receipt["files"] == {
        table: [
            {
                "path": dwd_source.paths[table].relative_to(root).as_posix(),
                "size_bytes": dwd_source.paths[table].stat().st_size,
                "sha256": _sha(dwd_source.paths[table]),
            }
        ]
        for table in TABLES
    }
    assert receipt["receipt_digest"] == digest_value(
        {key: value for key, value in receipt.items() if key != "receipt_digest"}
    )


@pytest.mark.parametrize(
    "tamper",
    (
        "manifest",
        "ready",
        "membership_sha",
        "membership_size",
        "membership_bytes",
        "node_sha",
        "node_size",
        "node_bytes",
    ),
)
def test_dwd_rejects_binding_tamper_before_opening_parquet(
    dwd_source: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    root = dwd_source.runtime.historical_base_root
    if tamper == "manifest":
        manifest = root / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif tamper == "ready":
        (root / "READY").write_text("sha256:" + "0" * 64 + "\n", encoding="ascii")
    else:
        table_index = 0 if tamper.startswith("membership") else 1
        entry = dwd_source.manifest["files"][table_index]
        if tamper.endswith("_bytes"):
            member = dwd_source.paths[TABLES[table_index]]
            original = member.read_bytes()
            changed = bytearray(original)
            changed[4] ^= 1
            member.write_bytes(changed)
            assert member.stat().st_size == len(original)
        else:
            if tamper.endswith("_sha"):
                entry["sha256"] = "sha256:" + "0" * 64
            else:
                entry["size_bytes"] += 1
            _bind_manifest(dwd_source)

    def forbidden_query(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unverified DWD bytes must not reach the query engine")

    monkeypatch.setattr(pytest.importorskip("duckdb"), "connect", forbidden_query)
    with pytest.raises(ExpertEvaluationError):
        inspection._dwd_reference(dwd_source.runtime, dwd_source.identities)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("package_id", "dwd-base-202607-v2"),
        ("content_digest", "sha256:" + "0" * 64),
    ),
)
def test_dwd_rejects_a_different_bound_package_identity_even_when_ready_matches(
    dwd_source: SimpleNamespace,
    field: str,
    invalid: object,
) -> None:
    dwd_source.manifest[field] = invalid
    _bind_manifest(dwd_source)
    with pytest.raises(ExpertEvaluationError):
        inspection._dwd_reference(dwd_source.runtime, dwd_source.identities)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("missing", ("file", "inventory"))
def test_missing_dwd_table_is_unavailable(
    dwd_source: SimpleNamespace,
    table: str,
    missing: str,
) -> None:
    if missing == "file":
        dwd_source.paths[table].unlink()
    else:
        relative = (
            dwd_source.paths[table].relative_to(dwd_source.runtime.historical_base_root).as_posix()
        )
        dwd_source.manifest["files"] = [
            row for row in dwd_source.manifest["files"] if row["path"] != relative
        ]
        _bind_manifest(dwd_source)
    with pytest.raises(ExpertEvaluationError) as error:
        inspection._dwd_reference(dwd_source.runtime, dwd_source.identities)
    assert error.value.reason_code == "p3_strat_003_industry_inspection_source_unavailable"


def test_dwd_inspection_does_not_open_unrelated_economic_or_outcome_files(
    dwd_source: SimpleNamespace,
) -> None:
    for name in ("strategy_economics", "outcome_labels"):
        dwd_source.manifest["files"].append(
            {
                "path": f"payload/{name}.parquet",
                "size_bytes": 999,
                "sha256": "sha256:" + "e" * 64,
            }
        )
    _bind_manifest(dwd_source)
    mapping, _reasons, receipt = inspection._dwd_reference(
        dwd_source.runtime, dwd_source.identities
    )
    assert mapping["sec-direct"] == "industry-a"
    assert set(receipt["files"]) == set(TABLES)
    assert not (dwd_source.runtime.historical_base_root / "payload/outcome_labels.parquet").exists()
    assert not (
        dwd_source.runtime.historical_base_root / "payload/strategy_economics.parquet"
    ).exists()


def _coverage(identities: list[dict[str, Any]], **sources: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "legacy": {},
        "legacy_by_code": {},
        "dwd": {},
        "dwd_reasons": {},
        "p4": {},
        "p4_by_code": {},
        "sample_limit": 20,
    }
    options.update(sources)
    return inspection._coverage(identities, **options)


def test_source_coverage_counts_dwd_and_p4_overlap_independently() -> None:
    result = _coverage(
        [_identity("both")],
        dwd={"sec-both": "industry-a"},
        dwd_reasons={"sec-both": "covered"},
        p4={"sec-both": ("industry-a", "600001", "SSE")},
        p4_by_code={("600001", "SSE"): ["sec-both"]},
    )
    counts = result["all_source_counts"]
    assert counts["security_count"] == 1
    assert counts["dwd_native_security_count"] == 1
    assert counts["p4_exact_security_count"] == 1
    assert counts["dwd_p4_overlap_security_count"] == 1
    assert counts["remaining_unresolved_security_count"] == 0
    assert counts["conflict_security_count"] == 0
    assert result["decision_source_counts"] == counts
    assert result["all_security_status_counts"] == {"dwd_native": 1}


def test_decision_counts_exclude_warmup_coverage_reasons_and_conflicts() -> None:
    result = _coverage(
        [
            _identity("warm-covered", "600001", decision=False),
            _identity("warm-missing", "600002", decision=False),
            _identity("decision-p4", "600003"),
            _identity("decision-missing", "600004"),
        ],
        dwd={"sec-warm-covered": "industry-a"},
        dwd_reasons={
            "sec-warm-covered": "covered",
            "sec-warm-missing": "no_company_industry_membership",
            "sec-decision-p4": "no_company_industry_membership",
            "sec-decision-missing": "industry_node_missing",
        },
        p4={
            "sec-warm-covered": ("industry-b", "600001", "SSE"),
            "sec-decision-p4": ("industry-c", "600003", "SSE"),
        },
    )
    assert result["all_dwd_reason_counts"] == {
        "covered": 1,
        "no_company_industry_membership": 2,
        "industry_node_missing": 1,
    }
    assert result["decision_dwd_reason_counts"] == {
        "no_company_industry_membership": 1,
        "industry_node_missing": 1,
    }
    all_counts = result["all_source_counts"]
    decision_counts = result["decision_source_counts"]
    assert all_counts["security_count"] == 4
    assert decision_counts["security_count"] == 2
    assert all_counts["dwd_native_security_count"] == 1
    assert decision_counts["dwd_native_security_count"] == 0
    assert all_counts["p4_exact_security_count"] == 2
    assert decision_counts["p4_exact_security_count"] == 1
    assert all_counts["remaining_unresolved_security_count"] == 2
    assert decision_counts["remaining_unresolved_security_count"] == 1
    assert all_counts["dwd_p4_industry_conflict_count"] == 1
    assert decision_counts["dwd_p4_industry_conflict_count"] == 0
    assert all_counts["conflict_security_count"] == 1
    assert decision_counts["conflict_security_count"] == 0


@pytest.mark.parametrize("p4_identity", (("600002", "SSE"), ("600001", "SZSE")))
@pytest.mark.parametrize("has_dwd", (False, True))
def test_p4_security_id_with_different_code_or_venue_is_not_exact_coverage(
    p4_identity: tuple[str, str],
    has_dwd: bool,
) -> None:
    result = _coverage(
        [_identity("same-id")],
        dwd={"sec-same-id": "industry-dwd"} if has_dwd else {},
        dwd_reasons={"sec-same-id": "covered" if has_dwd else "no_company_industry_membership"},
        p4={"sec-same-id": ("industry-p4", *p4_identity)},
        p4_by_code={p4_identity: ["sec-same-id"]},
    )
    for scope in ("all_source_counts", "decision_source_counts"):
        counts = result[scope]
        assert counts["p4_exact_security_count"] == 0
        assert counts["dwd_p4_overlap_security_count"] == 0
        assert counts["dwd_native_security_count"] == int(has_dwd)
        assert counts["remaining_unresolved_security_count"] == int(not has_dwd)
        assert counts["p4_identity_conflict_count"] == 1
        assert counts["dwd_p4_industry_conflict_count"] == 0
        assert counts["conflict_security_count"] == 1
    assert result["remaining_unresolved_security_count"] == int(not has_dwd)


def test_legacy_identity_and_code_venue_matches_never_resolve_source_gaps() -> None:
    identities = [
        _identity("legacy-exact", "600001"),
        _identity("code-only", "600002"),
        _identity("ambiguous-code", "600003"),
    ]
    result = _coverage(
        identities,
        dwd_reasons={
            identity["security_id"]: "no_company_industry_membership" for identity in identities
        },
        legacy={"sec-legacy-exact": "industry-legacy"},
        legacy_by_code={
            ("600001", "SSE"): ["sec-legacy-exact"],
            ("600002", "SSE"): ["legacy-code-only"],
            ("600003", "SSE"): ["legacy-a", "legacy-b"],
        },
        p4_by_code={("600002", "SSE"): ["p4-other-security"]},
    )
    for scope in ("all_source_counts", "decision_source_counts"):
        counts = result[scope]
        assert counts["security_count"] == 3
        assert counts["dwd_native_security_count"] == 0
        assert counts["p4_exact_security_count"] == 0
        assert counts["legacy_p3_exact_security_count"] == 1
        assert counts["remaining_unresolved_security_count"] == 3
        assert counts["p4_code_venue_only_reference_match_count"] == 1
        assert counts["legacy_p3_code_venue_only_match_count"] == 1
        assert counts["legacy_p3_code_venue_ambiguous_match_count"] == 1
    assert {row["security_id"] for row in result["remaining_unresolved_samples"]} == {
        identity["security_id"] for identity in identities
    }


def test_sample_limit_bounds_examples_without_truncating_counts() -> None:
    identities = [_identity(f"conflict-{index}", f"60000{index}") for index in range(3)]
    result = _coverage(
        identities,
        dwd_reasons={
            identity["security_id"]: "no_company_industry_membership" for identity in identities
        },
        p4={identity["security_id"]: ("industry-a", "999999", "SSE") for identity in identities},
        sample_limit=1,
    )
    for scope in ("all_source_counts", "decision_source_counts"):
        assert result[scope]["security_count"] == 3
        assert result[scope]["remaining_unresolved_security_count"] == 3
        assert result[scope]["p4_identity_conflict_count"] == 3
        assert result[scope]["conflict_security_count"] == 3
    assert result["remaining_unresolved_security_count"] == 3
    assert len(result["remaining_unresolved_samples"]) == 1
    assert len(result["identity_conflict_samples"]) == 1


def test_complete_build_seals_verified_dwd_receipt(
    tmp_path: Path,
    dwd_source: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Path(__file__).resolve().parents[2]
    request = project / "config/research/p3-strat-003/industry-inspection.v1.json"
    run_root = tmp_path / "event-runs"
    output = run_root / ".staging/job_synthetic/1/industry-inspection"
    candidate = {
        "status": "synthetic",
        "row_count": len(dwd_source.identities),
        "security_count": len(dwd_source.identities),
        "decision_security_count": len(dwd_source.identities),
    }
    monkeypatch.setattr(inspection, "_run_root", lambda: run_root)
    monkeypatch.setattr(
        inspection,
        "_request",
        lambda _path: {"source_runtime": {"path": "synthetic-runtime.json"}},
    )
    monkeypatch.setattr(
        inspection,
        "load_prospective_runtime_config",
        lambda *_args, **_kwargs: dwd_source.runtime,
    )
    monkeypatch.setattr(
        inspection,
        "_candidate_inventory",
        lambda *_args, **_kwargs: (candidate, dwd_source.identities),
    )
    monkeypatch.setattr(
        inspection,
        "_legacy_reference",
        lambda *_args, **_kwargs: ({}, {}, {"synthetic": True}),
    )
    monkeypatch.setattr(
        inspection,
        "_p4_reference",
        lambda *_args, **_kwargs: ({"synthetic": True}, {}, {}),
    )

    result = inspection.build_industry_inspection(
        request_path=request,
        output=output,
        code_revision="c" * 40,
    )

    assert result["disposition"] == inspection.COMPLETE
    assert result["b_acceptance"] is False
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["inspection"] == manifest["inspection"]
    receipt = manifest["inspection"]["dwd_reference"]
    assert receipt["manifest_sha256"] == dwd_source.runtime.historical_base_manifest_sha256
    assert set(receipt["files"]) == set(TABLES)
    assert receipt["receipt_digest"] == digest_value(
        {key: value for key, value in receipt.items() if key != "receipt_digest"}
    )
    assert manifest["inspection"]["coverage"]["all_source_counts"]["dwd_native_security_count"] == 2
