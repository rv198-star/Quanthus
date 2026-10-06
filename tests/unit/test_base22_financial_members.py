"""Metadata-only regression for the actual flat native company layout."""

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_financial_members import native_members


def test_flat_company_and_partitioned_financial_members_are_both_exact():
    paths = [
        "payload/identity/dwd_listed_company.parquet",
        "payload/financial_reporting/dwd_financial_fact/period_end_year=2025/part.parquet",
        "payload/other/not_dwd_listed_company.parquet",
        "evidence/dwd_listed_company.parquet",
    ]
    manifest = {"files": [{"path": p, "sha256": "synthetic"} for p in paths]}
    before = repr(manifest)
    assert native_members(manifest, "dwd_listed_company") == [manifest["files"][0]]
    assert native_members(manifest, "dwd_financial_fact") == [manifest["files"][1]]
    assert repr(manifest) == before


@pytest.mark.parametrize(
    "fault", ["empty", "wrong-table", "duplicate", "absolute", "parent", "alias", "missing-path"]
)
def test_ambiguous_or_missing_native_selection_fails_before_any_io(fault):
    entry = {"path": "payload/identity/dwd_listed_company.parquet"}
    manifest = {"files": [entry]}
    table = "dwd_listed_company"
    if fault == "empty":
        manifest["files"] = []
    elif fault == "wrong-table":
        table = "dwd_security_bar"
    elif fault == "duplicate":
        manifest["files"].append(dict(entry))
    elif fault == "absolute":
        entry["path"] = "/payload/identity/dwd_listed_company.parquet"
    elif fault == "parent":
        entry["path"] = "payload/../dwd_listed_company.parquet"
    elif fault == "alias":
        entry["path"] = "payload//identity/dwd_listed_company.parquet"
    elif fault == "missing-path":
        entry.clear()
    before = repr(manifest)
    with pytest.raises(BoundedError):
        native_members(manifest, table)
    assert repr(manifest) == before
