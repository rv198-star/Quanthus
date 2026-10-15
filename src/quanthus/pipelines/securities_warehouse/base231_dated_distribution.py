"""Dated official distribution terms with unchanged audited numeric parsing."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, load_contract
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_sources import _tree, physical_path
from .base231_disclosure_body import project_body
from .base231_official_action import materialize_distribution, parse_distribution
from .disclosure_event import disclosure_event_schemas
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base231-dated-official-implemented-distribution-terms-v1"


def project_terms(
    body: str,
    *,
    selection: dict[str, Any],
    receipt: dict[str, Any],
    index: dict[str, Any],
    parent: Any,
    venue: str,
    expected_ex_date: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    observed_at: datetime,
    vocabulary: dict[str, Any],
    currency_evidence: dict[str, Any] | None = None,
    payment_revision: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    announcement = selection["actual_index_row"]
    disclosure, source_proof = project_body(
        selection,
        receipt,
        index=index,
        body_sha256=receipt["body_sha256"],
        body_text=body,
        parent=parent,
        observed_at=observed_at,
    )
    currency_proof = None
    if currency_evidence is not None:
        from .base231_distribution_currency import qualify_currency_source

        currency_proof = qualify_currency_source(
            currency_evidence, implementation=announcement, observed_at=observed_at
        )
    record, term_proof = parse_distribution(
        body,
        code=parent.stock_code,
        venue=venue,
        expected_ex_date=expected_ex_date,
        currency_scheme_body=currency_evidence["body"] if currency_evidence is not None else None,
    )
    if currency_proof is not None and term_proof.get("currency_binding_contract") != (
        "same-issuer-current-approved-scheme-corroboration-v1"
    ):
        raise BoundedError("DISTRIBUTION_CURRENCY_CORROBORATION_NOT_NEEDED")
    revision_proof = None
    if payment_revision is not None:
        from .base231_distribution_revision import qualify_payment_revision

        if venue != "SZSE":
            raise BoundedError("DISTRIBUTION_PAYMENT_REVISION_VENUE_UNSUPPORTED")
        revision_proof = qualify_payment_revision(
            payment_revision["previous"],
            dict(selection=selection, receipt=receipt, index=index,
                 PDF_sha256=receipt["body_sha256"], body=body),
            payment_revision["notice"], parent=parent, observed_at=observed_at,
            expected_ex_date=expected_ex_date,
            currency_scheme_body=currency_evidence["body"] if currency_evidence else None,
        )
        record["payment_date"] = revision_proof["payment_date"]
    from .disclosure_event import _effective_start

    at = _effective_start(expected_ex_date)
    mic = {"SZSE": "XSHE", "SSE": "XSHG"}.get(venue)
    valid_venues = [
        r
        for r in venues
        if r["mic_code"] == mic
        and r["timezone"] == "Asia/Shanghai"
        and r["effective_from"] <= at
        and (r["effective_to"] is None or at < r["effective_to"])
        and r["knowledge_from"] <= instant(observed_at)
        and (r["knowledge_to"] is None or instant(observed_at) < r["knowledge_to"])
    ]
    if len(valid_venues) != 1:
        raise BoundedError("DISTRIBUTION_AUTHORITATIVE_VENUE_UNPROVEN")
    venue_id = valid_venues[0]["venue_id"]
    valid_listings = [
        r
        for r in listings
        if r["security_id"] == parent.security_id
        and r["venue_id"] == venue_id
        and r["effective_from"] <= at
        and (r["effective_to"] is None or at < r["effective_to"])
        and r["knowledge_from"] <= instant(observed_at)
        and (r["knowledge_to"] is None or instant(observed_at) < r["knowledge_to"])
    ]
    valid_calendar = [
        r
        for r in calendars
        if r["venue_id"] == venue_id
        and r["trade_date"] == expected_ex_date
        and r["session_type"] == "day"
        and r["is_open"] is True
        and r["knowledge_from"] <= instant(observed_at)
        and (r["knowledge_to"] is None or instant(observed_at) < r["knowledge_to"])
    ]
    if len(valid_listings) != 1 or len(valid_calendar) != 1:
        raise BoundedError("DISTRIBUTION_EX_DATE_LISTING_VENUE_CALENDAR_UNPROVEN")
    output = materialize_distribution(
        record,
        announcement_id=str(announcement["announcementId"]),
        observed_at=observed_at,
        pdf_received_at=datetime.fromisoformat(receipt["completed_at"]),
        pdf_sha256=receipt["body_sha256"],
        company_id=parent.company_id,
        security_id=parent.security_id,
        vocabulary=vocabulary,
    )
    published = disclosure["dwd_disclosure"][0]["announced_at"]
    for table, values in output.items():
        for row in values:
            row["announced_at"] = published
            row["source_revision"] = CONTRACT_ID
            if currency_proof is not None:
                row["source_revision"] = currency_proof["contract_id"]
                row["source_artifact_sha256"] = digest(currency_proof)
            if revision_proof is not None:
                row["source_revision"] = revision_proof["contract_id"]
                row["source_artifact_sha256"] = digest([currency_proof, revision_proof])
        if (
            pa.Table.from_pylist(values, schema=disclosure_event_schemas()[table]).to_pylist()
            != values
        ):
            raise BoundedError("DISTRIBUTION_NATIVE_VALUE_OR_INSTANT_CHANGED")
    proof = dict(
        contract_id=CONTRACT_ID,
        input_source_proof_sha256=digest(source_proof),
        audited_term_proof=term_proof,
        exact_original_publication_at=published,
        input_listing_sha256=digest(valid_listings),
        input_calendar_sha256=digest(valid_calendar),
        input_venue_sha256=digest(valid_venues),
        native_rows_sha256=digest(output),
        old_values_overwritten=False,
        full_share_adjustment_basis_qualified=False,
        cumulative_adjustment_qualified=False,
        historical_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
    )
    if currency_proof is not None:
        proof["currency_source_proof"] = currency_proof
    if revision_proof is not None:
        proof["payment_revision_proof"] = revision_proof
    return output, proof


def replay_terms_domain(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("DISTRIBUTION_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("DISTRIBUTION_OFFLINE_PHYSICAL_SET_CHANGED")
    for name, m in plan["members"].items():
        p = physical_path(root / name)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or p.stat().st_size != m["bytes"]
            or file_sha(p) != m["sha256"]
        ):
            raise BoundedError("DISTRIBUTION_OFFLINE_MEMBER_CHANGED")
    for name, sha in plan["code_inputs"].items():
        if file_sha(Path(name)) != sha:
            raise BoundedError("DISTRIBUTION_OFFLINE_CODE_CHANGED")
    has_currency = any(s.get("currency_scheme") for s in plan["selection"])
    if has_currency:
        from .base231_distribution_currency import CONTRACT_ID as currency_contract

        code = Path("src/quanthus/pipelines/securities_warehouse/base231_distribution_currency.py")
        if plan.get("currency_evidence_contract") != currency_contract or plan["code_inputs"].get(
            str(code)
        ) != file_sha(code):
            raise BoundedError("DISTRIBUTION_CURRENCY_OFFLINE_CONTRACT_REQUIRED")
    if any(s.get("payment_revision") for s in plan["selection"]):
        from .base231_distribution_revision import CONTRACT_ID as revision_contract

        code = Path("src/quanthus/pipelines/securities_warehouse/base231_distribution_revision.py")
        if plan.get("payment_revision_contract") != revision_contract or plan["code_inputs"].get(
            str(code)
        ) != file_sha(code):
            raise BoundedError("DISTRIBUTION_PAYMENT_REVISION_OFFLINE_CONTRACT_REQUIRED")
    if (
        file_sha(root / "inputs/image-manifest.json")
        != "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
    ):
        raise BoundedError("DISTRIBUTION_EXACT_PARENT_CHANGED")
    manifest = json.loads((root / "inputs/image-manifest.json").read_bytes())
    if file_sha(root / "inputs/image-materialization.json") != manifest["materialization_sha256"]:
        raise BoundedError("DISTRIBUTION_EXACT_PARENT_LAYOUT_CHANGED")
    layout = json.loads((root / "inputs/image-materialization.json").read_bytes())["layout"]
    parents: dict[str, list[dict[str, Any]]] = {}
    for t, members in plan["parents"].items():
        originals = {m["path"]: m["sha256"] for m in layout[t]["files"]}
        if {m["original"] for m in members} != set(originals):
            raise BoundedError("DISTRIBUTION_PARENT_PHYSICAL_SET_CHANGED")
        parents[t] = []
        for m in members:
            p = root / m["local"]
            if file_sha(p) != originals[m["original"]]:
                raise BoundedError("DISTRIBUTION_PARENT_FILE_CHANGED")
            parents[t].extend(pq.ParquetFile(p).read().to_pylist())
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    output: dict[str, list[dict[str, Any]]] = {
        t: [] for t in ["dwd_corporate_action", "dwd_corporate_action_term"]
    }
    proofs = []
    now = datetime.fromisoformat(plan["observed_at"])
    vocab = json.loads((root / "config/disclosure-event-vocabulary.v1.json").read_bytes())
    for s in plan["selection"]:
        original = json.loads((root / s["selection"]).read_bytes())["selection"]
        idx = root / s["index"]
        if file_sha(idx) != original["index_sha256"]:
            raise BoundedError("DISTRIBUTION_ORIGINAL_INDEX_CHANGED")
        parent, _ = resolver.resolve_for_report(
            original["actual_index_row"]["secCode"],
            period_end=date.fromisoformat(original["original_disclosure"]["business_date"]),
        )
        if parent is None:
            raise BoundedError("DISTRIBUTION_DATED_PARENT_UNAVAILABLE")
        receipt = json.loads((root / s["receipt"]).read_bytes())
        if file_sha(root / s["PDF"]) != receipt["body_sha256"]:
            raise BoundedError("DISTRIBUTION_PDF_CHANGED")
        body = subprocess.run(
            ["pdftotext", "-layout", str(root / s["PDF"]), "-"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout.decode()
        currency_evidence = None
        cs = s.get("currency_scheme")
        if cs is not None:
            currency_evidence = dict(
                actual_index_row=cs["actual_index_row"],
                index=json.loads((root / cs["index"]).read_bytes()),
                index_receipt=json.loads((root / cs["index_receipt"]).read_bytes()),
                PDF_receipt=json.loads((root / cs["PDF_receipt"]).read_bytes()),
                PDF_sha256=file_sha(root / cs["PDF"]),
                PDF_bytes=(root / cs["PDF"]).stat().st_size,
                index_sha256=file_sha(root / cs["index"]),
                index_bytes=(root / cs["index"]).stat().st_size,
                body=subprocess.run(
                    ["pdftotext", "-layout", str(root / cs["PDF"]), "-"],
                    check=True,
                    capture_output=True,
                    timeout=60,
                ).stdout.decode(),
            )
        revision_evidence = None
        revision_spec = s.get("payment_revision")
        if revision_spec is not None:
            from .base231_distribution_revision import load_revision_evidence

            revision_evidence = {
                k: load_revision_evidence(root, revision_spec[k]) for k in ["previous", "notice"]
            }
        rows, pf = project_terms(
            body,
            selection=original,
            receipt=receipt,
            index=json.loads(idx.read_bytes()),
            parent=parent,
            venue=s["venue"],
            expected_ex_date=date.fromisoformat(s["ex_date"]),
            listings=parents["dwd_listing"],
            calendars=parents["dwd_trading_calendar"],
            venues=parents["dwd_trading_venue"],
            observed_at=now,
            vocabulary=vocab,
            currency_evidence=currency_evidence,
            payment_revision=revision_evidence,
        )
        if cs is not None and digest(pf["currency_source_proof"]) != file_sha(
            root / cs["qualification"]
        ):
            raise BoundedError("DISTRIBUTION_CURRENCY_SOURCE_QUALIFICATION_CHANGED")
        if revision_spec is not None and digest(pf["payment_revision_proof"]) != file_sha(
            root / revision_spec["qualification"]
        ):
            raise BoundedError("DISTRIBUTION_PAYMENT_REVISION_QUALIFICATION_CHANGED")
        for t, v in rows.items():
            output[t].extend(v)
        proofs.append(pf)
    contract, _, _ = load_contract(root / "config")
    for t, values in output.items():
        physical = pq.ParquetFile(root / "outputs" / (t + ".parquet")).read()
        keys = contract["tables"][t]["version_key"]
        if (
            physical.schema != disclosure_event_schemas()[t]
            or physical.to_pylist() != values
            or len({digest([r[k] for k in keys]) for r in values}) != len(values)
        ):
            raise BoundedError("DISTRIBUTION_NATIVE_REPLAY_CHANGED")
    if digest(output) != plan["semantic_sha256"] or digest(proofs) != plan["proof_sha256"]:
        raise BoundedError("DISTRIBUTION_SEMANTIC_CHANGED")
    return dict(
        status="PASS_FULL_LOCAL_OFFICIAL_DISTRIBUTION_TERMS_REPLAY",
        rows={t: len(v) for t, v in output.items()},
        input_identity=plan["identity"],
        complete_Base=False,
        consumer_admission="NOT_GRANTED",
        cumulative_adjustment_qualified=False,
        old_source_roots_or_network_required=False,
    )
