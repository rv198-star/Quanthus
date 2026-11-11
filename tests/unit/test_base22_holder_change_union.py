"""Conservative native/source event observations, never inferred transaction aliases."""

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, GlobalScan
from quanthus.pipelines.securities_warehouse.base22_holder_change_union import (
    CONTRACT_ID,
    NATIVE_MANIFEST,
    PURPOSE,
    TABLE,
    audit_change_union,
    change_union_batches,
    register_change_selection,
)
from tests.unit.test_base22_bounded import limits


def row(**changes):
    return {
        "record_source": "fixture",
        "source_record_id": "r",
        "source_revision": "v1",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "knowledge_to": None,
        "effective_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        "announced_at": None,
        "shareholding_change_id": "source-event",
        "company_id": "c",
        "security_id": "s",
        "shareholder_source_name": "literal-holder",
        "shareholder_id": None,
        "change_type": "reported",
        "share_change": None,
        "percentage_change": None,
        "post_holding": None,
        "post_percentage": None,
        "disclosure_id": None,
        "holder_match_key": "literal-name-key",
        "raw_share_change": None,
        "share_change_unit": None,
        "raw_percentage_change": None,
        "percentage_change_unit": None,
        "raw_post_holding": None,
        "post_holding_unit": None,
        "raw_post_percentage": None,
        "post_percentage_unit": None,
        "parser_version": "fixture",
        **changes,
    }


def selected(tmp_path, native, source):
    _, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)[TABLE]
    with Budget(tmp_path / "job", [], limits(memory_bytes=256 * 1024**2)) as budget:
        paths = []
        for name, rows in [("native", native), ("source", source)]:
            path = budget.root / (name + ".parquet")
            physical = pa.schema([pa.field(f.name, f.type, nullable=True) for f in schema])
            pq.write_table(pa.Table.from_pylist(rows, schema=physical), path)
            paths.append(path)
        with GlobalScan(budget) as scan:
            query = register_change_selection(scan, paths[:1], paths[1:])
            return [
                r for batch in change_union_batches(scan, query, schema) for r in batch.to_pylist()
            ]


def test_distinct_source_observation_preserves_zero_decimal_original_clock_and_null(tmp_path):
    from decimal import Decimal

    source = row(
        holder_match_key="another-holder",
        share_change=Decimal("0.0000000000"),
        raw_share_change="0.0000000000",
    )
    assert selected(tmp_path, [row(shareholding_change_id="native")], [source]) == [source]


@pytest.mark.parametrize(
    "defect", ["different-type", "different-provider", "different-id", "already-closed"]
)
def test_any_possible_native_holder_day_blocks_entire_source_series(tmp_path, defect):
    native = row(shareholding_change_id="native-event")
    if defect == "different-type":
        native["change_type"] = "another-reported-kind"
    elif defect == "different-provider":
        native["record_source"] = "another-provider"
    elif defect == "different-id":
        native["source_record_id"] = "different-source-id"
    else:
        native["knowledge_to"] = datetime(2026, 8, 2, tzinfo=timezone.utc)
    first = row(knowledge_to=datetime(2026, 8, 8, tzinfo=timezone.utc))
    second = row(knowledge_from=datetime(2026, 8, 8, tzinfo=timezone.utc))
    assert selected(tmp_path, [native], [first, second]) == []


def test_exact_source_series_retains_actual_closed_interval_without_native_alias(tmp_path):
    first = row(
        holder_match_key="new-holder", knowledge_to=datetime(2026, 8, 8, tzinfo=timezone.utc)
    )
    second = row(
        holder_match_key="new-holder", knowledge_from=datetime(2026, 8, 8, tzinfo=timezone.utc)
    )
    assert selected(tmp_path, [row()], [first, second]) == [first, second]


@pytest.mark.parametrize("unknown", [None, "", "  "])
def test_source_unknown_holder_identity_is_not_treated_as_new_event(tmp_path, unknown):
    with pytest.raises(BoundedError, match="SOURCE_IDENTITY_UNKNOWN"):
        selected(tmp_path, [row()], [row(holder_match_key=unknown)])


@pytest.mark.parametrize("defect", ["purpose", "PIT", "priority", "file", "manifest"])
def test_offline_union_rejects_resigned_invented_admission_before_sources(tmp_path, defect):
    from quanthus.pipelines.securities_warehouse import base22_holder_change_union as module

    body = {
        "contract_id": CONTRACT_ID,
        "purpose": PURPOSE,
        "evaluator_sha256": file_sha(Path(module.__file__)),
        "native_manifest_sha256": NATIVE_MANIFEST,
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "complete_weekly_union": False,
        "historical_PIT_admission": False,
        "cross_provider_alias_or_priority_granted": False,
        "physical_members": {},
    }
    if defect == "purpose":
        body["purpose"] = "accepted-Base22"
    elif defect == "PIT":
        body["historical_PIT_admission"] = True
    elif defect == "priority":
        body["cross_provider_alias_or_priority_granted"] = True
    elif defect == "manifest":
        body["native_manifest_sha256"] = "sha256:" + "0" * 64
    else:
        (tmp_path / "unlisted").write_bytes(b"disposable")
    body["identity"] = digest(body)
    (tmp_path / "QUALIFICATION.json").write_bytes(b"disposable")
    with pytest.raises(BoundedError, match="OFFLINE_DESCRIPTOR_OR_FILE_SET"):
        audit_change_union(tmp_path, body, None)
