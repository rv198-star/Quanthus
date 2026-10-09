from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_f10_dividend import parse_dividend_content
from quanthus.pipelines.securities_warehouse.base23_dividend import (
    dividend_observation,
    latest_literal_captures,
    parse_september_dividend_content,
)


def raw(ex="2026-09-03", plan="10送0转增2.5派4.123456789元(含税)", status="实施"):
    body = ("【分红】\n"
            f"｜2026-06-30｜进展说明:{status}｜｜\n"
            f"｜｜{plan}｜｜\n"
            f"｜｜除权除息日:{ex}预案公布日:2026-08-30｜｜\n【增发】\n")
    return dict(security_id="000001", stock_code="000001", category="分红扩股",
                source_filename="synthetic", source_start=0, source_length=len(body.encode()),
                content=body, content_sha256="sha256:"+hashlib.sha256(body.encode()).hexdigest(),
                provider_updated_date=None, market_date="2026-09-05",
                source="tdx.get_company_info_content",
                source_revision="quanthus-fundamentals-provider-v1",
                acquired_at="2026-09-05T12:01:02.123456+08:00")


def project(row=None, *, parent=True, observed=None):
    _, model, ext = load_contract(Path("config/pipelines"))
    ss = schemas(model, ext)
    clock = datetime(2026, 10, 9, 10, 1, 2, 234567, tzinfo=timezone.utc)
    p = SimpleNamespace(company_id="c", security_id="s",
                        knowledge_from=datetime(2026, 8, 4, tzinfo=timezone.utc))
    resolver = SimpleNamespace(resolve_for_report=lambda *a, **kw:
                               (p, None) if parent else (None, "BEFORE_FIRST_LISTING"))
    return dividend_observation(
        row or raw(), source_sha256="sha256:"+"a"*64, revision="new-scope",
        resolver=resolver, observed_at=observed or clock,
        vocabulary=json.loads(Path("config/pipelines/disclosure-event-vocabulary.v1.json")
                              .read_text()), schemas=ss,
    )


def test_september_successor_keeps_frozen_august_cutoff():
    assert parse_dividend_content(raw())[0] == []
    assert parse_september_dividend_content(raw())[0][0]["ex_date"].isoformat() == "2026-09-03"
    assert parse_september_dividend_content(raw("2026-10-01"))[0] == []


def test_exact_serialized_terms_and_true_time_under_low_decimal_context():
    with localcontext() as context:
        context.prec = 3
        rows, proof = project()
    terms = {r["term_type"]: r for r in rows["dwd_corporate_action_term"]}
    assert terms["CASH_AMOUNT_PER_SHARE"]["amount_per_share"] == Decimal("0.4123456789")
    assert terms["TRANSFER_SHARES_PER_SHARE"]["ratio_numerator"] == Decimal("0.25")
    assert "BONUS_SHARES_PER_SHARE" not in terms
    assert rows["dwd_corporate_action"][0]["knowledge_from"] == proof["governance_observed_at"]
    assert rows["dwd_corporate_action"][0]["announced_at"] is None
    assert proof["source_acquisition_instant"].microsecond == 123456
    assert not proof["old_correction001_restoration"]
    assert not proof["provider_PIT_qualified"]


@pytest.mark.parametrize("changed", ["source", "source_revision", "security_id", "extra"])
def test_full_raw_namespace_and_field_closure(changed):
    row = raw()
    row[changed] = "unknown"
    with pytest.raises(BoundedError, match="COMPLETE_RAW_BINDING"):
        project(row)


def test_future_claim_and_missing_dated_parent_are_explicit_holds():
    rows, proof = project(raw("2026-09-10"))
    assert not any(rows.values())
    assert proof["held_typed_records"][0]["reason"] == "IMPLEMENTATION_DATE_AFTER_RETAINED_CAPTURE"
    rows, proof = project(parent=False)
    assert not any(rows.values())
    assert proof["held_typed_records"][0]["reason"] == "BEFORE_FIRST_LISTING"


def test_capture_cannot_be_backdated_as_governance():
    with pytest.raises(BoundedError, match="CAPTURE_AFTER_GOVERNANCE"):
        project(observed=datetime(2026, 9, 1, tzinfo=timezone.utc))


def test_proposal_never_enters_implemented_events():
    rows, proof = project(raw(status="预案"))
    assert not any(rows.values())
    assert proof["blocks"][0]["disposition"] == "IMPLEMENTATION_NOT_ESTABLISHED"


def test_latest_true_capture_and_equal_clock_conflict():
    before = raw()
    after = raw(plan="10派2元(含税)")
    after["acquired_at"] = "2026-09-05T12:02:03+08:00"
    def bind(row):
        return {"complete_raw_row_sha256": digest(row)}

    assert latest_literal_captures([(before, bind(before)), (after, bind(after))])[0][0] == after
    after["acquired_at"] = before["acquired_at"]
    with pytest.raises(BoundedError, match="EQUAL_CAPTURE_BODY_CONFLICT"):
        latest_literal_captures([(before, bind(before)), (after, bind(after))])


@pytest.mark.parametrize("conflict", [False, True])
def test_repeated_event_blocks_are_deduplicated_or_all_held(conflict):
    row = raw()
    block = row["content"].split("\n【增发】")[0].removeprefix("【分红】\n")
    if conflict:
        block = block.replace("4.123456789", "2.5")
    row["content"] = row["content"].replace("【增发】", block+"\n【增发】")
    row["content_sha256"] = "sha256:"+hashlib.sha256(row["content"].encode()).hexdigest()
    rows, proof = project(row)
    assert len(proof["blocks"]) == 2
    if conflict:
        assert not any(rows.values())
        assert proof["conflicting_event_keys"] == 1
        assert proof["held_typed_records"][0]["reason"].startswith("SAME_LITERAL_EVENT_CONFLICT")
    else:
        assert len(rows["dwd_corporate_action"]) == 1
        assert proof["identical_repeated_event_blocks"] == 1
