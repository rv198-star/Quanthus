import hashlib

import pytest

from quanthus.pipelines.securities_warehouse import base23_ownership_reconfirmation as adapter


def fixture():
    content = "截至日期：2026-06-30 十大股东情况\n原始完整矩阵测试"
    return dict(security_id="000001", stock_code="000001", category="股东研究",
                source_filename="retained.txt", source_start=0, source_length=1,
                content=content,
                content_sha256="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                provider_updated_date="2026-08-29", market_date="2026-08-29",
                source="tdx.get_company_info_content",
                source_revision="quanthus-fundamentals-provider-v1",
                acquired_at="2026-08-29T00:00:00.123456+08:00")


def test_identical_complete_body_keeps_separate_original_clocks_and_transport_changes():
    old = fixture()
    new = dict(old, acquired_at="2026-09-05T00:00:00+08:00", market_date="2026-09-05",
               source_start=999, source_filename="new.txt")
    a, b = adapter.literal_identity(old), adapter.literal_identity(new)
    assert a["literal_scope_fingerprint"] == b["literal_scope_fingerprint"]
    assert a["complete_raw_row_sha256"] != b["complete_raw_row_sha256"]
    assert a["actual_acquisition_instant"].microsecond == 123456
    assert b["body_match_means"] == "PRIOR_QUALIFIED_OR_HELD_DATED_MATRIX_SCOPE_ONLY"
    assert not b["other_categories_no_change_asserted"] and not b["provider_PIT_qualified"]


@pytest.mark.parametrize("field,value", [("content", "damaged"), ("content_sha256", "forged"),
                                         ("source", "different-source"),
                                         ("security_id", "600001"), ("category", "分红扩股"),
                                         ("source_length", -1)])
def test_unqualified_full_body_or_identity_is_rejected_without_input_changes(field, value):
    raw = fixture()
    raw[field] = value
    before = dict(raw)
    with pytest.raises(RuntimeError):
        adapter.literal_identity(raw)
    assert raw == before


def test_real_provider_update_is_a_scope_change_even_when_body_is_identical():
    old = fixture()
    new = dict(old, provider_updated_date="2026-09-05")
    assert (adapter.literal_identity(old)["literal_scope_fingerprint"]
            != adapter.literal_identity(new)["literal_scope_fingerprint"])
