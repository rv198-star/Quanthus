from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_identity_scope import (
    CONTRACT_ID,
    exact_identity_view,
    select_exact_identity_candidates,
)
from tests.unit.test_base22_notice_issuer_titles import BODY, TARGETS


def document(text=BODY, title="2025年度A股权益分派实施公告", code="600001"):
    return {"document_id": "123", "target_action_ids": ["a"],
            "publisher_security_code": code, "publisher_title": title, "text": text}


def test_literal_bare_header_and_original_publisher_identity_must_both_agree():
    text = BODY.replace("证券代码:600001", "600001 2026-012")
    inputs = [document(text)]
    before = deepcopy(inputs)
    selected, proof = select_exact_identity_candidates(TARGETS, inputs)
    assert set(selected) == {"a"} and inputs == before
    atom = selected["a"]["atom"]
    assert atom["contract_id"] == CONTRACT_ID
    assert atom["original_text_sha256"] == digest(text)
    identity = proof["exact_identity_document_evidence"]["123"]
    assert identity["publisher_and_literal_bare_header_code_agree"] is True
    assert identity["code_alias_or_currency_inferred"] is False


@pytest.mark.parametrize("header,code", [
    ("600002 2026-012", "600001"),
    ("600001 2026-012", "600002"),
    ("600001 600002 2026-012", "600001"),
    ("600001股 2026-012", "600001"),
    ("公告正文\n600001 2026-012", "600001"),
])
def test_no_alias_or_quantity_or_later_digit_promoted_to_security_header(header, code):
    text = BODY.replace("证券代码:600001", header)
    selected, _ = select_exact_identity_candidates(TARGETS, [document(text, code=code)])
    _, proof = exact_identity_view(document(text, code=code))
    assert not selected and not proof["publisher_and_literal_bare_header_code_agree"]


@pytest.mark.parametrize("operation", ["股息派发", "红利发放", "分红"])
def test_explicit_A_scope_and_H_operation_exclusion_preserve_every_source_token(operation):
    text = BODY + f"\n公司H 股股东的现金{operation}不适用本公告。"
    inputs = [document(text)]
    before = deepcopy(inputs)
    selected, proof = select_exact_identity_candidates(TARGETS, inputs)
    assert set(selected) == {"a"} and inputs == before
    record = proof["exact_identity_document_evidence"]["123"]
    assert record["H_label_reflows"] == 1
    assert record["H_operation_exclusion_sha256"]


@pytest.mark.parametrize("kind", ["no_A", "no_exclusion", "other_H", "H_cash", "B", "FX"])
def test_foreign_entitlement_or_missing_positive_scope_remains_held(kind):
    title = "2025年度A股权益分派实施公告"
    text = BODY + "\nH股股东的现金股息派发不适用本公告。"
    if kind == "no_A":
        title = "2025年度权益分派实施公告"
    elif kind == "no_exclusion":
        text = text.replace("不适用本公告", "另行派发")
    elif kind == "other_H":
        text += "H股参与本次分配。"
    elif kind == "H_cash":
        text += "H股股东每10股派发现金人民币2元(含税)。"
    elif kind == "B":
        text += "B股现金另行处理。"
    else:
        text += "港元现金另行处理。"
    selected, _ = select_exact_identity_candidates(TARGETS, [document(text, title)])
    assert not selected


def test_valid_identity_cannot_override_date_currency_or_primary_consistency():
    text = BODY.replace("证券代码:600001", "600001 2026-012")
    for other in [text.replace("8月3日", "8月4日"), text.replace("人民币", "港元")]:
        selected, _ = select_exact_identity_candidates(TARGETS, [document(other)])
        assert not selected
    second = {**document(text.replace("派1.5", "派1.6")), "document_id": "124"}
    selected, _ = select_exact_identity_candidates(TARGETS, [document(text), second])
    assert not selected
    with pytest.raises(BoundedError, match="DUPLICATE_DOCUMENT"):
        select_exact_identity_candidates(TARGETS, [document(text), document(text)])


def test_new_exclusion_view_cannot_erase_predecessor_printed_total_scope():
    from quanthus.pipelines.securities_warehouse.base22_notice_issuer_titles import (
        select_issuer_schedule_candidates,
    )
    from tests.unit.test_base22_notice_gross_layout import BODY as COMBINED

    inputs = [document(COMBINED)]
    previous, _ = select_issuer_schedule_candidates(TARGETS, inputs)
    current, proof = select_exact_identity_candidates(TARGETS, inputs)
    assert set(current) == set(previous) == {"a"}
    assert current["a"]["atom"]["cash_atom_sha256"] == previous["a"]["atom"]["cash_atom_sha256"]
    assert proof["unchanged_original_profile_selected_keys"] == ["a"]


def test_exact_header_new_version_preserves_old_atoms_and_unknown_publication():
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        CONTRACT_ID as REVISION_CONTRACT,
    )
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        restate_notice_cash,
    )
    from tests.unit.test_base22_notice_restatement import case

    raw, old, terms, _, binding = case()
    raw["stock_code"] = "600001"
    target = [{**TARGETS[0], "ex_date": raw["ex_date"].isoformat()}]
    text = BODY.replace("证券代码:600001", "600001 2026-012")
    text = text.replace("2026年8月3日", "2019年6月3日")
    selected, _ = select_exact_identity_candidates(target, [document(text)])
    atom = selected["a"]["atom"]
    binding.update(original_raw_row_sha256=digest(raw), notice_atom_sha256=digest(atom))
    actions, output, proof = restate_notice_cash(
        raw, old, terms, atom, binding=binding, observed_at="2026-10-08T02:00:00+00:00",
        revision=REVISION_CONTRACT + ":sha256:" + "d" * 64,
    )
    assert actions[0] == {**old, "knowledge_to": actions[1]["knowledge_from"]}
    assert output[0] == {**terms[0], "knowledge_to": output[1]["knowledge_from"]}
    assert actions[1]["announced_at"] is None and proof["historical_PIT_admission"] is False
