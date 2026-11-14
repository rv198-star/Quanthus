# 研究复核 MVP v0

本版本把 R6–R9 已验收的能力组织为一个固定历史研究入口，供 Owner 和研究负责人
复核判断、定位模块问题、比较研究版本。验收见
[R6–R9 结果审计](../acceptance/research-mvp-v0-audit-20261114.md)。

## 可用组成

| 组件 | 当前用途 | 版本与结果 |
| --- | --- | --- |
| R7 三算子与双专家报告 | 查询日期/证券、原值、专家依据、约束和最终动作 | 63条固定历史记录；57条三算子共同可用；最终63条no_action |
| R9 S02研究报告与模型 | 查看同案误报/漏报、翻转、原始分数和恢复模型 | 200步v0；NO_UPDATE；评价24例；研究retain-v0，业务R0保留 |
| R6/R8研究处置 | 复用方向选择和资格诊断，避免重复低价值试验 | R6原rank1停放；R8两选择NO_GO，资金比较未运行 |
| R10执行细则 | 从实际问题进入有预算的更新与用途验收 | [分阶段合同](../design/research-r10-feedback-v1.md)；当前为规划 |

R7的结构与量能是解释字段；持仓风险仍用原RSI/波动。R9模型尚在研究层，专家状态
为pending/ABSTAIN、probability=null。完整学习决策MVP按#540原合同继续验收。

## 交付目录

```text
quanthus-research-mvp-v0/
  README.md                  使用说明
  MANIFEST.json              每个成员的大小与SHA256
  r7-core/                   严格四文件核心包
  r7-report.html             已渲染的可查询报告
  r9-s02-report.md            同案版本比较
  r9-model/                  v0、预测承诺、诊断与比较记录
  result-audit.md             本轮结果审计
  r10-execution-spec.md       下一批执行细则
```

直接打开`r7-report.html`，搜索`000001 2020-04-10`，展开专家和来源；R9报告列出
24个评价案例及8个翻转。模型原始分数用于固定研究比较。

在已安装依赖的Quanthus仓库根目录，用Python3.10+冷读取核心包：

```bash
PYTHONPATH=src python3 -B scripts/research_r7_review.py query \
  --package /absolute/path/to/quanthus-research-mvp-v0/r7-core \
  --manifest-sha256 sha256:e22c9caa24396b3acf51aaacd18a7f0f26156c6b079647ca4f78d3dc07f19d4a \
  --security 000001 --date 2020-04-10
```

正确结果为matched=1、returned=1。查询会检查manifest、READY、精确文件集和payload。
从原8文件HF发布恢复核心包的操作见[R7读取手册](research-r7-review.md)。

## 固定身份与恢复

- 本次审计计算源码：`0f27fd50ccd2fabdfef7f5d9b77663077ee88c17`；本次改动为文档/规划。
- R7原发布：私有`rv192/quanthus-research-r1@f3e38f6296a9b25d20ffaaf03e62ee55907144a5`，
  `runs/RESEARCH-R7-MULTIOP-001/RESEARCH-R7-MULTIOP-001-T1/A2`。
- R7核心manifest：`e22c9caa24396b3acf51aaacd18a7f0f26156c6b079647ca4f78d3dc07f19d4a`；
  HTML：`cd96c8cdc3ab3970e9389315c60c7f92e9760b3659ba4882b10048dfb5e84f44`。
- R9原发布：私有同库`084437145701d9d6fd4e9b049deb6cb723924f53`，
  `runs/RESEARCH-R9-CALIBRATION-001/RESEARCH-R9-CALIBRATION-001-T1/SSH-REFERENCE-ASSETS-S02-001`。
- R9 v0文件：`706a6a578a713310730ff54f8b9ce0d80b6fca74c5ee4f6bb68bd9349d87256d`；
  fit manifest：`0c04b0d22939165a0e84a47edb1acb9a3a0d89d07e27f088f0cc7f5246655a1c`。

完整参考、输入身份、冷构造及旧失败从原固定发布恢复。MVP包只组织上述已发布字节与
本轮说明。R9 fit manifest中的sealed文件都随包提供；它的原始源/参考绑定继续指向原发布。

本次整理包已发布到私有资料库：
[`rv192/quanthus-research-r1@94e03fbf`](https://huggingface.co/datasets/rv192/quanthus-research-r1/tree/94e03fbf8a635c94fb10f3c4634836257565ae19/deliveries/research-review-mvp-v0/20261114)，
完整revision为`94e03fbf8a635c94fb10f3c4634836257565ae19`，目录
`deliveries/research-review-mvp-v0/20261114`。28文件、3,208,515字节已全量回读一致；
`MANIFEST.json` SHA为`1fb5bc37f0f790545b70db7bb19a47d95500d285cc20330fe51d578b058161c2`。
CN02保留目录为`/srv/agentdock/quanthus-ops/research-mvp0-audit-20261114/retained/quanthus-research-mvp-v0`。
包内包括独立审阅、原结果复算及本轮验证回执，数据和模型文件保持原字节。

## 使用反馈

每次实际复核只记录：一个事件键、预期回答的问题、已看到的依据、实际作出的决定、
仍需人工拼接的字段及耗时。反馈按“显示/读取缺陷、识别误差、专家映射、机会/数据资格”
定位责任；按R10预登记标准选一个问题，不在封存评价集上继续调参。
