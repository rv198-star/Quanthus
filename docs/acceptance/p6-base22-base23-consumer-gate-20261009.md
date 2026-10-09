# P6-BASE-001 — Base 2.2 / 2.3 消费适配与准入结论（2026-10-09）

## 裁决
**P6 以 Base 2.2 为优先历史 Seed 候选，不等待整份 Base 2.3。** Base 2.3 未来按同一逻辑接口但独立身份证据准入。P6 正式前瞻收集仍为 0/66；本方案不授权生产激活、数据修复或交易。

## 真实上游状态（CN02 只读）
- Base 2.2：截至 2026-08-31，独立自足 33 表实体候选，458,356,845 行、85,713 个 Parquet；两次独立冷验收。manifest SHA256：54346d61026bd600e4db3616c7d3e579e5307e8d1d4de657b35ec303f67fefb5。Owner 最终验收及任何 P6 consumer 授权尚未完成；consumer_admission=NOT_GRANTED，restricted_uses 包括 trading_entry/historical_pit。
- Base 2.3：计划截至 2026-09-30。77 个选定 September ODS 包物理检查完成；当前选定本地 inventory 仍缺 09-24/28/29/30 关键 core/events 以及 09-16 指定原始 ODS 身份。没有完整 Base 2.3 全镜像 READY。base23-core-* 下的 history seed 仍是 Base 2.2，而非 Base 2.3 正式候选。
- 2026 年日线、交易日历、可交易状态、证券与 listing、复权因子、涨跌停和公司行动等 12 类实体表物理存在。仅检查 schema/footer，不读取受保护 2025 行或经济结果。

## 当前 P6 消费落差
旧 P6 frozen runtime 精确绑定 Base V2.1 截止 2026-07-31、单独 correction-001、旧 payload/listed_company 与 security_market_observation 分域、旧 Daily 链和源 SHA。不能把 Base2.2 新路径直接指到旧 seed 以声称等价。
Base2.2 使用 payload/dwd_* 的 native 分区目录；日级 bar 在 dwd_security_bar/interval_size=1d/observation_start_year=2026，价格口径为 unadjusted 的 none。核心列包括 source_artifact_sha256、knowledge_from/to、effective_from/to、listing/venue/security ID，可建立版本化转换层，但转换不等于经济或 PIT 资格通过。

## 应执行的分离门槛
1. P6 先开发 versioned NativeBaseSeed 逻辑接口、最小必需字段/表约束和只读 candidate probe；后续在 Base2.3 正式发布时再增加其独立 source-kind/manifest binding，不猜测新版本字段及资格。
2. Base2.2 原有 trading_entry、historical_pit 等限制不能因 33 表全量验收自动豁免。由 P10 在实际 P6 close-to-next-open-v1 的 SSE/SZSE 日期、价格基准、数据到达时间、时间可知性、企业行动和 listing 范围给出用途限定资格；P6 还要独立审查 feature/纸面账本可用性。与策略无关的完整股东矩阵或 1m/5m 高精度，不自动变为 P6 硬门槛。
3. Base2.2 的 August31 → September/October 必须有版本化跨月 Daily DWD 接口。旧 September 增量虽实际来自 Base2.1 不能直接冒充 Base2.2 的原生后继，必须做独立键/值/时点语义比对；Base2.3 可消除部分拼接但自身含 09-24 后变化和月末来源缺口，不是当前可靠前置。
4. 只读 probe 对固定 manifest SHA/READY、基线版本、截止日期及 12 张决策涉及表的最小 Parquet footer schema 做检查；**绝不**验证真实价格、PIT 事件可知性、完整物理哈希、独立参考或 human authorization，不应写入 official Job。候选 manifest 中 consumer_admission=NOT_GRANTED 必须保持正式 P6 不可用。
5. 只有新版本受控发布、P6 权限/生产和激活签署、合格的激活后市场日以及独立冷复现全部通过，首个真实周期才可以记为 1/66。9/21–23 已失败 Job 永不补记。

## 本次工程验证的上限
独立只读探针：src/quanthus/forward_validation/native_base_probe.py。
它可在具备 PyArrow 的隔离环境中运行，通过 Python -m quanthus.forward_validation.native_base_probe 并显式输入 root 和外部冻结 SHA。即便物理元数据兼容也恒定返回 consumer_authorized=false；未知 Base2.3 身份必须拒绝“继承” Base2.2 的资格。

事实入口：P10 Base2.2 #480；P6-R1 #511；旧前驱来源 #512；消费者最小适配 #514；CN02 Base22 phase2-20261008T222307Z-5bfb014 / Base23 base23-preparation-20261008T224607Z-3249b16。