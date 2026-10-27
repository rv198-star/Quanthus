"""Synthetic checks for the bounded, existing-source Stage 3/4 intake."""

import hashlib
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.securities_warehouse.p10_stage34_prefix import (
    PrefixSourceError,
    checked_member,
    nonimplementation_title_disposition,
    observe_f10_action_dates,
    official_ex_date,
    payment_date_only_correction,
    validate_selected_native,
)

SH = ZoneInfo("Asia/Shanghai")


def sample():
    day = date(2021, 7, 1)
    instant = datetime(2021, 7, 1, 15, tzinfo=SH)
    selected = [{"code": "600001", "security_id": "sec", "listing_id": "listing",
                 "venue_id": "venue", "first_listing": str(day),
                 "proposed_start": str(day), "proposed_through": str(day)}]
    cal = {"calendar_session_id": "session", "trade_date": day, "venue_id": "venue",
           "is_open": True, "session_type": "REGULAR_DAY"}
    listing = {"listing_id": "listing", "security_id": "sec", "listing_code": "600001",
               "venue_id": "venue", "listing_currency": "CNY", "effective_from": instant}
    bar = {"listing_id": "listing", "calendar_session_id": "session",
           "observation_start": instant, "interval_size": "1d", "price_basis": "UNADJUSTED",
           "currency": "CNY", "source_artifact_sha256": "sha256:" + "a" * 64,
           **dict.fromkeys(["open", "high", "low", "close", "volume"], Decimal("1.000"))}
    state = {"listing_id": "listing", "calendar_session_id": "session",
             "observation_start": instant, "is_tradable": True}
    raw = {"stock_code": "600001", "date": str(day),
           **dict.fromkeys(["open", "high", "low", "close", "volume"], "1.000")}
    tables = {"dwd_listing": [listing], "dwd_trading_calendar": [cal],
              "dwd_security_bar": [bar], "dwd_tradability_state": [state],
              "ods_main": [raw], "ods_hotfix": []}
    return selected, tables


def audit(selected, tables):
    return validate_selected_native(selected, tables, {"main": "a" * 64, "hotfix": "b" * 64})


def test_exact_raw_dates_and_decimal_correspondence_is_only_non_action_pass():
    selected, tables = sample()
    result = audit(selected, tables)
    assert result["status"] == "PASS_NON_ACTION_CLOSURE_ONLY"
    assert result["exact_field_counts"] == dict.fromkeys(
        ["open", "high", "low", "close", "volume"], 1
    )
    assert result["source_purpose_admitted"] is False
    assert result["action_coverage"] == "NOT_QUALIFIED"


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
@pytest.mark.parametrize("field,value", [
    ("calendar_session_id", "unknown"),
    ("observation_start", datetime(2021, 7, 2, 15, tzinfo=SH)),
    ("listing_id", "other"),
])
def test_wrong_calendar_day_or_identity_rejected(table, field, value):
    selected, tables = sample()
    tables[table][0][field] = value
    with pytest.raises(PrefixSourceError):
        audit(selected, tables)


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_existing_session_with_wrong_date_rejected_for_bar_and_state(table):
    selected, tables = sample()
    tables["dwd_trading_calendar"].append({
        **tables["dwd_trading_calendar"][0], "calendar_session_id": "aug3",
        "trade_date": date(2021, 8, 3),
    })
    tables[table][0]["calendar_session_id"] = "aug3"
    with pytest.raises(PrefixSourceError, match="CALENDAR_CLOSURE"):
        audit(selected, tables)


def test_same_date_other_venue_rejected():
    selected, tables = sample()
    tables["dwd_trading_calendar"].append({
        **tables["dwd_trading_calendar"][0], "calendar_session_id": "other",
        "venue_id": "other",
    })
    tables["dwd_tradability_state"][0]["calendar_session_id"] = "other"
    with pytest.raises(PrefixSourceError, match="CALENDAR_CLOSURE"):
        audit(selected, tables)


@pytest.mark.parametrize("table", ["dwd_listing", "dwd_security_bar",
                                   "dwd_tradability_state", "dwd_trading_calendar"])
def test_duplicate_key_rejected(table):
    selected, tables = sample()
    tables[table].append(deepcopy(tables[table][0]))
    with pytest.raises(PrefixSourceError, match="DUPLICATE"):
        audit(selected, tables)


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("interval_size", "5m"),
                                         ("price_basis", "ADJUSTED")])
def test_wrong_coordinate_rejected(field, value):
    selected, tables = sample()
    tables["dwd_security_bar"][0][field] = value
    with pytest.raises(PrefixSourceError, match="PRICE_COORDINATE"):
        audit(selected, tables)


def test_unknown_source_hash_does_not_fall_back_to_same_date_raw():
    selected, tables = sample()
    tables["dwd_security_bar"][0]["source_artifact_sha256"] = "sha256:" + "c" * 64
    with pytest.raises(PrefixSourceError, match="RAW_LINEAGE"):
        audit(selected, tables)


def test_numeric_difference_rejected_without_printing_prices():
    selected, tables = sample()
    tables["ods_main"][0]["close"] = "91234567.8"
    with pytest.raises(PrefixSourceError, match="RAW_NUMERIC") as err:
        audit(selected, tables)
    assert "91234567" not in str(err.value)


def test_missing_bar_and_nontrading_remain_explicit_no_imputation():
    selected, tables = sample()
    tables["dwd_security_bar"] = []
    tables["dwd_tradability_state"][0]["is_tradable"] = False
    result = audit(selected, tables)
    assert result["status"] == "DATA_LIMITED_NON_ACTION_CENSUS"
    assert result["missing_bar_keys"] == 1
    assert result["nontrading_state_keys"] == 1


def test_zero_and_null_are_preserved_separately():
    selected, tables = sample()
    tables["dwd_security_bar"][0]["volume"] = Decimal(0)
    tables["ods_main"][0]["volume"] = "0"
    tables["dwd_security_bar"][0]["open"] = None
    tables["ods_main"][0]["open"] = None
    result = audit(selected, tables)
    assert result["zero_fields"] == {"volume": 1}
    assert result["null_fields"] == {"open": 1}
    assert result["source_purpose_admitted"] is False


def test_protected_year_or_ipo_before_start_is_rejected():
    selected, tables = sample()
    selected[0]["proposed_through"] = "2022-01-01"
    with pytest.raises(PrefixSourceError, match="SELECTION_SCOPE"):
        audit(selected, tables)
    selected[0]["proposed_through"] = "2021-07-01"
    selected[0]["proposed_start"] = "2020-01-01"
    with pytest.raises(PrefixSourceError, match="SELECTION_SCOPE"):
        audit(selected, tables)


@pytest.mark.parametrize("relative", ["../payload", "/tmp/payload", "x/../payload"])
def test_member_path_traversal_rejected(tmp_path, relative):
    with pytest.raises(PrefixSourceError, match="MEMBER_PATH"):
        checked_member(tmp_path, relative)


def test_member_parent_symlink_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "part").write_bytes(b"unchanged")
    (root / "linked").symlink_to(other, target_is_directory=True)
    with pytest.raises(PrefixSourceError, match="MEMBER_PATH"):
        checked_member(root, "linked/part")
    assert (other / "part").read_bytes() == b"unchanged"


def test_regular_member_path_kept(tmp_path: Path):
    (tmp_path / "payload").write_bytes(b"test")
    assert checked_member(tmp_path, "payload") == tmp_path / "payload"


def f10_sample(block=None, rights="暂无数据"):
    content = ("☆分红扩股☆ ◇600001 合成公司◇\n【分红】\n"
               + (block or "｜2020-12-31｜进展说明:实施｜\n"
                  "｜｜除权除息日:2021-07-01｜\n｜｜合成非标准金额文本｜\n")
               + "【2.配股】\n" + rights + "\n")
    return {"stock_code": "600001", "security_id": "600001", "category": "分红扩股",
            "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "acquired_at": "2026-09-05T07:00:00+00:00"}


def date_observations(row):
    return observe_f10_action_dates(row, date(2021, 1, 1), date(2021, 12, 31))


def test_date_only_observation_does_not_require_amount_parser_or_grant_coverage():
    result = date_observations(f10_sample())
    assert result["observed_ex_dates"] == ["2021-07-01"]
    assert result["rights_status"] == "EXPLICIT_PROVIDER_EMPTY"
    assert result["action_coverage_qualified"] is False
    assert result["provider_acquired_at"] == "2026-09-05T07:00:00+00:00"


def test_unknown_implemented_date_is_retained_even_for_pre_ipo_fiscal_period():
    result = date_observations(f10_sample("｜2019-12-31｜进展说明:实施｜\n"))
    assert result["observed_ex_dates"] == []
    assert result["blocks"][0]["disposition"] == "IMPLEMENTED_EX_DATE_UNKNOWN"
    assert result["blocks"][0]["fiscal_coordinate"] == "2019-12-31"
    assert result["unknown_date_blocks"] == 1


def test_numbered_nonempty_rights_are_not_treated_as_absence():
    result = date_observations(f10_sample(rights="配股记录待核"))
    assert result["rights_status"] == "UNRESOLVED_PROVIDER_SECTION"
    assert result["action_coverage_qualified"] is False


def test_conflicting_ex_dates_fail_closed():
    result = date_observations(f10_sample(
        "｜2020-12-31｜进展说明:实施｜\n"
        "｜｜除权除息日:2021-07-01 除权除息日:2021-07-02｜\n"
    ))
    assert result["blocks"][0]["disposition"] == "EX_DATE_CONFLICT"
    assert result["observed_ex_dates"] == []


def test_observed_date_outside_prefix_is_not_relabelled_into_scope():
    result = date_observations(f10_sample(
        "｜2021-12-31｜进展说明:实施｜\n｜｜除权除息日:2022-07-01｜\n"
    ))
    assert result["blocks"][0]["disposition"] == "DATED_OUTSIDE_PREFIX"
    assert result["observed_ex_dates"] == []


def test_unknown_progress_does_not_disappear_as_not_implemented():
    result = date_observations(f10_sample("｜2020-12-31｜进展说明:未知｜\n"))
    assert result["blocks"][0]["disposition"] == "PROGRESS_UNKNOWN"


def test_changed_f10_content_is_rejected():
    row = f10_sample()
    row["content"] += "changed"
    with pytest.raises(PrefixSourceError, match="F10_DOCUMENT_IDENTITY"):
        date_observations(row)


def test_official_chinese_date_and_utf8_quote_offsets():
    text = "证券代码：600001\n除权（息）日为2021年7月1日。"
    proof = official_ex_date(text, "600001", "SSE")
    assert proof["ex_date"] == "2021-07-01"
    for row in proof["date_evidence"]:
        assert text[row["start"]:row["end"]] == row["quote"]
        assert text.encode()[row["utf8_byte_start"]:row["utf8_byte_end"]].decode() == row["quote"]
    assert proof["historical_provider_acquisition"] is None
    assert proof["amounts_or_execution_terms_admitted"] is False


def test_official_sse_date_table_uses_declared_column_order():
    text = "股票代码:600001\n股份类别 股权登记日 除权（息）日\nA股 2021/6/30 2021/7/1\n"
    assert official_ex_date(text, "600001", "SSE")["ex_date"] == "2021-07-01"
    with pytest.raises(PrefixSourceError, match="UNPROVEN"):
        official_ex_date(text.replace("股权登记日 除权（息）日", "除权（息）日 股权登记日"),
                         "600001", "SSE")


def test_official_body_with_conflicting_dates_is_not_resolved_by_first_match():
    text = "证券代码:600001\n除权除息日2021年7月1日\n除权除息日2021年7月2日"
    with pytest.raises(PrefixSourceError, match="CONFLICT"):
        official_ex_date(text, "600001", "SSE")


def test_official_wrong_issuer_and_fiscal_year_are_not_ex_date_proof():
    with pytest.raises(PrefixSourceError, match="CODE_IDENTITY"):
        official_ex_date("证券代码:600002\n除息日2021年7月1日", "600001", "SSE")
    with pytest.raises(PrefixSourceError, match="UNPROVEN"):
        official_ex_date("证券代码:600001\n2020年度分红2021年6月30日登记", "600001", "SSE")


def test_pdf_page_separator_keeps_literal_header_quote_offsets():
    text = "证券代码:600001\n\f股份类别 股权登记日 除权（息）日\nA股 2021/6/30 2021/7/1\n"
    result = official_ex_date(text, "600001", "SSE")
    assert result["ex_date"] == "2021-07-01"
    for row in result["header_evidence"]:
        assert text[row["start"]:row["end"]] == row["quote"]


def test_wrapped_table_header_uses_visible_ex_column_with_real_last_trade_date():
    text = ("证券代码:600001\n"
            "                              除权（息）\n"
            "股份类别 股权登记日 最后交易日       日\n"
            "A股    2021/6/29  2021/6/30  2021/7/1\n")
    assert official_ex_date(text, "600001", "SSE")["ex_date"] == "2021-07-01"


def test_literal_publisher_doubled_xi_word_keeps_exact_quote():
    text = "证券代码:600001\n除权除息息日为：2021年7月1日。"
    result = official_ex_date(text, "600001", "SZSE")
    assert result["ex_date"] == "2021-07-01"
    assert "除权除息息日" in result["date_evidence"][0]["quote"]


def test_table_placeholders_preserve_ex_column_before_share_and_cash_arrival():
    text = ("证券代码:600001\n"
            "                                 新增无限售\n"
            "股份类别 股权登记日 最后交易日 除权（息）日 条件流通股 现金红利发放日\n"
            "Ａ股      2021/6/1    －       2021/6/2   2021/6/3   2021/6/4\n")
    assert official_ex_date(text, "600001", "SSE")["ex_date"] == "2021-06-02"


def test_different_text_cell_alignment_does_not_pick_cash_date():
    text = ("证券代码:600001\n"
            "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日\n"
            "Ａ股         2021/6/1 －                 2021/6/2   2021/6/3\n")
    assert official_ex_date(text, "600001", "SSE")["ex_date"] == "2021-06-02"


def test_date_only_table_without_share_class_keeps_declared_ex_column():
    text = ("证券代码:600001\n股权登记日 除权（息）日 现金红利发放日\n"
            "2021/6/1 2021/6/2 2021/6/3\n")
    assert official_ex_date(text, "600001", "SSE")["ex_date"] == "2021-06-02"
    with pytest.raises(PrefixSourceError, match="UNPROVEN"):
        official_ex_date(text.replace("股权登记日", "股份类别 股权登记日"), "600001", "SSE")


def correction_sample():
    original = "证券代码:600001 公告编号:2021-028\n除权除息日2021年7月1日。"
    updated = original + "\n权益分派实施公告（更正后）"
    method = "五、权益分派方法1.现金红利将于2021年6月30日直接划入其资金账户。"
    correction = ("证券代码:600001 引用公告编号:2021-028\n更正前：" + method
                  + "更正后：" + method.replace("6月30日", "7月1日")
                  + "除上述更正外，原公告其他内容不变。")
    return correction, original, updated


def test_payment_correction_binds_same_date_implementations_without_inventing_date():
    result = payment_date_only_correction(*correction_sample(), "600001", "SSE")
    assert result["ex_date"] == "2021-07-01"
    assert result["correction_itself_has_no_ex_date_proof"] is True
    assert result["before_payment_date"] == "2021-06-30"
    assert result["after_payment_date"] == "2021-07-01"


@pytest.mark.parametrize("mutation,reason", [
    ("issuer", "CORRECTION_ISSUER"),
    ("reference", "REVISION_CLOSURE"),
    ("unchanged", "REVISION_CLOSURE"),
    ("extra", "NOT_PAYMENT_DATE_ONLY"),
    ("ex_date", "EX_DATE_CONFLICT"),
])
def test_payment_correction_missing_or_conflicting_closure_fails(mutation, reason):
    correction, original, updated = correction_sample()
    if mutation == "issuer":
        correction = correction.replace("600001", "600002")
    elif mutation == "reference":
        correction = correction.replace("2021-028", "2021-029")
    elif mutation == "unchanged":
        correction = correction.replace("原公告其他内容不变", "其他内容另议")
    elif mutation == "extra":
        correction = correction.replace("更正后：", "更正后：登记日修改。")
    else:
        updated = updated.replace("2021年7月1日", "2021年7月2日")
    with pytest.raises(PrefixSourceError, match=reason):
        payment_date_only_correction(correction, original, updated, "600001", "SSE")


@pytest.mark.parametrize("title,disposition", [
    ("关于2020年度利润分配方案公告", "PROPOSAL_PLAN_OR_INVESTOR_MEETING_NOT_IMPLEMENTATION"),
    ("关于曾孙公司进行利润分配的公告", "SUBSIDIARY_NOT_LISTED_HOLDER_COORDINATE"),
    ("关于股东变更名称的公告", "SHAREHOLDER_NAME_NOT_LISTED_SECURITY_IDENTITY"),
    ("首次公开发行并上市公告书更正公告", "IPO_DOCUMENT_IDENTITY_CHECKED_SEPARATELY"),
    ("利润分配管理制度", "GOVERNANCE_NOT_IMPLEMENTATION"),
    ("权益分派实施的更正公告", "BODY_REQUIRED"),
    ("配股方案", "BODY_REQUIRED"),
    ("未知行动事项", "UNRESOLVED_TITLE"),
])
def test_other_screened_titles_do_not_silently_become_empty_actions(title, disposition):
    assert nonimplementation_title_disposition(title) == disposition
