"""Audit and seal a full daily dataset with parent-specific quality profiles."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_full import (
    HIST_MANIFEST,
    HIST_REVISION,
    PROFILE,
    bind,
    query,
)
from quanthus.pipelines.hist_1d_release import save, verify_manifest, write_manifest
from quanthus.pipelines.hist_1d_status_completion import census


def audit(root: Path, hist: Path, base: Path) -> dict[str, Any]:
    started = time.monotonic()
    if (root / "READY").exists():
        raise ValueError("FULL_DAILY_ALREADY_SEALED")
    verify_manifest(root)
    if file_sha256(hist / "manifest.json") != HIST_MANIFEST:
        raise ValueError("FULL_DAILY_HIST_IDENTITY_CHANGED")
    expected = {}
    for record in json.loads((hist / "manifest.json").read_bytes())["members"]:
        if record["path"].startswith("payload/dwd_security_bar/"):
            expected[record["path"]] = record["sha256"]
    for record in json.loads((base / "manifest.json").read_bytes())["content"]["files"]:
        if record["path"].startswith("payload/dwd_security_bar/interval_size=1d/"):
            expected[record["path"]] = record["sha256"].removeprefix("sha256:")
    actual = {
        p.relative_to(root).as_posix(): file_sha256(p)
        for p in (root / "payload/dwd_security_bar").rglob("*.parquet")
    }
    if actual != expected:
        raise ValueError("FULL_DAILY_BAR_SOURCE_IDENTITY_CHANGED")
    scratch = root / "audit-tmp"
    scratch.mkdir()
    c = importlib.import_module("duckdb").connect(
        str(scratch / "audit.duckdb"), config={"threads": 2, "memory_limit": "2GB"}
    )
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("SET preserve_insertion_order=false")
    for table in sorted(p.name for p in (root / "payload").iterdir()):
        bind(c, table, root / "payload" / table)
    c.execute("""CREATE TABLE presence AS SELECT listing_id,
      CAST(observation_start AS DATE) AS trade_date,count(*) AS n,
      count(*) FILTER(WHERE volume=0) AS zero_n
      FROM dwd_security_bar WHERE knowledge_to IS NULL GROUP BY ALL""")
    c.execute("""CREATE TABLE expected AS SELECT DISTINCT l.listing_id,l.security_id,
      l.listing_code,l.board_code,c.trade_date,c.open_at FROM dwd_listing l
      JOIN dwd_listing_status_interval s USING(listing_id)
      JOIN dwd_trading_calendar c ON c.venue_id=l.venue_id
      WHERE l.knowledge_to IS NULL AND s.knowledge_to IS NULL AND c.knowledge_to IS NULL
      AND l.listing_currency='CNY'
      AND l.board_code IN('SSE_main','SZ_main','ChiNext','CHINEXT','STAR')
      AND s.status_type='LISTING_LIFECYCLE' AND s.status_value='LISTED'
      AND c.is_open AND c.session_type='REGULAR_DAY'
      AND c.trade_date BETWEEN DATE '1990-12-19' AND DATE '2026-10-31'
      AND s.effective_from<=c.open_at AND (s.effective_to IS NULL OR c.open_at<s.effective_to)""")
    c.execute("""CREATE TABLE coverage AS SELECT e.*,
      CASE WHEN p.n>0 THEN CASE WHEN p.n=p.zero_n THEN 'SOURCE_ZERO_VOLUME_BAR'
      ELSE 'OBSERVED_BAR' END WHEN EXISTS(SELECT 1 FROM dwd_listing_status_interval s
      WHERE s.listing_id=e.listing_id AND s.knowledge_to IS NULL
      AND s.status_type='SUSPENSION' AND s.status_value='SUSPENDED'
      AND s.effective_from<=e.open_at AND (s.effective_to IS NULL OR e.open_at<s.effective_to))
      THEN 'EVIDENCED_SUSPENSION' ELSE 'REFERENCE_EXPECTED_BAR_ABSENT' END AS coverage_status
      FROM expected e LEFT JOIN presence p USING(listing_id,trade_date)""")
    checks = query(
        c,
        """SELECT
      (SELECT count(*) FROM presence WHERE n>1) AS duplicate_listing_dates,
      (SELECT count(*) FROM dwd_security_bar WHERE interval_size<>'1d'
       OR price_basis<>'UNADJUSTED') AS wrong_frequency_or_basis,
      (SELECT count(*) FROM dwd_security_bar WHERE CAST(observation_start AS DATE)
       NOT BETWEEN DATE '1990-12-19' AND DATE '2026-10-31') AS outside_scope,
      (SELECT count(*) FROM dwd_security_bar b ANTI JOIN dwd_listing l
       USING(listing_id)) AS listing_fk,
      (SELECT count(*) FROM dwd_security_bar b ANTI JOIN dwd_trading_calendar c
       USING(calendar_session_id)) AS calendar_fk,
      (SELECT count(*) FROM coverage WHERE trade_date<DATE '2020-01-01'
       AND coverage_status='REFERENCE_EXPECTED_BAR_ABSENT') AS historical_missing,
      (SELECT count(*) FROM dwd_adjustment_factor WHERE factor_value<=0) AS invalid_factors,
      (SELECT count(*) FROM (SELECT security_id,effective_from,count(*) AS n
       FROM dwd_adjustment_factor GROUP BY ALL HAVING n>1)) AS duplicate_factor_events""",
    )[0]
    if any(checks.values()):
        save(root / "evidence/FULL-AUDIT-FAILURE.json", checks)
        raise ValueError("FULL_DAILY_REQUIRED_AUDIT_FAILED:" + json.dumps(checks))
    binary, _tasks, _calendars = census(root)
    if binary["missing"] or binary["conflicting"] or binary["expected"] != 8_493_259:
        raise ValueError("FULL_DAILY_HIST_BINARY_ACCEPTANCE_CHANGED")

    def ledger(sql: str, name: str) -> None:
        path = root / "evidence" / name
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
        result = c.execute(sql).fetch_arrow_table()
        pq.write_table(
            pa.Table.from_arrays(list(result.columns), schema=result.schema),
            path,
            compression="zstd",
        )

    ledger(
        "SELECT * FROM coverage WHERE coverage_status='REFERENCE_EXPECTED_BAR_ABSENT'",
        "MODERN-REFERENCE-MISSING.parquet",
    )
    ledger(
        """SELECT p.listing_id,l.security_id,l.listing_code,p.trade_date
      FROM presence p JOIN dwd_listing l USING(listing_id)
      ANTI JOIN expected e ON p.listing_id=e.listing_id AND p.trade_date=e.trade_date""",
        "BARS-OUTSIDE-LISTED-REFERENCE.parquet",
    )
    clock = query(
        c,
        """SELECT year(CAST(b.observation_start AS DATE)) AS calendar_year,
      count(*) AS row_count,count(*) FILTER(WHERE c.open_at IS NULL OR c.close_at IS NULL)
      AS unavailable_parent_session_clocks,count(*) FILTER(WHERE
      b.observation_start IS DISTINCT FROM c.open_at
      OR b.observation_end IS DISTINCT FROM c.close_at)
      AS differing_parent_coordinates FROM dwd_security_bar b JOIN dwd_trading_calendar c
      USING(calendar_session_id) GROUP BY ALL ORDER BY ALL""",
    )
    factors = query(
        c,
        """WITH x AS(SELECT l.security_id,min(b.observation_start) AS first_bar
      FROM dwd_security_bar b JOIN dwd_listing l USING(listing_id) GROUP BY 1),
      f AS(SELECT security_id,min(effective_from) AS first_factor
      FROM dwd_adjustment_factor GROUP BY 1)
      SELECT count(*) AS securities,count(*) FILTER(WHERE first_factor IS NULL) AS no_factor,
      count(*) FILTER(WHERE first_factor>first_bar) AS first_factor_after_first_bar FROM x
      LEFT JOIN f USING(security_id)""",
    )[0]
    ledger(
        """WITH f AS(SELECT security_id,min(effective_from) AS first_factor
      FROM dwd_adjustment_factor GROUP BY 1) SELECT l.listing_code,l.security_id,
      count(*) AS affected_bars,min(b.observation_start) AS first_bar,
      max(b.observation_start) AS last_bar,
      f.first_factor FROM dwd_security_bar b JOIN dwd_listing l USING(listing_id)
      LEFT JOIN f USING(security_id) WHERE f.first_factor IS NULL
      OR b.observation_start<f.first_factor
      GROUP BY ALL""",
        "FACTOR-INITIAL-COVERAGE.parquet",
    )
    report = {
        "status": "SOURCE_PRESERVING_FULL_DAILY_ACCEPTED",
        "profile": PROFILE,
        "bar_files_source_sha256_matched": len(actual),
        "checks": checks,
        "bar_census": query(
            c,
            """SELECT count(*) AS row_count,count(DISTINCT listing_id) AS listings,
          min(CAST(observation_start AS DATE)) AS first_date,
          max(CAST(observation_start AS DATE)) AS last_date FROM dwd_security_bar""",
        )[0],
        "yearly_bars": query(
            c,
            """SELECT year(CAST(observation_start AS DATE)) AS calendar_year,
          count(*) AS row_count,count(DISTINCT listing_id) AS listings FROM dwd_security_bar
          GROUP BY ALL ORDER BY ALL""",
        ),
        "coverage_by_year": query(
            c,
            """SELECT year(trade_date) AS calendar_year,
          coverage_status,count(*) AS listing_sessions FROM coverage GROUP BY ALL ORDER BY ALL""",
        ),
        "bars_outside_listed_reference": query(
            c,
            """SELECT year(p.trade_date) AS calendar_year,
          count(*) AS row_count FROM presence p ANTI JOIN expected e USING(listing_id,trade_date)
          GROUP BY ALL ORDER BY ALL""",
        ),
        "parent_coordinates": clock,
        "factor_initial_coverage": factors,
        "historical_binary_st": binary,
        "reference_role": (
            "Parent-qualified observations; source-grade and "
            "clock/lifecycle/factor limits remain explicit."
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    c.close()
    shutil.rmtree(scratch)
    save(root / "evidence/FULL-AUDIT.json", report)
    return report


def seal(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    if report["status"] != "SOURCE_PRESERVING_FULL_DAILY_ACCEPTED":
        raise ValueError("FULL_DAILY_AUDIT_NOT_ACCEPTED")
    save(root / "ACCEPTANCE.json", report)
    material = json.loads((root / "MATERIALIZATION.json").read_bytes())
    quality = {
        "status": "READY",
        "profile": PROFILE,
        "scope": report["bar_census"],
        "quality_segments": json.loads((root / "contract.json").read_bytes())["quality_segments"],
        "table_rows": material["table_rows"],
        "parent_reference_limits": "evidence/BASE251-LIMITATIONS.json",
        "modern_reference_gaps": "evidence/MODERN-REFERENCE-MISSING.parquet",
        "outside_listed_reference": "evidence/BARS-OUTSIDE-LISTED-REFERENCE.parquet",
        "factor_initial_coverage": report["factor_initial_coverage"],
        "factor_initial_ledger": "evidence/FACTOR-INITIAL-COVERAGE.parquet",
        "tradability_state_role": "Frozen parent reference, not recomputed entry permission",
        "remaining_acceptance": [],
    }
    save(root / "QUALITY.json", quality)
    save(
        root / "READY",
        {
            "status": "READY",
            "profile": PROFILE,
            "acceptance_sha256": file_sha256(root / "ACCEPTANCE.json"),
            "contract_sha256": file_sha256(root / "contract.json"),
        },
    )
    configs = "\n".join(
        f"  - config_name: {p.name}\n"
        + ("    default: true\n" if p.name == "dwd_security_bar" else "")
        + "    data_files:\n"
        f"      - split: train\n        path: payload/{p.name}/**/*.parquet"
        for p in sorted((root / "payload").iterdir())
    )
    n = report["bar_census"]["row_count"]
    (root / "README.md").write_text(f"""---
language: [zh, en]
task_categories: [tabular-regression]
tags: [finance, a-shares, historical, daily]
configs:
{configs}
---
# Quanthus Hist-1D v2 — 1990 至 2026 年完整日线

**READY：1990-12-19 至 2026-10-31 封存截止，实际最后交易日 2026-10-30。**
共 {n:,} 条沪深 A 股未复权日线，包含区间内退市证券。完整数据与必要 DWD 参考
可从本仓库独立下载，行情频率只有 1d。13 张 DWD 表的行数和角色见 QUALITY.json。

历史段取已验收 Hist v1（1990–2019），现代段取封存 Base 2.5.1（2020–2026-10）。
435 个行情分区保留父包原始字节，来源、价格、成交量和知识时间全部保全。
价格 DECIMAL(38,10)、成交量股、成交额元，price_basis=UNADJUSTED。
交易日期按 Asia/Shanghai 从 observation_start 取得，时间戳保存为 UTC。
父包中自然日/交易时段坐标及缺少开收盘时钟的参考行原样保留，按日期和
calendar_session_id 关联，详见 ACCEPTANCE.json 的 parent_coordinates。

## 质量与参考

| 日期段 | 使用等级 |
|---|---|
| 1990–2004 | 历史参考级 |
| 2005–2019 | 继承已验收周期研究级，8,493,259 个应有证券交易日二元 ST 完整 |
| 2020–2026-10 | 继承 Base 2.5.1 已验收源观测；参考数据资格按父包逐项保留 |

原生因子合并 PROVIDER_ADJUST_FACTOR / PROVIDER_NATIVE 事件，重叠数值必须一致，
有效期末由下一条源事件确定。因子初始覆盖缺口见 evidence/FACTOR-INITIAL-COVERAGE.parquet。
证券/上市/日历等参考扩展到现代父包，历史补采状态随包保留。现代上市区间、日历时钟、
原生因子和历史来源限制见 QUALITY.json 及 evidence/BASE251-LIMITATIONS.json；
逐年上市交易日覆盖及源观测超出已资格化上市参考的范围由 ACCEPTANCE.json 和对应
Parquet 台账给出。ST 细分后补继续由 Quanthus #606 跟踪。

dwd_tradability_state 是原 Base 固定参考快照，其旧缺行情原因与本包已补行情分开审计。
研究时使用行情覆盖台账；策略执行仍按自身状态及可见性契约准入。数据为回溯研究用途，
保留真实知识时钟，历史 PIT 资格沿用各源限制。

## 下载与读取

```python
from huggingface_hub import snapshot_download
root = snapshot_download("rv192/quanthus-hist-1d", repo_type="dataset",
                         revision="FIXED_REVISION", token=False)
```

只读取行情时可添加 allow_patterns=["payload/dwd_security_bar/**/*.parquet"]。
安装 huggingface_hub、pyarrow 后，也可使用随包脚本：

```bash
python read_hist_1d.py --revision FIXED_REVISION --year 2020 --month 1
python read_hist_1d.py --revision FIXED_REVISION --year 2026 --month 10
```

脚本默认解析 main 到固定提交再读取，并核验文件 SHA256；省略 --revision 可读取当前默认版。
历史截至 2019 年的版本固定在 `{HIST_REVISION}`，继续可读。
PROVENANCE.json 记录构建代码与两个父版本；manifest.json 登记全部成员的 SHA256 与大小。
本包来源是已有 Hist 及 Base 2.5.1 的独立备份链，恢复路径见 SOURCES.json。
任务：rv198-star/Quanthus#619；下一版 ODS/DWD 四包 Base2.6 由 #615 跟踪。
""")
    save(
        root / "SOURCES.json",
        {
            "historical_revision": HIST_REVISION,
            "historical_dataset": "https://huggingface.co/datasets/rv192/quanthus-hist-1d/tree/"
            + HIST_REVISION,
            "historical_source_backup": "https://drive.google.com/drive/folders/1FD63EvsAGEyIs63hFG914otZBx0rkNoR",
            "base251_dataset": "https://huggingface.co/datasets/rv192/quanthus-base25-sealed/tree/1923c874b43d47e69190a6dc9546e6fd8f3d1ca3",
            "source_types": ["inherited_hist_dwd", "inherited_base251_dwd"],
            "raw_evidence": (
                "Preserved in the parents' source/ODS backups; "
                "selected Base member hashes and parent identities are included."
            ),
        },
    )
    provenance = json.loads((root / "PROVENANCE.json").read_bytes())
    manifest = write_manifest(root, provenance)
    manifest["status"] = "READY"
    save(root / "manifest.json", manifest)
    return dict(verify_manifest(root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--hist", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    args = parser.parse_args()
    report = audit(args.root, args.hist, args.base)
    print(json.dumps(seal(args.root, report)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
