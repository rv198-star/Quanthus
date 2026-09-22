# SSH / Linux 部署预检 — 2026-09-22

范围：#424，草稿 PR #426 → #428 → #430；本次仅验证与预检，不合并、不部署。
所有节点通过既有 SSH 免密连接，BatchMode=yes、StrictHostKeyChecking=yes；没有使用 MCP 节点控制。

## 已确认的生产状态

| 节点 | 代码 SHA | 只读预检 |
| --- | --- | --- |
| CN01 | b02fb3cc9fabefe014314aa9edf63914ea16b4c1 | 日编排运行中；tracked-file clean；systemd 249 / Linux 5.15 |
| Hermes | b02fb3cc9fabefe014314aa9edf63914ea16b4c1 | 19:30 日编排自然启动后运行中；tracked-file clean；systemd 257 |
| OCI | f644311b4258204f5f83d2aa61a8d7a882e09cdc | warehouse-current 指向同名 releases 目录；以仓库所有者 root 只读核验，无 tracked-file 修改 |

没有审计 untracked 文件。两采集节点均在运行，未停止 timer、重启服务或切换工作目录。
OCI 不同代码 SHA 不自动判为错误：它承担同步角色，本次没有做节点版本兼容性或物理包验收。
普通 ubuntu 用户被 Git 所有权保护拒绝后，改用已有 sudo -n 以 root 只读检查；未修改 safe.directory。

两采集节点有效 unit 均为 KillMode=control-group、SendSIGKILL=yes、TimeoutStopSec=90s、
TimeoutStartSec=9h。OCI 同步服务亦为 control-group / SendSIGKILL=yes，启动超时 13h。
这些是安装状态，不等于在生产服务上做过停止实验。

## 实际 Python 路径与依赖漂移

通过运行中服务后代的 argv0 以及严格限定的 Python 路径环境变量核对，未输出其他环境变量或凭据。

| 角色 | 解释器 | pandas | pyarrow | mootdx |
| --- | --- | --- | --- | --- |
| CN01 分钟采集 | /root/work/astock/.venv/bin/python，3.10.12 | 2.3.3 | 23.0.1 | 0.11.7 |
| Hermes lane | /root/work/astock/.venv/bin/python，3.13.5 | 3.0.3 | 25.0.0 | 未安装；分钟模块有独立解释器 |
| Hermes 分钟采集 | /root/work/astock/.venv-a-stock-data/bin/python，3.11.15 | 3.0.5 | 25.0.0 | 0.11.7 |

三个表列环境的 requests 均为 2.34.2。部署版本 pyproject.toml 的 ods 声明 pandas>=2.2,<3、
pyarrow>=18,<24，故 Hermes 的 lane/分钟环境超出已声明范围。不能据此推断昨日或今日故障的根因；
需独立环境修复或版本化兼容性验证后才能放行，不能在运行中直接降级依赖。
CN01 仓库内 .venv 不是当前 lane 的解释器，不把该残留环境缺包算作生产缺包。

## 独立 Linux 验证环境

Devops SSH 可达，Debian/systemd 252，Linux 6.8.12-17-pve，x86_64。
使用独立临时根 /tmp/quanthus-ops-linux-20260922.vvXt6T，精确版本直接从 GitHub 获取：
候选 2daa675b59dba8f944959aa20f61f358fc767e65，C1 基线 99c5b76d070f0fd747c3be86f894bc2d0377f401。
未复制、修改任何生产 checkout。该节点的结果不替代 CN01/Hermes 部署后验收。

PyPI 直连超时，Tsinghua 测试端点返回 403，Aliyun 与 USTC 测试端点返回 200。
隔离 venv 使用明确的 Aliyun HTTPS index；只影响新临时目录，没有更改任何全局或生产源设置。
首轮下载达到 240 秒安全上限后停止，利用已有缓存对剩余依赖作一次有界续传。

## 已完成的真实 systemd 故障实验

两次实验只建立全新 transient test unit；均限制 CPU、内存、任务数与最长运行时间，隔离网络，
启用 ProtectSystem/ProtectHome/NoNewPrivileges。没有触碰现有业务 unit。

1. 父子均忽略 TERM，子进程 setsid 脱离原进程组。停止测试 unit 后，systemd 在 2 秒宽限后
   SIGKILL 两个进程；原 PID 324155/324157 与 cgroup 均确认消失。
2. 只向控制器 PID 324402 发 SIGKILL。systemd 随后回收另建进程组且忽略 TERM 的 PID 324404；
   原 PID 与 cgroup 均确认消失。

实验故意产生 timeout/signal 终态，这不是业务故障。保留原始日志后，仅 reset-failed 这两个测试 unit；
再次检查均 LoadState=not-found / inactive。没有清除任何既有业务失败状态。
实验缩短停止宽限至 2 秒，未模拟生产 90 秒等待，也未在生产进程上执行终止操作。

## 仍需保留的验收边界

Python 3.10.20 下的 B/C1/C2、lane、schedule、provider 与文档扩大回归已完成：182 passed，37.16 秒。
测试服务限制 CPU 200%、内存 2 GiB、任务数 512；PrivateNetwork、ProtectSystem、ProtectHome 开启，
仅新临时根允许写入。依赖为 pandas 2.3.3、pyarrow 23.0.1、pytest 8.4.2 等，冻结清单另存验证目录。
同环境候选/基线全仓 make check 均因 600 秒上限终止，doctor 与 compile 已完成。
基线在测试执行末段被终止；候选进度到达 100%，但失败报告输出阶段被终止，没有自然结束的汇总/退出回执。
所以不能声明两次 make check 完整完成，也不能声明完整的 Linux 全仓零新增失败。

候选全新 pytest cache 记录 47 个失败节点，随后将这 47 项逐个在 C1 基线同环境执行：
47 failed，11.62 秒；两份失败节点集合完全一致（无未复现项）。这是已记录失败的有界归因，
不是用子集替代完整全仓对照，更不能与上批 macOS 90 个失败直接比较为“修复了 43 个”。

| 已记录失败 | 数量 | 已核实原因 |
| --- | --- | --- |
| planning/traceability | 19 | P3-STRAT-003-B-R1 缺少 governing LLD work-item row |
| legacy dual-lane 测试 | 27 | 测试冻结 polars 1.43.0，本次按宽版本范围解析为 1.44.2；运行时门禁拒绝 |
| industry inspection mock | 1 | unavailable mock 不接受调用方 json_limit 参数 |

独立 planning 命令也复现同一个缺 LLD 行问题。未修改其治理范围、放宽冻结运行时规则或重启旧 dual-lane 路线。
生产环境漂移与可复现验证配置归入 META-OPS-ENV-001，需角色级锁定配置，不能将“满足宽依赖范围”当成全部冻结契约的兼容性证明。

只重置了新建测试 unit 的失败状态。最终这些 unit 均已卸载/不活动，MainPID=0，cgroup 清空；
原业务 failed unit 未改。临时验证根保留约 1.3 GiB，包含精确源码、环境及原始日志，供复现，未删除材料。
本批评审方式仍为同一助手的执行/反向审查/验收阶段分离，不是独立审批。
独立评审、采集节点依赖一致性、批准代码逐节点部署、日期/包/校验和回执、回滚演练及 10 个交易日观察仍未完成。
Base V2.1 + Daily、CN01→OCI DWD 镜像、PIT、日期和 READY 门禁均未改变。
