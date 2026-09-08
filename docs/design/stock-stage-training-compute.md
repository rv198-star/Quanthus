# 个股阶段学习：算力与设备选择

| 字段 | 值 |
| --- | --- |
| Design status | draft |
| Delivery gate | open |
| Parent | `stock-stage-historical-learning.md` |
| Work item | `P3-STAGE-001-B` |
| Evidence | OCI只读快照；RX6800节点已通过专用SSH独立实测CPU/RAM/WSL/ext4/GPU可见性，并完成DWD Base v2.1+correction只读训练源核验 |
| Authority | P3-STAGE-001-B CPU-first容量benchmark已获负责人授权；不批准市场分类器训练、2022+开发读取、云GPU采购或生产Worker迁移 |

## 1. 结论

第一版是日线表格特征、有限规则参数学习和轻量模型，不是大语言模型/图像或深层时序网络训练。先CPU、后实测、再选设备。OCI可承载限额工程试点和小规模学习，不能据硬件清单承诺完整历史搜索耗时。生产共置及磁盘空间是当前比“有没有GPU”更紧迫的限制。

RX6800节点已经从“候选”进入B阶段主容量节点：WSL2实测为 i5-11400 6核12线程、约41GiB可见内存、16GiB swap、1007GiB ext4且解压后约930GiB可用，`/dev/dxg`可见。第一轮冻结为单并发、8线程、32GiB内存/32GiB scratch，仍以CPU为主；RX6800显卡仅保留后续可选加速基准，Linux GPU云当前不需要。

## 2. OCI现场观测与限制

| 项目 | 只读观测 |
| --- | --- |
| CPU | aarch64，ARM Neoverse-N1，4在线核，单线程/核 |
| RAM | `free -h`显示23GiB总量；6.3GiB used、约16GiB available；只是该时点余量 |
| Swap | 11GiB总量，约1.1GiB已用；不能当作正常训练RAM预算 |
| 文件系统 | 146GiB总量，93GiB已用，53GiB可用；worktree与/tmp同一文件系统 |
| 负载 | 1/5/15分钟0.16/0.12/0.13；不是长期性能保证 |
| 执行上下文 | `/system.slice/devspace.service`；CPUQuota/MemoryMax/MemoryHigh均未限制；memory.current约9.1GB包含服务子进程/缓存，不能与free字段简单相加 |
| GPU | `/dev/dri/card0`的PCI vendor=0x1af4、device=0x1050、driver=virtio-pci；仅虚拟显示，不是训练GPU |
| 既有工程虚拟环境 | Python3.10，DuckDB/PyArrow可用；numpy/sklearn/psutil未安装。未为本次任务安装包 |
| 既有Worker声明 | Replay/Event各有16g memory limit；它们不是新训练的可用配额，禁止同时按两个16GiB满载规划 |

命令来源：`lscpu`、`free -h`、`df -h . /tmp`、`/proc/loadavg`、`/proc/self/cgroup`、`systemctl show devspace.service`、对应cgroup文件、`/sys/class/drm/card0/device/{vendor,device,uevent}`、`ops/platform/compose.yaml`。未使用云元数据凭证接口，未读市场结果。

## 3. 为什么先CPU

规则搜索、线性模型和受限树对照不依赖CUDA；瓶颈可能是历史特征生成、episode扫描、磁盘临时表或串行评审，不一定是模型拟合。R1必须先建共享面板再搜索，不允许32个候选各自重建十年DWD投影。

仅用于数量级估算（不是实际证券数量或已扫描结果）：假设5000证券×250交易日×6年=750万行。60列float32的单份连续矩阵约1.8GB十进制/1.68GiB；float64约3.6GB。这不含键、可用性、排序、复制、树直方图、过程状态、解释证据及多折数据。

同样750万行若按12标签扩成长表可达9000万行，再逐行重复JSON证据会显著膨胀。应共享观察键和证据引用、候选稀疏存储、按日期/证券分块，分别估算训练密集矩阵与完整审计存储。不能把1.8GB当成训练总内存或模型显存需求。

## 4. B工单的基准与停机规则（建议，尚未执行）

先合成数据做安装/正确性测试，再在A批准的development范围内做分层小样本端到端基准：约10万行，必要时扩至50万/100万行，但不为凑行数越过分区或历史质量门槛。同时测冷构建与复用已有面板的拟合成本；不把有缓存的第二次耗时当冷构建。

阶段分解：读取/准入 → 特征与episode → 参考/target join → R0/R1/R2拟合 → 校准 → 评价/持久化。记录wall/CPU time、峰值RSS、可用RAM低水位、swap增长、输入输出/临时字节、rows/sec、trial数、线程及重用。至少两个规模点，必要时三点判断非线性，给出范围估算而不是承诺某分钟完成。

OCI首轮建议上限：并发1、2个CPU线程、6GiB进程树内存、8GiB临时磁盘、总wall30分钟；执行前单独配额环境应可强制限额。资源准入还要求主机available至少10GiB、磁盘free至少20GiB+本批scratch预算、无冲突中的重型生产/其他项目作业。运行时低于8GiB主机available、磁盘跌破20GiB、持续swap增长或服务健康受影响，安全停止研究任务，不杀生产进程。配额无法实际约束时不得声称已隔离。

这些是保护共享节点的建议初值，不是对生产SLO的替代。B由现场负载确认具体预算；正常试验并行和BLAS/OpenMP嵌套线程不得越过上限。容量不足输出BLOCKED/迁移建议，不自动放宽内存或使用无界swap。

## 5. 用户Win11设备

本轮用户提供：Windows11、AMD RX6800、1TB SSD。未确认CPU型号/核心、系统RAM、SSD可用量、WSL配额。显卡的16GB是显存，不是系统RAM；AMD RX6800官方规格确认为16GB GDDR6。[H1]

建议作为CPU工作站的候选档位（容量规划，不是采购要求）：现代6-8核及以上CPU、32GiB RAM可做多数首轮表格试验，64GiB更适合较大面板/多折；16GiB可先小样本但限制明显。SSD至少保留100-200GB工作余量是初始建议，最终按入选snapshot/特征包、临时排序、模型和保留策略测算，不能仅凭标称1TB保证足够。

代码仍从GitHub拉取。优先WSL2 Linux CPU环境以减少与Linux服务端差异，数据和环境放WSL Linux文件系统而非反复跨`/mnt/c`读写；这是Microsoft文档建议。[H6] 只传获准development的精确数据包与manifest，不传整仓密封分区，不复制OCI凭证。冻结依赖和CPU推断接口；不要在本轮让用户先换系统或买显卡。

### GPU后端核查（2026-09-08）

| 路线 | 官方资料核查 | 本项目判断 |
| --- | --- | --- |
| PyTorch/ROCm Windows与WSL | AMD 7.2.1 Radeon矩阵不列RX6800；已额外查7.13.0 Preview，新支持的PRO W6800不是RX6800；不能由同gfx1030推断受支持。[H2,H3] | 不把RX6800 Windows/WSL列为经官方确认的标准主训练路径；社区可运行不等于本机已验证 |
| Linux ROCm | 查询的Radeon矩阵同样不把RX6800列为已验证型号；TheRock/community范围可能不同 | 不保证把同一显卡装Linux就能无成本解决；获准的选型基准可单独测试，不以兼容性折腾阻塞首轮 |
| PyTorch DirectML | Microsoft仍有安装文档，文档写torch-directml支持至PyTorch2.3.1；DirectML处于维护模式，新Windows ML方向是模型执行，不能当通用训练替代。[H4] | 可选小实验；不作为新训练标准栈，也不先安装旧依赖到生产环境 |
| LightGBM OpenCL | 官方支持Windows的`device_type=gpu`构建及AMD OpenCL；仅直方图部分GPU化，其余仍CPU；当前另有Linux ROCm路线。[H5] | RX6800不是不能训练树；可在同模型下做选配GPU基准，但驱动/构建/端到端收益尚未实测 |
| XGBoost官方GPU后端 | CUDA-capable GPU路线。[H7] | 不把`device=cuda`直接套到RX6800；CPU版本仍可作对照 |

这里区分GPU硬件算力、驱动/框架支持与Quanthus可重复验收。无GPU并不阻塞R0/R1/CPU R2，16GB显存也不自动保证项目适配。

## 6. 何时才租节点

优先顺序：OCI限额试点 → 合格的自有Win11/WSL CPU工作站 → 独立Linux CPU云 → 有证据需要时Linux NVIDIA GPU。

CPU云候选：8-16 vCPU、32-64GiB RAM、足量本地SSD（初始规划200GB），按B的吞吐、RAM和I/O结果缩放。它用于隔离持续搜索和释放OCI生产压力，并非增加模型智力。

GPU云候选：单卡16-24GB显存起步、32-64GiB系统RAM和匹配CPU；只有模型路线确实支持该GPU且拟合成为主要瓶颈，或获准时序深度模型确实改善结果时才考虑。NVIDIA/CUDA是兼容性优先建议，不保证必然更快、更便宜。不预订A100/H100、不报未经实时询价的价格、不自动购买。

采购前冻结最小租用时长/金额上限、区域与数据授权、确切GPU与驱动/框架镜像、持久盘/出站成本和停机清理方式。比较总成本=安装+传输+数据构建+全部试验+持久存储/出站，而不只比较GPU每小时价格。

## 7. 设备准入交付

B输出同一基准在可用设备上的记录、完整环境/资源manifest、推断数值容差与冻结模型回归、CPU/GPU收益判定，以及推荐设备。用户Win11硬件补全可以使用本机系统信息/PowerShell只读采集，不要求用户判断算法或手工安装ROCm。

当前结论等级：OCI硬件=已观察；可做限额试点=工程判断；完整训练速度=未测；Win11训练适用=待CPU/RAM/空间；RX6800加速=可选、未实测；租机=暂不必要、未授权。

## 硬件/工具依据

- [H1] AMD RX6800规格：https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-rx/radeon-rx-6000-series/amd-radeon-rx-6800.html
- [H2] AMD Radeon Windows/WSL 7.2.1矩阵：https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html 与 https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/wsl/wsl_compatibility.html
- [H3] AMD 7.13.0 Preview兼容性/发布说明：https://rocm.docs.amd.com/en/7.13.0-preview/compatibility/compatibility-matrix.html 与 https://rocm.docs.amd.com/en/7.13.0-preview/about/release-notes.html
- [H4] Microsoft PyTorch DirectML：https://learn.microsoft.com/en-us/windows/ai/directml/pytorch-windows ；维护状态：https://learn.microsoft.com/en-us/windows/win32/direct3d12/dml-redirect
- [H5] LightGBM安装指南：https://lightgbm.readthedocs.io/en/stable/Installation-Guide.html
- [H6] WSL文件系统：https://learn.microsoft.com/en-us/windows/wsl/filesystems
- [H7] XGBoost GPU：https://xgboost.readthedocs.io/en/stable/gpu/

版本支持会变化，B执行前重核确切选定版本；旧支持矩阵或SDK能编译不能替代训练栈验收。
