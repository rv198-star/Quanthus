"""Retained Daily qualification preserves values and independently dated parents."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_retained_core import (
    QUALIFICATION,
    reobserve_partition,
)
from tests.unit.test_base23_core import run_day, september_case


def retained_case(table):
    raw, status, args = september_case()
    output, _, _ = run_day(raw, status, args)
    row = output[table][0]
    row["knowledge_from"] -= timedelta(days=1)
    if table == "dwd_security_bar":
        row["price_basis"] = "none"
    contract, model, extension = load_contract(Path("config/pipelines"))
    kwargs = dict(
        table=table,
        day=args["market_date"],
        schema=schemas(model, extension)[table],
        rule=contract["tables"][table],
        listings=[args["listing"]],
        indexes=[],
        original_calendars=[args["calendar"]],
        qualified_calendars=[args["calendar"]],
        observed_at=args["observed_at"],
        member_sha256=digest(["retained-file"]),
        package_manifest_sha256=digest(["whole-original-package"]),
        qualification=QUALIFICATION,
        input_digest=digest(["complete-input-closure"]),
    )
    return row, kwargs


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_new_governance_version_preserves_original_values_times_and_archive(tmp_path, table):
    row, kwargs = retained_case(table)
    # A physically typed retained Parquet has the schema's declared decimal scale.
    row = pa.Table.from_pylist([row], schema=kwargs["schema"]).to_pylist()[0]
    before = deepcopy((row, kwargs))
    output, proof = reobserve_partition([row], **kwargs)
    assert proof["original_raw_core_recovered"] is False
    assert proof["provider_acquisition"] is None
    assert proof["accepted_child_inferred"] is False
    assert proof["row_evidence"][0]["original_warehouse_knowledge_from"] == row["knowledge_from"]
    assert output[0]["knowledge_from"] == kwargs["observed_at"]
    assert output[0]["observation_start"] == row["observation_start"]
    if table == "dwd_security_bar":
        assert output[0]["price_basis"] == "UNADJUSTED"
        assert output[0]["close"].as_tuple() == row["close"].as_tuple()
    else:
        assert "interval_size" not in row
        assert all(output[0][f] == row[f] for f in ["is_tradable", "can_buy", "can_sell"])
    file = tmp_path / "retained.parquet"
    typed = pa.Table.from_pylist(output, schema=kwargs["schema"])
    pq.write_table(typed, file)
    assert pq.ParquetFile(file).read().equals(typed)
    assert (row, kwargs) == before


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
@pytest.mark.parametrize("fault", ["original-date", "new-date", "original-venue", "new-venue"])
def test_both_original_and_new_calendar_date_venue_are_required(table, fault):
    row, kwargs = retained_case(table)
    kwargs["original_calendars"] = deepcopy(kwargs["original_calendars"])
    kwargs["qualified_calendars"] = deepcopy(kwargs["qualified_calendars"])
    name = "original_calendars" if fault.startswith("original") else "qualified_calendars"
    if fault.endswith("date"):
        kwargs[name][0]["trade_date"] += timedelta(days=1)
    else:
        kwargs[name][0]["venue_id"] = "another-market"
    with pytest.raises(BoundedError, match="CALENDAR_DATE_VENUE"):
        reobserve_partition([row], **kwargs)


@pytest.mark.parametrize("fault", ["grade", "future", "duplicate", "minute", "basis", "null"])
def test_unqualified_scope_or_missing_critical_atoms_never_reobserve(fault):
    row, kwargs = retained_case("dwd_security_bar")
    rows = [row]
    if fault == "grade":
        kwargs["qualification"] = "RAW_ORIGINAL_RECOVERED"
    elif fault == "future":
        row["knowledge_from"] = kwargs["observed_at"] + timedelta(seconds=1)
    elif fault == "duplicate":
        rows.append(deepcopy(row))
    elif fault == "minute":
        row["interval_size"] = "1m"
    elif fault == "basis":
        row["price_basis"] = "hfq"
    else:
        row["close"] = None
    with pytest.raises(BoundedError, match="RETAINED_CORE"):
        reobserve_partition(rows, **kwargs)


@pytest.mark.parametrize("table", ["dwd_security_valuation", "dwd_index_bar"])
def test_retained_typed_measures_preserve_decimal_null_zero_and_business_times(tmp_path, table):
    from quanthus.pipelines.securities_warehouse.base22_daily_measures import (
        index_observation,
        valuation_observation,
    )
    from tests.unit.test_base22_daily_measures import index_case, valuation_case

    _, kwargs = retained_case("dwd_security_bar")
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    if table == "dwd_security_valuation":
        raw, args, _ = valuation_case()
        rows, _ = valuation_observation(raw, **args)
        kwargs["listings"] = [args["listing"]]
    else:
        raw, args, _ = index_case()
        rows, _ = index_observation(raw, **args)
        kwargs["indexes"] = [args["index"]]
    for row in rows:
        row["observation_start"] += timedelta(days=31)
        row["observation_end"] += timedelta(days=31)
    kwargs.update(table=table, schema=ss[table], rule=contract["tables"][table])
    rows = pa.Table.from_pylist(rows, schema=ss[table]).to_pylist()
    before = deepcopy(rows)
    output, proof = reobserve_partition(rows, **kwargs)
    assert proof["numeric_classifications"]["ZERO"] == 1
    assert proof["numeric_classifications"]["NULL_UNAVAILABLE"] == 1
    for original, result in zip(rows, output, strict=True):
        for field in ss[table]:
            if pa.types.is_decimal(field.type):
                assert original[field.name] == result[field.name]
                if original[field.name] is not None:
                    assert original[field.name].as_tuple() == result[field.name].as_tuple()
    path = tmp_path / "measures.parquet"
    pq.write_table(pa.Table.from_pylist(output, schema=ss[table]), path)
    assert pq.ParquetFile(path).read().to_pylist() == output
    assert rows == before
