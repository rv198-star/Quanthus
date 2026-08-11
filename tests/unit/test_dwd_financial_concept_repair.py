from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.dwd_financial_concept_repair import (
    DwdFinancialConceptRepairError,
    repair_financial_concept_effective_from,
)
from quanthus.pipelines.securities_warehouse.domain_base import verify_domain_base


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _source_base(root: Path) -> tuple[str, bytes]:
    concept_path = root / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
    fact_path = (
        root / "payload/financial_reporting/dwd_financial_fact/period_end_year=1990/part.parquet"
    )
    concept_path.parent.mkdir(parents=True)
    fact_path.parent.mkdir(parents=True)
    concepts = pa.table(
        {
            "financial_concept_id": ["a", "b", "c"],
            "effective_from": pa.array(
                [
                    datetime(1998, 12, 30, 16, tzinfo=timezone.utc),
                    datetime(1989, 12, 30, 16, tzinfo=timezone.utc),
                    datetime(2020, 1, 1, tzinfo=timezone.utc),
                ],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "knowledge_from": pa.array(
                [datetime(2026, 8, 4, tzinfo=timezone.utc)] * 3,
                type=pa.timestamp("us", tz="UTC"),
            ),
        }
    )
    facts = pa.table(
        {
            "financial_concept_id": ["a", "a", "b"],
            "period_end": pa.array(
                [date(1990, 12, 31), date(1995, 12, 31), date(1990, 12, 31)],
                type=pa.date32(),
            ),
        }
    )
    pq.write_table(concepts, concept_path)
    pq.write_table(facts, fact_path)
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "candidate_domain": "financial_reporting",
        }
        for path in (concept_path, fact_path)
    ]
    identity = {
        "package_id": "dwd-base-202607-v2.1",
        "run_id": "source-r13",
        "through_date": "2026-07-31",
        "input_plan_digest": "sha256:" + "1" * 64,
        "ods_base": {
            "package_id": "ods",
            "manifest_sha256": "sha256:" + "2" * 64,
            "content_digest": "sha256:" + "3" * 64,
        },
        "model_sha256": "sha256:" + "4" * 64,
        "model_extension_sha256": "sha256:" + "5" * 64,
        "code_revision": "6" * 40,
        "upstream_candidates": {"financial_reporting": {"semantic_digest": "sha256:x"}},
        "row_counts": {f"table_{index:02d}": 0 for index in range(30)}
        | {
            "dwd_financial_concept": 3,
            "dwd_financial_fact": 3,
            "dwd_financial_report": 0,
        },
    }
    manifest = {
        "schema_version": 1,
        "kind": "dwd_domain_base_package",
        **identity,
        "content_digest": "sha256:fixture",
        "table_count": 33,
        "total_rows": 6,
        "payload_file_count": 2,
        "payload_bytes": sum(path.stat().st_size for path in (concept_path, fact_path)),
        "files": files,
    }
    (root / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    manifest_sha = _sha256(root / "manifest.json")
    (root / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return manifest_sha, concept_path.read_bytes()


def test_derives_package_and_only_rewrites_concept_payload(tmp_path: Path) -> None:
    source = tmp_path / "source" / "dwd-base-202607-v2.1"
    source.mkdir(parents=True)
    source_manifest_sha, source_concept = _source_base(source)
    output = tmp_path / "target" / "dwd-base-202607-v2.1"

    result = repair_financial_concept_effective_from(
        source_root=source,
        output=output,
        run_id="repair-r16",
        code_revision="a" * 40,
        expected_source_manifest_sha256=source_manifest_sha,
        expected_fact_rows=3,
        expected_concept_rows=3,
        expected_changed_rows=1,
    )

    assert result.changed_rows == 1
    assert result.concept_rows == 3
    verify_domain_base(output)
    repaired = pq.read_table(
        output / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
    ).to_pylist()
    assert repaired[0]["effective_from"] == datetime(1990, 12, 30, 16, tzinfo=timezone.utc)
    assert repaired[1]["effective_from"] == datetime(1989, 12, 30, 16, tzinfo=timezone.utc)
    assert repaired[2]["effective_from"] == datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert (
        source_concept
        == (
            source / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
        ).read_bytes()
    )
    source_fact = next(
        (source / "payload/financial_reporting/dwd_financial_fact").rglob("*.parquet")
    )
    target_fact = next(
        (output / "payload/financial_reporting/dwd_financial_fact").rglob("*.parquet")
    )
    assert os.stat(source_fact).st_ino == os.stat(target_fact).st_ino
    manifest = json.loads((output / "manifest.json").read_text(encoding="ascii"))
    assert manifest["run_id"] == "repair-r16"
    assert manifest["repair_provenance"]["source_manifest_sha256"] == source_manifest_sha
    receipt = json.loads(
        (output / "evidence/repairs/financial-concept-effective-from.json").read_text(
            encoding="ascii"
        )
    )
    assert receipt["fact_rows"] == 3
    assert receipt["changed_rows"] == 1


def test_rejects_unexpected_change_count_without_output(tmp_path: Path) -> None:
    source = tmp_path / "source" / "dwd-base-202607-v2.1"
    source.mkdir(parents=True)
    source_manifest_sha, _ = _source_base(source)
    output = tmp_path / "target" / "dwd-base-202607-v2.1"

    with pytest.raises(DwdFinancialConceptRepairError, match="changes differ"):
        repair_financial_concept_effective_from(
            source_root=source,
            output=output,
            run_id="repair-r16",
            code_revision="a" * 40,
            expected_source_manifest_sha256=source_manifest_sha,
            expected_fact_rows=3,
            expected_concept_rows=3,
            expected_changed_rows=2,
        )

    assert not output.exists()
