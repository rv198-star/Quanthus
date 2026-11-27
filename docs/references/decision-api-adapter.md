# Decision API 适配器参考（issue #755）

* 状态：已交付研究资产（P11 v2 专案线 G0/G1 前工程准备，离线契约零外呼）
* 代码：`src/quanthus/decision_api/`（types / backends / providers / replay）
* 测试：`tests/unit/test_decision_api.py`、`tests/unit/test_decision_providers.py`、`tests/unit/test_decision_transport.py`（117 项，全部离线；传输层测试只打本地环回模拟服务器）
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
3. **多供应商链（推荐入口）**：`config/decision-providers.v1.json` 声明有序供应商表（base_url / type / endpoint_path / model / api_key_env / enabled / timeout），`build_priority_backend(load_provider_chain(PATH))` 构造 `PriorityBackend`。当前默认优先级：gemini-decision-1（网关）→ gpt-6-luna（OpenAI 直连）→ jev-latest（网关）；OpenRouter alpha 直连以禁用条目预登记。密钥只进环境变量，永不进版本化文档。
   * **failover 语义（窄于重试，精确集合）**：仅传输失败（DNS/TCP/TLS/reset）、超时与获准瞬态 HTTP 集合 `{408, 409, 425, 429, 500, 502, 503, 504}` 切换下一供应商；其余一切确定性 HTTP 状态（全部其他 4xx 与 5xx，如 400/401/403/404/418/422/520/524/529）与 schema 错误立即 fail-closed——换供应商不改变结果类，只会放大成本。**分类只由 HTTP 状态决定**：适配器在任何非 2xx 上保留状态码原样抛出 `DecisionHttpError`，不解析错误体；空体、HTML、JSON 数组或 JSON 对象都不能改变确定性状态的处置（离线矩阵测试覆盖 16 个状态 × 4 类响应体）。
   * **确定性状态在响应头完成时锁定（R3 修订）**：非 2xx 响应头一旦完整读入，该次尝试的状态与停止/切换语义即固定；错误体只在剩余预算内尽力读取，迟到、缺席或不可解析一律降级为空体，永远不会把已锁定的 401 改记为 timeout 或触发错误切换（离线链级测试：头即送 401、体迟到 0.9s、首跳配置超时 0.5s，结果为 http_401、第二供应商零命中）。2xx 的成功体则是严格的：必须在预算内完整读完，否则为 timeout。
   * **在线预算接口（适配器提供机制，消费侧持有预算）**：消费侧为每个预算窗口构造一次 `DecisionDeadline.after(秒)`（单调时钟绝对截止），跨批次复用同一对象传入每次 `decide()`；链在每次尝试前把 socket 预算截断到剩余时间，剩余不足 0.25s 即停止派发并降级（`DecisionProviderExhaustedError.deadline_stopped` 为真）。**同一截止覆盖从派发到返回的完整逻辑调用（R3 修订）**：传输是 raw socket 最小 HTTP/1.x 客户端，connect/TLS/发送/响应头/正文每一块（含 HTTP/1.0 关闭式正文的 EOF 等待与 chunked 逐块）逐次按剩余预算重新武装并复查，慢响应头、滴注正文与关闭式正文都撞同一堵墙；在截止后才拼装完成的完整应答以 `DecisionLateAnswerError` 拒收、永不交付——供应商确实处理了请求，因此拒收保留已解析 usage 作为计费证据。离线实测：0.6s 预算下慢响应头在 0.6s 硬超时（R2 的 http.client 实现 1.11s 才返回），0.65s 截止下 EOF 迟于截止的关闭式应答记 timeout 而非答案（R2 实测 0.84s 交付成功）。`max_attempts` 可截断链深；单供应商内部无自动重试。
     **R5 时钟不可延展化（R4 独审修订）**：
     - 传输层收到的不是相对余量样本，而是共享截止的**绝对时刻**（再被本后端更短的配置超时截断，时钟永不延展）。取样之后、进入真实传输之前的任何调度停顿都不可能把预算重新加到当前时钟上：过期则在拨号或 POST 之前以 `wire_dispatched=False` 的超时失败，停顿未跨窗时正常派发不受影响。
     - 首次解析的快速失败或空结果走标准 `socket.create_connection` 回落复现原失败；该入口内部二次解析不可中断，因此整体放入 daemon 线程并按剩余预算 join——调用线程恒在墙内返回。线程若被遗弃时仍在解析或拨号，其最终产生的连接在完成即刻关闭废弃、永不进入 HTTP 层，过期 POST 恒为零；预算内复现的解析失败透传原 OSError，保持 failover 分类不变。
     - 链在答案与逐次凭证全部组装完成后、最后交付前复查同一截止：组装期间的停顿跨窗时答复以 `DecisionLateAnswerError` 拒收，该次尝试改记 `late_answer` 并保留已解析计量作为计费证据；预算内的组装停顿照常交付。
   * **成本上限透明**：链长 N 即单次逻辑请求最多 N 次调用，消费侧的 `≤60s/T` 与 `≤$0.30/信号日` 预算必须按最坏情形核算。`reserve_worst_case_usd(spec, max_input, max_output)` 与 `chain_worst_case_usd(chain, ...)` 给出逐供应商与整链最坏预留；预留入参先经合法性校验，负数或非整数 token 上限是类型化 schema 错误而非负数报价（R3 修订）；任一供应商未声明单价时返回 None，消费侧必须按不可预留处理并阻断该配置的在线派发，直到单价在版本化配置中钉死。超时、传输失败与畸形错误体的尝试按未知用量处理，同样按最坏情形归账。**报价前的价格合法性覆盖两条入口（R5 修订）**：版本化 JSON 加载与代码直接构造的 `DecisionProviderSpec` 一视同仁——负价、布尔、NaN/Infinity 与溢出转换的巨型整数都在形成任何报价前以类型化 `DECISION_RESERVATION_PRICE` 拒绝，零价是合法的明示免费、有限正价正常报价；`DecisionDeadline` 直接构造与 `after()` 同享同一类型化数值边界，不可表示的时刻（非数值、布尔、非有限、溢出巨型整数）一律 `DECISION_DEADLINE_RANGE`，不再泄漏裸 OverflowError。
   * **审计溯源——每次逻辑调用一份独立凭证（R3 修订合同）**：每次 `decide()`——无论成功或失败——都交出一份独立的 attempts 元组：成功响应在 `response.attempts`，一切链上抛出的错误（含 `DecisionProviderExhaustedError`、schema 错误与确定性 HTTP 错误）在 `error.attempts`。该元组在本次调用的局部状态内构建，**复用同一链实例的并发批次不可能互相覆盖对方的计费证据**；`backend.last_attempts` 仅作诊断镜像保留，账本必须读取逐次凭证而非镜像。凭证覆盖预算内全部供应商：出网尝试逐次记录 provider_id/model/dialect/结果（answer / timeout / late_answer / transport / http_<status> / schema_error）/耗时/用量快照；从未出网的供应商以 outcome `not_dispatched` 落账（skip_reason ∈ deadline / deterministic_stop / answered_upstream / local_validation），这是其零派发零计费的正向证明。用量四态：`metered`（线上完整 usage 且正用量或正费用，按上报计）、`zero`（线上完整 usage 显式全零，或未达网面 wire_not_reached / 未派发 never_dispatched 的确知零费）、`unknown`（usage 缺席 usage_object_absent、空对象 usage_object_empty、字段不全 usage_incomplete、或响应从未成形 no_wire_response——一律按最坏情形预留）。缺失或不全的用量**不是**零费；负 token/负 cost/溢出数值在严格 schema 校验期即拒绝，不可能进入账本。2xx 后 schema 解析失败也会先落记录再 fail-closed；非 2xx 错误体内形态完好的 usage 会作为失败计费证据保留（metered），缺席或不可解析则为 unknown。全部供应商失败抛 `DecisionProviderExhaustedError` 并携带完整尝试历史。应答模型以响应 `model` 字段原样可见。
   * **构建期 fail-closed**：启用供应商缺 key 环境变量、链内混用方言（回放夹具按单一方言封存）都在构造时拒绝，不留到运行时。
4. **逐问容错**：单问 `refusal` 不阻断同批他题；消费侧按名取用（`response.require_named` 对缺失/拒绝 fail-closed）。
5. **离线零调用**：回放后端按 canonical 请求 SHA256 命中封存响应，任何 evidence/问题/模型偏差都是 miss 并 fail-closed（`DECISION_REPLAY_MISS`）；录制后端在授权在线窗口产出封存夹具。failover 录制与回放按**原始请求 digest** 键控——回放复现的是该逻辑请求的既判答案，不重演供应商选择。回放与录制都按声明方言构造，封存条目的方言标签与后端方言不符时构建期或命中期 fail-closed（`DECISION_REPLAY_DIALECT_MISMATCH` / `DECISION_RECORD_DIALECT_MISMATCH`）；回放答案与线上同样逐题绑定当批请求的数量、位置、原语与选项/索引，概率分布按声明序输出，线上与离线答案可直接相等比较。
6. **预算归集**：`response.usage` 暴露 input/output/cached tokens 与 cost（OpenRouter）；信号日额度（`≤$0.30/信号日`）与节奏（`≤60s/T`、首轮禁重试）归消费侧持有，适配器提供本节第 3 条的截止、留证与最坏预留机制。消费侧集成时的可用边界：(a) 每预算窗口一个共享 `DecisionDeadline`，跨批次复用；(b) 每次在线派发前用 `chain_worst_case_usd` 校验该批次最坏预留仍在信号日余额内，不完整（unpriced）即阻断；(c) 派发后按每次调用的独立凭证（`response.attempts` / `error.attempts`）中的 metered/zero/unknown 逐次归账，unknown 按最坏值计，not_dispatched 为零费正向证明；(d) `deadline_stopped` 为真时按降级路径处理，不补发。
   并发与重跑的统一预留：所有共享同一窗口的批次使用同一个 `DecisionDeadline`（单调时钟，挂钟调整与挂起都不能延长它）；每一次独立派发——包括并发批次与人工重跑——都按整链最坏情形另行预留，重跑不复用首次预留。窗口内实际归账以 metered 证据为准、unknown 按最坏值计，超出预留即停止派发并按降级路径处理。
7. **隐私红线**：适配器不检查也不添加账户数据；消费侧在构造 input 前必须剔除持仓数量、资金与委托详情。

## 4. 非目标与已知限制

* 不实现 `box` 视觉定位、音频/视频部件、OpenRouter `assets` 复用映射、提示缓存断点调优、GA 官方的逐问图像 instructions（`instructions` 数组形态，可给每个问题单独配图）；图像输入与 `input_json` 走通用部件透传（见 §5）。
* `score` 响应在 OpenRouter 方言下的标签从请求侧重建；若后端乱序返回索引，按索引对位而非标签。
* 烟测脚本只接受显式注入的环境变量凭据（单端点模式 `QUANTHUS_DECISION_API_KEY`，链模式逐条目的 `api_key_env`），无任何本地配置回退；带 bearer 的端点被约束在获准 HTTPS 主机白名单（gw.72live.com、api.openai.com、gw.mindthus.com），版本化供应商链一律要求 HTTPS base_url。

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

## 7. 密钥部署与运行环境（2026-11-27）

| 环境变量 | 内容 | 当前部署位置 |
| --- | --- | --- |
| `QUANTHUS_DECISION_KEY_OPENAI` | platform.openai.com API key（gpt-6-luna 直连） | CN02 `~/.quanthus/decision-api.env`（chmod 600，`.bashrc` 顶部受保护引用，登录与 ssh-exec shell 均已验证加载）；本机开发按需在 shell 临时注入 |
| `QUANTHUS_DECISION_KEY_MAGPIE` | gw.72live.com 网关 bearer token（gemini/jev） | 同上（从 CN02 本机 codex config 提取注入，未出网） |
| `QUANTHUS_DECISION_KEY_OPENROUTER` | OpenRouter key（alpha 直连，未资格化） | 未部署，对应条目默认禁用 |

密钥轮换原则：版本化配置只引用环境变量名；轮换时更新各节点 env 文件即可，配置文档与代码不变。已在对话明文出现的 key 建议在稳定运行后轮换。

链条在线核验（2026-11-27，CN 本机）：`--chain config/decision-providers.v1.json` 一次通过，首选供应商 gw72-gemini-decision-1 应答 5.0s，390 input tokens；封存夹具 `sha256:c625daad…`（`artifacts/p11-decision-adapter-755/smoke-20261127-chain.replay.json`），回放往返一致。failover 路径由离线脚本化传输测试覆盖（transport/timeout 与精确获准瞬态集合内状态切换、其余确定性状态不切换、exhaustion 携带完整历史），并经真实传输对本地模拟服务器的 16×4 状态码×响应体矩阵、分段滴注、慢响应头、关闭式正文与跨批共享截止测试复核；链模式烟测与单端点同享获准 HTTPS 主机白名单校验（R3 修订，离线进程级测试覆盖）。
