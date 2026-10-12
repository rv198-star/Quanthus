import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base231_financial_sparse as adapter
from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base231_financial_summary import load_contract
from tests.unit.test_base22_f10_ownership import resolve
from tests.unit.test_base23_ownership_reconfirmation import fixture

BODY = (
    "【主要财务指标】\n┌────┐\n"
    "｜财务指标｜2026-06-30｜-｜2026-03-31｜\n├────┤\n"
    "｜净利润(元)｜1.23456789012万｜-｜-0.00000｜\n"
    "｜加权净资产收益率(%)｜1.234567890123｜｜-｜\n└────┘\n"
)


def project(body=BODY, **updates):
    raw = fixture()
    raw.update(stock_code="002001", security_id="002001", category="财务分析",
               market_date="2026-09-05", acquired_at="2026-09-05T01:02:03.123456+08:00",
               content=body, content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    raw.update(updates)
    before = copy.deepcopy(raw)
    result = adapter.repair_sparse_summary(
        raw, source_sha256="sha256:" + "a" * 64, revision="original-selected-source",
        resolver=resolve(), observed_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        contract=load_contract(Path("config/pipelines/base231-domains.v1.json")),
    )
    assert raw == before
    return raw, result


def test_only_dated_columns_survive_with_exact_original_provenance():
    raw, (rows, proof) = project()
    facts = rows["dwd_financial_fact"]
    assert len(facts) == 4 and len(rows["dwd_financial_report"]) == 2
    assert facts[0]["value_decimal"] == Decimal("12345.6789012")
    assert facts[1]["value_decimal"].is_zero()
    assert facts[2]["value_decimal"] is None
    assert facts[2]["value_text"] == "1.234567890123"
    assert facts[3]["value_text"] == "-" and not facts[3]["is_nil"]
    assert all(r["published_at"] is None for r in facts)
    assert all(r["knowledge_from"] == datetime(2026, 10, 13, tzinfo=timezone.utc)
               for r in facts)
    assert proof["complete_original_raw_row_sha256"] == digest(raw)
    assert proof["original_acquired_at"] == raw["acquired_at"]
    assert proof["excluded_undated_cells"] == 2
    for fact in facts:
        dims = json.loads(fact["dimensions_json"])
        assert dims["source_content_sha256"] == raw["content_sha256"]
        assert dims["source_block_sha256"] == proof["frames"][0]["original_block_sha256"]
        assert fact["context_hash"] == hashlib.sha256(fact["dimensions_json"].encode()).hexdigest()


def test_nonempty_undated_observation_is_held_without_losing_dated_siblings():
    _, (rows, proof) = project(BODY.replace("万｜-｜", "万｜999｜"))
    assert len(rows["dwd_financial_fact"]) == 4
    assert proof["undated_nonempty_cells"] == 1
    assert proof["undated_nonempty_values_promoted"] is False


@pytest.mark.parametrize("body", [
    BODY.replace("｜-｜2026-03-31", "｜2026-06-30｜2026-03-31"),
    BODY.replace("｜-｜2026-03-31", "｜2026-02-30｜2026-03-31"),
    BODY.replace("｜-｜2026-03-31", "｜不明日期｜2026-03-31"),
    BODY.replace("｜-0.00000｜", "｜-0.00000｜多余列｜"),
    BODY.replace("└────┘", ""),
])
def test_ambiguous_headers_width_and_truncation_are_not_repaired(body):
    _, (rows, proof) = project(body)
    assert not rows["dwd_financial_fact"]
    assert proof["held_frames"]


def test_complete_existing_frame_is_reused_and_after_cutoff_is_excluded():
    body = BODY.replace("｜-｜", "｜2026-10-01｜", 1)
    _, (rows, proof) = project(body)
    assert not rows["dwd_financial_fact"]
    assert proof["already_valid_frames_reused"] == 1
    _, (rows, proof) = project(BODY.replace("2026-06-30", "2026-10-01"))
    assert len(rows["dwd_financial_fact"]) == 2
    assert all(r["period_end"].isoformat() <= "2026-09-30"
               for r in rows["dwd_financial_fact"])


def test_original_hash_is_checked_before_deriving_columns():
    with pytest.raises(RuntimeError, match="RAW_IDENTITY"):
        project(content_sha256="sha256:" + "b" * 64)
