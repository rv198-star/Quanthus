from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22_notice_issuer_titles import (
    issuer_schedule_view,
    select_issuer_schedule_candidates,
)

BODY = (
    "证券代码:600001\n本公司2025年度权益分派方案已获2026年6月1日召开的年度股东大会"
    "审议通过，现将权益分派事宜公告如下:\n一、权益分派方案\n"
    "本公司2025年度权益分派方案为:向全体股东每10股派1.5元人民币现金(含税，扣税后"
    "个人另行办理)。\n二、股权登记日与除权除息日\n股权登记日:2026年7月31日。"
    "除权除息日:2026年8月3日。"
)
TARGETS = [{"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}]


def doc(title="2025年度分红派息公告", text=BODY):
    return {"document_id": "123", "target_action_ids": ["a"],
            "publisher_security_code": "600001", "publisher_title": title, "text": text}


@pytest.mark.parametrize("title", ["2025年度分红派息公告", "2025年度A股利润分配公告",
                                   "2025年度A股利润分派实施公告"])
def test_dated_approved_notice_role_keeps_original_metadata_and_exact_cash(title):
    inputs = [doc(title)]
    original = deepcopy(inputs)
    selected, proof = select_issuer_schedule_candidates(TARGETS, inputs)
    assert set(selected) == {"a"} and inputs == original
    record = proof["issuer_schedule_document_evidence"]["123"]
    assert record["publisher_metadata_rewritten"] is False


@pytest.mark.parametrize("change", ["proposal", "preliminary", "wrong_date", "no_schedule"])
def test_title_is_not_implementation_authority_without_exact_body(change):
    text = BODY
    if change == "proposal":
        text = text.replace("方案已获", "拟提交")
    elif change == "preliminary":
        text += "A股利润分配预计时间安排，权益分派实施公告将在交易所网站另行公布。"
    elif change == "wrong_date":
        text = text.replace("8月3日", "8月4日")
    else:
        text = text.replace("除权除息日", "未定日期")
    selected, _ = select_issuer_schedule_candidates(TARGETS, [doc(text=text)])
    assert not selected
    if change == "preliminary":
        _, role = issuer_schedule_view(doc(text=text))
        assert role["preliminary_schedule_not_primary"] is True


def test_A_notice_explicit_H_exclusion_and_gross_period_do_not_admit_other_market():
    text = BODY.replace("(含税，扣税后", "(含税。扣税后")
    text += "H 股股东的现金股息派发不适用本公告。"
    selected, proof = select_issuer_schedule_candidates(
        TARGETS, [doc("2025年度A股利润分配公告", text)]
    )
    assert set(selected) == {"a"}
    record = proof["issuer_schedule_document_evidence"]["123"]
    assert record["literal_explicit_A_notice_H_exclusion"] is True
    assert record["literal_gross_period_reflows"] == 1
    selected, _ = select_issuer_schedule_candidates(TARGETS, [doc(
        "2025年度A股利润分配公告", text.replace("不适用本公告", "另行派发人民币现金")
    )])
    assert not selected


def test_every_new_primary_must_agree_not_only_one_successful_retained_body():
    docs = [doc(), {**doc(text=BODY.replace("派1.5", "派1.6")), "document_id": "124"}]
    selected, _ = select_issuer_schedule_candidates(TARGETS, docs)
    assert not selected


def test_title_profile_new_version_retains_old_atoms_and_unknown_publication_clock():
    from quanthus.pipelines.securities_warehouse.base22 import digest
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        CONTRACT_ID,
        restate_notice_cash,
    )
    from tests.unit.test_base22_notice_restatement import case

    raw, old, terms, _, binding = case()
    raw["stock_code"] = "600001"
    target = [{**TARGETS[0], "ex_date": raw["ex_date"].isoformat()}]
    source = doc(text=BODY.replace("2026年8月3日", "2019年6月3日"))
    selected, _ = select_issuer_schedule_candidates(target, [source])
    atom = selected["a"]["atom"]
    binding.update(original_raw_row_sha256=digest(raw), notice_atom_sha256=digest(atom))
    actions, output, proof = restate_notice_cash(
        raw, old, terms, atom, binding=binding, observed_at="2026-10-08T00:30:00+00:00",
        revision=CONTRACT_ID + ":sha256:" + "d" * 64,
    )
    assert actions[0] == {**old, "knowledge_to": actions[1]["knowledge_from"]}
    assert output[0] == {**terms[0], "knowledge_to": output[1]["knowledge_from"]}
    assert actions[1]["announced_at"] is None and proof["historical_PIT_admission"] is False
