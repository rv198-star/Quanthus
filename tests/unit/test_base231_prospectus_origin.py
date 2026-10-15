from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base231_prospectus_origin import qualify_origin
from tests.unit.test_base231_official_profile import source


def inputs():
    original = source()
    original.pop("document_uri")
    original["body"] = ("合成股份有限公司\n首次公开发行股票并上市招股说明书\n"
                        "一、发行人基本情况\n中文名称 合成股份有限公司\n"
                        "有限公司成立日期 2010 年 1 月 2 日\n"
                        "股份公司成立日期 2020 年 5 月 6 日\n二、发行人历史沿革\n")
    original["actual"] = dict(secCode=original["code"], orgId=original["issuer"]["orgId"],
                              announcementTitle="首次公开发行股票并上市招股说明书",
                              announcementId="1234567890", announcementTime=1641081600000,
                              adjunctUrl="finalpage/2022-01-02/1234567890.PDF")
    return original


def test_full_prospectus_explicit_issuer_origin_is_financial_only_new_knowledge():
    identity, company, proof = qualify_origin(**inputs())
    assert identity.effective_from.isoformat() == "2010-01-01T16:00:00+00:00"
    assert identity.effective_to == inputs()["parent"].effective_from
    assert company["company_id"] == inputs()["parent"].company_id
    assert proof["historical_PIT_qualified"] is False
    assert proof["historical_legal_continuity_qualified"] is False
    assert proof["security_or_listing_extended"] is False
    assert proof["ownership_qualified"] is False
    assert proof["original_provider_acquisition"] is None


@pytest.mark.parametrize("label", ["成立日期", "成立时间", "公司成立日期", "有限责任公司设立日期"])
@pytest.mark.parametrize("date", ["2010 年 1 月 2 日", "2010-01-02"])
def test_only_the_explicit_issuer_origin_field_is_used(label, date):
    kw = inputs()
    kw["body"] = kw["body"].replace("有限公司成立日期 2010 年 1 月 2 日", label + " " + date)
    _, _, proof = qualify_origin(**kw)
    assert proof["reported_origin_date"].isoformat() == "2010-01-02"


@pytest.mark.parametrize("fault", ["name", "code", "org", "summary", "future", "duplicate",
                                    "subsidiary", "share_only", "unlabelled", "reversed",
                                    "not_pdf", "title", "clock", "parent"])
def test_prospectus_cannot_infer_identity_from_a_query_or_subsidiary(fault):
    kw = deepcopy(inputs())
    if fault == "name":
        kw["expected_name"] = "另一股份有限公司"
    elif fault in {"code", "org"}:
        kw["actual"]["secCode" if fault == "code" else "orgId"] = "000000"
    elif fault == "summary":
        kw["actual"]["announcementTitle"] += "摘要"
    elif fault == "title":
        kw["body"] = kw["body"].replace("招股说明书", "定期报告")
    elif fault == "future":
        kw["body"] = kw["body"].replace("2010 年 1 月 2 日", "2027 年 1 月 2 日")
    elif fault == "duplicate":
        kw["body"] += "\n一、发行人基本情况\n中文名称 合成股份有限公司\n成立日期 2009-01-01"
    elif fault == "subsidiary":
        kw["body"] = kw["body"].replace("有限公司成立日期", "子公司成立日期")
    elif fault == "share_only":
        kw["body"] = kw["body"].replace("有限公司成立日期", "未知日期")
    elif fault == "unlabelled":
        kw["body"] = kw["body"].replace("中文名称 合成股份有限公司", "业务主体 合成股份有限公司")
    elif fault == "reversed":
        kw["body"] = kw["body"].replace("2010 年 1 月 2 日", "2022 年 1 月 4 日")
    elif fault == "not_pdf":
        kw["pdf_sha256"] = "unknown"
    elif fault == "clock":
        kw["received_at"] = kw["observed_at"].replace(year=2027)
    else:
        kw["issuer"]["orgId"] = "unrelated"
    with pytest.raises((RuntimeError, ValueError)):
        qualify_origin(**kw)


def test_prospectus_projects_only_source_bound_financial_periods():
    import hashlib
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base231_financial_summary import load_contract
    from quanthus.pipelines.securities_warehouse.base231_prospectus_origin import project_financial
    from tests.unit.test_base23_ownership_reconfirmation import fixture
    from tests.unit.test_base231_financial_summary import BODY

    kw = inputs()
    identity, _, proof = qualify_origin(**kw)
    raw = fixture()
    body = BODY.replace("2026-06-30", "2021-06-30").replace("2026-03-31", "2009-03-31")
    raw.update(stock_code=kw["code"], security_id=kw["code"], category="财务分析", content=body,
               content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    args = dict(identity=identity, qualification=proof, source_sha256=kw["pdf_sha256"],
                source_revision="retained-financial-raw", observed_at=kw["observed_at"],
                contract=load_contract(Path("config/pipelines/base231-domains.v1.json")))
    output, _ = project_financial(raw, **args)
    assert len(output["dwd_financial_report"]) == 1
    assert output["dwd_financial_report"][0]["period_end"].isoformat() == "2021-06-30"
    for flag in ("ownership_qualified", "historical_PIT_qualified", "legal_form_date_qualified"):
        changed = deepcopy(args)
        changed["qualification"][flag] = True
        with pytest.raises(RuntimeError, match="FINANCIAL_ONLY_SCOPE"):
            project_financial(raw, **changed)


@pytest.mark.parametrize("fault", [None, "index_code", "request", "PDF_hash", "partial", "clock"])
def test_contained_original_index_and_actual_transport_are_required(tmp_path, fault):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base231_prospectus_origin import verify_source

    kw = inputs()
    actual = kw["actual"]
    (tmp_path / "PDF").write_bytes(b"synthetic original document")
    (tmp_path / "index").write_text(json.dumps({"announcements": [actual]}))
    request = {"stock": kw["code"] + "," + kw["issuer"]["orgId"]}
    receipts = {}
    for label, uri in [("index", "https://www.cninfo.com.cn/new/hisAnnouncement/query"),
                       ("PDF", "https://static.cninfo.com.cn/" + actual["adjunctUrl"])]:
        receipts[label] = dict(URI=uri, actual_transport="urllib.request", http_status=200,
                               error=None, response_cap_reached=False,
                               body_sha256=file_sha(tmp_path / label),
                               actual_response_body_bytes=(tmp_path / label).stat().st_size,
                               completed_at=kw["received_at"].isoformat(),
                               request_payload=request if label == "index" else None)
    spec = dict(code=kw["code"], actual_index_row=deepcopy(actual), PDF="PDF", index="index",
                receipt="PDF.receipt", index_receipt="index.receipt")
    if fault == "index_code":
        spec["actual_index_row"]["secCode"] = "000000"
    elif fault == "request":
        receipts["index"]["request_payload"]["stock"] = "000000,wrong"
    elif fault == "PDF_hash":
        receipts["PDF"]["body_sha256"] = "sha256:" + "0" * 64
    elif fault == "partial":
        receipts["PDF"]["response_cap_reached"] = True
    elif fault == "clock":
        receipts["index"]["completed_at"] = kw["observed_at"].replace(year=2027).isoformat()
    for label in receipts:
        (tmp_path / (label + ".receipt")).write_text(json.dumps(receipts[label]))
    if fault:
        with pytest.raises(RuntimeError):
            verify_source(tmp_path, spec, observed_at=kw["observed_at"])
    else:
        assert verify_source(tmp_path, spec, observed_at=kw["observed_at"]) == actual


def test_new_prospectus_offline_contract_requires_its_exact_code_pin(tmp_path):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import digest
    from quanthus.pipelines.securities_warehouse.base231_official_profile import replay_origin_scope
    from quanthus.pipelines.securities_warehouse.base231_prospectus_origin import CONTRACT_ID

    plan = dict(contract_id=CONTRACT_ID, through_date="2026-09-30", members={}, code_inputs={},
                legal_form_date_qualified=False, provider_PIT=False,
                consumer_admission="NOT_GRANTED")
    plan["identity"] = digest(plan)
    (tmp_path / "INPUTS.json").write_text(json.dumps(plan))
    with pytest.raises(RuntimeError, match="SOURCE_CODE_UNPINNED"):
        replay_origin_scope(tmp_path)


@pytest.mark.parametrize("header,label", [("一、公司基本情况", "中文名称"),
                                           ("一、发行人概况", "发行人"),
                                           ("一、发行人基本情况", "发行人名称")])
def test_equivalent_explicit_basic_section_families_bind_the_native_name(header, label):
    kw = inputs()
    kw["body"] = kw["body"].replace("一、发行人基本情况", header).replace("中文名称", label)
    identity, _, _ = qualify_origin(**kw)
    assert identity.company_id == kw["parent"].company_id


def test_numbered_registered_chinese_name_and_origin_remain_inside_basic_section():
    kw = inputs()
    kw["body"] = kw["body"].replace("中文名称", "（一）注册名称（中文）：").replace(
        "有限公司成立日期", "（四）成立日期")
    identity, _, _ = qualify_origin(**kw)
    assert identity.company_id == kw["parent"].company_id
