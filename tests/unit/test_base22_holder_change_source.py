"""Real change-table fields stay separate from ranked-list increments."""

from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_change_source import (
    TABLE,
    project_change_tables,
)
from tests.unit.test_base22_f10_ownership import raw, resolve

BODY = (
    "【3.股东持股变动】\n"
    "｜变动日期｜股东名称｜持股数｜变动股数｜变动后｜变动后占｜变动类型｜\n"
    "｜｜｜(股)｜(股)｜持股数(股)｜总股本比例(%)｜｜\n"
    "├\n｜2026-06-30｜合成股东｜2万｜-1.25万｜7500｜0.123456789012｜二级市场卖出｜\n└\n"
)


def project(text=BODY):
    return project_change_tables(
        raw(text, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve()
    )


def test_source_change_fields_keep_sign_units_nulls_real_acquisition_and_safe_schema():
    from pathlib import Path

    import pyarrow as pa

    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas

    data = raw(BODY, "股东研究")
    frozen = deepcopy(data)
    rows, proof = project_change_tables(
        data, source_sha256="sha256:" + "a" * 64, resolver=resolve()
    )
    assert data == frozen
    row = rows[TABLE][0]
    assert row["share_change"] == Decimal("-12500")
    assert row["post_holding"] == Decimal("7500")
    assert row["percentage_change"] is None
    assert row["post_percentage"] == Decimal("0.123456789012")
    assert row["raw_share_change"] == "-1.25万"
    assert row["share_change_unit"] == "SHARE"
    assert row["announced_at"] is None
    assert row["knowledge_from"] > row["effective_at"]
    assert proof["historical_PIT_admission"] is False
    assert proof["consumer_admission"] == "NOT_GRANTED"
    _, model, extension = load_contract(Path("config/pipelines"))
    assert (
        pa.Table.from_pylist(rows[TABLE], schema=schemas(model, extension)[TABLE]).to_pylist()
        == rows[TABLE]
    )


@pytest.mark.parametrize("change", ["border", "date", "unit", "ratio", "holder", "unknown_row"])
def test_unknown_or_incomplete_nonempty_source_is_not_accepted_as_empty_complete(change):
    text = BODY
    if change == "border":
        text = text.replace("└", "")
    elif change == "date":
        text = text.replace("2026-06-30", "未知时间")
    elif change == "unit":
        text = text.replace("(股)", "(未知)").replace("-1.25万", "-12500")
    elif change == "ratio":
        text = text.replace("0.123456789012", "101")
    elif change == "holder":
        text = text.replace("合成股东", "")
    else:
        text = text.replace("└", "｜未说明的非空行｜\n└")
    rows, proof = project(text)
    assert not rows[TABLE]
    assert proof["held_blocks"] == 1
    assert proof["whole_change_scope"] == "HELD_NOT_EMPTY_COVERAGE"


def test_after_cutoff_is_excluded_without_relabeling_date_or_knowledge():
    rows, proof = project(BODY.replace("2026-06-30", "2026-09-01"))
    assert not rows[TABLE]
    assert proof["excluded_after_cutoff_rows"] == 1
    assert not proof["date_or_quantity_inferred"]


def test_list_comments_are_not_actual_change_events():
    text = (
        "截至日期：2026-06-30 十大股东情况\n"
        "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
        "｜合成股东｜1万｜1%｜流通A股｜新进｜\n└"
    )
    rows, proof = project(text)
    assert not rows[TABLE]
    assert proof["exact_change_tables"] == 0
    assert proof["whole_change_scope"] == "NO_SELECTED_CHANGE_TABLE_IN_EXACT_RAW_CATEGORY"


def test_duplicate_exact_source_row_deduplicates_but_distinct_same_key_is_held():
    line = "｜2026-06-30｜合成股东｜2万｜-1.25万｜7500｜0.123456789012｜二级市场卖出｜"
    rows, proof = project(BODY.replace(line, line + "\n" + line))
    assert len(rows[TABLE]) == 1
    assert proof["exact_duplicate_source_rows"] == 1
    rows, proof = project(BODY.replace(line, line + "\n" + line.replace("7500", "7600")))
    assert not rows[TABLE]
    assert proof["held_natural_keys"] == 1


def test_zero_and_explicit_null_are_not_collapsed_or_imputed():
    rows, _ = project(BODY.replace("-1.25万", "0").replace("7500", "-"))
    assert rows[TABLE][0]["share_change"] == Decimal(0)
    assert rows[TABLE][0]["post_holding"] is None


def test_scaled_header_is_not_silently_treated_as_a_single_share():
    rows, proof = project(BODY.replace("(股)", "(万股)").replace("-1.25万", "-12500"))
    assert not rows[TABLE]
    assert proof["held_blocks"] == 1


def test_unknown_percentage_denominator_stays_unknown_and_plans_stay_held():
    rows, _ = project(BODY.replace("总股本比例(%)", "比例(%)"))
    assert (
        rows[TABLE][0]["post_percentage_unit"] == "SOURCE_DECLARED_POST_PERCENT_BASIS_UNAVAILABLE"
    )
    rows, proof = project(BODY.replace("二级市场卖出", "减持计划"))
    assert not rows[TABLE] and proof["held_blocks"] == 1


def test_replay_closure_includes_all_recursive_local_imports():
    import ast
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base22_holder_change_source import REPLAY_MODULES

    directory = Path("src/quanthus/pipelines/securities_warehouse")
    queue, seen = ["base22_holder_change_source"], set()
    while queue:
        name = queue.pop()
        path = directory / (name + ".py")
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                queue.append(node.module.split(".")[0])
    assert {n + ".py" for n in seen} == set(REPLAY_MODULES)


def test_changed_raw_member_is_rejected_before_interpreting_its_payload(tmp_path, monkeypatch):
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
    from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
    from quanthus.pipelines.securities_warehouse.base22_holder_change_source import (
        replay_change_sources,
    )

    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base22_holder_change_source."
        "financial_company_resolver_from_listed_company_rows",
        lambda *_: resolve(),
    )

    _, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    (tmp_path / "contract").symlink_to(Path("config/pipelines").resolve(), target_is_directory=True)
    parents = {}
    for name in ("dwd_security", "dwd_security_identifier"):
        pq.write_table(pa.Table.from_pylist([], schema=ss[name]), tmp_path / (name + ".parquet"))
        parents[name] = [{"path": name + ".parquet", "rows": 0}]
    pq.write_table(pa.table({"unapproved": ["never interpreted"]}), tmp_path / "raw.parquet")
    body = {
        "parents": parents,
        "raw_inputs": [
            {"source_day": d, "path": "raw.parquet", "rows": 1, "sha256": "sha256:" + "a" * 64}
            for d in ("2026-08-01", "2026-08-08", "2026-08-22", "2026-08-29")
        ],
    }
    with pytest.raises(BoundedError, match="HOLDER_CHANGE_ORIGINAL_RAW_MEMBER_CHANGED"):
        replay_change_sources(tmp_path, body, None, None)
