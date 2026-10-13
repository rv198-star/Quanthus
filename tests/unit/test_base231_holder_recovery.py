import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_holder_components_v2 import (
    load_component_contract,
)
from quanthus.pipelines.securities_warehouse.base231_holder_recovery import (
    business_digest,
    recover_frame,
)
from tests.unit.test_base22_f10_ownership import resolve
from tests.unit.test_base23_ownership import BODY, project
from tests.unit.test_base23_ownership_reconfirmation import fixture


def args():
    raw = fixture()
    raw.update(
        stock_code="002001",
        security_id="002001",
        content=BODY,
        category="股东研究",
        market_date="2026-09-05",
        acquired_at="2026-09-05T00:00:00+08:00",
        content_sha256="sha256:" + hashlib.sha256(BODY.encode()).hexdigest(),
    )
    rows, _ = project(BODY)
    return dict(
        raw=raw,
        source_sha256="sha256:" + "a" * 64,
        source_revision="retained-release",
        raw_row_sha256=digest(raw),
        business_date=date(2026, 9, 3),
        holding_type="TOP_FLOATING_SHAREHOLDERS",
        expected_business_sha256=business_digest(rows["dwd_shareholding_observation"]),
        resolver=resolve(),
        component_contract=load_component_contract(Path("config/pipelines")),
        observed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def test_recovered_complete_frame_keeps_exact_values_and_real_new_knowledge():
    rows, proof = recover_frame(**args())
    assert len(rows) == 1
    assert rows[0]["knowledge_from"] == datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert str(rows[0]["holding_quantity"]) == "12345.6700000000"
    assert not proof["absent_holder_is_exit"]
    assert not proof["latest_capture_complete_inferred"]


@pytest.mark.parametrize(
    "fault", ["raw", "incomplete", "wrong-date", "wrong-type", "changed-bundle"]
)
def test_recovery_does_not_invent_completeness_or_source_priority(fault):
    kw = args()
    if fault == "raw":
        kw["raw"]["source_filename"] += "changed"
    elif fault == "incomplete":
        kw["raw"]["content"] = BODY.removesuffix("└")
        kw["raw"]["content_sha256"] = (
            "sha256:" + hashlib.sha256(kw["raw"]["content"].encode()).hexdigest()
        )
        kw["raw_row_sha256"] = digest(kw["raw"])
    elif fault == "wrong-date":
        kw["business_date"] = date(2026, 9, 4)
    elif fault == "wrong-type":
        kw["holding_type"] = "TOP_SHAREHOLDERS"
    else:
        kw["expected_business_sha256"] = "sha256:" + "b" * 64
    with pytest.raises(RuntimeError):
        recover_frame(**kw)
