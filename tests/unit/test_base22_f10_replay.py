from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_f10_replay import replay_f10_group
from tests.unit.test_base22_f10_dividend import raw


def fixture():
    r = raw(plan="10送2转1派4.123456789元(含税)")
    r.update(
        security_id="000001",
        market_date="2026-07-31",
        source="tdx.get_company_info_content",
        source_revision="quanthus-fundamentals-provider-v1",
    )
    r["acquired_at"] = "2026-07-31T13:10:02.123456+00:00"
    receipt = dict(
        dataset="raw_f10",
        security_id="000001",
        status="success_nonempty",
        reason_code="ok",
        request_json=json.dumps({"operation": "F10C+F10", "symbol": "000001"}),
        response_rows=1,
        response_bytes=len(r["content"].encode()),
        response_sha256=hashlib.sha256(r["content"].encode()).hexdigest(),
        acquired_at="2026-07-31T13:10:05.654321+00:00",
    )
    _, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    vocabulary = json.loads(
        Path("config/pipelines/disclosure-event-vocabulary.v1.json").read_bytes()
    )
    parent = SimpleNamespace(
        company_id="c", security_id="s", knowledge_from=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )

    class Resolver:
        def resolve_for_report(self, code, *, period_end):
            assert code == "000001" and period_end == date(2026, 7, 10)
            return parent, None

    args = dict(
        market_date=date(2026, 7, 31),
        observed_at="2026-10-05T09:43:55.876666+00:00",
        source_sha256="sha256:" + "a" * 64,
        revision="synthetic-original-release",
        resolver=Resolver(),
        vocabulary=vocabulary,
        schemas=ss,
    )
    return r, receipt, args


def test_full_original_group_replay_is_exact_and_does_not_infer_old_knowledge():
    r, receipt, args = fixture()
    before = copy.deepcopy((r, receipt))
    out, proof = replay_f10_group([r], receipt, **args)
    assert (r, receipt) == before
    assert proof["response_proof"]["complete_selected_response"]
    assert len(out["dwd_corporate_action"]) == 1
    action = out["dwd_corporate_action"][0]
    assert action["knowledge_from"].isoformat() == args["observed_at"]
    assert action["announced_at"] is None
    assert proof["source_proofs"][0]["proof"]["provider_acquisition"] == "UNAVAILABLE"
    assert proof["merged_row_hashes"] == {
        t: [digest(row) for row in rows] for t, rows in out.items()
    }


@pytest.mark.parametrize("fault", ["receipt", "original_body", "old_knowledge", "typed_value"])
def test_repackaged_output_or_receipt_cannot_replace_independent_raw_replay(fault):
    r, receipt, args = fixture()
    out, proof = replay_f10_group([r], receipt, **args)
    if fault == "receipt":
        receipt["response_rows"] = 2
        with pytest.raises(BoundedError):
            replay_f10_group([r], receipt, **args)
        return
    if fault == "original_body":
        r["content"] += " changed"
        r["content_sha256"] = hashlib.sha256(r["content"].encode()).hexdigest()
        with pytest.raises(BoundedError):
            replay_f10_group([r], receipt, **args)
        return
    changed = copy.deepcopy(out)
    if fault == "old_knowledge":
        changed["dwd_corporate_action"][0]["knowledge_from"] = datetime(
            2026, 7, 31, tzinfo=timezone.utc
        )
    else:
        term = changed["dwd_corporate_action_term"][0]
        numeric = next(k for k, v in term.items() if type(v).__name__ == "Decimal")
        term[numeric] += 1
    forged = {
        **proof,
        "merged_row_hashes": {t: [digest(row) for row in rows] for t, rows in changed.items()},
    }
    forged["identity"] = digest({k: v for k, v in forged.items() if k != "identity"})
    _, independently_replayed = replay_f10_group([r], receipt, **args)
    assert independently_replayed["merged_row_hashes"] != forged["merged_row_hashes"]


def test_unknown_dated_identity_keeps_original_block_and_has_no_output():
    r, receipt, args = fixture()

    class Missing:
        def resolve_for_report(self, code, *, period_end):
            return None, "UNKNOWN_DATED_IDENTITY"

    args["resolver"] = Missing()
    out, proof = replay_f10_group([r], receipt, **args)
    assert not any(out.values())
    assert (
        proof["source_proofs"][0]["dated_identity_dispositions"][0]["disposition"]
        == "DATED_PARENT_UNKNOWN_DATED_IDENTITY"
    )
    assert proof["source_proofs"][0]["proof"]["dated_blocks"] == 1


def test_replay_hashes_match_schema_serialization_without_losing_raw_precision(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    r, receipt, args = fixture()
    before = copy.deepcopy((r, receipt))
    out, proof = replay_f10_group([r], receipt, **args)
    differs = False
    for table, rows in out.items():
        path = tmp_path / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=args["schemas"][table]), path)
        stored = pq.ParquetFile(path).read().to_pylist()
        assert stored == rows  # Decimal values, nulls and time instants are exact.
        differs |= [digest(row) for row in rows] != [digest(row) for row in stored]
        assert proof["serialized_row_hashes"][table] == [digest(row) for row in stored]
        assert proof["merged_row_hashes"][table] == [digest(row) for row in rows]
    assert differs  # Fixed schema scale is a representation change, not a new value.
    assert (r, receipt) == before
    assert proof["source_proofs"][0]["proof"]["content_sha256"] == (
        "sha256:" + r["content_sha256"]
    )


def test_serialized_replay_still_rejects_changed_value_or_knowledge(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    r, receipt, args = fixture()
    out, proof = replay_f10_group([r], receipt, **args)
    for table, field, replacement in (
        ("dwd_corporate_action_term", "amount_per_share", 9),
        ("dwd_corporate_action", "knowledge_from", datetime(2026, 7, 31, tzinfo=timezone.utc)),
    ):
        changed = copy.deepcopy(out[table])
        changed[0][field] = replacement
        path = tmp_path / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(changed, schema=args["schemas"][table]), path)
        hashes = [digest(row) for row in pq.ParquetFile(path).read().to_pylist()]
        assert hashes != proof["serialized_row_hashes"][table]
