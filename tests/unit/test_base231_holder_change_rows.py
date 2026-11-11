import hashlib
from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base231_holder_change_rows import project_rows
from tests.unit.test_base22_f10_ownership import resolve
from tests.unit.test_base23_ownership_reconfirmation import fixture


def call(body):
    raw = fixture()
    raw.update(
        stock_code="002001",
        security_id="002001",
        category="股东研究",
        market_date="2026-09-05",
        acquired_at="2026-09-05T00:00:00+08:00",
        content=body,
        content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
    )
    return project_rows(
        raw,
        raw_row_sha256=digest(raw),
        source_sha256="sha256:" + "a" * 64,
        resolver=resolve(),
        observed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def body():
    from tests.unit.test_base22_holder_change_source import BODY

    return BODY.replace("\n└", "\n｜2026-06-29｜合成股东｜2万｜-1万｜1万｜1000%｜减持｜\n└")


def test_invalid_row_does_not_discard_an_independent_valid_source_atom():
    rows, proof = call(body())
    assert len(rows) == 1
    assert proof["qualified_rows"] == 1
    assert any(d["status"] == "HELD" for d in proof["decisions"])
    assert not proof["transaction_equivalence_granted"]
    assert rows[0]["disclosure_id"] is None
    assert all(not d["derived_view_is_original_vendor_payload"] for d in proof["decisions"])


def test_missing_original_closure_is_not_repaired_by_inventing_a_border():
    rows, proof = call(body().replace("\n└", "\n"))
    assert not rows
    assert proof["decisions"][0]["status"] == "HELD_ORIGINAL_TABLE_INCOMPLETE"


def test_already_qualified_block_is_not_emitted_again_as_new_recovery():
    from tests.unit.test_base22_holder_change_source import BODY

    with pytest.raises(RuntimeError, match="ONLY_HELD"):
        call(BODY)
