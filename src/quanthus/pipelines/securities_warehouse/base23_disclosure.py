"""Retained CNINFO document captures, with explicit late knowledge and holds."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from .base22 import business_date, digest
from .base22_bounded import BoundedError
from .base22_outputs import announcement_observation, instant
from .base23_sources import THROUGH

CONTRACT_ID = "p10-base23-retained-cninfo-latest-capture-v1"
QUALIFICATION = "RETAINED_OFFICIAL_API_CAPTURES_NOT_ORIGINAL_PROVIDER_PIT"
TABLES = ("dwd_disclosure", "dwd_disclosure_subject")
RAW_FIELDS = {
    "security_id",
    "stock_code",
    "ann_id",
    "title",
    "publish_time",
    "announce_date",
    "name",
    "url",
    "source",
    "acquired_at",
}
MAX_CAPTURES = 300000
MAX_DOCUMENTS = 100000


def select_documents(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
    *,
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Same API/document uses the latest real capture, never package creation.

    All earlier captures remain physical raw evidence with their actual clocks.
    They do not become retrospectively admitted warehouse versions. A changed
    body is observed at the actual governance clock; same-clock contradictory
    captures remain held. Source identity and business dates are not inferred.
    """
    clock = instant(observed_at)
    grouped: dict[str, list[dict[str, Any]]] = {}
    count = 0
    for sha, revision, rows in sources:
        if re.fullmatch(r"sha256:[a-f0-9]{64}", sha) is None or not revision:
            raise BoundedError("SEPTEMBER_DISCLOSURE_SOURCE_IDENTITY_INVALID")
        for raw in rows:
            count += 1
            if count > MAX_CAPTURES or len(grouped) > MAX_DOCUMENTS:
                raise BoundedError("SEPTEMBER_DISCLOSURE_CAPTURE_OR_ENTITY_BOUND_EXCEEDED")
            if (
                set(raw) != RAW_FIELDS
                or raw["security_id"] != raw["stock_code"]
                or re.fullmatch(r"[0-9]{6}", raw["stock_code"] or "") is None
                or raw["source"] != "cninfo.hisAnnouncement"
                or not raw["ann_id"]
            ):
                raise BoundedError("SEPTEMBER_DISCLOSURE_RAW_SCHEMA_OR_IDENTITY_INVALID")
            acquired = instant(raw["acquired_at"])
            day = date.fromisoformat(raw["announce_date"])
            if day > THROUGH or acquired > clock:
                raise BoundedError("SEPTEMBER_DISCLOSURE_BUSINESS_OR_CAPTURE_CUTOFF_FAILED")
            grouped.setdefault(raw["ann_id"], []).append(
                dict(raw=raw, sha256=sha, revision=revision, acquired=acquired)
            )
    selected, held = [], []
    census: Counter[str] = Counter(raw_captures=count, document_keys=len(grouped))
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda v: (v["acquired"], v["sha256"], v["revision"]))
        at_latest = [v for v in values if v["acquired"] == values[-1]["acquired"]]
        captures = [
            dict(
                raw_row_sha256=digest(v["raw"]),
                member_sha256=v["sha256"],
                revision=v["revision"],
                acquired_at=v["raw"]["acquired_at"],
            )
            for v in values
        ]
        if len({v["raw"]["stock_code"] for v in values}) != 1:
            held.append(
                dict(
                    document_key_sha256=digest(key),
                    reason="DOCUMENT_CODE_IDENTITY_CONFLICT",
                    captures=captures,
                    disposition="NO_NEW_OUTPUT",
                )
            )
            census["code_identity_conflict_keys"] += 1
            continue
        if len({digest(v["raw"]) for v in at_latest}) != 1:
            held.append(
                dict(
                    document_key_sha256=digest(key),
                    reason="SAME_CAPTURE_CLOCK_CONFLICT",
                    captures=captures,
                    disposition="NO_NEW_OUTPUT",
                )
            )
            census["same_clock_conflict_keys"] += 1
            continue
        if (
            len({digest({k: z for k, z in v["raw"].items() if k != "acquired_at"}) for v in values})
            > 1
        ):
            census["revised_capture_keys"] += 1
        selected.append(
            dict(
                raw=at_latest[0]["raw"],
                sha256=at_latest[0]["sha256"],
                revision=at_latest[0]["revision"],
                captures=captures,
            )
        )
    if len(grouped) > MAX_DOCUMENTS:
        raise BoundedError("SEPTEMBER_DISCLOSURE_CAPTURE_OR_ENTITY_BOUND_EXCEEDED")
    census["selected_document_keys"] = len(selected)
    proof = dict(
        contract_id=CONTRACT_ID,
        census=dict(census),
        provider_acquisition=None,
        retrospective_PIT_admission=False,
        consumer_admission="NOT_GRANTED",
    )
    proof["identity"] = digest(proof)
    return selected, held, proof


def project_documents(
    selected: list[dict[str, Any]],
    *,
    listings: list[dict[str, Any]],
    securities: list[dict[str, Any]],
    observed_at: datetime,
    ss: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    clock = instant(observed_at)
    by_code: dict[str, list[dict[str, Any]]] = {}
    by_security: dict[str, list[dict[str, Any]]] = {}
    for row in listings:
        code = row["listing_code"]
        if "." in code:
            prefix, code = code.split(".", 1)
            if prefix not in {"sh", "sz", "bj"}:
                raise BoundedError("SEPTEMBER_DISCLOSURE_NATIVE_CODE_FORMAT_UNQUALIFIED")
        by_code.setdefault(code, []).append(row)
    for row in securities:
        by_security.setdefault(row["security_id"], []).append(row)
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    holds, proofs = [], []
    for item in selected:
        raw = item["raw"]
        fraction = re.search(r"[ T]\d\d:\d\d:\d\d[.,](\d+)", raw["publish_time"])
        if fraction and any(c != "0" for c in fraction[1][6:]):
            raise BoundedError("SEPTEMBER_DISCLOSURE_CLOCK_PRECISION_UNSUPPORTED")
        # The source publication is a business coordinate, not warehouse availability.
        from .disclosure_event import _provider_timestamp

        business = _provider_timestamp(raw["publish_time"], "retained CNINFO publication")
        parents = [
            p
            for p in by_code.get(raw["stock_code"], [])
            if p["effective_from"] <= business
            and (p["effective_to"] is None or business < p["effective_to"])
            and p["knowledge_from"] <= clock
            and (p["knowledge_to"] is None or clock < p["knowledge_to"])
        ]
        security = (
            [
                p
                for p in by_security.get(parents[0]["security_id"], [])
                if p["effective_from"] <= business
                and (p["effective_to"] is None or business < p["effective_to"])
                and p["knowledge_from"] <= clock
                and (p["knowledge_to"] is None or clock < p["knowledge_to"])
            ]
            if len(parents) == 1
            else []
        )
        if len(parents) != 1 or len(security) != 1:
            holds.append(
                dict(
                    document_key_sha256=digest(raw["ann_id"]),
                    raw_row_sha256=digest(raw),
                    source_sha256=item["sha256"],
                    reason="DATED_NATIVE_COMPANY_SECURITY_UNRESOLVED",
                    disposition="NO_NEW_OUTPUT",
                )
            )
            continue
        rows = announcement_observation(
            raw,
            company_id=security[0]["company_id"],
            security_id=security[0]["security_id"],
            parent_knowledge=clock,
            source_sha256=item["sha256"],
            revision=item["revision"],
        )
        for table, values in rows.items():
            if any(
                business_date(r["effective_at"]) > THROUGH or r["knowledge_from"] != clock
                for r in values
            ):
                raise BoundedError("SEPTEMBER_DISCLOSURE_PUBLICATION_OR_GOVERNANCE_CLOCK_INVALID")
            typed = pa.Table.from_pylist(values, schema=ss[table])
            if typed.to_pylist() != values or any(
                not f.nullable and typed.column(f.name).null_count for f in ss[table]
            ):
                raise BoundedError("SEPTEMBER_DISCLOSURE_TYPED_ATOM_CHANGED")
            out[table].extend(typed.to_pylist())
        proofs.append(
            dict(
                document_key_sha256=digest(raw["ann_id"]),
                raw_row_sha256=digest(raw),
                listing_row_sha256=digest(parents[0]),
                security_row_sha256=digest(security[0]),
                rows_sha256={t: digest(rows[t]) for t in TABLES},
                captures=item["captures"],
                original_acquired_at=raw["acquired_at"],
                provider_acquisition=None,
                knowledge_observed_at=clock,
                publication_string=raw["publish_time"],
                publication_grade="SOURCE_DECLARATION_NOT_PROVIDER_AVAILABILITY_RECEIPT",
            )
        )
    return out, dict(
        holds=holds,
        proofs=proofs,
        qualified_document_keys=len(proofs),
        existing_native_rows_mutated=0,
        historical_PIT_admission=False,
    )
