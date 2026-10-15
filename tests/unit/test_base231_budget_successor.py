from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse import base231_budget_successor as mod
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base23_native_image import KIND


def descriptors():
    tables = [*mod.ADDITIONS, "unaffected"]
    return {
        "predecessor": {
            "layout": {
                t: {
                    "files": [
                        dict(
                            path=t + ".parquet",
                            partition="old",
                            rows=1,
                            size_bytes=12,
                            sha256="sha256:" + "a" * 64,
                        )
                    ]
                }
                for t in tables
            },
            "evidence": {},
        },
        **{
            sid: {
                "members": {
                    "outputs/" + t + ".parquet": {"bytes": 24, "sha256": "sha256:" + "b" * 64}
                    for t, (source, _, _) in mod.ADDITIONS.items()
                    if source == sid
                }
            }
            for sid in mod.INPUTS
        },
    }


def test_only_twenty_eight_append_rows_and_no_old_file_or_descriptor_mutation():
    d = descriptors()
    old = deepcopy(d)
    files = mod.composition_files(d)
    assert d == old
    assert sum(x["rows"] for t in files.values() for x in t) == 32
    for table, items in files.items():
        assert sum(x["source_id"] == "predecessor" for x in items) == 1
        assert (
            next(x for x in items if x["source_id"] == "predecessor")["path"] == table + ".parquet"
        )
    assert len(files["unaffected"]) == 1
    assert next(x for x in files["dwd_corporate_action"] if x["source_id"] == "actions")[
        "partition"
    ].endswith("month=9")


@pytest.mark.parametrize("fault", ["extra_source", "missing_source", "missing_table"])
def test_composition_rejects_unapproved_scope(fault):
    d = descriptors()
    if fault == "extra_source":
        d["unqualified"] = {}
    elif fault == "missing_source":
        del d["holders"]
    else:
        del d["predecessor"]["layout"]["dwd_corporate_action"]
    with pytest.raises(RuntimeError):
        mod.composition_files(d)


def local_fixture(root, monkeypatch):
    d = descriptors()

    def write(relative, value):
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(canonical(value))
        return p

    sources = {}
    for sid, body in d.items():
        p = write("evidence/sources/" + sid + ".json", body)
        sources[sid] = dict(descriptor_sha256=file_sha(p), root="/must-not-open/" + sid)
        if sid != "predecessor":
            write(mod.PREFIX + "/" + sid + "/INPUTS.json", body)
    prior = write(
        mod.PREFIX + "/prior-manifest.json",
        dict(materialization_sha256=sources["predecessor"]["descriptor_sha256"]),
    )
    monkeypatch.setattr(mod, "PREDECESSOR", file_sha(prior))
    monkeypatch.setattr(
        mod, "INPUTS", {sid: sources[sid]["descriptor_sha256"] for sid in ["actions", "holders"]}
    )
    plan = dict(
        predecessor_manifest_sha256=mod.PREDECESSOR,
        sources=sources,
        tables={t: dict(files=items) for t, items in mod.composition_files(d).items()},
    )
    write("evidence/input-plan.json", plan)
    return plan, write


def test_contained_selection_never_opens_archived_roots(tmp_path, monkeypatch):
    local_fixture(tmp_path, monkeypatch)
    assert mod.validate_composition(tmp_path)["materialization_sha256"]


@pytest.mark.parametrize(
    "fault",
    ["prior", "source_pin", "source_bytes", "contained", "drop_old", "extra_row", "partition"],
)
def test_full_rehash_does_not_authorize_changed_scope(tmp_path, monkeypatch, fault):
    plan, write = local_fixture(tmp_path, monkeypatch)
    if fault == "prior":
        write(mod.PREFIX + "/prior-manifest.json", {})
    elif fault == "source_pin":
        plan["sources"]["holders"]["descriptor_sha256"] = "sha256:" + "0" * 64
    elif fault == "source_bytes":
        write("evidence/sources/holders.json", {})
    elif fault == "contained":
        write(mod.PREFIX + "/holders/INPUTS.json", {})
    else:
        items = plan["tables"]["dwd_corporate_action"]["files"]
        if fault == "drop_old":
            items[:] = [x for x in items if x["source_id"] != "predecessor"]
        elif fault == "extra_row":
            items[0]["rows"] += 1
        else:
            items[0]["partition"] = "effective_at_year=2026/effective_at_month=10"
    write("evidence/input-plan.json", plan)
    with pytest.raises(RuntimeError):
        mod.validate_composition(tmp_path)


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
        kind=mod.RECEIPT_KIND,
        native=native,
        predecessor_manifest_sha256=mod.PREDECESSOR,
        complete_native_scan=True,
        source_roots_dereferenced=False,
    )
    r["identity"] = digest(r)
    return r


def test_seal_ready_is_last_and_never_overwrites(tmp_path):
    r = receipt(tmp_path)
    seal = mod.seal_budget_successor(tmp_path, r, code_revision="synthetic")
    assert seal["version"] == "2.3.1" and seal["consumer_admission"] == "NOT_GRANTED"
    assert (tmp_path / "READY").read_text().strip() == file_sha(tmp_path / "manifest.json")
    with pytest.raises(RuntimeError, match="SEALED_OUTPUT_EXISTS"):
        mod.seal_budget_successor(tmp_path, r, code_revision="synthetic")


@pytest.mark.parametrize("fault", ["scan", "roots", "native", "materialization"])
def test_partial_or_changed_receipt_cannot_seal(tmp_path, fault):
    r = receipt(tmp_path)
    if fault == "scan":
        r["complete_native_scan"] = False
    elif fault == "roots":
        r["source_roots_dereferenced"] = True
    elif fault == "native":
        r["native"]["assessment"]["build_eligible"] = False
    else:
        (tmp_path / "MATERIALIZATION.json").write_text("changed")
    r["identity"] = digest({k: v for k, v in r.items() if k != "identity"})
    with pytest.raises(RuntimeError, match="FULL_STRUCTURAL_RECEIPT"):
        mod.seal_budget_successor(tmp_path, r, code_revision="synthetic")
    assert not (tmp_path / "READY").exists()
