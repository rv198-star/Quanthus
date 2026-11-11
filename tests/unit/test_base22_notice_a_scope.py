"""Other-market tax chapters cannot alter an explicit A-share current per-share claim."""

from copy import deepcopy
from decimal import Decimal

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_notice_a_scope import (
    CONTRACT_ID,
    current_a_view,
    select_a_cash_candidates,
)

BODY = (
    "证券代码:600001\n二、分配方案\n分派对象为本公司全体A股股东。"
    "本次利润分配以方案实施前的公司总股本为基数，年度每股派发现金红利人民币0.1元(含税)，"
    "特别现金红利每股派发现金红利人民币0.4元(含税)，共计每股派发现金红利人民币0.5元(含税)。"
    "\n三、相关日期\n除息日:2026年8月3日\n四、扣税说明\n"
    "通过港股通投资H 股股票的现金红利以人民币派发，净税后另外办理。"
)
TARGETS = [{"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}]


def document(body=BODY):
    return {
        "document_id": "123",
        "target_action_ids": ["a"],
        "publisher_security_code": "600001",
        "publisher_title": "年度A股权益分派实施公告",
        "text": body,
    }


def test_explicit_current_A_object_keeps_source_aggregate_and_all_original_body_tokens():
    inputs = [document()]
    before = deepcopy(inputs)
    chosen, proof = select_a_cash_candidates(TARGETS, inputs)
    assert inputs == before
    assert set(chosen) == {"a"} and chosen["a"]["atom"]["contract_id"] == CONTRACT_ID
    assert chosen["a"]["atom"]["original_text_sha256"] == digest(BODY)
    assert chosen["a"]["atom"]["cash_atom_sha256"]
    assert proof["other_market_entitlements_or_currency_qualified"] is False


def test_no_A_object_or_foreign_entitlement_in_current_scheme_is_not_masked():
    for text in [
        BODY.replace("全体A股股东", "全体股东"),
        BODY.replace("本次利润分配", "H股股东每股派发现金红利人民币2元(含税)。本次利润分配"),
    ]:
        chosen, proof = select_a_cash_candidates(TARGETS, [document(text)])
        assert not chosen
        _, viewproof = current_a_view(text)
        assert viewproof["literal_H_label_reflows"] == 0


def test_conflicting_or_unreadable_primary_body_cannot_be_discarded():
    second = {
        **document(),
        "document_id": "124",
        "text": BODY.replace("共计每股派发现金红利人民币0.5", "共计每股派发现金红利人民币0.7"),
    }
    selected, proof = select_a_cash_candidates(TARGETS, [document(), second])
    assert not selected
    second = {**document(), "document_id": "124", "text": BODY.replace("全体A股股东", "全体股东")}
    selected, proof = select_a_cash_candidates(TARGETS, [document(), second])
    assert not selected


def test_A_scope_new_version_keeps_old_atoms_and_actual_clocks():
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        CONTRACT_ID as REVISION_CONTRACT,
    )
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        restate_notice_cash,
    )
    from tests.unit.test_base22_notice_restatement import case

    raw, old, terms, _, binding = case()
    raw["stock_code"] = "600001"
    targets = [{**TARGETS[0], "ex_date": raw["ex_date"].isoformat()}]
    chosen, _ = select_a_cash_candidates(
        targets, [document(BODY.replace("2026年8月3日", "2019年6月3日"))]
    )
    atom = chosen["a"]["atom"]
    binding.update(original_raw_row_sha256=digest(raw), notice_atom_sha256=digest(atom))
    actions, output, proof = restate_notice_cash(
        raw, old, terms, atom, binding=binding,
        observed_at="2026-10-08T00:00:00+00:00",
        revision=REVISION_CONTRACT + ":sha256:" + "d" * 64,
    )
    assert actions[0] == {**old, "knowledge_to": actions[1]["knowledge_from"]}
    assert output[0] == {**terms[0], "knowledge_to": output[1]["knowledge_from"]}
    assert output[1]["amount_per_share"] == Decimal("0.5")
    assert actions[1]["announced_at"] is None
    assert proof["historical_PIT_admission"] is False
