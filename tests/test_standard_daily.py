from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.base22_bounded import Budget
from quanthus.pipelines.securities_warehouse.daily_core_domain import _verify_base
from quanthus.pipelines.securities_warehouse.standard_daily import publish, verify
from tests.base_build_full_fixture import native_predecessor
from tests.test_base_build_composition import limits
from tests.test_base_build_period import fixture
from tests.unit.test_base24_ods_image import source_closure as source_closure
from tests.unit.test_native5m_daily_source import original as original


def test_market_and_closed_delivery_replay_only_self_contained_sources(
    tmp_path, original, monkeypatch
):
    args = fixture(
        tmp_path,
        original,
        base_factory=lambda root, schemas: native_predecessor(root, schemas, monkeypatch),
    )
    roots = {k: Path(v) for k, v in args["inputs"].roots.items()}
    config = roots["daily"] / "config.json"
    parent, _ = _verify_base(roots["base-dwd"], config)
    deliveries = {}
    for kind, day in (("market", "2026-10-08"), ("closed", "2026-10-09")):
        output = tmp_path / (kind + "-delivery")
        sources = {
            "calendar_daily_events": roots["sources"] / day / "events",
            "market_daily_core" if kind == "market" else "calendar_reference": roots["sources"]
            / "2026-10-08/market",
        }
        observed = None
        if kind == "closed":
            from tests.test_observed_daily_calendar import observation

            observed = observation(tmp_path / "observed-calendar")
        with Budget(tmp_path / (kind + "-publisher"), list(roots.values()), limits()) as budget:
            body = publish(
                projection=roots["daily"] if kind == "market" else None,
                sources=sources,
                config=config,
                base_root=roots["base-dwd"],
                output=output,
                code_revision="a" * 40,
                budget=budget,
                calendar_observation=observed,
            )
        assert body["day_kind"] == kind
        deliveries[kind] = output
    shutil.rmtree(roots["sources"])
    shutil.rmtree(roots["daily"])
    shutil.rmtree(original[0])
    for kind, output in deliveries.items():
        with Budget(tmp_path / (kind + "-cold"), [output, roots["base-dwd"]], limits()) as budget:
            result = verify(output, parent=parent, budget=budget, output=budget.root / "replay")
        assert pq.ParquetFile(result.tables["dwd_disclosure"][0]).metadata.num_rows == 1
        assert ("dwd_security_bar" in result.tables) is (kind == "market")


def test_next_generic_base_consumes_only_ordinary_market_and_closed_deliveries(
    tmp_path, original, source_closure, monkeypatch
):
    from quanthus.pipelines.securities_warehouse.base_build_audit import audit_pair
    from quanthus.pipelines.securities_warehouse.base_build_freeze import freeze
    from quanthus.pipelines.securities_warehouse.base_build_inputs import identity
    from quanthus.pipelines.securities_warehouse.base_build_ods import materialize_ods
    from quanthus.pipelines.securities_warehouse.base_build_pair import materialize_dwd
    from quanthus.pipelines.securities_warehouse.base_build_period import (
        VerifiedInputs,
        prepare_period,
    )
    from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
    from quanthus.pipelines.securities_warehouse.base_build_publication import (
        pair_documents,
        prepare_markers,
        write_markers,
    )
    from quanthus.pipelines.securities_warehouse.base_build_windows import build_windows
    from tests.base_build_full_fixture import source_pair

    first = tmp_path / "first"
    first.mkdir()
    ods, dwd, receipt, contract, _ = source_pair(first, source_closure, original, monkeypatch)
    controls = tmp_path / "publication"
    controls.mkdir()
    docs = pair_documents(
        ods=ods,
        dwd=dwd,
        receipt=receipt,
        contract=contract,
        revision=receipt["binding"]["code_revision"],
    )
    prepared = prepare_markers(controls, docs, attempt="attempt-000001")
    write_markers(controls, {"ods": ods, "dwd": dwd}, prepared, attempt="attempt-000001")
    from tests.base_build_full_fixture import standard_next_inputs

    selection, roots, configuration = standard_next_inputs(tmp_path, ods, dwd, "a" * 40)
    paths = [Path(p) for key, p in roots.items() if key.startswith("delivery-")]
    next_area, wire_area = tmp_path / "next-inputs", tmp_path / "next-wire"
    frozen = freeze(selection, roots)
    c = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.6",
        predecessor_version="2.5",
        through="2026-10-13",
    )
    binding = {
        "predecessor_manifest_sha256": next(
            p["manifest_sha256"] for p in selection["packages"] if p["role"] == "dwd"
        ),
        "frozen_input_identity": frozen["identity"],
        "code_revision": "a" * 40,
        "policy_identity": identity(c),
        "predecessor_through": "2026-10-09",
        "through_date": "2026-10-13",
        "target_version": "2.6",
    }
    shutil.rmtree(next_area)
    shutil.rmtree(wire_area)
    model = json.loads(Path("config/pipelines/dwd-domain-model.v1.json").read_bytes())
    extension = json.loads(
        Path("config/pipelines/dwd-domain-model.v1.1-extension.json").read_bytes()
    )
    from quanthus.pipelines.securities_warehouse.base23_native_image import image_schemas

    inputs = VerifiedInputs.verify(frozen, roots)
    rules = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())
    with Budget(tmp_path / "second-build", [Path(v) for v in roots.values()], limits()) as budget:
        period, windows = budget.root / "period", budget.root / "windows"
        result = prepare_period(
            inputs=inputs,
            binding=binding,
            rules=rules,
            schemas=image_schemas(model, extension),
            output=period,
            budget=budget,
            **configuration,
        )
        build_windows(
            predecessor=ods, period=period, binding=binding, output=windows, budget=budget
        )
        next_ods, next_dwd = budget.root / "ODS", budget.root / "DWD"
        materialize_ods(
            inputs=inputs,
            period=period,
            windows=windows,
            contract=c,
            output=next_ods,
            budget=budget,
        )
        materialize_dwd(
            predecessor=dwd,
            period=period,
            windows=windows,
            ods=next_ods,
            binding=binding,
            contract=c,
            rules=rules,
            model=model,
            extension=extension,
            config_root=Path("config/pipelines"),
            output=next_dwd,
            budget=budget,
        )
    assert result["new_windows"] == 2 and result["new_quality_held"] == 0
    descriptor = json.loads((period / "delta/DELTA.json").read_bytes())
    assert descriptor["tables"]["dwd_disclosure"]["output_rows"] == 4
    # Cold replay must use only the newly built pair, including a generic parent.
    for path in (ods, dwd, *paths, period, windows):
        shutil.rmtree(path)
    from tests.test_base_build_ods import readonly

    readonly(monkeypatch, [next_ods, next_dwd])
    with Budget(tmp_path / "second-audit", [next_ods, next_dwd], limits()) as budget:
        receipt = audit_pair(
            ods=next_ods,
            dwd=next_dwd,
            contract=c,
            expected_input_identity=inputs.verified_identity,
            budget=budget,
        )
    assert receipt["publication_eligible"]
    assert len(receipt["closure"]["table_counts"]) == 33
