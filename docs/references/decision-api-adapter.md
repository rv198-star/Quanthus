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
| `typesafe/jev-latest` 在 `/v1/decisions` 完整兼容 GA Schema（三原语、布尔选项、`input_json`），响应回传真实版本 `jev-1.14.0` | 适配器原样序列化载荷实测 200：三原语批次 419 input tokens / 0.73s；布尔选项批次 200（choice 回传 JSON true/false）；smoke 封存 `sha256:48773352…` 回放往返一致 |
| `typesafe/jev-1.13` / `jev-1.14` 版本化名称是存根，`/v1/decisions` 拒绝服务；可服务入口是 `typesafe/jev-latest` | 网关 400 原文「TypeSafe Jev has no OpenAI-style API for /decisions」（版本化名称）；`/v1/models` 清单 74 个模型不含任何 jev/decision 条目，路由判别模型不在清单暴露 |
| `/v1/systemone` 是 alpha 方言入口，适配器翻译层输出与其线上形态逐字段吻合 | 适配器 `request_to_openrouter_alpha` 原样载荷实测 200（jev-latest 0.69s、gemini-decision-1 1.49s）：answers 字典按名索引、choice 概率字典按选项名、score 概率字典按数字索引并附 `legend` 回显 |
| 早期手工探测脚本误用单数 `instruction` 字段（官方为复数 `instructions`）导致 jev 侧翻译丢字段，曾误判 jev 不可用；适配器代码始终使用正确的复数字段 | 对比探测：同批次仅字段名差异时 400「Noul question must have criteria or instructions」对 200 |

## 3. 消费侧集成契约

1. **问题构造**：用 `PredicateQuestion / ChoiceQuestion / ScoreQuestion` 声明指令与选项；单批最多 400 问，同一 evidence 的多问共享一次 input tokens 计费。金额分桶等定量前置在 Python 侧完成后再写入 input（对齐 PRD v1.1.2 REV-03）。
2. **后端选择**：`OpenAIDecisionsBackend`（GA 方言端点；网关上 gemini 与 jev-latest 均已实测）或 `OpenRouterDecisionsBackend`（alpha 方言；网关 `/v1/systemone` 与 OpenRouter 官方 schema 同构，适配器翻译载荷已在 systemone 实测 200）；两者输出同一组 `PredicateAnswer / ChoiceAnswer / ScoreAnswer / DecisionRefusal`。
3. **逐问容错**：单问 `refusal` 不阻断同批他题；消费侧按名取用（`response.require_named` 对缺失/拒绝 fail-closed）。
4. **离线零调用**：回放后端按 canonical 请求 SHA256 命中封存响应，任何 evidence/问题/模型偏差都是 miss 并 fail-closed（`DECISION_REPLAY_MISS`）；录制后端在授权在线窗口产出封存夹具。
5. **预算归集**：`response.usage` 暴露 input/output/cached tokens 与 cost（OpenRouter）；适配器不做自动重试、不做费用决策，超时与计费策略归消费侧（v2 设计 §3.2：`≤60s/T`、首轮禁重试）。
6. **隐私红线**：适配器不检查也不添加账户数据；消费侧在构造 input 前必须剔除持仓数量、资金与委托详情。

## 4. 非目标与已知限制

* 不实现 `box` 视觉定位、音频/视频部件、OpenRouter `assets` 复用映射、提示缓存断点调优、GA 官方的逐问图像 instructions（`instructions` 数组形态，可给每个问题单独配图）；图像输入与 `input_json` 走通用部件透传（见 §5）。
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

通用 Schema 在两套方言下都不承载 PDF，这不是适配器缺陷而是协议边界。需要区分两层事实：

* **Gemini 模型族在聊天补全 API 上确实原生支持 PDF**——这一点用户认知正确；但本网关的两个路由判别模型（`google/gemini-decision-1`、`typesafe/jev-latest`）在聊天端点拒绝服务，网关原文「only decides a routing group's model and effort; it holds no conversation」，因此无法借道聊天端点验证或利用该能力。
* **Decisions/System One 协议本身无文件通道**：OpenAI 官方 GA 文档明确「Files, audio, and item references aren't supported」（2026-11-27 核对），网关实测四种文件部件变体（`input_file file_data/file_url`、`input_document`、`file`）全部 400「System One has no counterpart」。`/v1/models` 清单中的 pdf modality 描述的是同族聊天模型的能力，不延伸进判别协议。

P11 消费侧（对齐 v2.3 L3 文本边界与 PRD v1.1.2）应在上游完成确定性处理后再进入判别：

1. **文本化路径（首选）**：PDF 文本抽取产物（如巨潮 Markdown 摘要）以 `input_text` 或 `input_json` 送入，附 `truncated / extraction_status` 元信息；抽取器版本与核 PDF SHA 的审计绑定留在消费侧证据链，与适配器无关。
2. **栅格化路径（版式语义必需时）**：将关键页渲染为 PNG 后走 `input_image`；成本按图像 token 计，远高于文本。

### 5.3 图像实测事实与校准警示

* 图像输入是**逐模型能力而非方言能力**：`google/gemini-decision-1` 实测接受 base64 `input_image`（120x80 测试图约 1200–1300 input tokens / 2.7s 返回）；`typesafe/jev-latest` 拒绝图像，网关 400 原文「System One takes images only for Cloudflare's Clef and Google's gemini-decision-1」。PDF 栅格化路径因此只能用 gemini-decision-1（或同能力的 Clef 类模型），jev 仅限文本/JSON 证据。
* **像素级定向问答可靠性不稳定**：左红右蓝硬边缘图判别正确（0.82），左红右蓝渐变图以 0.84 置信度错判为左蓝右红，对角线方向判别 0.84 命中。同一模型的置信度无法区分对错样本——图像通道可用于版式/存在性粗判，像素级方向与细节问答必须配合独立真值校验或弃用。
* GA 官方的逐问图像 instructions（`instructions` 数组内嵌 `input_image`）在本网关**不被支持**，网关 400 原文「System One has no counterpart for in a question's instructions」——该能力缺口是上游协议实现边界，即使适配器未来实现数组形态也无法在此网关使用。
* `input_json` 实测 200（155 input tokens / 0.96s），结构化证据（指标、分桶结果）可不经字符串拼接直接送入，与 PRD 定量前置原则一致。

## 6. OpenAI 官方直连路径（2026-11-27 实测通过）

| 事实 | 证据 |
| --- | --- |
| `POST https://api.openai.com/v1/decisions` 已 GA 且适配器**零改动实测通过**：`OpenAIDecisionsBackend` 仅替换 URL 与 model（`gpt-6-luna`）即完整工作 | smoke 脚本 `--base-url https://api.openai.com --model gpt-6-luna` 200 返回；中文新闻三问批次 580 input tokens / 0 output tokens / 1.1s；封存夹具 `sha256:4265ec20…`（`artifacts/p11-decision-adapter-755/smoke-20261127-openai.replay.json`），回放往返一致 |
| 官方 schema 与适配器 canonical 方言逐字段同构 | 三原语批次 200（predicate 1.0 / choice positive+confidence 1.0 / score 0.99+confidence 0.93）；**布尔选项实测 200**（choice 回传 JSON true/false，官方文档未记载但与适配器设计一致）；`input_json` 200（175 tokens）；`input_image` 200 且硬边缘方向判别正确（probability 1.0，190 tokens） |
| 官方逐问图像 instructions（数组形态）实测可用 | 2026-11-27 直连 200（191 tokens）——官方路径补齐了网关缺失的能力，适配器当前仅支持字符串 instructions，数组形态为已登记的实现缺口 |
| 官方 PDF 拒绝与文档一致 | 直连 400 原文「Supported values are: 'input_text', 'input_image', and 'input_json'」——文件通道在官方同样不存在，上游文本化/栅格化原则不受路径选择影响 |
| 访问凭证是 platform.openai.com 的 API key，按量计费（$0.10/1M input tokens、输出免费） | Owner 提供 key 后本机实测通过；网关不代理 OpenAI 系模型的 decisions 端点（400），两条路径互不替代 |

直连与网关的取舍：直连校准质量在实测样本上更锐利（方向判别 1.0 对网关 0.82/0.84 错判样本），逐问配图可用，输出 token 恒零（计费只有 input）；网关路径提供 gemini 与 jev 双模型交叉校准能力。消费侧选型属于 G1 后的专家链设计决策，适配器对两条路径同等支持。
