"""One explicitly identified official holder-transfer observation.

Original provider IDs are grounded in the actual notice and table row. This
does not grant equivalence to any F10 atom without an exact reconciliation.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .financial_reporting import FinancialCompanyIdentity
from .ownership_capital import _effective_start, _stable_holder_key, _stable_id

CONTRACT_ID = "p10-base231-official-holder-transfer-notice-row-v1"
_NUMBER = r"([−－-]?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"


def project_notice(
    body: str,
    *,
    code: str,
    expected_event_date: date,
    announcement_id: str,
    publication_ms: int,
    pdf_sha256: str,
    received_at: datetime,
    observed_at: datetime,
    parent: FinancialCompanyIdentity,
    document_uri: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    from .disclosure_event import disclosure_event_schemas
    from .ownership_capital import ownership_capital_schemas

    if "\x1f" in body or "§" in body:
        raise BoundedError("HOLDER_NOTICE_NUMERIC_TOKEN_BOUNDARY_UNPROVEN")
    # Preserve whitespace separating numeric columns. Removing it can turn
    # '1,099.50 11.00%' into an ambiguous, numerically different value.
    separated = re.sub(r"(?<=[0-9%])\s+(?=[0-9−－-])", "\x1f", body)
    text = re.sub(r"\s+", "", separated.replace("\x1f", "§"))
    clock = instant(observed_at)
    found = re.findall(r"权益变动时间(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    actor = re.findall(r"信息披露义务人(.{1,60}?)住所", text)
    if (
        len(set(found)) != 1
        or len(set(actor)) != 1
        or date(*map(int, found[0])) != expected_event_date
        or "股票代码" + code not in text
        or parent.stock_code != code
        or expected_event_date > date(2026, 9, 30)
        or not re.fullmatch(r"\d{6}", code)
        or not re.fullmatch(r"\d+", announcement_id)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", pdf_sha256)
        or not document_uri.startswith("https://static.cninfo.com.cn/finalpage/")
        or instant(received_at) > clock
        or parent.knowledge_from > clock
        or not parent.effective_from <= _effective_start(expected_event_date)
        or parent.effective_to is not None
        and _effective_start(expected_event_date) >= parent.effective_to
        or type(publication_ms) is not int
    ):
        raise BoundedError("HOLDER_NOTICE_EXACT_ID_DATE_PARENT_SOURCE_UNPROVEN")
    name = actor[0]
    section = re.search(r"2[.、]本次权益变动情况(.+?)3[.、]本次变动前后", text)
    if section is None or "变动股数（万股）" not in section.group(1):
        raise BoundedError("HOLDER_NOTICE_EXPLICIT_SOURCE_UNIT_REQUIRED")
    changes = set(
        re.findall(r"A股" + re.escape(name) + _NUMBER + "§+" + _NUMBER + "%", section.group(1))
    )
    after = set(
        re.findall(
            re.escape(name)
            + r"合计持有股份"
            + _NUMBER
            + "§+"
            + _NUMBER
            + "%§+"
            + _NUMBER
            + "§+"
            + _NUMBER
            + "%",
            text,
        )
    )
    if not after:
        # pdftotext places a vertically centered, row-spanned holder name on
        # the next floating-share row. Bind only that explicit layout; never
        # select the first aggregate or use numerical agreement as identity.
        lines = body.splitlines()
        aggregate_pattern = (
            r"合计持有股份\s+"
            + _NUMBER
            + r"\s+"
            + _NUMBER
            + r"%\s+"
            + _NUMBER
            + r"\s+"
            + _NUMBER
            + "%"
        )
        for n, line in enumerate(lines):
            aggregate = re.findall(aggregate_pattern, line)
            following = lines[n + 1 : n + 4]
            if (
                len(aggregate) == 1
                and not any("合计持有股份" in s for s in following)
                and any(
                    re.match(r"\s*" + re.escape(name) + r"\s+其中[：:]无限售条件股份", s)
                    for s in following
                )
            ):
                after.add(aggregate[0])
    if len(changes) != 1 or len(after) != 1 or "股数（万股）" not in text or "询价转让" not in text:
        raise BoundedError("HOLDER_NOTICE_SINGLE_EVENT_AND_AFTER_HOLDING_UNPROVEN")
    delta_tokens = next(iter(changes))
    holding_tokens = next(iter(after))
    with localcontext() as ctx:
        ctx.prec = 64
        delta, ratio = [
            Decimal(t.replace(",", "").replace("−", "-").replace("－", "-")) for t in delta_tokens
        ]
        before, before_ratio, after_value, after_ratio = [
            Decimal(t.replace(",", "").replace("−", "-").replace("－", "-")) for t in holding_tokens
        ]
        if (
            not all(
                v.is_finite()
                for v in [delta, ratio, before, before_ratio, after_value, after_ratio]
            )
            or delta >= 0
            or not -100 <= ratio < 0
            or before < 0
            or after_value < 0
            or not 0 <= before_ratio <= 100
            or not 0 <= after_ratio <= 100
            or after_value - before != delta
            or after_ratio - before_ratio != ratio
        ):
            raise BoundedError("HOLDER_NOTICE_EXACT_BEFORE_AFTER_AND_DELTA_CONFLICT")
        shares, post = delta * 10000, after_value * 10000
    published = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=publication_ms)
    if (
        published > clock
        or published.astimezone(ZoneInfo("Asia/Shanghai")).date() < expected_event_date
    ):
        raise BoundedError("HOLDER_NOTICE_DECLARED_PUBLICATION_CLOCK_CONFLICT")
    common = dict(
        record_source="cninfo.official_holder_transfer_notice",
        source_record_id=announcement_id,
        source_revision=CONTRACT_ID,
        source_artifact_sha256=pdf_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        announced_at=published,
    )
    disclosure_id = _stable_id("disclosure", CONTRACT_ID, announcement_id)
    disclosure = dict(
        **common,
        effective_at=published,
        disclosure_id=disclosure_id,
        company_id=parent.company_id,
        disclosure_type="OFFICIAL_HOLDER_TRANSFER",
        source_document_id=announcement_id,
        title="Official holder-transfer observation " + announcement_id,
        document_uri=document_uri,
        language="zh",
    )
    subject = dict(
        **common,
        effective_at=published,
        disclosure_id=disclosure_id,
        disclosure_subject_id=_stable_id("disclosure_subject", disclosure_id, parent.security_id),
        subject_type="SECURITY",
        security_id=parent.security_id,
        corporate_action_id=None,
        financial_report_id=None,
    )
    change = dict(
        **common,
        effective_at=_effective_start(expected_event_date),
        shareholding_change_id=_stable_id(
            "shareholding_change", CONTRACT_ID, announcement_id, "A_SHARE_TRANSFER_ROW", name
        ),
        company_id=parent.company_id,
        security_id=parent.security_id,
        shareholder_source_name=name,
        shareholder_id=None,
        change_type="DISCLOSED_INQUIRY_TRANSFER_DECREASE",
        share_change=shares,
        percentage_change=ratio,
        post_holding=post,
        post_percentage=after_ratio,
        disclosure_id=disclosure_id,
        holder_match_key=_stable_holder_key(name),
        raw_share_change=delta_tokens[0] + "万股",
        share_change_unit="SHARE",
        raw_percentage_change=delta_tokens[1] + "%",
        raw_post_holding=holding_tokens[2] + "万股",
        raw_post_percentage=holding_tokens[3] + "%",
        percentage_change_unit="PERCENT",
        post_holding_unit="SHARE",
        post_percentage_unit="PERCENT",
        parser_version=CONTRACT_ID,
    )
    output = dict(
        dwd_disclosure=[disclosure],
        dwd_disclosure_subject=[subject],
        dwd_shareholding_change=[change],
    )
    row_schemas = {**disclosure_event_schemas(), **ownership_capital_schemas()}
    for table, values in output.items():
        typed = pa.Table.from_pylist(values, schema=row_schemas[table])
        if typed.to_pylist() != values:
            raise BoundedError("HOLDER_NOTICE_NATIVE_NUMERIC_OR_TIME_CHANGED")
    proof = dict(
        contract_id=CONTRACT_ID,
        original_provider="CNINFO_OFFICIAL_NOTICE",
        original_disclosure_id=announcement_id,
        original_event_id=announcement_id + ":A_SHARE_TRANSFER_ROW",
        original_body_sha256=pdf_sha256,
        interpreted_body_sha256=digest(body),
        original_atom_sha256=digest([name, expected_event_date, delta_tokens, holding_tokens]),
        declared_publication_ms=publication_ms,
        actual_received_at=received_at,
        knowledge_at=clock,
        source_unit="TEN_THOUSAND_SHARES_AND_PERCENT",
        existing_F10_atom_equivalence_granted=False,
        provider_PIT_qualified=False,
        existing_bad_source_cell_rewritten=False,
        rows_sha256=digest(output),
    )
    return output, proof
