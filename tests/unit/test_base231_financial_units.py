import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext

import pytest

from quanthus.pipelines.securities_warehouse.base231_financial_summary import NAMESPACE
from quanthus.pipelines.securities_warehouse.base231_financial_units import qualify_ratio

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def source(value=Decimal("12.34")):
    common = dict(knowledge_from=NOW, source_artifact_sha256="sha256:" + "a" * 64,
                  source_record_id="record", knowledge_to=None, source_revision="original")
    concept = dict(**common, financial_concept_id="concept", taxonomy_namespace=NAMESPACE,
                   concept_name="净利润增长率(%)", concept_code="net_profit_growth",
                   statement_type="SOURCE_LITERAL_FINANCIAL_SUMMARY")
    fact = dict(**common, financial_fact_id="fact", financial_concept_id="concept",
                financial_report_id="report", period_end=date(2026, 6, 30), published_at=None,
                unit_code="PERCENT", value_decimal=value, value_text=None, nil_flag=value is None,
                dimensions_json=json.dumps(dict(source_literal_label="净利润增长率(%)",
                                                unit_authority="EXPLICIT_LITERAL_COLUMN_LABEL")))
    return fact, concept


@pytest.mark.parametrize("value", [Decimal("12.34"), Decimal("0"), Decimal("-3.2"), None])
def test_exact_ratio_preserves_original_and_unknown_publication(value):
    fact, concept = source(value)
    before = deepcopy((fact, concept))
    rows, proof = qualify_ratio(fact, concept, observed_at=NOW)
    result = rows["dwd_financial_fact"][0]
    assert result["value_decimal"] == (value / 100 if value is not None else None)
    assert result["unit_code"] == "RATIO"
    assert result["published_at"] is None
    assert (fact, concept) == before
    assert not proof["currency_CNY_inferred"]
    assert proof["historical_PIT_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize("fault", ["currency", "label", "parent", "future", "precision"])
def test_unqualified_unit_identity_or_precision_is_held(fault):
    fact, concept = source()
    if fault == "currency":
        fact["unit_code"] = "SOURCE_LITERAL_YUAN"
    elif fault == "label":
        fact["dimensions_json"] = "{}"
    elif fault == "parent":
        concept["financial_concept_id"] = "other"
    elif fault == "future":
        fact["knowledge_from"] = NOW.replace(day=15)
    else:
        fact["value_decimal"] = Decimal("0.0000000001")
    with pytest.raises(RuntimeError):
        qualify_ratio(fact, concept, observed_at=NOW)


def test_ambient_precision_cannot_change_exact_output():
    fact, concept = source(Decimal("123456789.12345678"))
    expected, _ = qualify_ratio(fact, concept, observed_at=NOW)
    with localcontext() as context:
        context.prec = 6
        actual, _ = qualify_ratio(fact, concept, observed_at=NOW)
    assert actual == expected
