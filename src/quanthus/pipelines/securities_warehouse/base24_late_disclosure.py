"""Bounded late rolling announcements as additive governance observations.

The ordinary Daily date window stays unchanged. A frozen prior-document census
and the exact source members feed this separate two-table projection. New knowledge
is recorded now; original captures, missing identity and old values stay intact.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_outputs import announcement_observation
from .base22_sources import _tree, physical_path
from .base23_disclosure import RAW_FIELDS, TABLES
from .disclosure_event import _provider_timestamp, disclosure_event_schemas

CONTRACT = "p10-base24-late-rolling-disclosure-observation-v1"
KIND = "base24_late_disclosure_frozen_census_projection_v1"
OPTIONAL_FIELDS = {"raw_response_sha256", "response_completed_at", "body_sha256"}


def _parent(
    raw: dict[str, Any],
    listings: dict[str, list[dict[str, Any]]],
    securities: dict[str, list[dict[str, Any]]],
    business: datetime,
    observed: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    def active(row: dict[str, Any]) -> bool:
        return bool(
            row["effective_from"] <= business
            and (row["effective_to"] is None or business < row["effective_to"])
            and row["knowledge_from"] <= observed
            and (row["knowledge_to"] is None or observed < row["knowledge_to"])
        )

    ls = [r for r in listings.get(raw["stock_code"], []) if active(r)]
    if not ls:
        return None, "DATED_LISTING_UNAVAILABLE"
    if len(ls) != 1:
        return None, "DATED_LISTING_AMBIGUOUS"
    ss = [r for r in securities.get(ls[0]["security_id"], []) if active(r)]
    if len(ss) != 1:
        return None, "DATED_SECURITY_UNAVAILABLE_OR_AMBIGUOUS"
    return ss[0], None


def project_late_disclosures(
    sources: list[dict[str, Any]],
    *,
    known: list[dict[str, Any]],
    listings: list[dict[str, Any]],
    securities: list[dict[str, Any]],
    observed_at: datetime,
    through: date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Replay a frozen, bounded census; never claim a complete Daily or body audit.

    Each source specifies its original revision/hash, market day, prior date and
    whole normalized member. Known documents come from an independently recorded
    exact Base/prior-Daily census. Its identity belongs in the enclosing input plan.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    observed = _clock(observed_at)
    if not date(2026, 10, 1) <= through <= date(2026, 10, 31):
        raise BoundedError("BASE24_LATE_THROUGH_SCOPE_INVALID")
    if len(sources) > 31 or len(known) > 100000:
        raise BoundedError("BASE24_LATE_INPUT_BOUND_EXCEEDED")
    grouped: dict[str, list[dict[str, Any]]] = {}
    census: Counter[str] = Counter()
    identities = []
    for src in sources:
        day = date.fromisoformat(src["market_date"])
        after = date.fromisoformat(src["covered_after"])
        if (
            not date(2026, 10, 1) <= day <= through
            or after >= day
            or re.fullmatch(r"sha256:[0-9a-f]{64}", src["sha256"]) is None
            or not src["revision"]
        ):
            raise BoundedError("BASE24_LATE_SOURCE_SCOPE_INVALID")
        identities.append(
            {k: src[k] for k in ["sha256", "revision", "market_date", "covered_after"]}
        )
        for raw in src["rows"]:
            census["raw_captures"] += 1
            if census["raw_captures"] > 200000:
                raise BoundedError("BASE24_LATE_INPUT_BOUND_EXCEEDED")
            if (
                not RAW_FIELDS <= set(raw) <= RAW_FIELDS | OPTIONAL_FIELDS
                or raw["security_id"] != raw["stock_code"]
                or re.fullmatch(r"[0-9]{6}", raw["stock_code"] or "") is None
                or raw["source"] != "cninfo.hisAnnouncement"
                or not raw["ann_id"]
            ):
                raise BoundedError("BASE24_LATE_RAW_IDENTITY_OR_FIELDS_INVALID")
            acquired = _clock(raw["acquired_at"])
            publication = raw["publish_time"]
            frac = re.search(r"[ T]\d\d:\d\d:\d\d[.,](\d+)", publication)
            if frac and any(c != "0" for c in frac[1][6:]):
                raise BoundedError("BASE24_LATE_PUBLICATION_PRECISION_UNSUPPORTED")
            business = _provider_timestamp(publication, "late announcement publication")
            if (
                acquired > observed
                or business > acquired
                or business.astimezone(ZoneInfo("Asia/Shanghai")).date()
                != date.fromisoformat(raw["announce_date"])
                or date.fromisoformat(raw["announce_date"]) > day
            ):
                raise BoundedError("BASE24_LATE_ORIGINAL_CLOCK_INVALID")
            if date.fromisoformat(raw["announce_date"]) > after:
                census["ordinary_window_captures"] += 1
                continue
            census["late_captures"] += 1
            grouped.setdefault(raw["ann_id"], []).append(
                dict(raw=raw, source=src, acquired=acquired, business=business)
            )
    by_document: dict[str, list[dict[str, Any]]] = {}
    by_code: dict[str, list[dict[str, Any]]] = {}
    by_security: dict[str, list[dict[str, Any]]] = {}
    for row in listings:
        by_code.setdefault(row["listing_code"], []).append(row)
    for row in securities:
        by_security.setdefault(row["security_id"], []).append(row)
    for row in known:
        if _clock(row["knowledge_from"]) > observed:
            raise BoundedError("BASE24_LATE_KNOWN_VERSION_AFTER_OBSERVATION")
        if row["knowledge_to"] is None or observed < _clock(row["knowledge_to"]):
            by_document.setdefault(row["source_document_id"], []).append(row)
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    holds, proofs = [], []
    for key, captures in sorted(grouped.items()):
        captures.sort(key=lambda x: (x["acquired"], x["source"]["sha256"]))
        latest = [c for c in captures if c["acquired"] == captures[-1]["acquired"]]
        identities_at_clock = {
            digest({k: c["raw"][k] for k in RAW_FIELDS - {"acquired_at", "name"}}) for c in latest
        }
        reason = None
        if len({c["raw"]["stock_code"] for c in captures}) != 1:
            reason = "DOCUMENT_CODE_IDENTITY_CONFLICT"
        elif len(identities_at_clock) != 1:
            reason = "SAME_CAPTURE_CLOCK_CONFLICT"
        item = latest[0]
        raw = item["raw"]
        parent, parent_error = _parent(raw, by_code, by_security, item["business"], observed)
        reason = reason or parent_error
        prior = by_document.get(key, [])
        latest_prior = max((_clock(r["knowledge_from"]) for r in prior), default=None)
        priors = [r for r in prior if _clock(r["knowledge_from"]) == latest_prior]
        if len({digest(r) for r in priors}) > 1:
            reason = reason or "PRIOR_SAME_KNOWLEDGE_VERSION_CONFLICT"
        if priors and parent and any(r["company_id"] != parent["company_id"] for r in priors):
            reason = reason or "PRIOR_DOCUMENT_COMPANY_CONFLICT"
        witness = dict(
            document_key_sha256=digest(key),
            captures=[
                dict(
                    raw_row_sha256=digest(c["raw"]),
                    source_sha256=c["source"]["sha256"],
                    original_acquired_at=c["raw"]["acquired_at"],
                )
                for c in captures
            ],
        )
        if reason:
            holds.append(dict(**witness, reason=reason, disposition="NO_NEW_OUTPUT"))
            continue
        assert parent is not None
        if priors and all(
            r["title"] == raw["title"]
            and r["document_uri"] == raw["url"]
            and r["announced_at"] == item["business"]
            for r in priors
        ):
            census["known_unchanged_documents"] += 1
            continue
        values = announcement_observation(
            raw,
            company_id=parent["company_id"],
            security_id=parent["security_id"],
            parent_knowledge=observed,
            source_sha256=item["source"]["sha256"],
            revision=item["source"]["revision"],
        )
        for t, rows in values.items():
            for row in rows:
                if t == TABLES[0]:
                    row["disclosure_type"] = "announcement"
                row["record_source"] = "base24_daily/late_disclosure"
                row["source_record_id"] = "announcements_delta.parquet#ann_id=" + key
            typed = pa.Table.from_pylist(rows, schema=disclosure_event_schemas()[t])
            if typed.to_pylist() != rows or any(
                not f.nullable and typed.column(f.name).null_count for f in typed.schema
            ):
                raise BoundedError("BASE24_LATE_TYPED_ATOM_CHANGED")
            out[t].extend(rows)
        census["new_documents" if not priors else "revised_documents"] += 1
        proofs.append(
            dict(
                **witness,
                selected_source_sha256=item["source"]["sha256"],
                original_publish_time=raw["publish_time"],
                governance_observed_at=observed,
                previous_versions_sha256=digest(priors),
                typed_rows_sha256=digest(values),
            )
        )
    for t, rows in out.items():
        primary = "disclosure_id" if t == TABLES[0] else "disclosure_subject_id"
        if len(rows) != len({r[primary] for r in rows}):
            raise BoundedError("BASE24_LATE_OUTPUT_KEYS_NOT_UNIQUE")
    proof = dict(
        contract_id=CONTRACT,
        through=through,
        observed_at=observed,
        source_identities=identities,
        census=dict(census),
        held=holds,
        selected=proofs,
        row_counts={t: len(v) for t, v in out.items()},
        frozen_prior_census_sha256=digest(known),
        consumer_admission="NOT_GRANTED",
        complete_Daily_admission=False,
        historical_PIT_admission=False,
        original_body_content_qualified=False,
        old_rows_overwritten=False,
    )
    return out, proof


def verify_projection(root: Path, *, manifest_sha256: str) -> dict[str, Any]:
    """Replay only the self-contained frozen census projection, never a full Daily.

    This verifies the complete member/row closure of the additive projection. The
    prior-document census is a separately qualified input, not a claim that an
    offline subset can prove absence from the original complete Base by itself.
    """
    import json

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    root = physical_path(root)
    if (
        file_sha(root / "manifest.json") != manifest_sha256
        or (root / "READY").read_text().strip() != manifest_sha256
    ):
        raise BoundedError("BASE24_LATE_MANIFEST_OR_READY_DIFFERS")
    plan = json.loads((root / "manifest.json").read_bytes())
    if (
        plan.get("kind") != KIND
        or plan.get("contract_id") != CONTRACT
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or any(
            plan.get(k) is not False
            for k in [
                "execution_authorized",
                "complete_Daily_admission",
                "historical_PIT_admission",
            ]
        )
        or re.fullmatch(r"[0-9a-f]{40}", plan.get("code_revision", "")) is None
        or plan.get("module_sha256") != file_sha(Path(__file__))
    ):
        raise BoundedError("BASE24_LATE_PURPOSE_OR_CODE_DIFFERS")
    inputs = plan["sources"]
    if not isinstance(inputs, list) or not 0 < len(inputs) <= 31:
        raise BoundedError("BASE24_LATE_SOURCE_MEMBER_INVALID")
    expected = {
        "inputs/known.parquet",
        "inputs/listing.parquet",
        "inputs/security.parquet",
        "inputs/prior-census.safe.json",
        "PROJECTION.json",
    } | {"tables/" + t + ".parquet" for t in TABLES}
    for src in inputs:
        name = src["member"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"inputs/source-[0-9]{2}\.parquet", name)
            or name in expected
        ):
            raise BoundedError("BASE24_LATE_SOURCE_MEMBER_INVALID")
        expected.add(name)
    if set(plan["files"]) != expected or _tree(root) != expected | {"manifest.json", "READY"}:
        raise BoundedError("BASE24_LATE_PHYSICAL_FILE_SET_DIFFERS")
    if sum((root / n).stat().st_size for n in expected) > 256 * 1024**2:
        raise BoundedError("BASE24_LATE_INPUT_BOUND_EXCEEDED")
    for name, entry in plan["files"].items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or str(rel) != name:
            raise BoundedError("BASE24_LATE_PHYSICAL_PATH_INVALID")
        p = physical_path(root / name)
        if (
            not p.is_file()
            or p.stat().st_nlink != 1
            or p.stat().st_size != entry["bytes"]
            or file_sha(p) != entry["sha256"]
        ):
            raise BoundedError("BASE24_LATE_PHYSICAL_MEMBER_DIFFERS")
    census = json.loads((root / "inputs/prior-census.safe.json").read_bytes())
    if (
        census.get("status") != "QUALIFIED_EXACT_PRIOR_DOCUMENT_CENSUS_INPUT"
        or census.get("known_member_sha256") != plan["files"]["inputs/known.parquet"]["sha256"]
        or not isinstance(census.get("original_input_identities"), list)
        or not census["original_input_identities"]
        or any(
            re.fullmatch(r"sha256:[a-f0-9]{64}", r.get("manifest_sha256", "")) is None
            for r in census["original_input_identities"]
        )
    ):
        raise BoundedError("BASE24_LATE_PRIOR_CENSUS_INPUT_UNQUALIFIED")
    sources = []
    for src in inputs:
        if src["sha256"] != plan["files"][src["member"]]["sha256"]:
            raise BoundedError("BASE24_LATE_SOURCE_IDENTITY_DIFFERS")
        pf = pq.ParquetFile(root / src["member"])
        if pf.metadata.num_rows > 200000:
            raise BoundedError("BASE24_LATE_INPUT_BOUND_EXCEEDED")
        sources.append(dict(src, rows=pf.read().to_pylist()))
    other = {}
    repository = Path(__file__).resolve().parents[4]
    _, model, extension = load_contract(repository / "config/pipelines")
    models = schemas(model, extension)
    for key in ["known", "listing", "security"]:
        pf = pq.ParquetFile(root / ("inputs/" + key + ".parquet"))
        table = "dwd_disclosure" if key == "known" else "dwd_" + key
        if pf.schema_arrow != models[table]:
            raise BoundedError("BASE24_LATE_REFERENCE_SCHEMA_DIFFERS")
        if pf.metadata.num_rows > 100000:
            raise BoundedError("BASE24_LATE_INPUT_BOUND_EXCEEDED")
        other[key] = pf.read().to_pylist()
    rows, proof = project_late_disclosures(
        sources,
        known=other["known"],
        listings=other["listing"],
        securities=other["security"],
        observed_at=datetime.fromisoformat(plan["observed_at"]),
        through=date.fromisoformat(plan["through"]),
    )
    if canonical(proof) + b"\n" != (root / "PROJECTION.json").read_bytes():
        raise BoundedError("BASE24_LATE_REPLAY_PROOF_DIFFERS")
    for table in TABLES:
        pf = pq.ParquetFile(root / ("tables/" + table + ".parquet"))
        if (
            pf.schema_arrow != disclosure_event_schemas()[table]
            or pf.read().to_pylist() != rows[table]
        ):
            raise BoundedError("BASE24_LATE_REPLAY_TYPED_ROWS_DIFFER")
    return dict(
        status="PASS_FROZEN_CENSUS_PROJECTION_NOT_COMPLETE_DAILY",
        manifest_sha256=manifest_sha256,
        row_counts=proof["row_counts"],
        census=proof["census"],
        held_documents=len(proof["held"]),
        consumer_admission="NOT_GRANTED",
        original_complete_Base_absence_independently_proven_by_this_subset=False,
    )
