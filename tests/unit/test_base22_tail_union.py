from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_tail_union as u
from quanthus.pipelines.securities_warehouse.base22 import (
    digest,
    file_sha,
    load_contract,
    partition,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)


def fixture(tmp_path):
    contract, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)[u.TABLE]
    rule = contract["tables"][u.TABLE]
    start = datetime(2026, 7, 30, 16, tzinfo=timezone.utc)
    old = dict(
        record_source="synthetic",
        source_record_id="retained",
        source_revision="old",
        source_artifact_sha256="sha256:" + "a" * 64,
        knowledge_from=start,
        knowledge_to=None,
        observation_start=start,
        observation_end=start + timedelta(days=1),
        listing_id="retained-listing",
        calendar_session_id="correct-calendar",
        interval_size="1d",
        price_basis="UNADJUSTED",
        currency="CNY",
        **{
            k: Decimal("1234567890123456789012345678.1234567890")
            for k in ("open", "high", "low", "close")
        },
        volume=Decimal("0.0000000000"),
        turnover=None,
        trade_count=None,
    )
    new = dict(
        old,
        listing_id="missing-listing",
        source_revision="new",
        knowledge_from=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )

    def save(name, rows):
        p = tmp_path / "inputs" / name
        p.parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
        return Member(p, file_sha(p), p.stat().st_size, len(rows))

    def proof(native, additions, rows):
        body = {
            "contract_id": u.CONTRACT_ID,
            "operation": "APPEND_ONLY_ABSENT_BUSINESS_KEYS",
            "partition": partition(new, rule),
            "native": [u.member_identity(m) for m in native],
            "additions": u.member_identity(additions),
            "allowed_additions": [u.addition_identity(r, rule) for r in rows],
            "repair_evidence_sha256": "sha256:" + "b" * 64,
            "history_descriptor_sha256": "sha256:" + "c" * 64,
            "configuration_sha256": digest({"schema": str(schema), "rule": rule}),
            "evaluator_sha256": file_sha(Path(u.__file__)),
            "consumer_admission": "NOT_GRANTED",
        }
        return {**body, "identity": digest(body)}

    return schema, rule, old, new, save, proof


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "existing-business",
        "other-native-file",
        "duplicate",
        "wrong-native-partition",
        "wrong-new-partition",
        "basis",
        "changed-input",
        "different-row",
        "wrong-schema",
        "proof",
    ],
)
def test_partition_union_exact_additions_and_no_old_replacements(tmp_path, failure):
    schema, rule, old, new, save, freeze = fixture(tmp_path)
    if failure == "existing-business":
        new["listing_id"] = old["listing_id"]  # Later knowledge is still an existing key.
    if failure == "wrong-native-partition":
        old["observation_start"] += timedelta(days=1)
    if failure == "wrong-new-partition":
        new["observation_start"] += timedelta(days=1)
    if failure == "basis":
        old["price_basis"] = "none"
    native = [save("old.parquet", [old])]
    if failure == "other-native-file":
        native.append(save("other-old.parquet", [dict(new, knowledge_from=old["knowledge_from"])]))
    rows = [new, new] if failure == "duplicate" else [new]
    added = save("added.parquet", rows)
    plan = freeze(native, added, rows)
    if failure == "different-row":
        plan["allowed_additions"][0]["row_sha256"] = "sha256:" + "e" * 64
        plan["identity"] = digest({k: v for k, v in plan.items() if k != "identity"})
    if failure == "changed-input":
        save("added.parquet", [dict(new, source_revision="changed-after-freeze")])
    if failure == "wrong-schema":
        pq.write_table(pa.table({"only": [1]}), added.path)
        added = Member(added.path, file_sha(added.path), added.path.stat().st_size, 1)
        plan = freeze(native, added, rows)
    if failure == "proof":
        plan["consumer_admission"] = "PASS"
    input_bytes = {m.path: m.path.read_bytes() for m in [*native, added]}
    limits = Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2)
    with Budget(tmp_path / "job", [tmp_path / "inputs"], limits) as budget:
        out = budget.root / "daily" / "part.parquet"
        kwargs = dict(proof=plan, schema=schema, rule=rule, output=out, budget=budget)
        if failure:
            with pytest.raises(BoundedError):
                u.append_missing_bar_partition(native, added, **kwargs)
            assert not out.exists()
        else:
            result = u.append_missing_bar_partition(native, added, **kwargs)
            assert pq.ParquetFile(out).read().to_pylist() == [old, new]
            assert result["native_rows_preserved"] == result["new_missing_observation_rows"] == 1
            assert result["native_row_replacements"] == result["state_gap_changes"] == 0
            assert out.stat().st_nlink == 1 and out.stat().st_ino != native[0].path.stat().st_ino
            before = out.read_bytes()
            with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
                u.append_missing_bar_partition(native, added, **kwargs)
            assert out.read_bytes() == before
        assert all(p.read_bytes() == content for p, content in input_bytes.items())


def test_freeze_rejects_unknown_evaluator_before_any_output(tmp_path):
    schema, rule, old, new, save, freeze = fixture(tmp_path)
    native, added = [save("old.parquet", [old])], save("new.parquet", [new])
    proof = freeze(native, added, [new])
    proof["evaluator_sha256"] = "sha256:" + "0" * 64
    proof["identity"] = digest({k: v for k, v in proof.items() if k != "identity"})
    with Budget(tmp_path / "job", [tmp_path / "inputs"], Limits(free_floor_bytes=1)) as budget:
        with pytest.raises(BoundedError, match="FROZEN_PROOF_INVALID"):
            u.append_missing_bar_partition(
                native,
                added,
                proof=proof,
                schema=schema,
                rule=rule,
                output=budget.root / "output.parquet",
                budget=budget,
            )
        assert list(budget.root.iterdir()) == []
