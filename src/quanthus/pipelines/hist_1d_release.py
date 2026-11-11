"""Stage and verify an immutable Hist-1D candidate, without granting READY."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def member(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def write_manifest(root: Path, provenance: dict[str, Any]) -> dict[str, Any]:
    files = sorted(p for p in root.rglob("*") if p.is_file() and p != root / "manifest.json")
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("SYMLINK_MEMBER")
    manifest = {
        "schema_version": "quanthus.hist-1d.manifest.v1",
        "status": "CANDIDATE_PENDING_ACCEPTANCE",
        "provenance": provenance,
        "members": [member(root, path) for path in files],
    }
    save(root / "manifest.json", manifest)
    return manifest


def verify_manifest(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    seen: set[str] = set()
    total = 0
    for record in manifest["members"]:
        relative = PurePosixPath(record["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != record["path"]
            or record["path"] in seen
        ):
            raise ValueError("MANIFEST_MEMBER_PATH_INVALID")
        path = root / relative
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError("SYMLINK_MEMBER")
        if not path.is_file() or member(root, path) != record:
            raise ValueError("MANIFEST_MEMBER_MISMATCH:" + record["path"])
        total += record["bytes"]
        seen.add(record["path"])
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != seen | {"manifest.json"}:
        raise ValueError("MANIFEST_MEMBER_SET_MISMATCH")
    return {
        "status": "ALL_MEMBERS_READBACK_PASSED",
        "members": len(seen),
        "bytes": total,
        "manifest_sha256": file_sha256(root / "manifest.json"),
    }


def structural_audit(product: Path, base: Path) -> dict[str, Any]:
    pq = importlib.import_module("pyarrow.parquet")
    duckdb = importlib.import_module("duckdb")
    connection = duckdb.connect(config={"threads": 2, "memory_limit": "1GB"})
    connection.execute("SET TimeZone='Asia/Shanghai'")
    counts: dict[str, int] = {}
    for directory in sorted((product / "payload").iterdir()):
        paths = sorted(directory.rglob("*.parquet"))
        reference = sorted((base / "payload" / directory.name).rglob("*.parquet"))[0]
        schema = pq.ParquetFile(reference).schema_arrow
        for path in paths:
            parquet = pq.ParquetFile(path)
            if not parquet.schema_arrow.equals(schema, check_metadata=False):
                raise ValueError("DWD_SCHEMA_MISMATCH:" + str(path))
        counts[directory.name] = sum(pq.ParquetFile(p).metadata.num_rows for p in paths)
        connection.read_parquet([str(p) for p in paths], hive_partitioning=False).create_view(
            directory.name
        )

    def query(sql: str) -> list[dict[str, Any]]:
        result = connection.execute(sql)
        return [
            dict(zip([d[0] for d in result.description], row, strict=True))
            for row in result.fetchall()
        ]

    factors = "dwd_adjustment_factor"
    intervals = query(f"""WITH x AS (SELECT *,lead(effective_from) OVER (
        PARTITION BY security_id,adjustment_method,adjustment_direction ORDER BY effective_from
        ) next_from FROM {factors}) SELECT count(*) intervals,
        count(*) FILTER(WHERE factor_value<=0) nonpositive,
        count(*) FILTER(WHERE effective_to IS DISTINCT FROM next_from) broken_intervals,
        count(*) FILTER(WHERE adjustment_method<>'PROVIDER_ADJUST_FACTOR'
        OR adjustment_direction<>'PROVIDER_NATIVE') wrong_basis FROM x""")
    first_factor = query(f"""WITH b AS (SELECT security_id,min(observation_start) first_bar
        FROM dwd_security_bar JOIN dwd_listing USING(listing_id) GROUP BY 1),
        f AS (SELECT security_id,min(effective_from) first_factor FROM {factors} GROUP BY 1)
        SELECT count(*) securities,count(*) FILTER(WHERE first_factor IS NULL) no_factor,
        count(*) FILTER(WHERE first_factor>first_bar) bars_before_first_factor
        FROM b LEFT JOIN f USING(security_id)""")
    seam = query(f"""SELECT security_id,effective_from,effective_to,factor_value,
        record_source,source_artifact_sha256 FROM {factors}
        WHERE effective_from<TIMESTAMPTZ '2011-01-04 00:00:00+08'
        AND (effective_to IS NULL OR effective_to>TIMESTAMPTZ '2011-01-04 00:00:00+08')
        ORDER BY security_id""")
    research_holds = query("""SELECT reason_code,count(*) intervals,
        count(DISTINCT security_id) securities FROM dwd_security_status_interval
        WHERE status_type='SPECIAL_TREATMENT' AND status_value='UNKNOWN'
        AND effective_from<TIMESTAMPTZ '2011-01-01 00:00:00+08'
        AND (effective_to IS NULL OR effective_to>TIMESTAMPTZ '2005-01-01 00:00:00+08')
        GROUP BY reason_code ORDER BY reason_code""")
    factor_gaps = query("""WITH f AS (SELECT security_id,min(effective_from) first_factor
        FROM dwd_adjustment_factor GROUP BY 1)
        SELECT l.listing_code,f.first_factor,count(*) bar_rows,min(b.observation_start) first_bar,
        max(b.observation_start) last_bar FROM dwd_security_bar b
        JOIN dwd_listing l USING(listing_id)
        LEFT JOIN f USING(security_id) WHERE f.first_factor IS NULL
        OR b.observation_start<f.first_factor GROUP BY ALL ORDER BY l.listing_code""")
    clock = query("""SELECT count(*) mismatched_bar_session_clocks
        FROM dwd_security_bar b JOIN dwd_trading_calendar c USING(calendar_session_id)
        WHERE b.observation_start IS DISTINCT FROM c.open_at
        OR b.observation_end IS DISTINCT FROM c.close_at""")
    tradability = query("""SELECT count(*) legacy_missing_reasons_with_hist_bar
        FROM dwd_tradability_state t JOIN dwd_security_bar b
        USING(listing_id,calendar_session_id) WHERE t.reason_code='TRADABLE_OBSERVATION_MISSING'""")
    connection.close()
    return {
        "schema_members": "ALL_MATCH_SEALED_DWD",
        "table_rows": counts,
        "factor_intervals": intervals,
        "factor_initial_coverage": first_factor,
        "factor_2011_seam": seam,
        "research_ST_subtype_holds": research_holds,
        "factor_initial_gaps": factor_gaps,
        "bar_calendar_clocks": clock,
        "base_tradability_reconciliation": tradability,
    }


def stage(workspace: Path, product: Path, audit: Path, base: Path, output: Path) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[3]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(product / "payload", output / "payload", copy_function=os.link)
    shutil.copytree(product / "evidence", output / "evidence")
    shutil.copytree(audit, output / "evidence/coverage")
    shutil.copyfile(product / "MATERIALIZATION.json", output / "MATERIALIZATION.json")
    for name in [
        "SOURCE-PREFLIGHT.json",
        "RECOVERY-RECEIPT.json",
        "RESTORE-SELECTION.json",
        "COLLECTION-PLAN.json",
        "SOURCE-CAPTURE-CENSUS.json",
        "SESSION-RECOVERY.json",
    ]:
        shutil.copyfile(workspace / name, output / "evidence" / name)
    for name in ["FACTOR-COMPLETION-PLAN.json", "FACTOR-COMPLETION.json"]:
        if (workspace / name).is_file():
            shutil.copyfile(workspace / name, output / "evidence" / name)
    for name in [
        "SZSE-ACQUISITION.json",
        "INDEPENDENT-PRICE-SOURCE.json",
        "INDEPENDENT-ADJUSTED-PRICE-CHECK.json",
        "SUMMARY.json",
        "RELAY-V2-SUMMARY.json",
        "SSE-SUMMARY.json",
        "SSE-CONTROLS.json",
        "SZSE-SUMMARY.json",
    ]:
        source = workspace / "raw/source-probes" / name
        if source.is_file():
            shutil.copyfile(source, output / "evidence" / name)
    shutil.copyfile(repo / "config/pipelines/hist-1d-v1.json", output / "contract.json")
    shutil.copyfile(repo / "scripts/read_hist_1d.py", output / "read_hist_1d.py")
    structure = structural_audit(product, base)
    save(output / "evidence/STRUCTURAL-AUDIT.json", structure)
    provenance = {
        "packaging_code_revision": revision,
        "materialization_code_revision": "2cbd992f042c79fa2793baf71c42320dbedcb747",
        "base251_revision": "1923c874b43d47e69190a6dc9546e6fd8f3d1ca3",
    }
    materialization = json.loads((product / "MATERIALIZATION.json").read_bytes())
    if "factor_completion" in materialization:
        provenance["completion_code_revision"] = (
            materialization["factor_completion"]["code_revision"]
        )
    save(output / "PROVENANCE.json", provenance)
    bars = structure["table_rows"]["dwd_security_bar"]
    parquet = sorted((output / "payload").rglob("*.parquet"))
    sizes = sum(p.stat().st_size for p in parquet)
    quality = {
        "status": "CANDIDATE_PENDING_ACCEPTANCE",
        "bars": bars,
        "parquet_files": len(parquet),
        "parquet_bytes": sizes,
        "remaining_acceptance": ["historical ST/*ST/delisting-risk subtype disposition"],
        "overlap_disposition": [
            {
                "listing_code": "002336",
                "date": "2020-06-17",
                "field": "volume",
                "legacy": "1118342",
                "base251": "0",
                "action": "RETAIN_AUDIT_VARIANCE",
            },
            {
                "listing_code": "002500",
                "date": "2020-06-17",
                "field": "volume",
                "legacy": "38945309",
                "base251": "0",
                "action": "RETAIN_AUDIT_VARIANCE",
            },
        ],
        "overlap_scope": "Audit-only; bars are published through 2019-12-31.",
        "factor_interval_audit": structure["factor_intervals"],
        "factor_initial_coverage": structure["factor_initial_coverage"],
        "factor_initial_gaps": structure["factor_initial_gaps"],
        "bar_calendar_clocks": structure["bar_calendar_clocks"],
        "base_tradability_reconciliation": structure["base_tradability_reconciliation"],
        "research_ST_subtype_holds": structure["research_ST_subtype_holds"],
    }
    save(output / "QUALITY.json", quality)
    (output / "README.md").write_text(f"""---
language: [zh, en]
task_categories: [tabular-regression]
tags: [finance, a-shares, historical, daily]
---
# Quanthus Hist-1D v1 — candidate

独立历史日线产品，沪深 A 股普通股，1990-12-19 至 2019-12-31，含区间内退市证券。
当前为候选：历史 ST/*ST/退市风险细分等待验收处置，READY 尚未签发。

共 {bars:,} 条日线，{len(parquet)} 个 Parquet，{sizes:,} 字节。
2005–2019 为研究级目标区间，1990–2004 为参考级。
覆盖审计：两区间未解释证券交易日缺口均为 0，退市证券 112/112。
`evidence/coverage/` 提供按年/板块覆盖、证券差集与逐字段对账台账。

价格为未复权 DECIMAL(38,10)，成交量单位股、成交额元。
复权因子采用 BaoStock adjustFactor，PROVIDER_ADJUST_FACTOR / PROVIDER_NATIVE，
以 effective_from/effective_to 查询；独立来源 600601 的 2010 年 231 日抽样
统一锚点复权价格最大差 0.000253061 元。接缝与初始覆盖详见 STRUCTURAL-AUDIT。
保留旧归档零成交量 bar；新来源停牌只生成状态。原生因子只在供应商有效区间内可用。

二元 ST 在 PROVIDER_DAILY_STATE；经深交所官方历史简称佐证的 ST/*ST
在 SPECIAL_TREATMENT，其余细分保留 UNKNOWN 及原因，见 QUALITY.json。
复用的公司行动记录保持已封存参考版本；新原生因子的事件日期及数值可回溯原始响应。
沿用的 tradability_state 是 Base 2.5.1 固定快照；其中旧缺行情原因与本次已补行情
的相交数登记在 QUALITY.json，当前行情覆盖以 coverage 台账为准。
旧归档日级午夜坐标按其 calendar_session_id 映射到开收盘，规则
hist1d-session-clock-v1，值与源字节保留，逐条变更见 legacy-clock-normalization 台账。
000005 在 1990-12-19 至 1991-01-14 的 18 条参考级行情早于首条原生因子；
这些日期的复权查询保留无适用因子状态。
供应商以 601607 返回的 21 条上市前前身因子，按冻结上市区间排除并保全原响应，
见 factor-outside-listing-lifetime 台账。600849→601607 在 2010-03-05 更换上市代码，
各自有效区间使用对应原生因子；跨重组收益需另行处理该上市边界。
P2 估值/股本及新增指数留待后续。

周期研究级，非历史 PIT 认证。业务时间按 Asia/Shanghai，时间戳 UTC。
原归档 knowledge_from 保留；新增回溯数据使用首次完整采集时刻；
官方简称使用 SHA 绑定的采集回执时钟。契约见 contract.json。

2020–2021 仅用于对账，不发布重复行情。002336、002500 在 2020-06-17
各有一条旧归档与 Base 2.5.1 的成交量版本差异：OHLC 一致，双方成交额为零，
分别保留 1,118,342 / 38,945,309 股对 Base 的零股差异。逐项台账见 QUALITY.json。

## Read a fixed revision

公开版本验收后安装 huggingface_hub、pyarrow，然后执行：

```bash
python read_hist_1d.py --revision FIXED_DATASET_COMMIT_SHA
```

读取器明确 token=False。私有候选仅供持有者检查。
manifest.json 逐成员登记 path/bytes/SHA256；manifest 自身 SHA 及固定云版本由外部回执绑定。
原始源字节通过独立备份包保全；历史 Base 2.3 云包复用已核验归档，恢复索引随产品登记。
代码与数据来源精确版本见 PROVENANCE.json。任务：rv198-star/Quanthus#602。
""")
    counts = Counter(p.relative_to(output).parts[1] for p in parquet)
    write_manifest(output, provenance)
    return {**verify_manifest(output), "parquet_by_table": dict(counts), "root": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--product", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--base251", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_manifest(args.verify)
    else:
        if not all([args.workspace, args.product, args.audit, args.base251, args.output]):
            parser.error("staging requires workspace, product, audit, base251 and output")
        result = stage(args.workspace, args.product, args.audit, args.base251, args.output)
    print(json.dumps(result, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
