from datetime import date

import pytest

from quanthus.pipelines import p10_complete_daily_sync as sync
from quanthus.pipelines.p10_delivery_common import atomic, file_digest


def test_mirror_mail_distinguishes_source_declaration_from_receiver_acceptance(tmp_path):
    day = date(2026, 10, 14)
    relative = "packages/2026-10-14/exact/manifest.json"
    manifest = tmp_path / sync.CACHE / relative
    atomic(
        manifest,
        {
            "market_date": str(day),
            "content_digest": "sha256:" + "a" * 64,
            "minute_quality_grades": {"1m": {"R0": 2, "R2": 1}, "5m": {"R0": 3}},
        },
    )
    pointer = {
        "kind": "cn01_complete_daily_dwd_current",
        "market_date": str(day),
        "manifest_path": relative,
        "package_id": "exact",
        "manifest_sha256": file_digest(manifest),
        "content_digest": "sha256:" + "a" * 64,
    }
    atomic(
        tmp_path / sync.CACHE / "pending.json",
        {
            "market_date": str(day),
            "source_pointer": pointer,
        },
    )
    context = sync.mirror_mail_context(tmp_path, day)
    assert "已发布" in context["source_complete_status"]
    assert "不代表 OCI 本端验收状态" in context["source_complete_status"]
    assert context["source_manifest_sha256"] == pointer["manifest_sha256"]
    assert "精确验收回执为准" in context["consumer_admission_status"]
    other = sync.mirror_mail_context(tmp_path, date(2026, 10, 13))
    assert "未取得" in other["source_complete_status"]
    manifest.write_text("{}")
    assert "未取得" in sync.mirror_mail_context(tmp_path, day)["source_complete_status"]


@pytest.mark.parametrize("nested", [None, [], "not-an-object", 7])
def test_invalid_nested_source_pointer_does_not_block_failure_mail(tmp_path, nested):
    atomic(
        tmp_path / sync.CACHE / "pending.json",
        {
            "market_date": "2026-10-14",
            "source_pointer": nested,
        },
    )
    context = sync.mirror_mail_context(tmp_path, date(2026, 10, 14))
    assert "未取得" in context["source_complete_status"]
    assert "不构成放行" in context["consumer_admission_status"]
