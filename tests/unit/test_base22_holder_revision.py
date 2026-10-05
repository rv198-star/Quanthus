from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.base22_holder_exact import PARSER, load_holder_contract
from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
    load_native_holder_contract,
)
from quanthus.pipelines.securities_warehouse.base22_holder_revision import (
    CONTRACT_ID,
    audit_holder_revision_scope,
    materialize_holder_revision_partition,
    validate_holder_revision_partition,
)
from tests.unit.test_base22_independent_image import _row


def _inputs(tmp_path, defect=None):
    _, _, exact = load_native_holder_contract(Path("config/pipelines"))
    _, capture_schema = load_holder_contract(Path("config/pipelines"))
    old = _row(exact)
    old.update(
        record_source="same-vendor",
        published_at=old["knowledge_from"],
        raw_average_holding="1.1234567890123",
        average_holding=Decimal("1.1234567890"),
        average_holding_exact=Decimal("1.1234567890123"),
    )
    new = dict(
        old,
        raw_holder_count="42",
        holder_count=42,
        raw_average_holding="2.12345678901",
        average_holding=None,
        average_holding_exact=Decimal("2.12345678901"),
        parser_version=PARSER,
        published_at=None,
        knowledge_from=old["knowledge_from"] + timedelta(days=4),
        source_revision="observed-new",
        source_artifact_sha256="a" * 64,
    )
    if defect == "equal-clock":
        new["knowledge_from"] = old["knowledge_from"]
    if defect == "earlier-clock":
        new["knowledge_from"] = old["knowledge_from"] - timedelta(days=1)
    if defect == "other-provider":
        new["record_source"] = "other-provider"
    if defect == "other-company":
        new["company_id"] = "other-company"
    if defect == "other-security":
        new["security_id"] = "other-security"
    if defect == "other-period":
        new["period_end"] -= timedelta(days=1)
    if defect == "other-scope":
        new["observation_scope"] = "other-scope"
    if defect == "unknown-entity":
        new["shareholder_count_observation_id"] = "unknown"
    if defect == "closed-old":
        old["knowledge_to"] = new["knowledge_from"]
    if defect == "closed-new":
        new["knowledge_to"] = new["knowledge_from"] + timedelta(days=1)
    if defect == "invented-publication":
        new["published_at"] = new["knowledge_from"]
    if defect == "new-rounding":
        new["average_holding_exact"] = Decimal("2.12345678900")
    if defect == "wrong-parser":
        new["parser_version"] = "legacy"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    native = inputs / "native.parquet"
    capture = inputs / "capture.parquet"
    pq.write_table(
        pa.Table.from_pylist([old] * (2 if defect == "duplicate-native" else 1), schema=exact),
        native,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [new] * (2 if defect == "duplicate-capture" else 1), schema=capture_schema
        ),
        capture,
    )

    def member(path):
        return Member(
            path, file_sha(path), path.stat().st_size, pq.ParquetFile(path).metadata.num_rows
        )

    return exact, capture_schema, [member(native)], member(capture), old, new


def _limits():
    return Limits(
        allocated_bytes=32 * 1024**2,
        temp_bytes=16 * 1024**2,
        free_floor_bytes=1,
        rss_bytes=4 * 1024**3,
        wall_seconds=60,
    )


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "equal-clock",
        "earlier-clock",
        "other-provider",
        "other-company",
        "other-security",
        "other-period",
        "other-scope",
        "unknown-entity",
        "closed-old",
        "closed-new",
        "invented-publication",
        "new-rounding",
        "wrong-parser",
        "duplicate-native",
        "duplicate-capture",
    ],
)
def test_only_real_later_same_provider_same_entity_closes_old_interval(tmp_path, defect):
    exact, cap, native, capture, old, new = _inputs(tmp_path, defect)
    before = {m.path: m.path.read_bytes() for m in [*native, capture]}
    with Budget(tmp_path / "job", [tmp_path / "inputs"], _limits()) as budget:
        output = budget.root / "part.parquet"
        if defect:
            with pytest.raises(BoundedError):
                materialize_holder_revision_partition(
                    native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
                )
            assert not output.exists()
        else:
            result = materialize_holder_revision_partition(
                native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
            )
            rows = pq.ParquetFile(output).read().to_pylist()
            expected = dict(old, knowledge_to=new["knowledge_from"])
            assert rows[0] == expected and rows[1] == new
            assert rows[1]["published_at"] is None
            assert result["old_knowledge_ends_closed"] == 1 and result["rows"] == 2
            assert output.stat().st_nlink == 1
            with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
                materialize_holder_revision_partition(
                    native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
                )
    assert before == {m.path: m.path.read_bytes() for m in [*native, capture]}


@pytest.mark.parametrize(
    "change",
    [
        "old-value",
        "old-raw",
        "old-time",
        "old-end",
        "new-value",
        "new-time",
        "lost-old",
        "lost-new",
        "extra-row",
    ],
)
def test_offline_full_typed_replay_rejects_allowed_file_repack_tampering(tmp_path, change):
    exact, cap, native, capture, _, _ = _inputs(tmp_path)
    with Budget(tmp_path / "job", [tmp_path / "inputs"], _limits()) as budget:
        output = budget.root / "part.parquet"
        materialize_holder_revision_partition(
            native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
        )
        rows = pq.ParquetFile(output).read().to_pylist()
        if change == "old-value":
            rows[0]["average_holding"] += Decimal("1")
        if change == "old-raw":
            rows[0]["raw_average_holding"] = "0"
        if change == "old-time":
            rows[0]["published_at"] += timedelta(seconds=1)
        if change == "old-end":
            rows[0]["knowledge_to"] += timedelta(seconds=1)
        if change == "new-value":
            rows[1]["average_holding_exact"] += Decimal("0.0000000000001")
        if change == "new-time":
            rows[1]["knowledge_from"] += timedelta(seconds=1)
        if change == "lost-old":
            rows = rows[1:]
        if change == "lost-new":
            rows = rows[:1]
        if change == "extra-row":
            rows.append(dict(rows[1]))
        pq.write_table(pa.Table.from_pylist(rows, schema=exact), output)
        # This passes no stale output hash to the validator. Exact source-derived
        # typed semantics, including the one permitted knowledge_to edit, reject it.
        with pytest.raises(BoundedError):
            validate_holder_revision_partition(
                native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "candidate"),
        ("projection_contract_id", "legacy"),
        ("consumer_admission", "ADMITTED"),
        ("whole_image_schema_admission", "APPROVED"),
        ("whole33_acceptance", True),
        ("READY", True),
        ("original_4173_visibility", "ZEROED"),
    ],
)
def test_cold_revision_scope_rejects_admission_or_availability_laundering(tmp_path, field, value):
    from quanthus.pipelines.securities_warehouse.base22 import digest

    d = {
        "kind": "isolated_later_holder_native_union_not_complete_base22",
        "projection_contract_id": CONTRACT_ID,
        "consumer_admission": "NOT_GRANTED",
        "whole_image_schema_admission": "pending_final_candidate_review",
        "whole33_acceptance": False,
        "READY": False,
        "original_4173_visibility": "UNCHANGED_UNAVAILABLE",
    }
    d[field] = value
    d["identity"] = digest(d)
    with (
        Budget(tmp_path / "job", [], _limits()) as budget,
        pytest.raises(BoundedError, match="SCOPE_CONTRACT_INVALID"),
    ):
        audit_holder_revision_scope(budget.root, d, budget)
