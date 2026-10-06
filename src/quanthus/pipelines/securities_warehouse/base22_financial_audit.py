"""Image-local full financial replay with bounded row comparisons.

The original four-release projector's deterministic flush contract is replayed.
Reports and facts are compared immediately, without another 11-million-row
materialization. Only the compact concept/observation-clock selection survives
between groups. This qualifies the retained observation scope, never native
history, provider-wide PIT, consumers or the complete Base 2.2.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import CONTRACT_ID, TABLES, statement_batch
from .base22_outputs import financial_concept_versions, instant
from .base22_sources import _tree
from .financial_reporting import (
    FinancialStatementSource,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
)

MAX_GROUP_ROWS = 32768
CONCEPT_FIELDS = (
    "taxonomy_namespace",
    "taxonomy_version",
    "concept_code",
    "concept_name",
    "statement_type",
    "data_type",
    "period_type",
    "balance_type",
)


class FinancialReplaySink:
    """The frozen projector's partition sequence with compact global concepts."""

    def __init__(
        self, rules: dict[str, Any], compare: Callable[..., None], *, flush_rows: int = 16384
    ) -> None:
        if type(flush_rows) is not int or not 0 < flush_rows <= MAX_GROUP_ROWS:
            raise BoundedError("FINANCIAL_REPLAY_FLUSH_BOUND_INVALID")
        self.rules, self.compare, self.flush_rows = rules, compare, flush_rows
        self.buffers: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
        self.numbers: Counter[tuple[str, str]] = Counter()
        self.concepts: dict[tuple[Any, Any], dict[str, Any]] = {}
        self.semantics: dict[str, str] = {}
        self.counts: Counter[str] = Counter()

    def add(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        if set(rows) != set(TABLES):
            raise BoundedError("FINANCIAL_REPLAY_TABLE_SET_INVALID")
        if any(len(rs) > MAX_GROUP_ROWS for rs in rows.values()):
            raise BoundedError("FINANCIAL_REPLAY_GROUP_ROW_BOUND_FAILED")
        for table in TABLES:
            if len(self.buffers[table]) + len(rows[table]) > MAX_GROUP_ROWS:
                self.flush()
            self.buffers[table].extend(rows[table])
        if max(map(len, self.buffers.values())) >= self.flush_rows:
            self.flush()

    def _concepts(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            identity = row["financial_concept_id"]
            semantic = digest([row[f] for f in CONCEPT_FIELDS])
            if self.semantics.setdefault(identity, semantic) != semantic:
                raise BoundedError("FINANCIAL_CONCEPT_SOURCE_SEMANTICS_CONFLICT")
            key = identity, row["knowledge_from"]

            def order(r: dict[str, Any]) -> tuple[Any, ...]:
                return tuple(
                    r[f]
                    for f in (
                        "effective_from",
                        "source_record_id",
                        "source_artifact_sha256",
                        "record_source",
                        "source_revision",
                    )
                )

            if key not in self.concepts or order(row) < order(self.concepts[key]):
                self.concepts[key] = dict(row)
            if len(self.concepts) > MAX_GROUP_ROWS:
                raise BoundedError("FINANCIAL_GLOBAL_CONCEPT_SCOPE_BOUND_FAILED")

    def flush(self) -> None:
        concepts = financial_concept_versions(self.buffers[TABLES[1]])
        self._concepts(concepts)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for table in (TABLES[0], TABLES[2]):
            for row in self.buffers[table]:
                groups[table, partition(row, self.rules[table])].append(row)
        for (table, part), rows in sorted(groups.items()):
            number = self.numbers[table, part]
            self.numbers[table, part] += 1
            self.compare(table, part, number, rows)
            self.counts[table] += len(rows)
        for rows in self.buffers.values():
            rows.clear()

    def finish(self) -> None:
        self.flush()
        rows = financial_concept_versions(self.concepts.values())
        if not rows:
            raise BoundedError("FINANCIAL_GLOBAL_CONCEPT_CENSUS_UNAVAILABLE")
        self.compare(TABLES[1], partition(rows[0], self.rules[TABLES[1]]), 0, rows)
        self.counts[TABLES[1]] = len(rows)


def _rows(path: Path, budget: Budget, *, batch_rows: int = 16) -> Iterator[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_rows):
        budget.check()
        yield from batch.to_pylist()


def _object(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads(_physical(root, relative, independent=True).read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("FINANCIAL_REPLAY_OBJECT_REQUIRED")
    return value


def audit_financial_raw_scope(
    root: Path,
    body: dict[str, Any],
    budget: Budget,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Reproduce every original envelope, receipt, physical row and global key."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if (
        body.get("projection_contract_id") != CONTRACT_ID
        or body.get("kind") != "sealed_actual_domain_observations_not_complete_base22"
        or body.get("label") != "financial-domain"
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("complete_native_union") != "NOT_QUALIFIED"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or set(body["output_rows"]) != set(TABLES)
        or set(body["parent_archive"])
        != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}
    ):
        raise BoundedError("FINANCIAL_REPLAY_SCOPE_INVALID")
    prefix = "observations/financial-domain/"
    expected = {prefix + "OBSERVATIONS.json", body["safe_envelope_evidence_path"]}
    physical_hashes = {}

    def member(item: dict[str, Any]) -> Path:
        rel = item["path"]
        if rel in expected:
            raise BoundedError("FINANCIAL_REPLAY_DUPLICATE_PHYSICAL_MEMBER")
        expected.add(rel)
        path = _physical(root, rel, independent=True)
        sha = file_sha(path)
        if sha != item["sha256"] or (
            "size_bytes" in item and path.stat().st_size != item["size_bytes"]
        ):
            raise BoundedError("FINANCIAL_REPLAY_MEMBER_CHANGED")
        physical_hashes[rel] = sha
        budget.check()
        return path

    for name, sha in body["contract_members"].items():
        member({"path": prefix + "contract/" + name, "sha256": sha})
    contract, _, _ = load_contract(root / prefix / "contract")
    schemas = financial_reporting_schemas()
    parents = {t: [member(i) for i in items] for t, items in body["parent_archive"].items()}
    sec = [r for p in parents["dwd_security"] for r in _rows(p, budget, batch_rows=1024)]
    ids = [r for p in parents["dwd_security_identifier"] for r in _rows(p, budget, batch_rows=1024)]
    resolver = financial_company_resolver_from_listed_company_rows(sec, ids)
    archives = {i["path"]: i for i in body["archived_true_source_observations"]}
    bindings = body["raw_replay_bindings"]
    if (
        len(bindings) != 4
        or len(archives) != 4
        or set(archives) != {i["archive_path"] for i in bindings}
    ):
        raise BoundedError("FINANCIAL_REPLAY_FOUR_RELEASE_CLOSURE_REQUIRED")
    closures: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in body["local_source_closure"]:
        if item["kind"] in closures[item["day"]]:
            raise BoundedError("FINANCIAL_REPLAY_DUPLICATE_SOURCE_EVIDENCE")
        member(item)
        closures[item["day"]][item["kind"]] = item
    if len(closures) != 4 or any(
        set(v) != {"manifest", "READY", "receipts", "safe-closure"} for v in closures.values()
    ):
        raise BoundedError("FINANCIAL_REPLAY_SOURCE_EVIDENCE_CLOSURE_FAILED")
    sources, metadata, receipts = [], [], []
    for binding in bindings:
        item = archives[binding["archive_path"]]
        path = member(item)
        day = path.stem.removeprefix("raw-")
        closure = closures[day]
        source = FinancialStatementSource(**{**binding["source"], "path": path})
        manifest = _object(root, closure["manifest"]["path"])
        safe = _object(root, closure["safe-closure"]["path"])
        if (
            source.source_sha256 != item["sha256"]
            or closure["manifest"]["sha256"] != binding["original_manifest_sha256"]
            or (root / closure["READY"]["path"]).read_text().strip()
            != binding["original_manifest_sha256"]
            or closure["safe-closure"]["sha256"] != binding["safe_raw_receipt_closure_sha256"]
            or safe["identity"] != digest({k: v for k, v in safe.items() if k != "identity"})
            or manifest["market_date"] != day
            or safe["source_envelopes"] != item["rows"]
        ):
            raise BoundedError("FINANCIAL_REPLAY_RELEASE_IDENTITY_CHANGED")
        receipt_map: dict[tuple[Any, ...], int] = Counter()
        for row in _rows(root / closure["receipts"]["path"], budget, batch_rows=256):
            if row["dataset"] == "financial_statements" and row["status"] == "success_nonempty":
                receipt_map[
                    (
                        str(row["response_sha256"]).removeprefix("sha256:"),
                        row["security_id"],
                        row["response_bytes"],
                        instant(row["acquired_at"]),
                    )
                ] += 1
        sources.append((path, source))
        metadata.append(safe["per_envelope_closure"])
        receipts.append(receipt_map)
    outputs: dict[tuple[str, str, int], dict[str, Any]] = {}
    for item in body["outputs"]:
        member(item)
        output_key = item["table"], item["partition"], int(Path(item["path"]).stem.split("-")[-1])
        if item["table"] not in TABLES or output_key in outputs:
            raise BoundedError("FINANCIAL_REPLAY_EXACT_TABLE_PARTITION_CHANGED")
        outputs[output_key] = item
    evidence_path = _physical(root, body["safe_envelope_evidence_path"], independent=True)
    if (
        file_sha(evidence_path) != body["safe_envelope_evidence_sha256"]
        or set(_tree(root)) != expected
    ):
        raise BoundedError("FINANCIAL_REPLAY_EXACT_LOCAL_FILE_SET_CHANGED")
    scans, compared = {}, set()

    def compare(table: str, part: str, number: int, rows: list[dict[str, Any]]) -> None:
        key = table, part, number
        item = outputs.get(key)
        if item is None or key in compared or item["rows"] != len(rows):
            raise BoundedError("FINANCIAL_RAW_REPLAY_PHYSICAL_PARTITION_MISMATCH")
        path = root / item["path"]
        actual = pq.ParquetFile(path).read()
        expected_rows = pa.Table.from_pylist(rows, schema=schemas[table])
        if not actual.equals(expected_rows, check_metadata=False):
            raise BoundedError("FINANCIAL_RAW_TO_TYPED_EXACT_ATOMS_MISMATCH")
        scan = semantic_scan([path], schemas[table], batch_rows=1024, check=budget.check)
        if scan != item["semantic_scan"]:
            raise BoundedError("FINANCIAL_RAW_REPLAY_RECOMPUTED_SUMMARY_MISMATCH")
        scans[item["path"]] = scan
        compared.add(key)
        budget.check()

    sink = FinancialReplaySink(contract["tables"], compare)
    seen: set[tuple[str, str]] = set()
    classifications: Counter[str] = Counter()
    source_rows: Counter[str] = Counter()
    admitted: set[tuple[str, str, str]] = set()
    with evidence_path.open() as evidence:
        for index, group in enumerate(
            itertools.zip_longest(*(_rows(p, budget) for p, _ in sources))
        ):
            if any(not isinstance(r, dict) for r in group):
                raise BoundedError("FINANCIAL_RAW_REPLAY_ROW_ORDER_OR_COUNT_CHANGED")
            key = group[0]["stock_code"], group[0]["statement_type"]
            if key in seen:
                raise BoundedError("FINANCIAL_RAW_REPLAY_DUPLICATE_ENVELOPE_KEY")
            seen.add(key)
            envelopes = []
            for n, raw in enumerate(group):
                closure = metadata[n][index]
                row_binding = digest(
                    [
                        [
                            k,
                            type(v).__name__,
                            {"binary_bytes": len(v), "binary_sha256": hashlib.sha256(v).hexdigest()}
                            if isinstance(v, bytes)
                            else v,
                        ]
                        for k, v in raw.items()
                    ]
                )
                receipt_key = (
                    str(raw["raw_payload_sha256"]).removeprefix("sha256:"),
                    raw["security_id"],
                    raw["raw_payload_bytes"],
                    instant(raw["acquired_at"]),
                )
                if (
                    key != (raw["stock_code"], raw["statement_type"])
                    or key != (closure["stock_code"], closure["statement_type"])
                    or closure["row_binding_sha256"] != row_binding
                    or receipts[n].get(receipt_key) != 1
                    or closure["matching_receipt_count"] != 1
                    or closure["provider_acquisition_asserted"] is not False
                ):
                    raise BoundedError("FINANCIAL_RAW_REPLAY_ENVELOPE_RECEIPT_BINDING_CHANGED")
                envelopes.append((raw, sources[n][1]))
            rows, proof = statement_batch(envelopes, resolver=resolver, rules=contract["tables"])
            saved = json.loads(evidence.readline())
            if (
                saved["index"] != index
                or saved["key_sha256"] != digest(key)
                or digest(saved["proof"]) != digest(proof)
            ):
                raise BoundedError("FINANCIAL_RAW_REPLAY_COMPLETE_ENVELOPE_PROOF_CHANGED")
            for item in proof["envelope_evidence"]:
                classifications.update(item["scalar_classifications"])
                source_rows.update(item["row_counts"])
                for q in item["quality_exceptions"]:
                    classifications["QUALITY_" + q["code"]] += 1
            admitted.update(
                (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                for rs in rows.values()
                for r in rs
            )
            sink.add(rows)
            budget.check()
            if progress is not None and (index + 1) % 256 == 0:
                progress(
                    {
                        "groups_reproduced": index + 1,
                        "envelopes_reproduced": 4 * (index + 1),
                        "exact_physical_partitions_compared": len(compared),
                        "replayed_output_rows": dict(sink.counts),
                        "compact_concept_clock_entries": len(sink.concepts),
                        "peak": dict(budget.peak),
                        "complete_Base22": False,
                    }
                )
        if evidence.readline():
            raise BoundedError("FINANCIAL_RAW_REPLAY_EXTRA_ENVELOPE_PROOF")
    sink.finish()
    if (
        compared != set(outputs)
        or dict(sink.counts) != body["output_rows"]
        or any(len(m) != len(seen) for m in metadata)
        or any(i["rows"] != len(seen) for i in archives.values())
        or dict(classifications) != body["quality_summary"]
        or admitted != {(i["record_source"], i["revision"], i["sha256"]) for i in body["inputs"]}
    ):
        raise BoundedError("FINANCIAL_RAW_REPLAY_COMPLETE_CENSUS_CHANGED")
    with GlobalScan(budget) as scan:
        for table in TABLES:
            scan.add(table, [root / i["path"] for i in outputs.values() if i["table"] == table])
            rule = contract["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
            scan.row_rules(table, schemas[table], rule)
            scan.lineage(table, sorted(admitted))
        scan.add("dwd_listed_company", parents["dwd_listed_company"])
        scan.reject(
            "SELECT 1 FROM dwd_financial_report r WHERE NOT EXISTS("
            "SELECT 1 FROM dwd_listed_company p WHERE p.company_id=r.company_id "
            "AND p.knowledge_from<=r.knowledge_from "
            "AND (p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to) "
            "AND p.effective_from<=timezone('Asia/Shanghai',CAST(r.period_end AS TIMESTAMP)) "
            "AND (p.effective_to IS NULL OR "
            "timezone('Asia/Shanghai',CAST(r.period_end AS TIMESTAMP))<p.effective_to)) LIMIT 1",
            "FINANCIAL_DATED_NATIVE_COMPANY_CLOSURE_FAILED",
        )
        for parent, column in [
            (TABLES[0], "financial_report_id"),
            (TABLES[1], "financial_concept_id"),
        ]:
            scan.reject(
                f"SELECT 1 FROM dwd_financial_fact f WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{column}=f.{column} AND p.knowledge_from<=f.knowledge_from "
                "AND (p.knowledge_to IS NULL OR f.knowledge_from<p.knowledge_to)) LIMIT 1",
                "FINANCIAL_LOCAL_GLOBAL_REFERENCE_CLOSURE_FAILED",
            )
        scan.reject(
            "SELECT 1 FROM dwd_financial_fact f WHERE NOT EXISTS("
            "SELECT 1 FROM dwd_financial_report r "
            "WHERE r.financial_report_id=f.financial_report_id AND r.period_end=f.period_end) "
            "OR NOT EXISTS(SELECT 1 FROM dwd_financial_concept c "
            "WHERE c.financial_concept_id=f.financial_concept_id AND "
            "c.effective_from<=timezone('Asia/Shanghai',CAST(f.period_end AS TIMESTAMP))) LIMIT 1",
            "FINANCIAL_GLOBAL_PERIOD_REFERENCE_CLOSURE_FAILED",
        )
    if any(file_sha(root / p) != sha for p, sha in physical_hashes.items()):
        raise BoundedError("FINANCIAL_RAW_REPLAY_INPUT_CHANGED_DURING_VERIFICATION")
    result = {
        "status": "FULL_IMAGE_LOCAL_FINANCIAL_RAW_REPLAY_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "raw_envelopes": len(seen) * 4,
        "groups": len(seen),
        "output_rows": dict(sink.counts),
        "physical_output_files": len(compared),
        "complete_receipt_linkage": "PASS",
        "whole_raw_to_typed_exact_atoms": "PASS",
        "global_keys_versions_time_lineage_references": "PASS",
        "all_output_semantic_scans": scans,
        "quality_summary": dict(classifications),
        "original_unmerged_rows": dict(source_rows),
        "raw_and_output_bytes_unchanged": True,
        "complete_native_union": "NOT_QUALIFIED",
        "provider_PIT_admission": "NOT_GRANTED",
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
