from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan, Limits
from quanthus.pipelines.securities_warehouse.base231_current_statement import current_statement
from quanthus.pipelines.securities_warehouse.base231_domain_pack import schemas
from quanthus.pipelines.securities_warehouse.base231_financial_integration import (
    financial_references,
    integrate_concepts,
    native_batches,
    report_revision_views,
)
from tests.unit.test_base231_current_statement import current


def rows():
    raw, source, resolver, observed = current("0")
    return current_statement(raw, source=source, resolver=resolver, observed_at=observed)[0]


def concept():
    return rows()["dwd_financial_concept"][0]


def test_legacy_same_clock_scope_variants_are_retained_and_later_capture_reuses():
    a = concept()
    b = dict(a, effective_from=a["effective_from"] + timedelta(days=1))
    new = dict(
        b,
        knowledge_from=b["knowledge_from"] + timedelta(days=1),
        source_record_id="new-synthetic-capture",
    )
    before = deepcopy([a, b, new])
    actual, proof = integrate_concepts([a, b], [new])
    assert actual == [a, b]
    assert proof["added_versions"] == 0 and proof["covering_native_versions_reused"] == 1
    assert [a, b, new] == before


def test_earlier_effective_scope_adds_new_real_clock_without_rewriting_old():
    a = concept()
    new = dict(
        a,
        effective_from=a["effective_from"] - timedelta(days=90),
        knowledge_from=a["knowledge_from"] + timedelta(days=1),
    )
    actual, proof = integrate_concepts([a], [new])
    assert a in actual and new in actual
    assert proof["old_atoms_unchanged"] and proof["added_versions"] == 1


def test_finite_native_end_and_gap_are_never_extended_or_filled():
    a = concept()
    a["knowledge_to"] = a["knowledge_from"] + timedelta(hours=1)
    new = dict(a, knowledge_from=a["knowledge_to"] + timedelta(days=1), knowledge_to=None)
    actual, _ = integrate_concepts([a], [new])
    assert a in actual and new in actual
    assert actual[0]["knowledge_to"] == a["knowledge_to"]


def test_concept_business_conflict_and_duplicate_native_versions_fail():
    a = concept()
    with pytest.raises(RuntimeError, match="SEMANTICS_CONFLICT"):
        integrate_concepts([a], [dict(a, concept_name="other-synthetic-concept")])
    with pytest.raises(RuntimeError, match="VERSION_CONFLICT"):
        integrate_concepts([a, a], [a])


def report_case(tmp_path, old, new):
    schema = schemas()["dwd_financial_report"]
    with (
        Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget,
        GlobalScan(budget) as scan,
    ):
        for name, values in [("old_reports", old), ("incoming_reports", new)]:
            p = budget.root / (name + ".parquet")
            pq.write_table(pa.Table.from_pylist(values, schema=schema), p)
            scan.add(name, [p])
        proof = report_revision_views(scan)
        result = [
            r
            for b in native_batches(scan, "integrated_reports", schema, ["knowledge_from"])
            for r in b.to_pylist()
        ]
        return result, proof


def test_report_new_version_closes_only_old_open_end_and_preserves_all_other_atoms(tmp_path):
    a = rows()["dwd_financial_report"][0]
    new = dict(
        a,
        knowledge_from=a["knowledge_from"] + timedelta(days=1),
        published_at=None,
        source_revision="qualified-synthetic-successor",
    )
    actual, proof = report_case(tmp_path, [a], [new])
    assert actual == [dict(a, knowledge_to=new["knowledge_from"]), new]
    assert proof["changed_old_report_intervals"] == 1
    assert a["knowledge_to"] is None


def test_finite_report_end_stays_exact_with_gap_before_new_observation(tmp_path):
    a = rows()["dwd_financial_report"][0]
    a["knowledge_to"] = a["knowledge_from"] + timedelta(hours=1)
    new = dict(a, knowledge_from=a["knowledge_to"] + timedelta(days=1), knowledge_to=None)
    actual, proof = report_case(tmp_path, [a], [new])
    assert actual == [a, new] and proof["changed_old_report_intervals"] == 0


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("provider", "CROSS_PROVIDER"),
        ("retro", "NOT_LATER"),
        ("equal", "NOT_LATER"),
        ("duplicate", "DUPLICATE"),
        ("finite", "FINITE_REPORT_END"),
    ],
)
def test_unauthorized_report_revisions_fail_closed(tmp_path, fault, reason):
    a = rows()["dwd_financial_report"][0]
    new = dict(a, knowledge_from=a["knowledge_from"] + timedelta(days=1))
    if fault == "provider":
        new["record_source"] = "other-provider"
    elif fault == "retro":
        new["knowledge_from"] = a["knowledge_from"] - timedelta(days=1)
    elif fault == "equal":
        new["knowledge_from"] = a["knowledge_from"]
    elif fault == "finite":
        a["knowledge_to"] = new["knowledge_from"] + timedelta(days=1)
    with pytest.raises(RuntimeError, match=reason):
        report_case(tmp_path, [a], [new, new] if fault == "duplicate" else [new])


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "company-clock",
        "company-date",
        "concept-clock",
        "concept-date",
        "report-date",
        "report-missing",
    ],
)
def test_financial_temporal_references_share_real_scope_without_listing_extension(tmp_path, fault):
    data = rows()
    report = data["dwd_financial_report"][0]
    fact = data["dwd_financial_fact"][0]
    c = data["dwd_financial_concept"][0]
    company = dict(
        company_id=report["company_id"],
        effective_from=c["effective_from"],
        effective_to=None,
        knowledge_from=c["knowledge_from"],
        knowledge_to=None,
    )
    if fault == "company-clock":
        company["knowledge_from"] += timedelta(days=1)
    elif fault == "company-date":
        company["effective_from"] = datetime(2026, 7, 1, tzinfo=timezone.utc)
    elif fault == "concept-clock":
        c["knowledge_from"] += timedelta(days=1)
    elif fault == "concept-date":
        c["effective_from"] = datetime(2026, 7, 1, tzinfo=timezone.utc)
    elif fault == "report-date":
        fact["period_end"] -= timedelta(days=1)
    elif fault == "report-missing":
        fact["financial_report_id"] = "missing"
    with (
        Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget,
        GlobalScan(budget) as scan,
    ):
        for name, table in [
            (
                "companies",
                pa.Table.from_pylist(
                    [company],
                    schema=pa.schema(
                        [
                            ("company_id", pa.string()),
                            *[
                                (f, pa.timestamp("us", tz="UTC"))
                                for f in [
                                    "effective_from",
                                    "effective_to",
                                    "knowledge_from",
                                    "knowledge_to",
                                ]
                            ],
                        ]
                    ),
                ),
            ),
            ("reports", pa.Table.from_pylist([report], schema=schemas()["dwd_financial_report"])),
            ("concepts", pa.Table.from_pylist([c], schema=schemas()["dwd_financial_concept"])),
            ("facts", pa.Table.from_pylist([fact], schema=schemas()["dwd_financial_fact"])),
        ]:
            path = budget.root / (name + ".parquet")
            pq.write_table(table, path)
            scan.add(name, [path])
        if fault:
            with pytest.raises(RuntimeError, match="TEMPORAL_REFERENCE"):
                financial_references(scan)
        else:
            financial_references(scan)
