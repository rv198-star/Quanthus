"""Delivery status reconciles receipts; it never substitutes for payload readers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base24_release_status as release
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base23_delivery import HARD_CHECKS
from quanthus.pipelines.securities_warehouse.base24_delivery import CONTRACT_ID, PURPOSE


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def seal_receipt(path, receipt):
    receipt["identity"] = digest({k: v for k, v in receipt.items() if k != "identity"})[7:]
    write(path, receipt)


@pytest.fixture
def fixture(tmp_path):
    tables = {"dwd_synthetic_" + str(i) for i in range(33)}
    summaries = {t: {"all": {"rows": 0, "typed_arrow_sha256": digest(t)}} for t in tables}
    checks = dict.fromkeys(HARD_CHECKS, "PASS")
    checks.update(dict.fromkeys(release.OUTER_CHECKS, "NOT_RUN"))
    assessment = {
        "contract_id": CONTRACT_ID,
        "purpose": PURPOSE,
        "tables": sorted(tables),
        "build_eligible": True,
        "independently_verified": False,
        "failed_checks": [],
        "not_run_checks": list(release.OUTER_CHECKS),
        "structural_checks": checks,
        "limitations": [{"id": "synthetic-unavailable", "blocked_uses": ["historical_pit"]}],
        "known_limitation_count": 1,
        "consumer_admission": "NOT_GRANTED",
    }
    assessment["identity"] = digest(assessment)
    manifests = {
        "dwd": {
            "kind": release.DWD_KIND,
            "version": "2.4",
            "purpose": PURPOSE,
            "business_through": "2026-09-30",
            "business_timezone": "Asia/Shanghai",
            "default_intervals": ["1d", "5m"],
            "consumer_admission": "NOT_GRANTED",
            "assessment": assessment,
            "summaries": summaries,
            "scan_identity": digest("dwd-audit"),
            "independent_acceptance": "NOT_RUN_SEPARATE_RECEIPTS_REQUIRED",
        },
        "ods": {
            "kind": release.ODS_KIND,
            "content": {
                "contract": release.ODS_CONTRACT,
                "through_date": "2026-09-30",
                "business_timezone": "Asia/Shanghai",
                "default_frequencies": ["1d", "5m"],
                "purpose": release.ODS_PURPOSE,
                "consumer_admission": "NOT_GRANTED",
                "audit_identity": digest("ods-audit"),
            },
        },
    }
    binding = {
        "contract": release.CONTRACT,
        "version": "2.4",
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "purpose": "governance_baseline",
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "products": {},
    }
    roots = {}
    receipts = {}
    evidence = tmp_path / "delivery"
    for product, manifest in manifests.items():
        root = tmp_path / product
        root.mkdir()
        roots[product] = root
        if product == "dwd":
            (root / "MATERIALIZATION.json").write_bytes(b"synthetic metadata\n")
            manifest["materialization_sha256"] = file_sha(root / "MATERIALIZATION.json")
        else:
            manifest["content_digest"] = digest(manifest["content"])
        write(root / "manifest.json", manifest)
        (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")
        receipt = {
            "status": "PASS",
            "product": product,
            "source": "/unavailable/archival/source",
            "source_manifest_sha256": file_sha(root / "manifest.json"),
            "source_READY_sha256": file_sha(root / "READY"),
            "audit_identity": digest(product + "-audit"),
            "source_unchanged": True,
            "distinct_new_processes": True,
            "two_independent_physical_relocations": True,
            "missing_and_corrupt_fail_closed": True,
            "results": [],
        }
        if product == "dwd":
            receipt.update(
                source_materialization_sha256=manifest["materialization_sha256"],
                payload_byte_corruption_fail_closed=True,
            )
        for i, label in enumerate(["move1", "move2", "missing", "corrupt"]):
            if label.startswith("move"):
                child = {"identity": receipt["audit_identity"]}
                if product == "dwd":
                    child.update(
                        summaries=deepcopy(summaries),
                        assessment=deepcopy(assessment),
                        global_keys_versions_references_lineage="PASS",
                    )
                else:
                    child.update(
                        status="PASS",
                        contract=release.ODS_CONTRACT,
                        source_roots_dereferenced=False,
                    )
            else:
                child = {
                    "status": "EXPECTED_FAIL_CLOSED",
                    "reason": release.NEGATIVES[product][label],
                }
            receipt["results"].append(
                {
                    "label": label,
                    "pid": i + 100,
                    "product": product,
                    "new_process": True,
                    "kernel_readonly_image": True,
                    "network_namespace_probe": "PASS",
                    "source_roots_mounted_inaccessible": True,
                    "receipt": child,
                }
            )
        relative = f"evidence/{product}-independent-acceptance.safe.json"
        seal_receipt(evidence / relative, receipt)
        receipts[product] = receipt
        binding["products"][product] = {
            "manifest_sha256": file_sha(root / "manifest.json"),
            "acceptance_path": relative,
            "acceptance_sha256": file_sha(evidence / relative),
        }
    return binding, roots, evidence, tables, receipts, manifests


def resolve(fixture):
    binding, roots, evidence, tables, _, _ = fixture
    return release.resolve_delivery_status(binding, roots, evidence, expected_tables=tables)


def reseal(fixture, product):
    binding, roots, evidence, _, receipts, manifests = fixture
    write(roots[product] / "manifest.json", manifests[product])
    manifest_sha = file_sha(roots[product] / "manifest.json")
    (roots[product] / "READY").write_text(manifest_sha + "\n")
    receipts[product].update(
        source_manifest_sha256=manifest_sha,
        source_READY_sha256=file_sha(roots[product] / "READY"),
    )
    relative = binding["products"][product]["acceptance_path"]
    seal_receipt(evidence / relative, receipts[product])
    binding["products"][product].update(
        manifest_sha256=manifest_sha,
        acceptance_sha256=file_sha(evidence / relative),
    )


def test_exact_later_receipts_resolve_frozen_not_run_without_mutation(fixture):
    before = {p: p.read_bytes() for root in fixture[1].values() for p in root.iterdir()}
    result = resolve(fixture)
    assert result["status"] == "PASS_RECORDED_PAIRED_IMAGE_ACCEPTANCE"
    assert result["current_payload_verification"] == "NOT_RUN_STATUS_RESOLUTION_ONLY"
    dwd = result["products"]["dwd"]
    assert dwd["sealed_independent_acceptance"] == "NOT_RUN_SEPARATE_RECEIPTS_REQUIRED"
    assert dwd["effective_independent_acceptance"] == "PASS"
    assert set(dwd["effective_structural_checks"].values()) == {"PASS"}
    assert dwd["limitations"] == fixture[5]["dwd"]["assessment"]["limitations"]
    assert result["consumer_admission"] == "NOT_GRANTED"
    assert result["execution_authorized"] is False
    assert all(p.read_bytes() == b for p, b in before.items())


@pytest.mark.parametrize("product", ["dwd", "ods"])
def test_missing_receipt_cannot_fall_back_to_ready_or_archive_path(fixture, product):
    binding, _, evidence, *_ = fixture
    (evidence / binding["products"][product]["acceptance_path"]).unlink()
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


@pytest.mark.parametrize("product", ["dwd", "ods"])
def test_wrong_receipt_hash_fails(fixture, product):
    fixture[0]["products"][product]["acceptance_sha256"] = digest("wrong")
    with pytest.raises(release.ReleaseStatusError, match="SHA256"):
        resolve(fixture)


@pytest.mark.parametrize("product", ["dwd", "ods"])
def test_receipt_for_another_manifest_fails_even_when_receipt_rehashed(fixture, product):
    binding, _, evidence, _, receipts, _ = fixture
    receipts[product]["source_manifest_sha256"] = digest("other")
    path = evidence / binding["products"][product]["acceptance_path"]
    seal_receipt(path, receipts[product])
    binding["products"][product]["acceptance_sha256"] = file_sha(path)
    with pytest.raises(release.ReleaseStatusError, match="BINDING"):
        resolve(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "FAIL"),
        ("source_unchanged", False),
        ("two_independent_physical_relocations", False),
        ("distinct_new_processes", False),
        ("missing_and_corrupt_fail_closed", False),
        ("payload_byte_corruption_fail_closed", False),
    ],
)
def test_failure_cannot_be_overridden_by_ready(fixture, field, value):
    fixture[4]["dwd"][field] = value
    reseal(fixture, "dwd")
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 100),
        ("source_roots_mounted_inaccessible", False),
        ("kernel_readonly_image", False),
        ("new_process", False),
        ("network_namespace_probe", "FAIL"),
    ],
)
def test_two_moves_require_distinct_processes_and_actual_isolation(fixture, field, value):
    fixture[4]["dwd"]["results"][1][field] = value
    reseal(fixture, "dwd")
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


def test_counterfeit_summary_pass_does_not_qualify_different_table_results(fixture):
    fixture[4]["dwd"]["results"][1]["receipt"]["summaries"].pop(next(iter(fixture[3])))
    reseal(fixture, "dwd")
    with pytest.raises(release.ReleaseStatusError, match="AUDIT"):
        resolve(fixture)


@pytest.mark.parametrize("field,value", [("status", "PASS"), ("reason", "unrelated failure")])
def test_damage_evidence_requires_correct_rejection(fixture, field, value):
    fixture[4]["ods"]["results"][3]["receipt"][field] = value
    reseal(fixture, "ods")
    with pytest.raises(release.ReleaseStatusError, match="NEGATIVE"):
        resolve(fixture)


def test_outer_receipt_cannot_erase_an_inner_schema_failure(fixture):
    a = fixture[5]["dwd"]["assessment"]
    a["structural_checks"]["physical_schema"] = "FAIL"
    a["failed_checks"] = ["physical_schema"]
    reseal(fixture, "dwd")
    with pytest.raises(release.ReleaseStatusError, match="ASSESSMENT"):
        resolve(fixture)


def test_exact_tables_required_not_count(fixture):
    m = fixture[5]["dwd"]
    m["summaries"]["unexpected"] = m["summaries"].pop(next(iter(fixture[3])))
    reseal(fixture, "dwd")
    with pytest.raises(release.ReleaseStatusError, match="TABLE"):
        resolve(fixture)


@pytest.mark.parametrize(
    "field,value",
    [("purpose", "trading"), ("consumer_admission", "GRANTED"), ("execution_authorized", True)],
)
def test_delivery_binding_cannot_grant_business_admission(fixture, field, value):
    fixture[0][field] = value
    with pytest.raises(release.ReleaseStatusError, match="CONTRACT"):
        resolve(fixture)


@pytest.mark.parametrize("relative", ["../receipt.json", "/tmp/receipt.json"])
def test_receipt_path_must_stay_in_delivery_envelope(fixture, relative):
    fixture[0]["products"]["dwd"]["acceptance_path"] = relative
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


def test_source_paths_payloads_and_network_never_opened(fixture, monkeypatch):
    import socket

    original = Path.open

    def guarded(path, *args, **kwargs):
        assert "payload" not in path.parts and "unavailable" not in path.parts
        return original(path, *args, **kwargs)

    def denied(*args, **kwargs):
        raise AssertionError("source network opened")

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(socket, "socket", denied)
    assert resolve(fixture)["status"] == "PASS_RECORDED_PAIRED_IMAGE_ACCEPTANCE"


def test_symlink_receipt_rejected(fixture, tmp_path):
    path = fixture[2] / fixture[0]["products"]["dwd"]["acceptance_path"]
    target = tmp_path / "elsewhere.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


def test_omitted_product_rejected(fixture):
    fixture[0]["products"].pop("ods")
    with pytest.raises(release.ReleaseStatusError):
        resolve(fixture)


@pytest.mark.parametrize("product", ["dwd", "ods"])
def test_image_metadata_cannot_sneak_in_execution_permission(fixture, product):
    manifest = fixture[5][product]
    content = manifest if product == "dwd" else manifest["content"]
    content["execution_authorized"] = True
    if product == "ods":
        manifest["content_digest"] = digest(content)
    reseal(fixture, product)
    with pytest.raises(release.ReleaseStatusError, match="CONTRACT"):
        resolve(fixture)


def test_ready_alone_never_means_payload_currently_verified(fixture):
    root = fixture[1]["dwd"]
    payload = root / "payload" / "synthetic.parquet"
    payload.parent.mkdir()
    payload.write_bytes(b"not a parquet file")
    # This API reports recorded acceptance, not fresh physical integrity. Consumers
    # must still call their existing physical reader rather than admitting rows here.
    result = resolve(fixture)
    assert result["current_payload_verification"] == "NOT_RUN_STATUS_RESOLUTION_ONLY"
    assert result["governance_read"] == "AVAILABLE_WITH_EXISTING_PHYSICAL_READER_CHECKS"
