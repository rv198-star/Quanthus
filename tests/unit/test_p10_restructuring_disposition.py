"""Exact non-beneficial entitlement and unbounded-session source semantics."""

import copy
from datetime import date, datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import p10_restructuring_disposition as p


def texts():
    return {
        "1207399015": (
            "000792 不向原股东分配。不再对公司股票进行除权处理。"
            "股权登记日为2020年3月30日，转增股本上市日为2020年3月31日。"
        ),
        "1207399016": "000792 不向原股东分配。不再对公司股票进行除权处理。",
        "1210709225": (
            "000792 股票自2021年8月10日起恢复上市。"
            "首日（即2021年8月10日）不实行价格涨跌幅限制。"
            "次一交易日（即2021年8月11日）起，日涨跌幅限制为10%。"
        ),
    }


def test_nonbeneficial_restructuring_is_not_a_bonus_or_an_inferred_exdate():
    facts = p.parse_terms(texts())
    assert facts["original_shareholders_receive_new_shares"] is False
    assert facts["ordinary_shareholder_bonus_entitlement"] == "0"
    assert facts["stock_exright_adjustment"] is False
    assert facts["actual_share_credit_date"] is facts["historical_known_at"] is None
    assert facts["record_date"] == "2020-03-30"
    assert facts["new_share_listing_date"] == "2020-03-31"
    assert facts["resume_unbounded_date"] == "2021-08-10"


@pytest.mark.parametrize(
    "notice,before,after",
    [
        ("1207399015", "不向原股东分配", "向原股东分配"),
        ("1207399016", "不再对公司股票进行除权处理", "进行除权处理"),
        ("1207399015", "3月30日", "3月29日"),
        ("1207399015", "3月31日", "4月1日"),
        ("1210709225", "股票自2021年8月10日", "股票自2021年8月11日"),
        ("1210709225", "不实行价格涨跌幅限制", "实行价格涨跌幅限制"),
        ("1210709225", "限制为10%", "限制为5%"),
        ("1207399015", "000792", "000748"),
    ],
)
def test_changed_authoritative_terms_rejected(notice, before, after):
    source = texts()
    source[notice] = source[notice].replace(before, after)
    with pytest.raises(ValueError, match="RESTRUCTURING_"):
        p.parse_terms(source)


def exceptions():
    rows = [
        dict(
            exception_id=p.EXCEPTION,
            action_id=p.ACTION,
            security_id="000792",
            effective_date=None,
            blocked_from=date(2020, 3, 25),
            blocked_through=None,
            reason_code="missing_effective_date",
            authority_evidence_digests=[],
        ),
        dict(
            exception_id="other",
            action_id="other-action",
            security_id="000792",
            effective_date=None,
            blocked_from=date(2021, 4, 1),
            blocked_through=None,
            reason_code="unresolved-other-action",
            authority_evidence_digests=[],
        ),
    ]
    return rows


def test_only_exact_qualified_exception_changes_other_same_security_guard_retained():
    source = pa.Table.from_pylist(exceptions())
    result = p.qualified_exceptions(source)
    assert result.equals(source.slice(1), check_metadata=True)
    assert len(source) == 2


@pytest.mark.parametrize(
    "mutation", ["duplicate", "wrong_action", "wrong_reason", "effective", "authority"]
)
def test_exception_before_image_cannot_be_resealed_or_generalized(mutation):
    rows = exceptions()
    if mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "wrong_action":
        rows[0]["action_id"] = "another-action"
    elif mutation == "wrong_reason":
        rows[0]["reason_code"] = "another-quality-defect"
    elif mutation == "effective":
        rows[0]["effective_date"] = date(2020, 3, 31)
    else:
        rows[0]["authority_evidence_digests"] = ["conflicting-authority"]
    with pytest.raises(ValueError, match="EXCEPTION_"):
        p.qualified_exceptions(pa.Table.from_pylist(rows))


def limits():
    return pa.Table.from_pylist(
        [
            dict(
                security_id=code,
                execution_date=day,
                is_executable=True,
                observed_tradestatus="1",
                upper_limit_pct=Decimal("10"),
                lower_limit_pct=Decimal("10"),
                upper_limit_price=Decimal("11"),
                lower_limit_price=Decimal("9"),
                price_reference=Decimal("10"),
                knowledge_cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
                revision="unchanged-old-source",
            )
            for code, day in [
                ("000792", date(2021, 8, 10)),
                ("000792", date(2021, 8, 11)),
                ("000748", date(2021, 8, 10)),
            ]
        ]
    )


def test_exact_resume_null_bounds_leave_next_day_other_security_and_reference_unchanged(tmp_path):
    source, target = tmp_path / "source.parquet", tmp_path / "output.parquet"
    table = limits()
    pq.write_table(table, source)
    before_sha = p.sha(source)
    clock = datetime(2026, 10, 25, tzinfo=timezone.utc)
    result = p.rewrite_resume_limits(source, target, clock)
    assert result == dict(rows=3, changed_keys=1, unchanged_rows=2)
    assert p.rewrite_resume_limits(source, target, clock, verify_only=True) == result
    actual = pq.read_table(target)
    assert actual.slice(1).equals(table.slice(1), check_metadata=True)
    first = actual.to_pylist()[0]
    assert all(first[k] is None for k in p.LIMIT_FIELDS - {"revision", "knowledge_cutoff"})
    assert first["price_reference"] == Decimal("10") and first["is_executable"] is True
    assert p.sha(source) == before_sha
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        p.rewrite_resume_limits(source, target, clock)


def test_cold_recomputed_limit_values_reject_fully_reencoded_other_change(tmp_path):
    source, target = tmp_path / "source.parquet", tmp_path / "output.parquet"
    pq.write_table(limits(), source)
    clock = datetime(2026, 10, 25, tzinfo=timezone.utc)
    p.rewrite_resume_limits(source, target, clock)
    actual = pq.read_table(target)
    rows = actual.to_pylist()
    rows[1]["is_executable"] = False
    pq.write_table(pa.Table.from_pylist(rows, schema=actual.schema), target)
    with pytest.raises(ValueError, match="UNAUTHORIZED_LIMIT_CHANGE"):
        p.rewrite_resume_limits(source, target, clock, verify_only=True)


def test_source_symlink_and_materialization_overlap_fail_before_any_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    link = tmp_path / "link"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="PHYSICAL_ROOT"):
        p.load_source(link, "a" * 64)
    output = source / "new-output"
    with pytest.raises(ValueError, match="ISOLATION"):
        p.materialize(source, tmp_path / "official", "a" * 64, output)
    assert not output.exists()
