import hashlib
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base231_report_issuer_bridge import (
    NOTICE_URI,
    ORIGINAL_MEMBER,
    ORIGINAL_NOTICE,
    project_report_observation,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity
from tests.unit.test_base22_holder_change_source import BODY
from tests.unit.test_base23_ownership_reconfirmation import fixture


def sample():
    raw = fixture()
    body = BODY.replace("2026-06-30", "2025-01-17")
    raw.update(
        stock_code="302132",
        security_id="302132",
        category="股东研究",
        market_date="2026-09-05",
        acquired_at="2026-09-05T00:00:00+08:00",
        content=body,
        content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
    )
    return raw, dict(
        raw_sha256=digest(raw),
        source_sha256=ORIGINAL_MEMBER,
        dated_parent=FinancialCompanyIdentity(
            "300114",
            "s",
            "c",
            datetime(1990, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 2, 1, tzinfo=timezone.utc),
        ),
        current_parent=FinancialCompanyIdentity(
            "302132", "s", "c", datetime(2025, 2, 1, tzinfo=timezone.utc), None
        ),
        official_text="300114 302132 公司法人主体存续 上市主体没有发生实质变化 权利义务平移",
        official_receipt=dict(
            URI=NOTICE_URI,
            body_sha256=ORIGINAL_NOTICE,
            http_status="200",
            curl_exit=0,
            actual_captured_at="2026-10-14T00:00:00+00:00",
        ),
        observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
    )


def test_source_report_subject_bridge_does_not_activate_a_trading_alias():
    raw, args = sample()
    before = deepcopy(raw)
    rows, proof = project_report_observation(raw, **args)
    assert len(rows) == 1
    assert rows[0]["security_id"] == "s"
    assert raw == before and raw["stock_code"] == "302132"
    assert proof["original_trading_code_activated"] is False
    assert proof["new_security_identifier_rows"] == 0
    assert proof["transaction_equivalence_granted"] is False


def test_other_original_dates_do_not_gain_the_selected_subject_bridge():
    raw, args = sample()
    raw["content"] = raw["content"].replace(
        "\n└", "\n｜2025-01-18｜另一股东｜2万｜-1万｜1万｜1%｜减持｜\n└"
    )
    raw["content_sha256"] = "sha256:" + hashlib.sha256(raw["content"].encode()).hexdigest()
    args["raw_sha256"] = digest(raw)
    rows, proof = project_report_observation(raw, **args)
    assert len(rows) == 1
    assert rows[0]["shareholder_source_name"] != "另一股东"
    assert proof["derived_view_is_original_vendor_payload"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "raw",
        "company",
        "security",
        "dated_interval",
        "current_interval",
        "date",
        "notice",
        "continuity",
        "clock",
    ],
)
def test_bridge_never_opens_other_sources_dates_or_legal_subjects(fault):
    from dataclasses import replace

    raw, args = sample()
    if fault == "raw":
        raw["source_revision"] = "changed"
    elif fault == "company":
        args["current_parent"] = replace(args["current_parent"], company_id="other")
    elif fault == "security":
        args["current_parent"] = replace(args["current_parent"], security_id="other")
    elif fault == "dated_interval":
        args["dated_parent"] = replace(
            args["dated_parent"], effective_from=datetime(2025, 1, 18, tzinfo=timezone.utc)
        )
    elif fault == "current_interval":
        args["current_parent"] = replace(
            args["current_parent"], effective_from=datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
    elif fault == "date":
        raw["content"] = raw["content"].replace("2025-01-17", "2025-01-18")
        raw["content_sha256"] = "sha256:" + hashlib.sha256(raw["content"].encode()).hexdigest()
        args["raw_sha256"] = digest(raw)
    elif fault == "notice":
        args["official_receipt"]["body_sha256"] = "sha256:" + "c" * 64
    elif fault == "continuity":
        args["official_text"] = "300114 302132 拟变更代码"
    else:
        args["official_receipt"]["actual_captured_at"] = "2026-10-15T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        project_report_observation(raw, **args)
