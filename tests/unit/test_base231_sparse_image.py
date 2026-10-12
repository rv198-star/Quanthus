"""Financial source atoms cannot overwrite history during sparse integration."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base231_sparse_image as adapter
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan, Limits

TABLE = "dwd_financial_fact"
SCHEMA = pa.schema(
    [
        ("financial_fact_id", pa.string()),
        ("value_decimal", pa.decimal128(24, 11)),
        ("source_artifact_sha256", pa.string()),
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("knowledge_from", pa.timestamp("us", tz="UTC")),
        ("knowledge_to", pa.timestamp("us", tz="UTC")),
    ]
)
OLD = datetime(2026, 10, 11, tzinfo=timezone.utc)
NEW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def atom(key="old", *, value="1.00000000001", source="original", published=None, known=OLD):
    return {
        "financial_fact_id": key,
        "value_decimal": Decimal(value),
        "source_artifact_sha256": source,
        "published_at": published,
        "knowledge_from": known,
        "knowledge_to": None,
    }


def select(root, old, new):
    with Budget(root, [], Limits(free_floor_bytes=1024**2)) as budget, GlobalScan(budget) as scan:
        for name, rows in [("prior_" + TABLE, old), ("sparse_" + TABLE, new)]:
            path = root / (name + ".parquet")
            pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)
            scan.add(name, [path])
        counts = adapter.select_absent(scan, TABLE, SCHEMA)
        rows = scan.connection.execute("SELECT * FROM absent_" + TABLE).fetchall()
        return counts, rows


def test_equal_original_atom_keeps_old_clock_and_only_new_key_is_output(tmp_path):
    counts, rows = select(tmp_path / "job", [atom()], [atom(known=NEW), atom("new", known=NEW)])
    assert counts == {"source_rows": 2, "new_identifiers": 1, "equal_existing_identifiers": 1}
    assert rows == [("new", Decimal("1.00000000001"), "original", None, NEW, None)]


@pytest.mark.parametrize(
    "change",
    [
        {"value": "1.00000000002"},
        {"source": "unknown"},
        {"published": NEW},
    ],
)
def test_revised_value_source_or_invented_publication_cannot_silently_win(tmp_path, change):
    with pytest.raises(RuntimeError, match="OVERLAPPING_ATOM_CONFLICT"):
        select(tmp_path / "job", [atom()], [atom(known=NEW, **change)])


def test_repeated_source_identifier_fails_instead_of_multiplying_output(tmp_path):
    with pytest.raises(RuntimeError, match="AMBIGUOUS_IDENTIFIER"):
        select(tmp_path / "job", [atom()], [atom("new"), atom("new")])


def test_identical_historical_knowledge_versions_are_preserved(tmp_path):
    counts, rows = select(tmp_path / "job", [atom(), atom(known=NEW)], [atom(known=NEW)])
    assert counts["new_identifiers"] == 0
    assert rows == []


def test_conflict_in_an_old_version_is_not_hidden_by_latest_version(tmp_path):
    with pytest.raises(RuntimeError, match="OVERLAPPING_ATOM_CONFLICT"):
        select(tmp_path / "job", [atom(), atom(value="2", known=NEW)], [atom(known=NEW)])


def test_unknown_identifier_is_not_treated_as_missing_record(tmp_path):
    bad = atom()
    bad["financial_fact_id"] = None
    with pytest.raises(RuntimeError, match="AMBIGUOUS_IDENTIFIER"):
        select(tmp_path / "job", [atom()], [bad])


def local_scope(root, monkeypatch):
    parent = root / "inputs/predecessor"
    sparse = root / "inputs/sparse"
    parent.mkdir(parents=True)
    sparse.mkdir(parents=True)
    (parent / "input-plan.json").write_bytes(canonical({"synthetic": True}))
    (parent / "MATERIALIZATION.json").write_bytes(
        canonical(
            {
                "evidence": {"evidence/input-plan.json": file_sha(parent / "input-plan.json")},
                "layout": {},
            }
        )
    )
    (parent / "manifest.json").write_bytes(
        canonical(
            {
                "materialization_sha256": file_sha(parent / "MATERIALIZATION.json"),
            }
        )
    )
    (sparse / "manifest.json").write_bytes(canonical({"synthetic": True}))
    monkeypatch.setattr(adapter, "PREDECESSOR", file_sha(parent / "manifest.json"))
    monkeypatch.setattr(adapter, "SPARSE", file_sha(sparse / "manifest.json"))
    delta = {
        "outputs": {t: [] for t, n in adapter.EXPECTED.items() if n},
        "census": {},
        "old_value_changes": 0,
        "old_history_deleted": 0,
        "new_provider_or_publication_times_inferred": 0,
    }
    return adapter.write_scope(root, delta, code_revision="synthetic")


def test_offline_scope_cannot_include_unregistered_physical_evidence(tmp_path, monkeypatch):
    local_scope(tmp_path, monkeypatch)
    (tmp_path / "extra").write_bytes(b"unregistered")
    with pytest.raises(RuntimeError, match="PHYSICAL_SET_CHANGED"):
        adapter.validate_local_scope(tmp_path)


def test_resealed_changed_original_authority_still_fails(tmp_path, monkeypatch):
    scope = local_scope(tmp_path, monkeypatch)
    name = "inputs/sparse/manifest.json"
    (tmp_path / name).write_bytes(canonical({"replacement": True}))
    scope["physical_members"][name] = {
        "sha256": file_sha(tmp_path / name),
        "size_bytes": (tmp_path / name).stat().st_size,
    }
    scope["identity"] = digest({k: v for k, v in scope.items() if k != "identity"})
    (tmp_path / "SCOPE.json").write_bytes(canonical(scope))
    with pytest.raises(RuntimeError, match="ORIGINAL_AUTHORITY_CHANGED"):
        adapter.validate_local_scope(tmp_path)


def test_changed_parent_plan_fails_even_if_all_new_scope_hashes_match(tmp_path, monkeypatch):
    scope = local_scope(tmp_path, monkeypatch)
    name = "inputs/predecessor/input-plan.json"
    (tmp_path / name).write_bytes(canonical({"unqualified": True}))
    scope["physical_members"][name] = {
        "sha256": file_sha(tmp_path / name),
        "size_bytes": (tmp_path / name).stat().st_size,
    }
    scope["identity"] = digest({k: v for k, v in scope.items() if k != "identity"})
    (tmp_path / "SCOPE.json").write_bytes(canonical(scope))
    with pytest.raises(RuntimeError, match="PARENT_PLAN_CHANGED"):
        adapter.validate_local_scope(tmp_path)


@pytest.mark.parametrize(
    "new_start,expected",
    [
        (NEW, None),
        (OLD, None),
        (datetime(2026, 1, 1, tzinfo=timezone.utc), "EARLIER_PERIOD_REVISION"),
    ],
)
def test_concept_subset_reuses_cumulative_earliest_definition(tmp_path, new_start, expected):
    table = "dwd_financial_concept"
    schema = pa.schema(
        [
            ("financial_concept_id", pa.string()),
            ("concept_name", pa.string()),
            ("effective_from", pa.timestamp("us", tz="UTC")),
        ]
    )
    with (
        Budget(tmp_path / "job", [], Limits(free_floor_bytes=1024**2)) as budget,
        GlobalScan(budget) as scan,
    ):
        for name, start in [("prior_" + table, OLD), ("sparse_" + table, new_start)]:
            p = budget.root / (name + ".parquet")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "financial_concept_id": "same-definition",
                            "concept_name": "literal",
                            "effective_from": start,
                        }
                    ],
                    schema=schema,
                ),
                p,
            )
            scan.add(name, [p])
        if expected:
            with pytest.raises(RuntimeError, match=expected):
                adapter.select_absent(scan, table, schema)
        else:
            assert adapter.select_absent(scan, table, schema)["new_identifiers"] == 0


def context(block="original-first", **changes):
    body = {
        "currency": None,
        "source_block_sha256": block,
        "source_content_sha256": "original-body",
        "source_literal_label": "literal",
        "source_literal_token": "1.00000000001",
        "unit_authority": "SOURCE_LITERAL",
    }
    body.update(changes)
    text = json.dumps(body, sort_keys=True)
    return text, hashlib.sha256(text.encode()).hexdigest()


def test_both_equivalent_blocks_require_original_raw_body_authority():
    old, old_hash = context()
    new, new_hash = context("original-second")
    assert adapter.validate_context_pair(
        "source",
        old,
        new,
        old_hash,
        new_hash,
        {("source", "original-body"): {"original-first", "original-second"}},
    )


@pytest.mark.parametrize("mode", ["token", "unit", "body", "unknown-block", "hash"])
def test_raw_block_equivalence_cannot_hide_values_units_body_or_unproven_hash(mode):
    old, old_hash = context()
    edits = {
        "token": {"source_literal_token": "1.00000000002"},
        "unit": {"unit_authority": "INFERRED"},
        "body": {"source_content_sha256": "another-body"},
    }
    new, new_hash = context(
        "unknown" if mode == "unknown-block" else "original-second", **edits.get(mode, {})
    )
    if mode == "hash":
        new_hash = "forged"
    with pytest.raises(RuntimeError, match="UNPROVEN_OR_DIFFERENT_RAW_CONTEXT"):
        adapter.validate_context_pair(
            "source",
            old,
            new,
            old_hash,
            new_hash,
            {("source", "original-body"): {"original-first", "original-second"}},
        )
