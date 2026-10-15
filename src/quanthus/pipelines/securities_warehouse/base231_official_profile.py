"""Current issuer financial literals bounded by an official reported origin.

The profile's establishment date is not a share-company conversion date or
historical legal-continuity/PIT proof. This separate purpose never extends a
security/listing or changes the existing legal-form qualification contract.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .financial_reporting import FinancialCompanyIdentity
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-official-profile-current-financial-origin-v1"
SCOPE = "CURRENT_FINANCIAL_LITERAL_SOURCE_REPORTED_ORIGIN_ONLY"
TZ = ZoneInfo("Asia/Shanghai")
QUALIFIED_PARENT = "sha256:78d090892f436577f3b7b50a4cf6f951e3814ac55a351e699ab5b27cef6b49c6"


def qualify_origin(
    body: str, *, code: str, expected_name: str, issuer: dict[str, Any],
    parent: FinancialCompanyIdentity, pdf_sha256: str, document_uri: str,
    received_at: datetime, observed_at: datetime,
) -> tuple[FinancialCompanyIdentity, dict[str, Any], dict[str, Any]]:
    clock, received = instant(observed_at), instant(received_at)
    expected_uri = "https://static.cninfo.com.cn/finalpage/enpage/" + code + "_1.pdf"
    if (not re.fullmatch(r"[0-9]{6}", code) or code != parent.stock_code
            or issuer.get("code") != code or issuer.get("category") != "A股"
            or not issuer.get("orgId")
            or parent.company_id != _stable_id("company", "CNINFO_ORG_ID", issuer["orgId"])
            or document_uri != expected_uri or received > clock or parent.knowledge_from > clock
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", pdf_sha256)
            or not isinstance(body, str) or not 0 < len(body.encode()) <= 8 * 1024**2
            or not isinstance(expected_name, str) or not expected_name):
        raise BoundedError("OFFICIAL_PROFILE_SOURCE_ISSUER_OR_CLOCK_UNPROVEN")
    # Bind the actual code and standalone company header, never a name mentioned
    # in an unrelated subsidiary/business paragraph. Repeated dates are held.
    code_lines = list(re.finditer(r"(?m)^\s*代码[：:]\s*([0-9]{6})\b", body))
    if len(code_lines) != 1 or code_lines[0].group(1) != code:
        raise BoundedError("OFFICIAL_PROFILE_UNIQUE_CODE_HEADER_REQUIRED")
    preceding = [line.strip() for line in body[:code_lines[0].start()].splitlines()
                 if line.strip()]
    header_proof: dict[str, Any] = {}
    if not preceding or preceding[-1].split()[0] != expected_name:
        # A native profile may put its explicitly labelled industry between the
        # company/transaction header and code. Only this one intervening field
        # and ASCII/fullwidth parenthesis typography are accepted; a company
        # mentioned in a business/subsidiary paragraph is never an identity.
        candidate = preceding[-2:] if preceding else []
        if candidate and re.fullmatch(r"所属行业[：:]\s*\S.*", candidate[-1]):
            candidate.pop()
        native_name = ""
        if candidate:
            header = re.fullmatch(r"(\S+)\s+交易信息", candidate[-1])
            if header:
                native_name = header.group(1)
        typography = str.maketrans({"（": "(", "）": ")"})
        header_lines = [line for line in preceding if re.fullmatch(r"\S+\s+交易信息", line)]
        same_name = native_name.translate(typography) == expected_name.translate(typography)
        if (not native_name or not same_name
                or len(header_lines) != 1 or len(preceding) > 8
                or preceding[-1] != header_lines[0]
                and (len(preceding) < 2 or preceding[-2] != header_lines[0])):
            raise BoundedError("OFFICIAL_PROFILE_EXACT_ORIGINAL_NAME_REQUIRED")
        header_proof = dict(
            header_layout_contract="explicit-profile-header-typography-v1",
            header_name_typography_only=native_name != expected_name,
            native_header_sha256=digest(preceding[-2:]),
        )
    fields: dict[str, date] = {}
    for label in ("成立日期", "上市日期"):
        found = re.findall(r"(?m)^\s*" + label + r"\s+([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})\b", body)
        if len(found) != 1:
            raise BoundedError("OFFICIAL_PROFILE_EXPLICIT_UNIQUE_DATE_REQUIRED")
        fields[label] = date(*map(int, found[0]))
    origin, ipo = fields["成立日期"], fields["上市日期"]
    start = instant(datetime.combine(origin, time(), TZ))
    if (origin > date(2026, 9, 30) or start >= parent.effective_from
            or ipo != parent.effective_from.astimezone(TZ).date()):
        raise BoundedError("OFFICIAL_PROFILE_ORIGIN_IPO_COORDINATE_CONFLICT")
    identity = FinancialCompanyIdentity(
        stock_code=code, company_id=parent.company_id, security_id=parent.security_id,
        effective_from=start, effective_to=parent.effective_from, knowledge_from=clock,
    )
    company = dict(
        record_source="cninfo.company_profile.reported_origin",
        source_record_id=digest([code, expected_name, fields]), source_revision=CONTRACT_ID,
        source_artifact_sha256=pdf_sha256, knowledge_from=clock, knowledge_to=None,
        effective_from=start, effective_to=None, company_id=parent.company_id,
        display_name=expected_name, company_type="official_source_scoped_financial_issuer",
        resolution_scope=SCOPE,
    )
    proof = dict(
        contract_id=CONTRACT_ID, scope=SCOPE, stock_code=code, source_pdf_sha256=pdf_sha256,
        source_text_sha256=digest(body), source_fields_sha256=digest(fields),
        original_name_sha256=digest(expected_name), company_id=parent.company_id,
        reported_establishment_date=origin, first_ipo_date=ipo,
        received_at=received, new_knowledge_at=clock, original_provider_acquisition=None,
        profile_not_september_capture=True, legal_form_date_qualified=False,
        historical_legal_continuity_qualified=False, historical_names_asserted=False,
        historical_PIT_qualified=False, security_or_listing_extended=False,
        consumer_admission="NOT_GRANTED",
    )
    proof.update(header_proof)
    return identity, company, proof


def project_origin_financial(
    raw: dict[str, Any], *, identity: FinancialCompanyIdentity,
    qualification: dict[str, Any], source_sha256: str, source_revision: str,
    observed_at: datetime, contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    from .base231_financial_summary import summary_observation
    from .financial_reporting import FinancialCompanyResolver

    if (qualification.get("contract_id") != CONTRACT_ID
            or qualification.get("scope") != SCOPE
            or qualification.get("stock_code") != identity.stock_code
            or qualification.get("company_id") != identity.company_id
            or qualification.get("legal_form_date_qualified") is not False
            or qualification.get("historical_PIT_qualified") is not False
            or identity.knowledge_from != instant(observed_at)
            or qualification.get("new_knowledge_at") != identity.knowledge_from
            or identity.effective_to is None):
        raise BoundedError("OFFICIAL_PROFILE_CURRENT_FINANCIAL_SCOPE_CHANGED")
    output, proof = summary_observation(
        raw, source_sha256=source_sha256, revision=source_revision,
        resolver=FinancialCompanyResolver([identity]), observed_at=observed_at, contract=contract,
    )
    if any(not identity.effective_from <= instant(datetime.combine(
        r["period_end"], time(23, 59, 59), TZ)) < identity.effective_to
        for r in output["dwd_financial_report"]):
        raise BoundedError("OFFICIAL_PROFILE_FINANCIAL_PERIOD_OUTSIDE_ORIGIN_SCOPE")
    proof.update(
        origin_contract_id=CONTRACT_ID, origin_scope=SCOPE,
        origin_qualification_sha256=digest(qualification),
        source_reported_origin_start=identity.effective_from,
        actual_first_IPO_upper_bound=identity.effective_to,
        legal_form_date_qualified=False, historical_legal_continuity_qualified=False,
        historical_PIT_qualified=False, security_or_listing_extended=False,
    )
    return output, proof


def replay_origin_scope(root: Path) -> dict[str, Any]:
    """Reconstruct the entire small domain from local evidence, without sources."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha, load_contract, schemas
    from .base22_f10_ownership import _cells
    from .base22_sources import _tree, physical_path
    from .base231_financial_summary import load_contract as load_summary
    from .base231_prospectus_origin import CONTRACT_ID as PROSPECTUS_CONTRACT
    from .base231_prospectus_origin import project_financial as project_prospectus
    from .base231_prospectus_origin import qualify_origin as qualify_prospectus
    from .base231_prospectus_origin import verify_source as verify_prospectus_source
    from .financial_reporting import financial_company_resolver_from_listed_company_rows

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (plan.get("contract_id") not in {CONTRACT_ID, PROSPECTUS_CONTRACT}
            or plan.get("through_date") != "2026-09-30"
            or plan.get("legal_form_date_qualified") is not False
            or plan.get("provider_PIT") is not False
            or plan.get("consumer_admission") != "NOT_GRANTED"
            or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})):
        raise BoundedError("OFFICIAL_ORIGIN_OFFLINE_PLAN_UNQUALIFIED")
    controls = {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"}
    if _tree(root) - controls != set(plan["members"]):
        raise BoundedError("OFFICIAL_ORIGIN_OFFLINE_PHYSICAL_SET_CHANGED")
    for name, member in plan["members"].items():
        path = physical_path(root / name)
        if (root.resolve() not in path.parents or path.stat().st_nlink != 1
                or path.stat().st_size != member["bytes"] or file_sha(path) != member["sha256"]):
            raise BoundedError("OFFICIAL_ORIGIN_OFFLINE_MEMBER_CHANGED")
    for path, sha in plan["code_inputs"].items():
        if file_sha(Path(path)) != sha:
            raise BoundedError("OFFICIAL_ORIGIN_OFFLINE_ORDINARY_CODE_CHANGED")
    is_prospectus = plan["contract_id"] == PROSPECTUS_CONTRACT
    if is_prospectus:
        for module in ("base231_prospectus_origin.py", "base231_official_holder_replay.py"):
            name = "src/quanthus/pipelines/securities_warehouse/" + module
            if plan["code_inputs"].get(name) != file_sha(Path(name)):
                raise BoundedError("PROSPECTUS_FINANCIAL_SOURCE_CODE_UNPINNED")
    parent_path = root / "inputs/qualified-parent-manifest.json"
    parent = json.loads(parent_path.read_bytes())
    if (file_sha(parent_path) != QUALIFIED_PARENT
            or parent.get("identity") != digest({k: v for k, v in parent.items()
                                                 if k != "identity"})
            or parent.get("qualification", {}).get("status") != "REAL_RETAINED_SOURCE_CLOSURE_PASS"
            or parent.get("consumer_admission") != "NOT_GRANTED"):
        raise BoundedError("OFFICIAL_ORIGIN_RETAINED_FINANCIAL_PARENT_UNQUALIFIED")
    entries = {entry["path"]: entry for entry in parent["members"]}
    for local, original in [("inputs/parent-INPUTS.json", "domain/INPUTS.json"),
                            ("inputs/parent-selection.json", "domain/projection/selection.json")]:
        if file_sha(root / local) != entries[original]["sha256"]:
            raise BoundedError("OFFICIAL_ORIGIN_RETAINED_PARENT_SELECTION_CHANGED")
    original_plan = json.loads((root / "inputs/parent-INPUTS.json").read_bytes())
    source_members = {entry["path"]: entry for entry in original_plan["raw_members"]}
    source_selection = {(entry["member"], entry["ordinal"]): entry for entry in json.loads(
        (root / "inputs/parent-selection.json").read_bytes())["selected"]}
    for table, names in plan["parents"].items():
        originals_for_table = original_plan["parents"].get(table, [])
        if len(names) != len(originals_for_table):
            raise BoundedError("OFFICIAL_ORIGIN_EXACT_PARENT_NATIVE_SET_CHANGED")
        for local, original in zip(names, originals_for_table, strict=True):
            if file_sha(root / local) != entries["domain/" + original]["sha256"]:
                raise BoundedError("OFFICIAL_ORIGIN_RETAINED_NATIVE_PARENT_CHANGED")
    parent_rows = {table: [row for name in names
                           for row in pq.ParquetFile(root / name).read().to_pylist()]
                   for table, names in plan["parents"].items()}
    resolver = financial_company_resolver_from_listed_company_rows(
        parent_rows["dwd_security"], parent_rows["dwd_security_identifier"])
    issuers = {row["code"]: row for row in json.loads(
        (root / "inputs/issuer-map.json").read_bytes())["stockList"]}
    originals = {row["stock_code"]: row for row in pq.ParquetFile(
        root / "inputs/original-profiles.parquet").read().to_pylist()}
    clock = datetime.fromisoformat(plan["observed_at"])
    identities, qualifications = {}, {}
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spec in plan["profiles"]:
        references = ("PDF", "receipt", "index", "index_receipt") if is_prospectus else (
            "PDF", "receipt")
        if any(spec.get(key) not in plan["members"] for key in references):
            raise BoundedError("OFFICIAL_ORIGIN_PROFILE_REFERENCE_OUTSIDE_MEMBERS")
        code = spec["code"]
        if digest(originals[code]) != spec["original_profile_raw_row_sha256"]:
            raise BoundedError("OFFICIAL_ORIGIN_ORIGINAL_NAME_SOURCE_CHANGED")
        fields = {cells[n + 1] for line in originals[code]["content"].splitlines()
                  if len(cells := _cells(line)) in {2, 4} for n in range(0, len(cells), 2)
                  if cells[n] == "公司名称"}
        if len(fields) != 1:
            raise BoundedError("OFFICIAL_ORIGIN_ORIGINAL_NAME_AMBIGUOUS")
        parent, _ = resolver.resolve_for_report(code, period_end=date(2026, 9, 30))
        if parent is None:
            raise BoundedError("OFFICIAL_ORIGIN_ORIGINAL_PARENT_MISSING")
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        uri = "https://static.cninfo.com.cn/finalpage/enpage/" + code + "_1.pdf"
        if is_prospectus:
            actual = verify_prospectus_source(root, spec, observed_at=clock)
        elif (receipt.get("URI") != uri or receipt.get("http_status") != "200"
              or receipt.get("curl_exit") != 0
              or receipt.get("body_sha256") != file_sha(root / spec["PDF"])):
            raise BoundedError("OFFICIAL_ORIGIN_REAL_RECEIPT_CHANGED")
        body = subprocess.run(["pdftotext", "-layout", str(root / spec["PDF"]), "-"],
                              capture_output=True, check=True, timeout=30).stdout.decode()
        arguments: dict[str, Any] = dict(
            code=code, expected_name=next(iter(fields)), issuer=issuers.get(code, {}),
            parent=parent, pdf_sha256=file_sha(root / spec["PDF"]),
            received_at=datetime.fromisoformat(receipt["completed_at"]), observed_at=clock)
        if is_prospectus:
            identity, company, qualification = qualify_prospectus(body, actual=actual, **arguments)
        else:
            identity, company, qualification = qualify_origin(body, document_uri=uri, **arguments)
        if digest(qualification) != spec["qualification_sha256"]:
            raise BoundedError("OFFICIAL_ORIGIN_QUALIFICATION_CHANGED")
        identities[code], qualifications[code] = identity, qualification
        output["dwd_listed_company"].append(company)
    raws = pq.ParquetFile(root / "inputs/financial-raw.parquet").read().to_pylist()
    contract = load_summary(root / "config/base231-domains.v1.json")
    for spec in plan["selection"]:
        raw = raws[spec["raw_index"]]
        if digest(raw) != spec["raw_row_sha256"] or raw["stock_code"] != spec["code"]:
            raise BoundedError("OFFICIAL_ORIGIN_COMPLETE_RAW_FINANCIAL_CHANGED")
        accepted = source_selection.get((spec["member"], spec["ordinal"]), {})
        if (source_members.get(spec["member"], {}).get("sha256") != spec["member_sha256"]
                or not accepted or any(raw.get(key) != accepted.get(key) for key in
                    ("stock_code", "category", "source", "source_revision", "content_sha256",
                     "acquired_at", "provider_updated_date", "market_date"))):
            raise BoundedError("OFFICIAL_ORIGIN_RAW_NOT_IN_EXACT_QUALIFIED_PARENT_SELECTION")
        project = project_prospectus if is_prospectus else project_origin_financial
        rows, _ = project(
            raw, identity=identities[spec["code"]], qualification=qualifications[spec["code"]],
            source_sha256=spec["member_sha256"], source_revision=spec["source_revision"],
            observed_at=clock, contract=contract,
        )
        for table, values in rows.items():
            output[table].extend(values)
    output["dwd_financial_concept"] = list({digest(r): r for r in output[
        "dwd_financial_concept"]}.values())
    image_contract, model, extension = load_contract(root / "config")
    expected_schema = schemas(model, extension)
    keys = {table: image_contract["tables"][table]["version_key"] for table in
            ("dwd_financial_fact", "dwd_financial_report", "dwd_financial_concept",
             "dwd_listed_company")}
    if set(output) != set(keys) or set(plan["output_tables"]) != set(keys):
        raise BoundedError("OFFICIAL_ORIGIN_EXACT_OUTPUT_TABLES_CHANGED")
    counts = {}
    for table, values in output.items():
        normalized = pa.Table.from_pylist(values, schema=expected_schema[table]).to_pylist()
        if normalized != values:
            raise BoundedError("OFFICIAL_ORIGIN_NATIVE_VALUE_OR_INSTANT_CHANGED")
        # Hash the fixed native Decimal representation; the original lexical
        # token remains bound separately in dimensions_json and the raw body.
        output[table] = normalized
        native = pq.ParquetFile(root / "outputs" / (table + ".parquet"))
        if native.schema_arrow != expected_schema[table] or native.read().to_pylist() != normalized:
            raise BoundedError("OFFICIAL_ORIGIN_NATIVE_SERIALIZATION_CHANGED")
        if len({digest([r[k] for k in keys[table]]) for r in values}) != len(values):
            raise BoundedError("OFFICIAL_ORIGIN_DUPLICATE_NATIVE_VERSION")
        counts[table] = len(values)
    reports = {r["financial_report_id"] for r in output["dwd_financial_report"]}
    concepts = {r["financial_concept_id"] for r in output["dwd_financial_concept"]}
    if any(r["financial_report_id"] not in reports or r["financial_concept_id"] not in concepts
           for r in output["dwd_financial_fact"]):
        raise BoundedError("OFFICIAL_ORIGIN_NATIVE_FOREIGN_KEY_MISSING")
    if counts != plan["rows"] or digest(dict(output)) != plan["semantic_sha256"]:
        raise BoundedError("OFFICIAL_ORIGIN_NATIVE_SEMANTICS_CHANGED")
    return dict(status="PASS_FULL_LOCAL_ORIGIN_AND_FINANCIAL_RECONSTRUCTION", rows=counts,
                input_identity=plan["identity"], semantic_sha256=plan["semantic_sha256"],
                source_network_or_old_roots_required=False, complete_Base=False,
                legal_form_date_qualified=False, historical_PIT_qualified=False,
                consumer_admission="NOT_GRANTED")
