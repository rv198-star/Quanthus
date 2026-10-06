from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_cash_restatement import cash_restatement_binding
from quanthus.pipelines.securities_warehouse.disclosure_event import _stable_id
from tests.unit.test_base22_canonical_action_revision import case


def fixture():
    raw, old, donor, terms, binding, clock, revision = case()
    raw.update(
        source="md.dividend_history",
        progress="实施",
        cash_per_share=0.125,
        bonus_shares=0,
        transfer_shares=0,
    )
    donor["record_source"] = "base22_f10/dividend_implemented"
    terms[0]["record_source"] = donor["record_source"]
    terms[0]["amount_per_share"] = Decimal("0.1250000000")
    before = {
        **terms[0],
        "corporate_action_id": old["corporate_action_id"],
        "corporate_action_term_id": _stable_id(
            "corporate_action_term", old["corporate_action_id"], "CASH_AMOUNT_PER_SHARE"
        ),
        "knowledge_from": old["knowledge_from"],
        "announced_at": old["announced_at"],
        "record_source": old["record_source"],
        "source_record_id": old["source_record_id"],
        "source_revision": old["source_revision"],
        "source_artifact_sha256": old["source_artifact_sha256"],
    }
    return raw, old, [before], donor, terms, binding


def bind(c):
    raw, old, before, donor, terms, binding = c
    return cash_restatement_binding(
        raw,
        old,
        before,
        donor,
        terms,
        source_scope_identity=binding["source_scope_identity"],
        raw_replay_receipt_identity=binding["raw_replay_receipt_sha256"],
    )


def test_exact_same_cash_is_new_source_evidence_without_priority():
    c = fixture()
    before = deepcopy(c)
    binding = bind(c)
    assert c == before
    assert binding["disposition"] == "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR"
    assert binding["original_raw_row_sha256"] == digest(c[0])
    assert binding["cash_source_priority_granted"] is False
    assert binding["historical_PIT_admission"] is False


def test_unknown_original_share_components_remain_unknown_in_cash_only_successor():
    c = fixture()
    c[0]["bonus_shares"] = c[0]["transfer_shares"] = None
    before = deepcopy(c)
    binding = bind(c)
    assert c == before
    assert binding["original_unavailable_share_fields"] == ["bonus_shares", "transfer_shares"]
    assert len(c[4]) == 1 and c[4][0]["term_type"] == "CASH_AMOUNT_PER_SHARE"


@pytest.mark.parametrize(
    "change",
    [
        "raw_cash",
        "old_cash",
        "donor_cash",
        "currency",
        "status",
        "source",
        "multiple_terms",
        "nonzero_bonus",
    ],
)
def test_conflict_or_incomplete_term_set_cannot_be_admitted(change):
    c = fixture()
    if change == "raw_cash":
        c[0]["cash_per_share"] = 0.126
    elif change == "old_cash":
        c[2][0]["amount_per_share"] += 1
    elif change == "donor_cash":
        c[4][0]["amount_per_share"] += 1
    elif change == "currency":
        c[4][0]["currency"] = "USD"
    elif change == "status":
        c[0]["progress"] = "预案"
    elif change == "source":
        c[0]["source"] = "opaque-normalization"
    elif change == "multiple_terms":
        c[4].append(deepcopy(c[4][0]))
    else:
        c[2].append(
            {
                **c[2][0],
                "term_type": "BONUS_SHARES_PER_SHARE",
                "amount_per_share": None,
                "ratio_numerator": Decimal(1),
                "ratio_denominator": Decimal(1),
            }
        )
    with pytest.raises(BoundedError, match="CASH_RESTATEMENT"):
        bind(c)


def test_whole_raw_receipt_reproduces_semantics_without_requiring_same_elapsed_time():
    from quanthus.pipelines.securities_warehouse.base22_cash_restatement import _cash_replay_receipt

    content = {
        "scope": "FULL_F10_RAW_RECEIPT_TYPED_REPLAY_NOT_PIT",
        "response_groups": 4992,
        "raw_category_rows": 79872,
        "source_identity": "sha256:" + "b" * 64,
    }
    receipt = {**content, "identity": digest(content)}
    timed = {**receipt, "elapsed_seconds": 141.37}
    assert _cash_replay_receipt(timed) == _cash_replay_receipt(receipt)
    assert timed["elapsed_seconds"] == 141.37


@pytest.mark.parametrize("change", ["count", "source", "identity", "unknown", "negative_time"])
def test_complete_raw_semantic_receipt_cannot_hide_changes_in_runtime_metadata(change):
    from quanthus.pipelines.securities_warehouse.base22_cash_restatement import _cash_replay_receipt

    content = {
        "scope": "FULL_F10_RAW_RECEIPT_TYPED_REPLAY_NOT_PIT",
        "response_groups": 4992,
        "raw_category_rows": 79872,
        "source_identity": "sha256:" + "b" * 64,
    }
    receipt = {**content, "identity": digest(content), "elapsed_seconds": 1.0}
    if change == "count":
        receipt["raw_category_rows"] += 1
    elif change == "source":
        receipt["source_identity"] = "sha256:" + "c" * 64
    elif change == "identity":
        receipt["identity"] = "sha256:" + "d" * 64
    elif change == "unknown":
        receipt["ignored_raw_proofs"] = True
    else:
        receipt["elapsed_seconds"] = -1
    with pytest.raises(BoundedError, match="RAW_REPLAY_RECEIPT"):
        _cash_replay_receipt(receipt)


@pytest.mark.parametrize("defect", [None, "missing_company", "term_clock"])
def test_offline_cash_global_reads_immutable_image_outside_audit_TMP(tmp_path, monkeypatch, defect):
    import os
    from pathlib import Path
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import file_sha, load_contract, schemas
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_cash_restatement import _scan_cash_global

    root = tmp_path / "relocated-image"
    root.mkdir()
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    _, old, before, _, _, _ = fixture()
    if defect == "term_clock":
        before[0]["knowledge_from"] = before[0]["knowledge_from"].replace(year=2027)
    body = {"outputs": [], "parents": {}}
    for table, rows in [("dwd_corporate_action", [old]), ("dwd_corporate_action_term", before)]:
        p = root / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), p)
        p.chmod(0o444)
        body["outputs"].append({"table": table, "path": p.name, "rows": len(rows)})
    for table, column, value in [
        ("dwd_listed_company", "company_id", old["company_id"]),
        ("dwd_security", "security_id", old["security_id"]),
    ]:
        p = root / (table + ".parquet")
        pq.write_table(
            pa.table(
                {
                    column: [
                        "absent"
                        if defect == "missing_company" and column == "company_id"
                        else value
                    ]
                }
            ),
            p,
        )
        p.chmod(0o444)
        body["parents"][table] = [{"path": p.name}]
    hashes = {p.name: file_sha(p) for p in root.iterdir()}
    real_statvfs = os.statvfs
    # Unit fixture models only the RO flag. The actual cold harness proves the
    # private kernel RO bind, original-source hiding and native reader denials.
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda path: SimpleNamespace(f_flag=os.ST_RDONLY)
        if Path(path) == root
        else real_statvfs(path),
    )

    def rows(item):
        return pq.ParquetFile(root / item["path"]).read().to_pylist()

    with Budget(tmp_path / "audit-TMP", [root], Limits(free_floor_bytes=1)) as budget:
        if defect is None:
            assert _scan_cash_global(root, body, budget, contract, ss, rows) == {
                "dwd_corporate_action": 1,
                "dwd_corporate_action_term": 1,
            }
        else:
            with pytest.raises(BoundedError, match="GLOBAL_ENTITY_PARENT|GLOBAL_TERM_PARENT"):
                _scan_cash_global(root, body, budget, contract, ss, rows)
    assert hashes == {p.name: file_sha(p) for p in root.iterdir()}
