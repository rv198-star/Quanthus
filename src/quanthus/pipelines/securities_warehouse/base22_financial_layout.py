"""Full financial layout checks with a complete, key-derived year census.

Global uniqueness is proved across all files of each year, then across years by
the period_end business-key coordinate. Parent maps always include the whole
financial scope. No financial values are copied to a key index or safe receipt.
Source qualification and the original-column restoration proof remain separate.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, identifier
from .base22_financial_union import YEAR_TABLES
from .base22_sources import _tree

TABLES = {*YEAR_TABLES, "dwd_financial_concept", "dwd_listed_company"}


def audit_financial_layout(
    root: Path,
    layout: dict[str, list[dict[str, Any]]],
    *,
    schemas: dict[str, Any],
    rules: dict[str, Any],
    model: dict[str, Any],
    admitted: dict[str, list[tuple[str, str, str]]],
    expected_partition_rows: dict[str, dict[str, int]],
    budget: Budget,
) -> dict[str, Any]:
    """Offline physical/global checks, not source or complete-image admission."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if any(
        set(value) != TABLES
        for value in [layout, schemas, rules, admitted, expected_partition_rows]
    ):
        raise BoundedError("FINANCIAL_LAYOUT_EXACT_TABLE_SET_INVALID")
    if {t["name"] for t in model["tables"]} != TABLES:
        raise BoundedError("FINANCIAL_LAYOUT_MODEL_SET_INVALID")
    if any(
        rules[t]["partition_by"] != ["period_end_year"]
        or "period_end" not in rules[t]["business_key"]
        or rules[t]["version_key"] != rules[t]["business_key"] + ["knowledge_from"]
        for t in YEAR_TABLES
    ):
        raise BoundedError("FINANCIAL_LAYOUT_KEY_DERIVED_YEAR_UNPROVEN")
    all_paths: dict[str, list[Path]] = {}
    classes: dict[str, dict[str, list[Path]]] = {}
    declared: set[str] = set()
    summaries: dict[str, dict[str, int]] = {}
    for table in sorted(TABLES):
        paths: defaultdict[str, list[Path]] = defaultdict(list)
        counts: defaultdict[str, int] = defaultdict(int)
        if not layout[table]:
            raise BoundedError("FINANCIAL_LAYOUT_REQUIRED_PHYSICAL_TABLE_EMPTY")
        for item in layout[table]:
            rel, part = item["path"], item["partition"]
            if rel in declared or not rel.startswith("payload/"):
                raise BoundedError("FINANCIAL_LAYOUT_DUPLICATE_OR_EXTERNAL_MEMBER")
            prefix = "payload/" + rules[table]["domain"] + "/" + table + "/"
            tail = rel.removeprefix(prefix).split("/")
            flat = (
                table not in YEAR_TABLES
                and rel == "payload/" + rules[table]["domain"] + "/" + table + ".parquet"
            )
            directory = rel.startswith(prefix) and (
                len(tail) == 2 and tail[0] == part if table in YEAR_TABLES else len(tail) == 1
            )
            if not (flat or directory):
                raise BoundedError("FINANCIAL_LAYOUT_PHYSICAL_TABLE_OR_PARTITION_MISMATCH")
            if (
                type(item["rows"]) is not int
                or item["rows"] < 0
                or type(item["size_bytes"]) is not int
                or item["size_bytes"] <= 0
            ):
                raise BoundedError("FINANCIAL_LAYOUT_PHYSICAL_CENSUS_TYPE_INVALID")
            declared.add(rel)
            path = _physical(root, rel, independent=True)
            Member(path, item["sha256"], item["size_bytes"], item["rows"]).verify()
            file = pq.ParquetFile(path)
            if (
                not file.schema_arrow.equals(schemas[table], check_metadata=False)
                or file.metadata.num_rows != item["rows"]
            ):
                raise BoundedError("FINANCIAL_LAYOUT_SCHEMA_OR_ROWS_CHANGED")
            if table in YEAR_TABLES:
                try:
                    if part != "period_end_year=" + str(int(part.removeprefix("period_end_year="))):
                        raise ValueError
                except (ValueError, TypeError) as exc:
                    raise BoundedError("FINANCIAL_LAYOUT_PARTITION_INVALID") from exc
            elif part != "unpartitioned":
                raise BoundedError("FINANCIAL_LAYOUT_PARTITION_INVALID")
            paths[part].append(path)
            counts[part] += item["rows"]
            budget.check()
        if dict(counts) != expected_partition_rows[table] or any(n <= 0 for n in counts.values()):
            raise BoundedError("FINANCIAL_LAYOUT_EXACT_PARTITION_CENSUS_CHANGED")
        classes[table], summaries[table] = dict(paths), dict(counts)
        all_paths[table] = [p for files in paths.values() for p in files]
    if _tree(root / "payload") != {str(Path(p).relative_to("payload")) for p in declared}:
        raise BoundedError("FINANCIAL_LAYOUT_PHYSICAL_FILE_SET_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(TABLES):
            scan.add(table, all_paths[table])
            for n, (part, year_files) in enumerate(sorted(classes[table].items())):
                name = table + "_class_" + str(n)
                scan.add(name, year_files)
                rule = rules[table]
                if table in YEAR_TABLES:
                    year = int(part.split("=")[1])
                    scan.reject(
                        f"SELECT 1 FROM {identifier(name)} WHERE year(period_end)<>{year} LIMIT 1",
                        "FINANCIAL_LAYOUT_PHYSICAL_YEAR_MISMATCH",
                    )
                scan.keys(name, rule["business_key"], rule["version_key"])
                scan.row_rules(name, schemas[table], rule)
                scan.lineage(name, admitted[table])
                scan.connection.execute("DROP VIEW " + identifier(name))
                scan.tables.remove(name)
                scan.source_paths.pop(name)
        # These maps include every parent version, across every year and file.
        scan.references(model, {"tables": rules})
        scan.connection.execute(
            "CREATE TEMP TABLE financial_report_period_keys AS "
            "SELECT DISTINCT financial_report_id,period_end FROM dwd_financial_report"
        )
        for files in scan._reference_batches("dwd_financial_fact"):
            scan.connection.read_parquet(
                [str(p) for p in files], hive_partitioning=False
            ).create_view("financial_fact_period_child", replace=True)
            scan.reject(
                "SELECT 1 FROM financial_fact_period_child f LEFT JOIN "
                "financial_report_period_keys r ON r.financial_report_id=f.financial_report_id "
                "AND r.period_end=f.period_end WHERE r.financial_report_id IS NULL LIMIT 1",
                "FINANCIAL_LAYOUT_REPORT_PERIOD_REFERENCE_MISSING",
            )
        key_metrics = scan.key_metrics
    if any(
        file_sha(root / item["path"]) != item["sha256"]
        for items in layout.values()
        for item in items
    ):
        raise BoundedError("FINANCIAL_LAYOUT_MEMBER_CHANGED_DURING_READ")
    result = {
        "status": "FULL_FINANCIAL_PHYSICAL_YEAR_GLOBAL_KEY_REFERENCE_PASS_NOT_SOURCE_ADMISSION",
        "partition_rows": summaries,
        "complete_table_set": sorted(TABLES),
        "key_metrics": key_metrics,
        "global_uniqueness_between_years": (
            "period_end in complete business/version key; every physical row year checked"
        ),
        "complete_global_parent_versions": True,
        "source_qualification": "SEPARATE_REQUIRED_GATE",
        "complete_Base22": False,
    }
    return {**result, "identity": digest(result)}
