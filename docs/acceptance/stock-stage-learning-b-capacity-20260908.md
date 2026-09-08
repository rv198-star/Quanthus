# P3-STAGE-001-B：RX6800 CPU-first 训练节点容量验收

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Work item | `P3-STAGE-001-B` / GitHub #239 |
| Code revision | `d40297a6ad5ff9ff6a82048ead555103af43ed78` |
| Node | `RX6800` / WSL2 Ubuntu 26.04.1 LTS |
| Capacity report | `/home/qiuyu/quanthus-data/training/manifests/capacity/stage-training-capacity-20260908T053213Z.json` |
| Report SHA-256 | `sha256:745b51dedad47296e3206bd8d0877cfd85ce2a37a4e8aebbaf34426f3f4f6a98` |
| Scope | CPU-first容量/工程资格；不是状态分类器训练、语义准确率、未来收益或GPU验收 |

## 1. 结论

RX6800 WSL 节点通过 P3-STAGE-001-B 的 CPU-first 容量资格，可作为后续 stock-stage 历史学习的主训练节点。首轮不需要租 Linux GPU 云节点，也不需要把生产 `event-worker` / `replay-worker` 搬到该设备。GPU 仅保留为未来同模型可选加速基准；是否建立 `quanthus-stage-training-worker` 由 P3-STAGE-001-C 的 Job contract 决定。

该结论只说明当前 8 logical CPU / 32 GiB memory budget / 单并发的训练环境具有明显工程余量。它不把 100万行小型容量探针线性外推成完整 2012-2021 训练耗时承诺，也不证明任何离散阶段定义或模型优于连续 Facts。

## 2. 节点与环境

实测节点：Intel Core i5-11400，6核12线程；WSL2 实际可见内存约 41 GiB，16 GiB swap；训练盘 ext4，总量约 1007 GiB。本次 preflight 时 logical CPUs=12、available memory=43,091,660,800 bytes、free disk=997,007,278,080 bytes，满足配置中的 8线程、32 GiB内存、32 GiB scratch、至少28 GiB available RAM及200 GiB free disk门槛。

独立训练环境位于 Quanthus checkout 的 `.venv-stage-training`。本次实际版本：Python 3.14.4、NumPy 2.5.3、scikit-learn 1.9.0、DuckDB 1.5.5、PyArrow 23.0.1、threadpoolctl 3.6.0。此前 Ubuntu 26.04 首次 bootstrap 因系统未安装 `python3.14-venv`、venv 内无 pip而 fail-closed；没有进入 benchmark，系统日志无 OOM/segfault/kernel panic。修复 commit `d40297a` 在缺 ensurepip/pip 支持时明确报错，不再形成无解释的半成品环境。

## 3. 数据源与访问边界

训练源为只读：

- Base `dwd-base-202607-v2.1`：manifest `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`；content digest `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9`；33 tables；through 2026-07-31。
- Correction `dwd-base-202607-v2.1-correction-20260813-001`：manifest `sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde`；content digest `sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da`。

完整 Base 已物理镜像到训练节点，因此 2022-2025 不再是“该机器完全未持有字节”的物理隔离；项目仍保留 P0 的 2012-2021 development / 2022-2024 arbitration / 2025 permanent-blind 逻辑用途，开发程序必须通过 read guard 拒绝后两段。B benchmark 的真实 DWD 查询代码只构造 2020/2021 分区路径，并在 SQL 再限制 `2020-01-01..2021-12-31`；报告不输出价格、收益、标签分数、未来目标或策略结果。

这意味着后续不能再将 2022-2025 描述成“从未存在于训练硬件”的保留集，只能描述为受代码/权限/审计保护的逻辑 holdout。任何 F 阶段外样本主张仍需独立授权和访问账本。

## 4. 容量结果

### 4.1 合成 R1/R2 探针

32个 float32 特征，R1 为18个事前限定候选的向量化阈值计算，R2 为受限 CPU Logistic Regression：

| 行数 | 矩阵构建 | R1 / 18 candidates | R2 Logistic | 记录到的峰值 RSS |
| ---: | ---: | ---: | ---: | ---: |
| 100,000 | 0.048 s | 0.011 s | 0.147 s | 187,265,024 B |
| 500,000 | 0.267 s | 0.100 s | 0.526 s | 379,387,904 B |
| 1,000,000 | 0.512 s | 0.208 s | 1.011 s | 603,410,432 B |

该探针仅证明轻量 CPU 路线不是当前算力瓶颈，不能外推最终模型规模或最终样本量。

### 4.2 真实 DWD 2020-2021 Parquet/DuckDB 探针

读取 `dwd_security_bar` 的 `1d / UNADJUSTED / knowledge_to IS NULL`，按 `listing_id, observation_start` 排序并计算 1日收益与20日成交量均值：

| 行数 | first process pass | second process pass | 记录到的峰值 RSS |
| ---: | ---: | ---: | ---: |
| 100,000 | 0.522 s | 0.410 s | 621,314,048 B |
| 500,000 | 0.611 s | 0.632 s | 712,413,184 B |
| 1,000,000 | 0.890 s | 0.849 s | 855,826,432 B |

first process pass 的 OS page-cache 状态未知，**不称为 cold disk benchmark**。第二遍也只代表同进程/OS条件下的重复执行，不作为未来完整训练缓存收益承诺。

整份 capacity benchmark 从 05:32:13Z 到完成共 7.453秒。结束时 available memory 42,384,867,328 bytes；swap used 303,104 bytes，与 preflight 相同量级；未出现内存压力或 scratch 容量风险。

## 5. 资格决定

`P3-STAGE-001-B = ACCEPTED`：

- 默认主训练设备：RX6800 WSL 节点，**CPU-first**。
- 初始工作预算：单并发、最多8 logical CPU threads、32 GiB memory limit、32 GiB scratch；保留4线程及明显内存余量给Windows/WSL宿主。
- RX6800 GPU：不需要参与首轮；未来只有在同输入/同模型拟合成为实际瓶颈时才另做兼容性/收益基准。
- Linux CPU/GPU云：当前不需要租用；若后续完整任务触发实测容量或墙钟阻塞，再按相同报告口径重新选型。
- 生产 Worker：不迁移。未来训练 Worker 是独立 kind，不能复用生产 `event-worker` / `replay-worker` 的运行权限。

下一项 `P3-STAGE-001-C` 仍保持 blocked，直到负责人明确授权实现最小 Facts/历史 harness。B 的接受不自动授权分类器训练、未来结果、2022-2025开发读取、日更或交易。
