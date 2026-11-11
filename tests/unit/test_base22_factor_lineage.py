"""Complete disposable raw replay, including globally closed factor intervals."""

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_factor_lineage import replay_factor_lineage
from quanthus.pipelines.securities_warehouse.disclosure_event import (
    close_adjustment_factor_intervals,
    materialize_adjustment_factor_rows,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
)


def fixture():
    start = datetime(2010, 1, 1, tzinfo=timezone.utc)
    resolver = FinancialCompanyResolver(
        [
            FinancialCompanyIdentity("000001", "security", "company", start, None, start),
        ]
    )
    raw = [
        {
            "stock_code": "000001",
            "effective_date": date(2026, 7, day),
            "fore_adjust_factor": 1.0000000000000002,
            "back_adjust_factor": 0.0,
            "adjust_factor": 1.0,
            "adj_version": "revision",
            "source_revision": "source-revision",
            "knowledge_cutoff": date(2026, 7, 31),
            "visibility_origin": "harvest_day_exception|no_proveable_announce",
        }
        for day in (1, 10)
    ]
    exceptions = [
        {
            **r,
            "effective_date": r["effective_date"].isoformat(),
            "exception_type": "factor_pit_visibility",
            "reason_code": "no_proveable_announce_after_em_exact_link",
            "policy": (
                "factor_unavailable_before_knowledge_cutoff; no forge; degrade/block adj features"
            ),
        }
        for r in raw
    ]
    vocabulary = json.loads(
        Path("config/pipelines/disclosure-event-vocabulary.v1.json").read_bytes()
    )
    tables = {"dwd_adjustment_factor": [], "dwd_adjustment_factor_input": []}
    for r in raw:
        projected = materialize_adjustment_factor_rows(
            r,
            source_sha256="sha256:" + "a" * 64,
            company_security_id="security",
            vocabulary=vocabulary,
            parent_knowledge_from=start,
        )
        for table, rows in projected.items():
            tables[table].extend(rows)
    close_adjustment_factor_intervals(*tables.values())
    for row in tables["dwd_adjustment_factor"]:
        row["factor_value"] = row["factor_value"].quantize(Decimal("1e-18"))
    return raw, exceptions, resolver, vocabulary, tables


def run(values):
    raw, exceptions, resolver, vocabulary, tables = values
    return replay_factor_lineage(
        iter(raw),
        iter(exceptions),
        {k: iter(v) for k, v in tables.items()},
        resolver=resolver,
        vocabulary=vocabulary,
        raw_sha256="sha256:" + "a" * 64,
    )


def test_all_methods_exact_typed_replay_and_unavailable_keys_are_not_admitted():
    values = fixture()
    original = deepcopy((values[0], values[1], values[4]))
    proof = run(values)
    assert proof["raw_rows"] == 2 and proof["factor_rows"] == 6
    assert proof["exception_rows"] == 2 and proof["unavailable_native_factor_versions"] == 6
    assert proof["normalization_action_dependency_rows"] == 0
    assert proof["historical_PIT_admission"] is False
    assert proof["provider_acquisition_inferred"] is False
    assert (values[0], values[1], values[4]) == original
    # Original double -> legacy Decimal(38,18) is reproduced, not claimed to be
    # the original vendor's exact decimal text or an approved new price basis.
    assert any(
        row["factor_value"] == Decimal("1.0000000000000002")
        for row in values[4]["dwd_adjustment_factor"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "value",
        "time",
        "close",
        "source",
        "evidence",
        "input",
        "duplicate",
        "missing",
        "source_key",
        "exception_value",
        "exception_revision",
        "exception_date",
        "exception_policy",
        "exception_missing",
        "raw_duplicate",
        "raw_future",
    ],
)
def test_repacked_or_incomplete_source_and_native_closures_fail(mutation):
    values = fixture()
    raw, exceptions, _, _, tables = values
    factor = tables["dwd_adjustment_factor"][0]
    if mutation == "value":
        factor["factor_value"] += Decimal("0.000000000000000001")
    elif mutation == "time":
        factor["knowledge_from"] = datetime(2026, 7, 1, tzinfo=timezone.utc)
    elif mutation == "close":
        factor["effective_to"] = None
    elif mutation == "source":
        factor["source_artifact_sha256"] = "sha256:" + "b" * 64
    elif mutation == "evidence":
        factor["evidence_set_digest"] = "sha256:" + "b" * 64
    elif mutation == "input":
        tables["dwd_adjustment_factor_input"][0]["corporate_action_id"] = "pretend-action"
    elif mutation == "duplicate":
        tables["dwd_adjustment_factor"].append(factor.copy())
    elif mutation == "missing":
        tables["dwd_adjustment_factor"].pop()
    elif mutation == "source_key":
        factor["source_record_id"] = "wrong"
    elif mutation == "exception_value":
        exceptions[0]["fore_adjust_factor"] = 2.0
    elif mutation == "exception_revision":
        exceptions[0]["source_revision"] = "unknown"
    elif mutation == "exception_date":
        exceptions[0]["effective_date"] = "2026-08-03"
    elif mutation == "exception_policy":
        exceptions[0]["policy"] = "allow"
    elif mutation == "exception_missing":
        exceptions.pop()
    elif mutation == "raw_duplicate":
        raw.append(raw[0].copy())
    elif mutation == "raw_future":
        raw[0]["effective_date"] = date(2026, 9, 1)
    with pytest.raises(BoundedError):
        run(values)


def test_prelisting_exception_remains_unavailable_and_does_not_create_factors():
    values = fixture()
    raw, exceptions, resolver, _, _ = values
    earlier = dict(raw[0], effective_date=date(2009, 1, 1))
    raw.append(earlier)
    exceptions.append(dict(exceptions[0], effective_date="2009-01-01"))
    proof = run(values)
    assert proof["exception_rows"] == 3
    assert proof["unavailable_excluded_raw_rows"] == 1
    assert proof["excluded_raw_dispositions"] == {"BEFORE_FIRST_LISTED_COMPANY_IDENTITY": 1}


def test_input_iterators_are_bounded_before_accepting_oversized_history():
    values = list(fixture())
    raw = dict(values[0][0], visibility_origin="corp_actions.announce_date")
    values[0] = [dict(raw, effective_date=date.fromordinal(i + 1)) for i in range(65537)]
    with pytest.raises(BoundedError):
        run(values)
