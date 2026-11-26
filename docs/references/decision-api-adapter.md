# Decision API 适配器参考（issue #755）

* 状态：已交付研究资产（P11 v2 专案线 G0/G1 前工程准备，离线契约零外呼）
* 代码：`src/quanthus/decision_api/`（types / backends / replay）
* 测试：`tests/unit/test_decision_api.py`（30 项，全部离线固件）
* 授权在线烟测：`scripts/decision_api_smoke.py`（单批次 3 问，一次调用）

## 1. 两套现存方言与翻译关系

| 维度 | OpenAI GA 方言（内部规范） | OpenRouter alpha 方言 |
| --- | --- | --- |
| 端点 | `POST /v1/decisions` | `POST /api/alpha/decisions` |
| 证据字段 | `input`（文本或消息数组） | `state`（字符串或字符串/图像部件混排列表） |
| 问题容器 | 列表（1–400），可选 `name` | 字典，键即问题名（必须命名） |
| 二元原语 | `predicate` | `noul` |
| 分类原语 | `choice` + `choices[{value,description}]`（value 可为字符串或布尔） | `choice` + `criteria{键:描述}`（键只能是字符串） |
| 量表原语 | `score` + `levels[{label,description}]` | `score` + `criteria[描述]`（无标签概念） |
| 响应容器 | `answers` 列表（按问题顺序） | `answers` 字典（按问题名） |
| 量表响应 | `score` + `probabilities[{label,value,probability}]` | `score` + `probabilities{"索引":概率}` |
| 视觉原语 | 无 | `box`（本适配器未实现，无 GA 对应物） |

翻译是有损点显式化的：OpenRouter 方言无法携带布尔选项与量表标签，适配器在请求构造期就 fail-closed（`DECISION_OPENROUTER_BOOL_CHOICE` / 标签从请求侧重建）；未命名问题在 OpenRouter 方言下直接拒绝（`DECISION_OPENROUTER_REQUIRES_NAME`），保证响应可按名复原顺序。

## 2. 已实测基线（2026-11-27，gw.72live.com）

| 事实 | 证据 |
| --- | --- |
| `google/gemini-decision-1` 完整兼容 OpenAI GA Schema（三原语、校准概率、置信度、usage） | 实测 200 返回；单批 3 问约 1.6–2.0s、300–424 input tokens、output tokens 恒 0 |
| 消息数组输入、混合批次、布尔选项、无名问题（响应 `name: null`）全部可用 | 适配器烟测 `sha256:9d607582…`（`artifacts/p11-decision-adapter-755/`） |
| `typesafe/jev-1.13/1.14` 在该网关是路由判别器存根，`/v1/decisions` 与 `/v1/systemone` 均不可用 | 网关 400 原文「jev-1.x is not a model of TypeSafe Jev」 |
| `/v1/systemone` 是 OpenRouter 方言入口（`choice/noul/score`，无 `predicate`） | 网关 400 原文列举支持类型 |
| OpenRouter 真实密钥本机不可用，Jev 联调未执行 | `openrouter-alpha` 后端以离线固件验收 |

## 3. 消费侧集成契约

1. **问题构造**：用 `PredicateQuestion / ChoiceQuestion / ScoreQuestion` 声明指令与选项；单批最多 400 问，同一 evidence 的多问共享一次 input tokens 计费。金额分桶等定量前置在 Python 侧完成后再写入 input（对齐 PRD v1.1.2 REV-03）。
2. **后端选择**：`OpenAIDecisionsBackend`（GA 方言端点，当前唯一实测可用路径）或 `OpenRouterDecisionsBackend`（OpenRouter 直连 Jev，待密钥资格化）；两者输出同一组 `PredicateAnswer / ChoiceAnswer / ScoreAnswer / DecisionRefusal`。
3. **逐问容错**：单问 `refusal` 不阻断同批他题；消费侧按名取用（`response.require_named` 对缺失/拒绝 fail-closed）。
4. **离线零调用**：回放后端按 canonical 请求 SHA256 命中封存响应，任何 evidence/问题/模型偏差都是 miss 并 fail-closed（`DECISION_REPLAY_MISS`）；录制后端在授权在线窗口产出封存夹具。
5. **预算归集**：`response.usage` 暴露 input/output/cached tokens 与 cost（OpenRouter）；适配器不做自动重试、不做费用决策，超时与计费策略归消费侧（v2 设计 §3.2：`≤60s/T`、首轮禁重试）。
6. **隐私红线**：适配器不检查也不添加账户数据；消费侧在构造 input 前必须剔除持仓数量、资金与委托详情。

## 4. 非目标与已知限制

* 不实现 `box` 视觉定位、音频/视频部件、OpenRouter `assets` 复用映射、提示缓存断点调优；图像输入与 `input_json` 走通用部件透传（见 §5）。
* `score` 响应在 OpenRouter 方言下的标签从请求侧重建；若后端乱序返回索引，按索引对位而非标签。
* 烟测脚本的本地 config.toml 密钥解析仅为维护者开发机便利，不构成部署形态；正式运行显式注入 `QUANTHUS_DECISION_API_KEY`。

## 5. 多模态与文档输入（2026-11-27 补测）

### 5.1 两套方言的多模态覆盖

| 输入类型 | OpenAI GA 方言 | OpenRouter alpha 方言 | 适配器处置 |
| --- | --- | --- | --- |
| 文本 | `input_text` / 纯字符串 | 字符串或字符串部件 | 双向直通 |
| 图像 | `input_image`（base64 data URL 或公开 HTTP(S) URL，detail 档） | `image_url` 部件，另有可复用 `assets` 映射（适配器未用） | GA 直传；alpha 自动翻译为 `{"type":"image_url","image_url":{"url":...}}` |
| 结构化 JSON | `input_json`（≤32 层 / ≤1 万值 / ≤256KiB） | 无对应物 | alpha 侧 fail-closed（`DECISION_OPENROUTER_INPUT_PART_TYPE`），不退化为字符串拼接 |
| 音频 / 视频 | 不支持 | `input_audio` / `video_url` 部件（按供应商适配表而异） | 本适配器未实现，GA 无法承载 |
| PDF / 任意文件 | 不支持（无 file 类型、无 file_id） | 不支持 | 两条方言的通用 Schema 都没有原生 PDF/文件通道 |

### 5.2 PDF 的工程处置原则

通用 Schema 在两套方言下都不承载 PDF，这不是适配器缺陷而是协议边界。P11 消费侧（对齐 v2.3 L3 文本边界与 PRD v1.1.2）应在上游完成确定性处理后再进入判别：

1. **文本化路径（首选）**：PDF 文本抽取产物（如巨潮 Markdown 摘要）以 `input_text` 或 `input_json` 送入，附 `truncated / extraction_status` 元信息；抽取器版本与核 PDF SHA 的审计绑定留在消费侧证据链，与适配器无关。
2. **栅格化路径（版式语义必需时）**：将关键页渲染为 PNG 后走 `input_image`；成本按图像 token 计，远高于文本。

### 5.3 图像实测事实与校准警示

* `google/gemini-decision-1` 实测接受 base64 `input_image`（2026-11-27：120x80 测试图，1297 input tokens / 2.76s 返回）。
* **像素级定向问答不可靠**：对一张左红右蓝渐变图，模型以 0.84 置信度错判为左蓝右红；对角线方向判别 0.84 命中。结论：图像通道可用于版式/存在性粗判，像素级方向与细节问答必须配合独立真值校验或弃用，高置信度不等于正确性。
* `input_json` 实测 200（155 input tokens / 0.96s），结构化证据（指标、分桶结果）可不经字符串拼接直接送入，与 PRD 定量前置原则一致。
