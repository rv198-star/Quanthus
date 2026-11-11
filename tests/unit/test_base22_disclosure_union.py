from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_disclosure_union as u
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
    GlobalScan,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.base22_tail_union import member_identity
from quanthus.pipelines.securities_warehouse.disclosure_event import materialize_announcement_rows


@pytest.mark.parametrize("table", sorted(u.ENTITIES))
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "existing",
        "other-file",
        "changed-publication-existing-entity",
        "duplicate-version",
        "census",
        "schema",
        "partition",
        "native-partition",
        "changed-input",
        "proof",
        "source-proof",
    ],
)
def test_append_preserves_native_and_only_absent_entities(tmp_path, table, fault):
    contract, model, extension = load_contract(Path("config/pipelines"))
    schema, rule = schemas(model, extension)[table], contract["tables"][table]
    clock = datetime(2026, 8, 4, tzinfo=timezone.utc)

    def observation(identifier):
        return materialize_announcement_rows(
            {
                "ann_id": identifier,
                "publish_time": "2026-07-25 09:00:00",
                "title": "synthetic",
                "category": "UNCLASSIFIED",
                "url": None,
            },
            company_id="company",
            security_id="security",
            source_sha256="sha256:" + "a" * 64,
            source_revision="synthetic",
            parent_knowledge_from=clock,
        )[table][0]

    old, new = observation("old"), observation("new")
    if fault in {"existing", "changed-publication-existing-entity"}:
        new = dict(old, knowledge_from=clock + timedelta(days=1))
        if fault == "changed-publication-existing-entity":
            new["effective_at"] += timedelta(days=1)
    if fault == "partition":
        new["effective_at"] += timedelta(days=10)
    if fault == "native-partition":
        old["effective_at"] += timedelta(days=10)

    def save(name, rows):
        path = tmp_path / "inputs" / name
        path.parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
        return Member(path, file_sha(path), path.stat().st_size, len(rows))

    native = [save("native.parquet", [old])]
    if fault == "other-file":
        native.append(save("other.parquet", [dict(new)]))
    added = save("added.parquet", [new, new] if fault == "duplicate-version" else [new])
    # Freeze without invoking the production validation: invalid inputs still
    # reach the exact rejection point before the output writer starts.
    proof = {
        "contract_id": u.CONTRACT_ID,
        "table": table,
        "partition": partition(observation("new"), rule),
        "native": [member_identity(m) for m in native],
        "additions": member_identity(added),
        "added_row_census_sha256": digest(
            sorted(digest(r) for r in ([new, new] if fault == "duplicate-version" else [new]))
        ),
        "whole_native_absence_proof_sha256": "sha256:" + "b" * 64,
        "source_qualification_sha256": "sha256:" + "c" * 64,
        "configuration_sha256": digest({"schema": str(schema), "rule": rule}),
        "evaluator_sha256": file_sha(Path(u.__file__)),
        "consumer_admission": "NOT_GRANTED",
    }
    if fault == "census":
        proof["added_row_census_sha256"] = "sha256:" + "d" * 64
    if fault == "source-proof":
        proof["source_qualification_sha256"] = "sha256:invalid"
    if fault == "schema":
        pq.write_table(pa.table({"wrong": [1]}), added.path)
        added = Member(added.path, file_sha(added.path), added.path.stat().st_size, 1)
        proof["additions"] = member_identity(added)
    proof["identity"] = digest(proof)
    if fault == "proof":
        proof["identity"] = "sha256:" + "e" * 64
    if fault == "changed-input":
        save("added.parquet", [dict(new, source_revision="changed")])
    before = {m.path: m.path.read_bytes() for m in [*native, added]}
    with Budget(tmp_path / "job", [tmp_path / "inputs"], Limits(free_floor_bytes=1)) as budget:
        output = budget.root / "payload" / "output.parquet"
        if fault:
            with pytest.raises(BoundedError):
                u.append_absent_partition(
                    native,
                    added,
                    proof=proof,
                    schema=schema,
                    rule=rule,
                    output=output,
                    budget=budget,
                )
            assert not output.exists()
        else:
            receipt = u.append_absent_partition(
                native, added, proof=proof, schema=schema, rule=rule, output=output, budget=budget
            )
            assert receipt["native_rows_preserved"] == receipt["added_rows"] == 1
            assert receipt["old_row_replacements"] == 0
            assert pq.ParquetFile(output).read().to_pylist() == [old, new]
            assert output.stat().st_nlink == 1
            with GlobalScan(budget) as scan:
                scan.add(table, [output])
                scan.keys(table, rule["business_key"], rule["version_key"])
                scan.row_rules(table, schema, rule)
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("rows", [0, u.MAX_ADDITIONS + 1])
def test_incoming_partition_has_explicit_bound(tmp_path, rows):
    with pytest.raises(BoundedError, match="SCOPE_TOO_LARGE_OR_EMPTY"):
        u.observation_census(
            Member(tmp_path / "not-opened", "unread", 0, rows), schema=None, rule={}
        )
