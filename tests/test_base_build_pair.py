from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget
from quanthus.pipelines.securities_warehouse.base_build_freeze import freeze
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_ods import materialize_ods
from quanthus.pipelines.securities_warehouse.base_build_pair import materialize_dwd
from quanthus.pipelines.securities_warehouse.base_build_period import VerifiedInputs
from quanthus.pipelines.securities_warehouse.base_build_windows import build_windows
from tests.test_base_build_composition import fixture as composition_fixture
from tests.test_base_build_composition import limits
from tests.test_base_build_ods import save, source_inputs
from tests.unit.test_base24_ods_image import source_closure as source_closure


def pair_fixture(tmp_path, source_closure, *, audit_coherent=False):
    """Real materializers, synthetic rows; not full workflow A or paired audit B."""
    dwd_area = tmp_path / "dwd-inputs"
    dwd_area.mkdir()
    predecessor, delta, rules, c, binding, old, config = composition_fixture(dwd_area)
    retained = predecessor / "evidence/synthetic-retained-restriction.json"
    save(retained, {"unavailable": ["synthetic-field"], "original_grade": "R3"})
    limitations = [
        {
            "id": "synthetic-unit-scope",
            "disposition": "PRESERVE_UNAVAILABLE",
            "evidence_paths": [retained.relative_to(predecessor).as_posix()],
        }
    ]
    additional_evidence = []
    if audit_coherent:
        from tests.unit.test_base24_image import base24_limitations

        limitations[0].update(
            tables=["dwd_security_bar"],
            scope_kind="fields",
            scope=["synthetic-field"],
            blocked_uses=["cross_provider_exact_price"],
            risk="synthetic unavailable field",
            repair_cost="fixture-only",
        )
        frequency = base24_limitations()
        limitations += frequency
        frequency_evidence = predecessor / frequency[0]["evidence_paths"][0]
        save(frequency_evidence, {"synthetic_restrictions_only": frequency})
        additional_evidence.append(frequency_evidence)
    save(predecessor / "evidence/limitations.json", limitations)
    record = json.loads((predecessor / "MATERIALIZATION.json").read_bytes())
    record["evidence"] = {
        p.relative_to(predecessor).as_posix(): file_sha(p)
        for p in (
            retained,
            predecessor / "evidence/limitations.json",
            predecessor / "evidence/input-plan.json",
            *additional_evidence,
        )
    }
    save(predecessor / "MATERIALIZATION.json", record)
    manifest = json.loads((predecessor / "manifest.json").read_bytes())
    manifest["materialization_sha256"] = file_sha(predecessor / "MATERIALIZATION.json")
    save(predecessor / "manifest.json", manifest)
    (predecessor / "READY").write_text(file_sha(predecessor / "manifest.json"))
    input_area = tmp_path / "archive-inputs"
    input_area.mkdir()
    initial, period, _, archive_contract = source_inputs(input_area, source_closure)
    assert c == archive_contract
    roots = dict(initial.roots, dwd=str(predecessor))
    selection = initial.frozen["content"]["selection"]
    next(r for r in selection["packages"] if r["role"] == "dwd")["manifest_sha256"] = file_sha(
        predecessor / "manifest.json"
    )
    frozen = freeze(selection, roots)
    inputs = VerifiedInputs.verify(frozen, roots)
    binding.update(
        predecessor_manifest_sha256=file_sha(predecessor / "manifest.json"),
        frozen_input_identity=inputs.verified_identity,
    )
    descriptor = json.loads((delta / "DELTA.json").read_bytes())
    descriptor["binding"] = binding
    descriptor["identity"] = identity({k: v for k, v in descriptor.items() if k != "identity"})
    save(delta / "DELTA.json", descriptor)
    if audit_coherent:
        from datetime import datetime, timezone

        import pyarrow as pa
        import pyarrow.parquet as pq

        from quanthus.pipelines.securities_warehouse.base23_native_image import image_schemas
        from quanthus.pipelines.securities_warehouse.base_build_delta import build_delta
        from tests.unit.test_base22_independent_image import _row

        tables = {
            t: [delta / r["path"] for r in descriptor["files"] if r["table"] == t]
            for t in descriptor["tables"]
        }
        schemas = image_schemas(old.model, old.extension)
        seed = tmp_path / "coherent-period-inputs"
        seed.mkdir()
        bar = pq.ParquetFile(tables["dwd_security_bar"][0]).read().to_pylist()[0]
        listing_path = predecessor / record["layout"]["dwd_listing"]["files"][0]["path"]
        bar["listing_id"] = pq.ParquetFile(listing_path).read().to_pylist()[0]["listing_id"]
        pq.write_table(
            pa.Table.from_pylist([bar], schema=schemas["dwd_security_bar"]), seed / "bar.parquet"
        )
        tables["dwd_security_bar"] = [seed / "bar.parquet"]
        calendar = _row(
            schemas["dwd_trading_calendar"], datetime(2026, 10, 30, tzinfo=timezone.utc)
        )
        calendar["calendar_session_id"] = bar["calendar_session_id"]
        pq.write_table(
            pa.Table.from_pylist([calendar], schema=schemas["dwd_trading_calendar"]),
            seed / "calendar.parquet",
        )
        tables["dwd_trading_calendar"] = [seed / "calendar.parquet"]
        with Budget(
            tmp_path / "coherent-delta-job", [predecessor, delta, seed], limits()
        ) as budget:
            delta = budget.root / "delta"
            descriptor = build_delta(
                tables=tables,
                rules=rules,
                schemas=schemas,
                binding=binding,
                output=delta,
                budget=budget,
            )
    shutil.copytree(delta, period / "delta")
    admission = json.loads((period / "ADMISSION.json").read_bytes())
    admission.update(binding=binding, delta_identity=descriptor["identity"])
    admission["identity"] = identity({k: v for k, v in admission.items() if k != "identity"})
    save(period / "ADMISSION.json", admission)
    with Budget(
        tmp_path / "pair-windows", [Path(p) for p in roots.values()] + [period], limits()
    ) as budget:
        windows = budget.root / "windows"
        build_windows(
            predecessor=Path(roots["ods"]),
            period=period,
            binding=binding,
            output=windows,
            budget=budget,
        )
    with Budget(
        tmp_path / "pair-ods", [Path(p) for p in roots.values()] + [period, windows], limits()
    ) as budget:
        ods = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=ods, budget=budget
        )
    return predecessor, period, windows, ods, binding, rules, c, old, config, limitations, inputs


def materialize(tmp_path, f):
    predecessor, period, windows, ods, binding, rules, c, old, config, _ = f[:10]
    with Budget(tmp_path / "pair-dwd", [predecessor, period, windows, ods], limits()) as budget:
        output = budget.root / "DWD"
        record = materialize_dwd(
            predecessor=predecessor,
            period=period,
            windows=windows,
            ods=ods,
            binding=binding,
            rules=rules,
            contract=c,
            model=old.model,
            extension=old.extension,
            config_root=config,
            output=output,
            budget=budget,
        )
    return output, record


def test_all_33_tables_materialize_with_exact_ods_period_and_original_limitations(
    tmp_path,
    source_closure,
):
    f = pair_fixture(tmp_path, source_closure)
    output, record = materialize(tmp_path, f)
    assert len(record["layout"]) == 33
    assert record["layout"]["dwd_security_bar"]["expected_partition_rows"]
    assert json.loads((output / "evidence/limitations.json").read_bytes()) == f[9]
    assert (output / "evidence/synthetic-retained-restriction.json").read_bytes() == (
        f[0] / "evidence/synthetic-retained-restriction.json"
    ).read_bytes()
    assert (output / "evidence/build/ODS-MATERIALIZATION.json").read_bytes() == (
        f[3] / "MATERIALIZATION.json"
    ).read_bytes()
    assert (output / "evidence/build/ADMISSION.json").read_bytes() == (
        f[1] / "ADMISSION.json"
    ).read_bytes()
    assert not (output / "READY").exists()
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("damage", ["period", "window", "ods", "limitation-evidence"])
def test_pair_binding_and_limitation_changes_fail_before_dwd_output(
    tmp_path, source_closure, damage
):
    f = pair_fixture(tmp_path, source_closure)
    if damage == "period":
        path = f[1] / "ADMISSION.json"
        value = json.loads(path.read_bytes())
        value["binding"] = {}
        save(path, value)
    elif damage == "window":
        (f[2] / "WINDOWS.parquet").write_bytes(b"corrupt")
    elif damage == "ods":
        path = f[3] / "MATERIALIZATION.json"
        value = json.loads(path.read_bytes())
        value["period_admission_identity"] = "sha256:" + "f" * 64
        value["identity"] = identity({k: v for k, v in value.items() if k != "identity"})
        save(path, value)
    else:
        (f[0] / "evidence/synthetic-retained-restriction.json").write_bytes(b"changed")
    with pytest.raises(BoundedError, match="BUILD_(PAIR|WINDOWS)_"):
        materialize(tmp_path, f)
    assert not (tmp_path / "pair-dwd/DWD").exists()


def test_real_materialization_graph_recovers_failed_dwd_without_rebuilding_shared_stages(
    tmp_path,
    source_closure,
    monkeypatch,
):
    from quanthus.pipelines.securities_warehouse import base_build_pair as pair
    from quanthus.pipelines.securities_warehouse.base_build_stages import Stage, StageRunner

    f = pair_fixture(tmp_path, source_closure)
    calls = []

    def period_build(output, dependencies):
        calls.append("period")
        shutil.copytree(f[1], output / "period")
        return {"identity": json.loads((f[1] / "ADMISSION.json").read_bytes())["identity"]}

    def period_verify(output, result):
        admission = json.loads((output / "period/ADMISSION.json").read_bytes())
        assert admission["identity"] == result["identity"]

    stages = [Stage("period", (), period_build, period_verify)]
    stages.extend(
        pair.materialization_stages(
            inputs=f[10], binding=f[4], contract=f[6], config_root=f[8], limits=limits()
        )
    )
    runner = StageRunner(
        root=tmp_path / "graph-job",
        sources=[Path(p) for p in f[10].roots.values()],
        binding={
            "input_identity": f[10].verified_identity,
            "code_revision": "e" * 40,
            "environment_identity": "sha256:" + "a" * 64,
            "policy_identity": identity(f[6]),
            "scope": {"through_date": "2026-10-31", "target_version": "2.5"},
        },
        stages=stages,
    )
    original = pair.materialize_dwd

    def fail(**kwargs):
        raise BoundedError("SYNTHETIC_DWD_INTERRUPTION")

    monkeypatch.setattr(pair, "materialize_dwd", fail)
    with pytest.raises(BoundedError, match="SYNTHETIC_DWD_INTERRUPTION"):
        runner.execute()
    monkeypatch.setattr(pair, "materialize_dwd", original)
    completed = runner.execute()
    assert calls == ["period"]
    assert all(completed[n]["reused"] for n in ("period", "windows", "ods"))
    assert completed["dwd"]["reused"] is False
    assert completed["dwd"]["receipt"]["content"]["result"]["tables"] == 33
    assert (tmp_path / "graph-job/stages/dwd/attempt-000001/FAILURE.json").is_file()
    assert (tmp_path / "graph-job/stages/dwd/attempt-000002/SUCCESS.json").is_file()
    repeated = runner.execute()
    assert all(r["reused"] for r in repeated.values())
    assert repeated["dwd"]["receipt"] == completed["dwd"]["receipt"]
    assert not list((tmp_path / "graph-job/stages/dwd").rglob("READY"))
