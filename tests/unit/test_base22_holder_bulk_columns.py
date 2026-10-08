"""Bounded exact-current repairs preserve old atoms and reject wrong scope."""

from datetime import timedelta
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base22_holder_bulk_columns as module
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from tests.unit.test_base22_holder_current_columns import case


def test_batch_closes_only_exact_open_before_and_preserves_all_other_atoms():
    args = case()
    row = args["current"][0]
    closed = {**row, "knowledge_to": args["observed_at"] - timedelta(days=1)}
    other = {**row, "holder_rank": 2}
    original = [row, closed, other]
    output, found = module.apply_column_closures(original, {digest(row)}, args["observed_at"])
    assert output == [{**row, "knowledge_to": args["observed_at"]}, closed, other]
    assert found == {digest(row): 1}
    assert row["knowledge_to"] is None and original[1] == closed


@pytest.mark.parametrize("defect", ["closed", "same-clock", "backward-clock", "bound"])
def test_no_closed_history_or_backward_knowledge_revision(defect):
    args = case()
    row = dict(args["current"][0])
    if defect == "closed":
        row["knowledge_to"] = args["observed_at"]
    elif defect == "same-clock":
        row["knowledge_from"] = args["observed_at"]
    elif defect == "backward-clock":
        row["knowledge_from"] = args["observed_at"] + timedelta(seconds=1)
    rows = [row] * (2049 if defect == "bound" else 1)
    with pytest.raises(BoundedError, match="BULK_CURRENT_"):
        module.apply_column_closures(rows, {digest(row)}, args["observed_at"])


class MemoryBudget:
    def check(self, reserve=0):
        assert 0 <= reserve <= 512 * 1024**2


def test_metadata_index_retains_offsets_across_variable_arrow_chunks():
    import pyarrow as pa

    chunks = [
        pa.table({"source_artifact_sha256": ["source"] * n, "source_record_id": [str(n)] * n})
        for n in (3, 2, 5)
    ]
    table = pa.concat_tables(chunks)
    assert module.current_row_index(table, MemoryBudget()) == {
        ("source", "3"): [0, 1, 2],
        ("source", "2"): [3, 4],
        ("source", "5"): [5, 6, 7, 8, 9],
    }


@pytest.mark.parametrize("defect", ["empty", "unknown-source", "unknown-record", "raw-bound"])
def test_metadata_index_rejects_unproven_or_unbounded_raw_records(defect):
    import pyarrow as pa

    n = 0 if defect == "empty" else 81 if defect == "raw-bound" else 1
    table = pa.table(
        {
            "source_artifact_sha256": ["" if defect == "unknown-source" else "source"] * n,
            "source_record_id": [None if defect == "unknown-record" else "record"] * n,
        }
    )
    with pytest.raises(BoundedError, match="BULK_CURRENT_"):
        module.current_row_index(table, MemoryBudget())


@pytest.mark.parametrize(
    "defect", ["purpose", "ready", "PIT", "priority", "numeric", "evaluator", "closure", "extra"]
)
def test_resigned_bad_descriptor_fails_before_opening_any_source(tmp_path, defect):
    from quanthus.pipelines.securities_warehouse.base22_holder_current_columns import (
        REPLAY_MODULES,
    )

    body = {
        "contract_id": module.CONTRACT_ID,
        "purpose": module.PURPOSE,
        "evaluator_sha256": file_sha(Path(module.__file__)),
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "complete_weekly_union": False,
        "historical_PIT_admission": False,
        "numeric_or_percentage_token_changes_granted": False,
        "provider_priority_granted": False,
        "physical_members": {},
        "qualifier_code_closure": dict.fromkeys(REPLAY_MODULES, "not-opened"),
    }
    field = {
        "purpose": "purpose",
        "ready": "READY",
        "PIT": "historical_PIT_admission",
        "priority": "provider_priority_granted",
        "numeric": "numeric_or_percentage_token_changes_granted",
    }.get(defect)
    if field:
        body[field] = "wrong" if field == "purpose" else True
    elif defect == "evaluator":
        body["evaluator_sha256"] = "sha256:" + "0" * 64
    elif defect == "closure":
        body["qualifier_code_closure"] = {}
    else:
        (tmp_path / "unlisted").write_bytes(b"disposable")
    body["identity"] = digest(body)
    (tmp_path / "QUALIFICATION.json").write_bytes(b"disposable")
    with pytest.raises(BoundedError, match="DESCRIPTOR_OR_PHYSICAL_SET"):
        module.audit_bulk_columns(tmp_path, body, None)
