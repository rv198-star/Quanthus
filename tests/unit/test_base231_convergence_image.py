from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base23_native_image import KIND
from quanthus.pipelines.securities_warehouse.base231_convergence_image import (
    PREDECESSOR,
    composition_files,
    seal_convergence_image,
)


def member(path, part="all"):
    return dict(path=path, partition=part, rows=1, size_bytes=100, sha256="sha256:" + "a" * 64)


def sources():
    return dict(
        predecessor=dict(
            layout={
                "dwd_financial_report": dict(files=[member("old-report")]),
                "dwd_financial_concept": dict(files=[member("old-concept")]),
                "dwd_financial_fact": dict(files=[member("old-fact")]),
                "dwd_disclosure": dict(
                    files=[member("old-july", "July"), member("old-sept", "September")]
                ),
                "dwd_shareholding_observation": dict(files=[member("old-holder")]),
            }
        ),
        financial=dict(
            layout={
                "dwd_financial_report": [member("new-report")],
                "dwd_financial_concept": [member("new-concept")],
                "dwd_financial_fact": [member("new-fact")],
            }
        ),
        event_identity=dict(layout={"dwd_disclosure": [member("new-sept", "September")]}),
        holder=dict(layout={"dwd_shareholding_observation": [member("official-holder")]}),
        priority=dict(
            tables={
                "dwd_shareholding_observation": [member("prior-holder")],
                "dwd_financial_fact": [member("already-composed-do-not-add-twice")],
            }
        ),
    )


def test_exact_replacement_and_append_composition_preserves_unaffected_partitions():
    d = sources()
    original = deepcopy(d)
    result = composition_files(d)
    assert [f["path"] for f in result["dwd_financial_report"]] == ["new-report"]
    assert [f["path"] for f in result["dwd_financial_concept"]] == ["new-concept"]
    assert {f["path"] for f in result["dwd_financial_fact"]} == {"old-fact", "new-fact"}
    assert {f["path"] for f in result["dwd_disclosure"]} == {"old-july", "new-sept"}
    assert {f["path"] for f in result["dwd_shareholding_observation"]} == {
        "old-holder",
        "official-holder",
        "prior-holder",
    }
    assert d == original


@pytest.mark.parametrize("fault", ["extra", "missing"])
def test_unrecognized_composition_source_fails(fault):
    d = sources()
    if fault == "extra":
        d["unqualified"] = {}
    else:
        del d["financial"]
    with pytest.raises(RuntimeError, match="EXACT_SOURCE_SET_CHANGED"):
        composition_files(d)


def receipt(root):
    (root / "MATERIALIZATION.json").write_text("synthetic")
    native = dict(
        kind=KIND,
        assessment=dict(build_eligible=True),
        materialization_sha256=file_sha(root / "MATERIALIZATION.json"),
        summaries={},
        pyarrow_version="synthetic",
    )
    native["identity"] = digest(native)
    r = dict(
        kind="p10_base231_convergence_full_native_acceptance_v1",
        native=native,
        predecessor_manifest_sha256=PREDECESSOR,
        complete_native_scan=True,
        source_roots_dereferenced=False,
    )
    r["identity"] = digest(r)
    return r


def test_successor_ready_binds_new_manifest_and_cannot_overwrite(tmp_path):
    r = receipt(tmp_path)
    seal = seal_convergence_image(tmp_path, r, code_revision="synthetic-only")
    assert seal["version"] == "2.3.1" and seal["consumer_admission"] == "NOT_GRANTED"
    assert seal["independent_acceptance"] == "NOT_RUN_SEPARATE_RECEIPTS_REQUIRED"
    assert (tmp_path / "READY").read_text().strip() == file_sha(tmp_path / "manifest.json")
    with pytest.raises(RuntimeError, match="SEALED_OUTPUT_EXISTS"):
        seal_convergence_image(tmp_path, r, code_revision="synthetic-only")


@pytest.mark.parametrize("fault", ["identity", "kind", "predecessor", "partial", "changed-file"])
def test_invalid_receipt_never_writes_ready(tmp_path, fault):
    r = receipt(tmp_path)
    if fault == "identity":
        r["identity"] = "bad"
    elif fault == "kind":
        r["kind"] = "partial"
    elif fault == "predecessor":
        r["predecessor_manifest_sha256"] = "bad"
    elif fault == "partial":
        r["complete_native_scan"] = False
    else:
        (tmp_path / "MATERIALIZATION.json").write_text("changed")
    if fault != "identity":
        r.pop("identity")
        r["identity"] = digest(r)
    with pytest.raises(RuntimeError, match="FULL_STRUCTURAL_RECEIPT_REQUIRED"):
        seal_convergence_image(tmp_path, r, code_revision="synthetic-only")
    assert not (tmp_path / "READY").exists()
    assert not (tmp_path / "manifest.json").exists()
