"""Bounded image-local raw replay for the three retained normalization sources.

These are internal P10 dependencies, never legacy manifest recovery or provider
priority decisions. Callers freeze and verify the exact physical input closure.
All returned atoms must reproduce the already sealed typed observations exactly.
"""

from __future__ import annotations

import bisect
import itertools
import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.corporate_action_corpus import _decimal
from quanthus.corporate_action_normalization import _digest as raw_digest

from .base22 import canonical, digest, file_sha, load_contract, schemas
from .base22_action_archive import response_bytes
from .base22_action_authority import materialize_implemented_action, parse_implemented_action
from .base22_action_factor import (
    materialize_retained_factor_record,
    normalize_retained_factor_record,
)
from .base22_bounded import BoundedError, Budget
from .base22_f10_replay import replay_f10_group
from .base22_output_audit import audit_observation_stage
from .financial_reporting import financial_company_resolver_from_listed_company_rows

TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
MAX_ACTIONS = 65536
MAX_TERMS = 196608


def _read(root: Path, item: dict[str, Any], budget: Budget) -> Any:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    path = root / item["path"]
    if file_sha(path) != item["sha256"]:
        raise BoundedError("SUCCESSOR_DONOR_MEMBER_HASH_CHANGED")
    count = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=256):
        budget.check()
        for row in batch.to_pylist():
            count += 1
            yield row
    if count != item["rows"]:
        raise BoundedError("SUCCESSOR_DONOR_MEMBER_ROW_CENSUS_CHANGED")


def _parents(root: Path, items: list[dict[str, Any]], budget: Budget) -> Any:
    parents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        table = item.get("table")
        if table in {"dwd_listed_company", "dwd_security", "dwd_security_identifier"}:
            parents[table].extend(_read(root, item, budget))
            if len(parents[table]) > MAX_ACTIONS:
                raise BoundedError("SUCCESSOR_DONOR_PARENT_BOUND_EXCEEDED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    return resolver


def _actual(root: Path, body: dict[str, Any], budget: Budget) -> Any:
    actions: dict[str, dict[str, Any]] = {}
    terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hashes: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    total: Counter[str] = Counter()
    for item in body["outputs"]:
        table = item["table"]
        if table not in TABLES:
            raise BoundedError("SUCCESSOR_DONOR_OUTPUT_TABLE_NOT_EXACT")
        for row in _read(root, item, budget):
            total[table] += 1
            hashes[table][digest(row)] += 1
            if table == TABLES[0]:
                key = row["source_record_id"]
                if key in actions or len(actions) >= MAX_ACTIONS:
                    raise BoundedError("SUCCESSOR_DONOR_ENTITY_DUPLICATE_OR_BOUND_EXCEEDED")
                actions[key] = row
            else:
                if total[table] > MAX_TERMS:
                    raise BoundedError("SUCCESSOR_DONOR_TERM_BOUND_EXCEEDED")
                terms[row["corporate_action_id"]].append(row)
    if dict(total) != body["output_rows"]:
        raise BoundedError("SUCCESSOR_DONOR_DECLARED_OUTPUT_CENSUS_CHANGED")
    return actions, terms, hashes


def _subtract(rows: dict[str, list[dict[str, Any]]], hashes: Any, ss: Any) -> None:
    import pyarrow as pa  # type: ignore[import-untyped]

    for table, items in rows.items():
        typed = pa.Table.from_pylist(items, schema=ss[table]).to_pylist()
        if typed != items:
            raise BoundedError("SUCCESSOR_DONOR_RAW_TYPED_VALUE_OR_CLOCK_CHANGED")
        for row in typed:
            key = digest(row)
            if hashes[table][key] <= 0:
                raise BoundedError("SUCCESSOR_DONOR_RAW_TYPED_ATOM_NOT_REPRODUCED")
            hashes[table][key] -= 1


def _finish(hashes: Any) -> None:
    if any(n for counter in hashes.values() for n in counter.values()):
        raise BoundedError("SUCCESSOR_DONOR_EXTRA_OR_MISSING_RAW_TYPED_ROW")


def replay_implementation_source(
    root: Path, body: dict[str, Any], budget: Budget
) -> tuple[dict[str, Any], Any, Any]:
    """Reproduce every selected implementation raw response and rejection."""
    stage = root / "observations/retained-action-authority"
    audit_observation_stage(root, body, stage / "contract")
    _, model, extension = load_contract(stage / "contract")
    ss = schemas(model, extension)
    vocab = json.loads((stage / "contract/disclosure-event-vocabulary.v1.json").read_bytes())
    archive = body["archived_true_source_observations"]
    resolver = _parents(root, archive, budget)
    lineage = next(i for i in archive if i.get("role") == "original_lineage_not_raw_values")
    raw_archive = next(i for i in archive if i.get("role") == "exact_original_raw_byte_archive")
    if file_sha(root / raw_archive["path"]) != raw_archive["sha256"]:
        raise BoundedError("SUCCESSOR_IMPLEMENTATION_ARCHIVE_HASH_CHANGED")
    bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for count, row in enumerate(_read(root, lineage, budget), start=1):
        if count > MAX_ACTIONS:
            raise BoundedError("SUCCESSOR_IMPLEMENTATION_LINEAGE_BOUND_EXCEEDED")
        if row["authority_source_row_ref"] is not None:
            bindings[row["authority_source_row_ref"]].append(row)
    actions, terms, hashes = _actual(root, body, budget)
    qualified = rejected = selected = files = 0
    proof_path = root / body["raw_replay_proof_path"]
    if file_sha(proof_path) != body["raw_replay_proof_sha256"]:
        raise BoundedError("SUCCESSOR_IMPLEMENTATION_RAW_PROOF_HASH_CHANGED")
    raw_expected = {k: tuple(v) for k, v in raw_archive["original_members"].items()}
    with proof_path.open("rb") as stream:
        for name, sha, data in response_bytes(root / raw_archive["path"], raw_expected):
            files += 1
            response = json.loads(data, parse_float=Decimal)
            code = Path(name).stem
            if str(response.get("code", response.get("stock_code"))) != code:
                raise BoundedError("SUCCESSOR_IMPLEMENTATION_RESPONSE_IDENTITY_CHANGED")
            stored = json.loads(stream.readline(1024**2 + 1))
            computed = []
            for ordinal, raw in enumerate(response["rows"]):
                ref = name + "#rows[" + str(ordinal) + "]"
                for old in bindings.get(ref, []):
                    selected += 1
                    if (
                        old["security_code"] != raw["SECURITY_CODE"]
                        or str(old["effective_date"]) != raw["EX_DIVIDEND_DATE"][:10]
                    ):
                        raise BoundedError("SUCCESSOR_IMPLEMENTATION_ORIGINAL_DATED_KEY_CHANGED")
                    safe = {
                        "old_action_id": old["action_id"],
                        "old_source_row_ref": old["source_row_ref"],
                        "stock_code": old["security_code"],
                        "effective_date": old["effective_date"],
                        "source_row_ref": ref,
                        "original_raw_sha256": digest(raw),
                        "original_file_sha256": sha,
                    }
                    try:
                        record, proof = parse_implemented_action(
                            raw, source_row_ref=ref, fetched_at=response.get("fetched_at")
                        )
                        parent, reason = resolver.resolve_for_report(
                            record["stock_code"], period_end=record["ex_date"]
                        )
                        if parent is None:
                            raise BoundedError("ACTION_AUTHORITY_DATED_PARENT_" + str(reason))
                        record["source_row_key"] = old["action_id"]
                        out = materialize_implemented_action(
                            record,
                            observed_at=body["new_observed_at"],
                            source_sha256=sha,
                            revision=body["registration_identity"],
                            parent=parent,
                            vocabulary=vocab,
                            schemas=ss,
                        )
                    except BoundedError as exc:
                        rejected += 1
                        safe.update(status="UNQUALIFIED_RETAINED", reason_code=str(exc))
                    else:
                        qualified += 1
                        safe.update(
                            status="EXACT_NEW_OBSERVATION",
                            source_proof=proof,
                            bound_record_sha256=digest(record),
                            typed_hashes={t: [digest(r) for r in rs] for t, rs in out.items()},
                            provider_acquisition=None,
                        )
                        _subtract(out, hashes, ss)
                    computed.append(safe)
            if stored["original_file_sha256"] != sha or canonical(computed) != canonical(
                stored["selected_keys"]
            ):
                raise BoundedError("SUCCESSOR_IMPLEMENTATION_COMPLETE_RAW_PROOF_CHANGED")
            budget.check()
        if stream.read(1):
            raise BoundedError("SUCCESSOR_IMPLEMENTATION_EXTRA_PROOF_RECORD")
    _finish(hashes)
    result = {
        "scope": "FULL_IMPLEMENTATION_RAW_TYPED_REPLAY_NOT_PIT",
        "files": files,
        "selected": selected,
        "qualified": qualified,
        "rejected": rejected,
        "source_identity": body["identity"],
    }
    result["identity"] = digest(result)
    return result, actions, terms


def replay_f10_source(
    root: Path, body: dict[str, Any], budget: Budget
) -> tuple[dict[str, Any], Any, Any]:
    """Replay complete actual category groups and receipts; original clocks only."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    stage = root / "observations/f10-dividend"
    audit_observation_stage(root, body, stage / "contract")
    contract, model, extension = load_contract(stage / "contract")
    ss = schemas(model, extension)
    vocab = json.loads((stage / "contract/disclosure-event-vocabulary.v1.json").read_bytes())
    parents = [{**i, "table": t} for t, items in body["parent_archive"].items() for i in items]
    resolver = _parents(root, parents, budget)
    full = {}
    for i in body["complete_raw_response_local_archives"]:
        p = root / i["path"]
        if file_sha(p) != i["sha256"]:
            raise BoundedError("SUCCESSOR_F10_FULL_RESPONSE_MEMBER_CHANGED")
        key = str(p.relative_to(stage / "complete-raw-response"))
        if key in full:
            raise BoundedError("SUCCESSOR_F10_DUPLICATE_RESPONSE_MEMBER")
        full[key] = p
    if set(full) != {
        "artifacts/raw_f10.parquet",
        "artifacts/fundamentals_receipts.parquet",
        "release.json",
        "READY",
        "original-source-qualification-safe.jsonl",
    }:
        raise BoundedError("SUCCESSOR_F10_COMPLETE_MEMBER_SET_MISMATCH")
    release = json.loads(full["release.json"].read_bytes())
    if (
        full["READY"].read_text().strip() != file_sha(full["release.json"])
        or release["release_id"] != body["raw_sources"][0]["revision"]
    ):
        raise BoundedError("SUCCESSOR_F10_ORIGINAL_RELEASE_IDENTITY_CHANGED")
    sha = file_sha(full["artifacts/raw_f10.parquet"])
    if sha != body["raw_sources"][0]["member"]["sha256"]:
        raise BoundedError("SUCCESSOR_F10_ORIGINAL_PAYLOAD_IDENTITY_CHANGED")
    receipts: dict[str, dict[str, Any]] = {}
    for batch in pq.ParquetFile(full["artifacts/fundamentals_receipts.parquet"]).iter_batches(
        batch_size=256
    ):
        for row in batch.to_pylist():
            if row["dataset"] == "raw_f10":
                key = row["security_id"]
                if key in receipts or len(receipts) >= MAX_ACTIONS:
                    raise BoundedError("SUCCESSOR_F10_RECEIPT_DUPLICATE_OR_BOUND_EXCEEDED")
                receipts[key] = row
        budget.check()
    actions, terms, hashes = _actual(root, body, budget)

    def raw_rows() -> Iterator[dict[str, Any]]:
        for batch in pq.ParquetFile(full["artifacts/raw_f10.parquet"]).iter_batches(batch_size=8):
            budget.check()
            yield from batch.to_pylist()

    proof_path = root / body["safe_raw_replay_evidence_path"]
    if file_sha(proof_path) != body["safe_raw_replay_evidence_sha256"]:
        raise BoundedError("SUCCESSOR_F10_RAW_PROOF_HASH_CHANGED")
    seen = set()
    nrows = ngroups = 0
    with (
        proof_path.open("rb") as proof_stream,
        full["original-source-qualification-safe.jsonl"].open("rb") as origin_stream,
    ):
        for code, group in itertools.groupby(raw_rows(), lambda r: r["stock_code"]):
            rows = []
            size = 0
            for row in group:
                rows.append(row)
                size += len(row["content"].encode("utf-8"))
                if len(rows) > 128 or size > 16 * 1024**2:
                    raise BoundedError("SUCCESSOR_F10_GROUP_BOUND_EXCEEDED")
            if code in seen or code not in receipts:
                raise BoundedError("SUCCESSOR_F10_GROUP_OR_RECEIPT_CLOSURE_FAILED")
            seen.add(code)
            out, proof = replay_f10_group(
                rows,
                receipts[code],
                market_date=date.fromisoformat(release["market_date"]),
                observed_at=body["new_observed_at"],
                source_sha256=sha,
                revision=release["release_id"],
                resolver=resolver,
                vocabulary=vocab,
                schemas=ss,
                rules=contract["tables"],
            )
            old = json.loads(proof_stream.readline(2 * 1024**2 + 1))
            origin = json.loads(origin_stream.readline(2 * 1024**2 + 1))
            if (
                old["stock_code"] != code
                or old["index"] != ngroups
                or canonical(origin["response_proof"]) != canonical(proof["response_proof"])
            ):
                raise BoundedError("SUCCESSOR_F10_ORIGINAL_RECEIPT_PROOF_CHANGED")
            for key in (
                "source_proofs",
                "merged_output_rows",
                "merged_outputs_sha256",
                "merged_row_hashes",
            ):
                if canonical(old[key]) != canonical(proof[key]):
                    raise BoundedError("SUCCESSOR_F10_COMPLETE_RAW_REPLAY_CHANGED")
            # replay_f10_group records fixed Arrow schema hashes; Python Decimal
            # representation in merged rows is not the native storage atom.
            for table, rs in out.items():
                for index, _ in enumerate(rs):
                    h = proof["serialized_row_hashes"][table][index]
                    if hashes[table][h] <= 0:
                        raise BoundedError("SUCCESSOR_F10_RAW_TYPED_ATOM_NOT_REPRODUCED")
                    hashes[table][h] -= 1
            ngroups += 1
            nrows += len(rows)
            budget.check()
        if proof_stream.read(1) or origin_stream.read(1) or seen != set(receipts):
            raise BoundedError("SUCCESSOR_F10_FULL_RESPONSE_PROOF_CLOSURE_FAILED")
    _finish(hashes)
    result = {
        "scope": "FULL_F10_RAW_RECEIPT_TYPED_REPLAY_NOT_PIT",
        "response_groups": ngroups,
        "raw_category_rows": nrows,
        "source_identity": body["identity"],
    }
    result["identity"] = digest(result)
    return result, actions, terms


def replay_factor_source(
    root: Path, body: dict[str, Any], budget: Budget
) -> tuple[dict[str, Any], Any, Any]:
    """Replay retained complete selected price/factor dependencies without exposure."""
    stage = root / "observations/retained-factor-revision"
    audit_observation_stage(root, body, stage / "contract")
    _, model, extension = load_contract(stage / "contract")
    ss = schemas(model, extension)
    policy = json.loads((stage / "contract/frozen-numeric-policy.json").read_bytes())
    vocab = json.loads((stage / "contract/disclosure-event-vocabulary.v1.json").read_bytes())
    archive = body["archived_true_source_observations"]
    resolver = _parents(root, archive, budget)
    data = {}
    for i in archive:
        if i.get("dataset"):
            rows = list(_read(root, i, budget))
            if len(rows) > MAX_ACTIONS:
                raise BoundedError("SUCCESSOR_FACTOR_RAW_DEPENDENCY_BOUND_EXCEEDED")
            data[i["dataset"]] = rows
    originals = {}
    for row in data["original-dividend-history"]:
        day = row["ex_dividend_date"]
        day = day.date() if isinstance(day, datetime) else day
        key = row["stock_code"], str(day)
        if key in originals:
            raise BoundedError("SUCCESSOR_FACTOR_ORIGINAL_DATE_KEY_NOT_UNIQUE")
        originals[key] = row
    canonical_actions = {r["action_id"]: r for r in data["original-canonical-actions"]}
    prices = {r["action_id"]: r for r in data["actual-prior-price-observations"]}
    factors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["actual-factor-history"]:
        factors[row["stock_code"]].append(row)
    for rows in factors.values():
        rows.sort(key=lambda r: r["effective_date"])
        if len({r["effective_date"] for r in rows}) != len(rows):
            raise BoundedError("SUCCESSOR_FACTOR_EFFECTIVE_KEY_NOT_UNIQUE")
    actions, terms, hashes = _actual(root, body, budget)
    if file_sha(root / body["raw_replay_proof_path"]) != body["raw_replay_proof_sha256"]:
        raise BoundedError("SUCCESSOR_FACTOR_RAW_PROOF_HASH_CHANGED")
    bindings = json.loads((root / body["raw_replay_proof_path"]).read_bytes())
    for binding in bindings:
        key = binding["key"]
        old = canonical_actions[key["action_id"]]
        raw = originals[key["stock_code"], key["effective_date"]]
        price = prices[key["action_id"]]
        fs = factors[key["stock_code"]]
        index = bisect.bisect_left([r["effective_date"] for r in fs], old["ex_date"])
        current = fs[index]
        prior = fs[index - 1] if index else None
        if (
            current["effective_date"] != old["ex_date"]
            or binding["original_dividend_row_sha256"] != raw_digest(raw)
            or binding["canonical_row_sha256"] != digest(old)
            or binding["current_factor_row_sha256"] != digest(current)
            or binding["prior_factor_row_sha256"] != (digest(prior) if prior else None)
            or binding["actual_price_projection_sha256"] != digest(price)
        ):
            raise BoundedError("SUCCESSOR_FACTOR_FULL_RAW_DEPENDENCY_BINDING_CHANGED")
        with localcontext() as context:
            context.prec = 64
            if prior:
                previous = _decimal(prior["fore_adjust_factor"])
            else:
                left = _decimal(price["prior_qfq_close"])
                right = _decimal(price["prior_close"])
                if left is None or right is None or right <= 0:
                    raise BoundedError("SUCCESSOR_FACTOR_ORIGINAL_PRICE_DEPENDENCY_INVALID")
                previous = left / right
        notice = raw["announce_date"]
        notice = notice.date() if isinstance(notice, datetime) else notice
        record = {
            "stock_code": key["stock_code"],
            "source_row_key": key["action_id"],
            "ex_date": old["ex_date"],
            "announce_date": notice,
            "record_date": None,
            "payment_date": None,
            "progress": "实施",
            "cash_per_share": raw["dividend_per_share"],
            "bonus_shares": raw["bonus_shares"],
            "transfer_shares": raw["transfer_shares"],
        }
        normalized, proof = normalize_retained_factor_record(
            record,
            prior_close=price["prior_close"],
            factor_before=previous,
            factor_after=current["fore_adjust_factor"],
            baseline="prior_factor" if prior else "prior_qfq_ratio",
            policy=policy,
        )
        if (
            digest(proof) != digest(binding["proof"])
            or binding["status"] != "EXACT_NEW_RETROSPECTIVE_OBSERVATION"
        ):
            raise BoundedError("SUCCESSOR_FACTOR_FROZEN_NUMERIC_PROOF_CHANGED")
        parent, reason = resolver.resolve_for_report(
            record["stock_code"], period_end=record["ex_date"]
        )
        if parent is None:
            raise BoundedError("SUCCESSOR_FACTOR_DATED_PARENT_UNAVAILABLE:" + str(reason))
        candidate = actions[key["action_id"]]
        out = materialize_retained_factor_record(
            normalized,
            observed_at=body["new_observed_at"],
            source_sha256=candidate["source_artifact_sha256"],
            revision=body["registration_identity"],
            parent=parent,
            vocabulary=vocab,
            schemas=ss,
        )
        for table, rows in out.items():
            if sorted(digest(r) for r in rows) != sorted(binding["typed_hashes"][table]):
                raise BoundedError("SUCCESSOR_FACTOR_TYPED_RAW_REPLAY_CHANGED")
        _subtract(out, hashes, ss)
        budget.check()
    _finish(hashes)
    result = {
        "scope": "FULL_SELECTED_FACTOR_PRICE_RAW_TYPED_REPLAY_NOT_PIT",
        "keys": len(bindings),
        "source_identity": body["identity"],
    }
    result["identity"] = digest(result)
    return result, actions, terms
