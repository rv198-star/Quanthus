"""Bounded image-local replay of weekly ownership source observations.

This validates the exact raw-to-typed observation scope. Native history union and
knowledge-version consolidation are separate gates; observation replay grants
neither complete-domain coverage nor consumer/PIT admission.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_f10_ownership import CATEGORIES, CONTRACT_ID, TABLES, ownership_observation
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows
from .ownership_capital import OwnershipCapitalBuildError


class OwnershipReplaySink:
    """Deterministic bounded partition buffers shared by producer and replay."""

    def __init__(
        self,
        rules: dict[str, Any],
        compare: Callable[..., None],
        *,
        shard_rows: int = 2048,
        buffer_rows: int = 8192,
    ) -> None:
        if (
            type(shard_rows) is not int
            or not 0 < shard_rows <= 2048
            or type(buffer_rows) is not int
            or not shard_rows <= buffer_rows <= 8192
            or not set(TABLES) <= set(rules)
        ):
            raise BoundedError("OWNERSHIP_REPLAY_BUFFER_BOUND_INVALID")
        self.rules, self.compare = rules, compare
        self.shard_rows, self.buffer_rows = shard_rows, buffer_rows
        self.buffers: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.numbers: Counter[tuple[str, str]] = Counter()
        self.counts: Counter[str] = Counter()

    def add(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        if not rows or not set(rows) <= set(TABLES) or sum(map(len, rows.values())) > 4096:
            raise BoundedError("OWNERSHIP_REPLAY_GROUP_SCOPE_INVALID")
        for table, items in rows.items():
            for row in items:
                key = table, partition(row, self.rules[table])
                self.buffers[key].append(row)
                self.counts[table] += 1
                if len(self.buffers[key]) >= self.shard_rows:
                    self.flush(key)
                if sum(map(len, self.buffers.values())) >= self.buffer_rows:
                    self.flush(max(self.buffers, key=lambda k: len(self.buffers[k])))

    def flush(self, key: tuple[str, str]) -> None:
        rows = self.buffers[key]
        if not rows:
            return
        number = self.numbers[key]
        self.compare(*key, number, rows)
        self.numbers[key] += 1
        self.buffers[key] = []

    def finish(self) -> None:
        for key in list(self.buffers):
            self.flush(key)


def audit_ownership_observations(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Replay every selected original raw row without opening historical roots."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose")
        != "WEEKLY_F10_OWNERSHIP_SOURCE_DERIVED_ENTITY_SCOPE_NOT_COMPLETE_UNION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("projector_sha256")
        != file_sha(Path(__file__).with_name("base22_f10_ownership.py"))
        or body.get("replay_evaluator_sha256") != file_sha(Path(__file__))
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False for k in ("READY", "complete_Base22"))
    ):
        raise BoundedError("OWNERSHIP_OFFLINE_OBSERVATION_SCOPE_INVALID")
    if _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("OWNERSHIP_OBSERVATION_PHYSICAL_FILE_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("OWNERSHIP_OBSERVATION_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    parents = {}
    for table in ("dwd_security", "dwd_security_identifier"):
        items = body["parents"][table]
        if sum(i["rows"] for i in items) > 32768:
            raise BoundedError("OWNERSHIP_LOCAL_IDENTITY_PARENT_BOUND")
        parents[table] = [
            row
            for i in items
            for row in pq.ParquetFile(_physical(root, i["path"], independent=True))
            .read()
            .to_pylist()
        ]
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    outputs = {}
    for item in body["outputs"]:
        key = item["table"], item["partition"], item["number"]
        if key in outputs or item["table"] not in TABLES or not 0 < item["rows"] <= 2048:
            raise BoundedError("OWNERSHIP_OBSERVATION_PHYSICAL_SHARD_INVALID")
        outputs[key] = item
    checked = set()

    def compare(table: str, part: str, number: int, expected: list[dict[str, Any]]) -> None:
        key = table, part, number
        item = outputs.get(key)
        if item is None or key in checked:
            raise BoundedError("OWNERSHIP_OBSERVATION_EXACT_SHARD_SET_CHANGED")
        path = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(path)
        if (
            not pf.schema_arrow.equals(ss[table], check_metadata=False)
            or pf.metadata.num_rows != item["rows"]
            or item["rows"] != len(expected)
            or pf.read().to_pylist() != expected
            or semantic_scan([path], ss[table], check=budget.check) != item["semantic_scan"]
        ):
            raise BoundedError("OWNERSHIP_RAW_REPLAY_TYPED_ATOM_CLOCK_OR_SCHEMA_CHANGED")
        checked.add(key)

    sink = OwnershipReplaySink(contract["tables"], compare)
    raw_counts: Counter[str] = Counter()
    successes: Counter[str] = Counter()
    held: Counter[str] = Counter()
    dates: Counter[str] = Counter()
    scalars: Counter[str] = Counter()
    sources = {i["day"]: i for i in body["source_members"]}
    raw_members = body["raw_local_members"]
    if (
        len(sources) != 4
        or len(raw_members) != 4
        or {i["source_day"] for i in raw_members} != set(sources)
    ):
        raise BoundedError("OWNERSHIP_EXACT_WEEKLY_SOURCE_SET_INVALID")
    evidence_path = _physical(root, body["raw_row_accounting"], independent=True)
    if file_sha(evidence_path) != body["raw_row_accounting_sha256"]:
        raise BoundedError("OWNERSHIP_RAW_ACCOUNTING_IDENTITY_CHANGED")
    with evidence_path.open() as stream:
        for item in raw_members:
            source = sources[item["source_day"]]
            raw_path = _physical(root, item["path"], independent=True)
            pf = pq.ParquetFile(raw_path)
            if file_sha(raw_path) != item["sha256"] or pf.metadata.num_rows != item["rows"]:
                raise BoundedError("OWNERSHIP_LOCAL_RAW_MEMBER_CHANGED")
            for batch in pf.iter_batches(batch_size=64):
                budget.check()
                for raw in batch.to_pylist():
                    category = raw["category"]
                    if category not in CATEGORIES or sum(raw_counts.values()) >= 39936:
                        raise BoundedError("OWNERSHIP_RAW_SELECTION_SCOPE_OR_BOUND_CHANGED")
                    raw_counts[category] += 1
                    raw_key = {
                        "source_day": source["day"],
                        "stock_code": raw["stock_code"],
                        "category": category,
                        "raw_row_sha256": digest(raw),
                    }
                    try:
                        typed, evidence = ownership_observation(
                            raw,
                            source_sha256=source["member"]["sha256"],
                            resolver=resolver,
                        )
                    except (BoundedError, OwnershipCapitalBuildError) as exc:
                        reason = (
                            str(exc)
                            if isinstance(exc, BoundedError)
                            else "LEGACY_FORMAT_PARSE_REJECTED"
                        )
                        held[reason] += 1
                        expected: dict[str, Any] = {
                            **raw_key,
                            "status": "HELD_NONEMPTY_RAW_CATEGORY",
                            "reason": reason,
                        }
                    else:
                        successes[category] += 1
                        dates.update(x["reason"] for x in evidence["held_observations"])
                        scalars.update(evidence["scalar_classes"])
                        expected = {
                            **raw_key,
                            "status": "TYPED_MODEL_ROWS_WITH_EXPLICIT_HELD_DATES",
                            "evidence": evidence,
                        }
                        sink.add(typed)
                    line = stream.readline()
                    if not line or digest(json.loads(line)) != digest(expected):
                        raise BoundedError("OWNERSHIP_RAW_SAFE_DISPOSITION_NOT_REPRODUCED")
        if stream.read(1):
            raise BoundedError("OWNERSHIP_RAW_ACCOUNTING_EXTRA_ROWS")
    sink.finish()
    if checked != set(outputs) or (
        dict(sink.counts) != body["output_rows"]
        or dict(raw_counts) != body["raw_selected_counts"]
        or dict(successes) != body["raw_successful_categories"]
        or dict(held) != body["held_raw_categories"]
        or dict(dates) != body["held_dated_observations"]
        or dict(scalars) != body["scalar_classes"]
    ):
        raise BoundedError("OWNERSHIP_RAW_EXACT_OBSERVATION_CENSUS_CHANGED")
    return {
        "status": "ALL_LOCAL_RAW_OWNERSHIP_OBSERVATIONS_REPLAY_PASS_NOT_COMPLETE_UNION",
        "scope_identity": body["identity"],
        "raw_rows": sum(raw_counts.values()),
        "output_rows": dict(sink.counts),
        "held_raw_categories": dict(held),
        "held_dates": dict(dates),
        "physical_shards": len(checked),
        "native_history_union": "NOT_QUALIFIED",
        "complete_Base22": False,
        "source_root_dereferences": 0,
    }
