"""Seal the Owner-approved binary-ST Hist-1D release and preserve subtype debt."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_materialize import midnight
from quanthus.pipelines.hist_1d_release import save, verify_manifest, write_manifest
from quanthus.pipelines.hist_1d_status_completion import INPUT_MANIFEST, census

PROFILE = "hist1d-cycle-research-binary-st-v1"


def require_acceptance(contract: dict[str, Any], coverage: dict[str, Any]) -> None:
    acceptance = contract.get("acceptance", {})
    if (
        acceptance.get("profile") != PROFILE
        or acceptance.get("authority") != "explicit_owner_feedback"
        or acceptance.get("binary_st_required") is not True
        or not acceptance.get("owner_instruction")
        or not acceptance.get("deferred_issue")
    ):
        raise ValueError("OWNER_BINARY_ST_ACCEPTANCE_NOT_BOUND")
    if (
        coverage["missing"]
        or coverage["conflicting"]
        or coverage["known"] != coverage["expected"]
        or coverage["expected"] <= 0
    ):
        raise ValueError("REQUIRED_BINARY_ST_COVERAGE_FAILED")


def finalize(root: Path, candidate: Path) -> dict[str, Any]:
    if (root / "READY").exists():
        raise ValueError("RELEASE_ALREADY_SEALED")
    if file_sha256(candidate / "manifest.json") != INPUT_MANIFEST:
        raise ValueError("PREDECESSOR_CANDIDATE_CHANGED")
    repo = Path(__file__).resolve().parents[3]
    contract_path = repo / "config/pipelines/hist-1d-v1.json"
    contract = json.loads(contract_path.read_bytes())
    coverage, _tasks, _calendars = census(root)
    require_acceptance(contract, coverage)
    predecessor = json.loads((candidate / "manifest.json").read_bytes())
    unchanged = 0
    for record in predecessor["members"]:
        path = record["path"]
        if path.startswith("payload/") and not path.startswith(
            "payload/dwd_security_status_interval/"
        ):
            if file_sha256(root / path) != record["sha256"]:
                raise ValueError("NON_STATUS_PAYLOAD_CHANGED:" + path)
            unchanged += 1
    pq = importlib.import_module("pyarrow.parquet")
    pa = importlib.import_module("pyarrow")
    status_path = next((root / "payload/dwd_security_status_interval").rglob("*.parquet"))
    status = pq.ParquetFile(status_path).read()
    debt = [
        row
        for row in status.to_pylist()
        if row["status_type"] == "SPECIAL_TREATMENT" and row["status_value"] == "UNKNOWN"
    ]
    ledger = root / contract["acceptance"]["deferred_ledger"]
    pq.write_table(pa.Table.from_pylist(debt, schema=status.schema), ledger, compression="zstd")
    early_research = {
        row["security_id"]
        for row in debt
        if row["effective_from"] < midnight(date(2011, 1, 1))
        and (row["effective_to"] is None or row["effective_to"] > midnight(date(2005, 1, 1)))
    }
    structure_path = root / "evidence/STRUCTURAL-AUDIT.json"
    structure = json.loads(structure_path.read_bytes())
    structure["table_rows"]["dwd_security_status_interval"] = status.num_rows
    structure["binary_status_coverage"] = coverage
    structure["non_status_payload_reused"] = unchanged
    save(structure_path, structure)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    provenance = json.loads((root / "PROVENANCE.json").read_bytes())
    provenance.update(
        release_code_revision=revision, predecessor_candidate_manifest_sha256=INPUT_MANIFEST
    )
    save(root / "PROVENANCE.json", provenance)
    shutil.copyfile(contract_path, root / "contract.json")
    save(root / "evidence/BINARY-STATUS-AUDIT.json", coverage)
    acceptance = {
        "status": "ACCEPTED_FOR_CYCLE_RESEARCH",
        "profile": PROFILE,
        "owner_decision": contract["acceptance"],
        "binary_status_coverage": coverage,
        "unchanged_non_status_parquet_members": unchanged,
        "deferred_subtype_ledger": contract["acceptance"]["deferred_ledger"],
        "deferred_subtype_ledger_sha256": file_sha256(ledger),
        "deferred_subtype_intervals": len(debt),
        "research_pre2011_unknown_subtype_securities": len(early_research),
        "predecessor_candidate_manifest_sha256": INPUT_MANIFEST,
        "release_code_revision": revision,
    }
    save(root / "ACCEPTANCE.json", acceptance)
    quality = json.loads((root / "QUALITY.json").read_bytes())
    quality.update(
        status="READY",
        acceptance_profile=PROFILE,
        remaining_acceptance=[],
        binary_status_coverage=coverage,
        deferred_subtype_issue=contract["acceptance"]["deferred_issue"],
        deferred_subtype_ledger=contract["acceptance"]["deferred_ledger"],
        deferred_subtype_intervals=len(debt),
        research_pre2011_unknown_subtype_securities=len(early_research),
    )
    quality["parquet_bytes"] = sum(p.stat().st_size for p in (root / "payload").rglob("*.parquet"))
    save(root / "QUALITY.json", quality)
    rows = quality["bars"]
    (root / "README.md").write_text(f"""---
language: [zh, en]
task_categories: [tabular-regression]
tags: [finance, a-shares, historical, daily]
---
# Quanthus Hist-1D v1

**READY：周期研究用历史日线，按二元 ST 必需、细分缺口后补的口径验收。**

沪深 A 股普通股，1990-12-19 至 2019-12-31，含期间退市证券。共 {rows:,} 条
未复权日线；2005–2019 为研究级，1990–2004 为参考级。
研究/参考两区间行情未解释缺口均为零，退市覆盖 112/112。

Owner 2026-11-11 决定：本次以二元 ST 为必需；ST/*ST 等细分缺口后补。
研究区间 {coverage["expected"]:,} 个上市证券交易日的明确二元状态覆盖完整、冲突为零。
二元读取规则见 contract.json：PROVIDER_DAILY_STATE 的 NORMAL/ST 分别为 0/1；
SPECIAL_TREATMENT 的明确 NORMAL/ST/DELISTING_RISK 分别为 0/1/1。
UNKNOWN 保留未知。新增来源只采 date/code/isST，使用首次完整采集时刻。

2005–2010 的 {len(early_research)} 只证券保留细分 UNKNOWN；参考级与继承快照的
细分未知也保留。逐区间债务见 evidence/ST-SUBTYPE-DEFERRED.parquet；
后续工作：{contract["acceptance"]["deferred_issue"]}。
ACCEPTANCE.json 记录验收范围，QUALITY.json 和 evidence/ 给出完整审计与来源绑定。

价格 DECIMAL(38,10)，price_basis=UNADJUSTED；成交量股、成交额元。
因子为 BaoStock adjustFactor，PROVIDER_ADJUST_FACTOR / PROVIDER_NATIVE。
000005 最初 18 条参考级行情早于首条因子，保留无适用因子状态。
601607 上市前的 21 条前身因子按冻结上市期排除并留存台账；跨重组收益单独处理
2010-03-05 的上市边界。567 条旧午夜坐标按关联交易日历规范为开收盘，行情值保全。

2020–2021 仅用于对账。2020-06-17 的 002336、002500 各有一条既有成交量版本差异：
旧档分别 1,118,342 / 38,945,309 股，Base 2.5.1 为零；OHLC 相同、成交额均为零。
原 tradability 快照保留原版本，旧缺行情原因与当前行情的相交数见质量报告。

周期研究级，非历史 PIT 认证。交易日 Asia/Shanghai，时间戳 UTC。
历史来源保留原知识时钟，新回溯观测使用采集时刻，官方名称使用 SHA 绑定回执时钟。
原始响应、失败响应与 SHA 在独立备份链中保全。

## 固定版本匿名读取

安装 huggingface_hub、pyarrow，从本版本下载 read_hist_1d.py，然后执行：

```bash
python read_hist_1d.py --revision FIXED_DATASET_COMMIT_SHA
```

读取器明确 token=False；发布回执提供实跑的固定 commit SHA。
manifest.json 登记全部成员 path/bytes/SHA256，外部发布回执绑定 manifest SHA 和云端版本。
READY 绑定验收及契约摘要。原始全量候选备份与发布增补层共同恢复本版本。
任务：rv198-star/Quanthus#602；Base 2.5.1 与生产 Daily 保持既有版本。
""")
    save(
        root / "READY",
        {
            "status": "READY",
            "profile": PROFILE,
            "acceptance_sha256": file_sha256(root / "ACCEPTANCE.json"),
            "contract_sha256": file_sha256(root / "contract.json"),
        },
    )
    manifest = write_manifest(root, provenance)
    manifest["status"] = "READY"
    save(root / "manifest.json", manifest)
    return {
        **verify_manifest(root),
        "readiness": "READY",
        "profile": PROFILE,
        "deferred_issue": contract["acceptance"]["deferred_issue"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.root, args.candidate)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
