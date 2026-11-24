# p11.paper-replay-request.v1 — 历史回放请求合同

Schema: `p11.paper-replay-request.v1`（`paper_replay.load_replay_request` 严格校验）

## 用途

第一阶段 2026-10 历史模拟体验：对固定 P11 规则在 Base 开市日逐 T 运行真实
scan/feature/expert/veto 链，仅工程回放，不评价策略表现。

## 必填字段

- `schema_version` = `p11.paper-replay-request.v1`
- `purpose_mode` = `historical_replay`（其他拒激活）
- `permitted_account_use` = `personal_simulation`
- `scenario_id`：回放账本命名空间 `historical-replay:{scenario_id}`
- `package_id` + `manifest_sha256`：父 Base 绑定（standard-base-2.5.1-8cd966be87fc）
- `market_dates`：要回放的 T 列表（升序，必须全部在 open_sessions 内）
- `open_sessions`：该 Base 权威开市日清单（日历成员子集，升序）。
  T+1 = 清单中 T 之后第一个开市日；缺失即 fail-closed。
- `signal_window`：可选 `{from, through}`；只有落在窗口内的 market_dates
  才是信号日。唯一例外是显式声明的 final_day_policy.market_date，它在
  窗口外按 T-only 回放；其余窗口外 market_date 一律拒绝，不做修剪。
- `member_evidence` + `required_member_ids` + `source_members`：
  成员自述时钟（knowledge_at/visible_at 原样保留，不改写）。required 必须
  全部被 source_members 供应。
- `calendar_rows` / `listing_rows`：H1 信封原样字段。
- `securities`：H1 `securities[]` 同构；回放只取每 bar ≤T 且在
  open_sessions 的部分（61 窗口是回执合同，面板 warmup 使用全部 ≤T bar）。
- `exclusions`：#737 排除元信息 `{security_code, session_date?}`；
  session_date 缺省 = 全程排除。逐 T 在信封前过滤并记录，不读 #737 内容。
- `account`：`account_id` 必须 `replay-` 前缀（隔离账本）；字段同
  AccountFactsView。
- `policy`：同 AdvicePolicy 字段；`decision_cutoff/published_at` 由回放
  逻辑时钟生成（T 21:30+08:00），`eligible_at` = T+1 09:30，
  `expires_at` = T+1 15:00。
- `facts_template`：batch/source/calendar/price/intake 字段；intake_*
  为源声明批次时刻（as-observed）。
- `run_id`：作业运行标识。
- `action_coverage`：可选 `{default_state, registers, securities, window_evidence}`；
  按 P11-OCTOBER-AS-OBSERVED-v1 与 P11-P10-ACTION-REGISTER-EVIDENCE-v1，
  空 actions[] 不得读作无除权。`default_state` 只能为 `unknown`；
  `registers` 为具名覆盖名录（coverage_id/version/sha256/venue_id/
  event_types，仅定位用）；`securities` 绑定券的
  security_id/listing_id/venue_id 身份；`window_evidence` 为
  (券, 信号日, scan_two_session|returns_61_session) 记录，须携带
  verified_none|verified_event 的 event_state、已登记 coverage_id 与
  已哈希成员 sha256。证据不全/未核/unresolved/身份不符的券按
  replay_action_coverage_unknown 在信封前隔离新增评估；held 持仓仍按其
  自有证据进入正式 held 专家。

## 时钟边界

- 源声明时刻（member/calendar/listing/bar 的 *_at）逐字保留，绝不改写。
- 逻辑决策坐标 = T 21:30 Asia/Shanghai；`next_session_open` 来自 open_sessions。
- 实际读取/运行时刻只进 `p11.paper-replay-day.v1` 的 `replay_observed_at`。
- 输出等级恒为 `ENGINEERING_REPLAY_AS_OBSERVED`，只进隔离 replay 账本。

## P12 预览

`preview_replay_plan(raw_request)` 是元信息入口，P12 在读任何物理 raw
之前先调用：校验请求并返回逐日的 signal/final_display_only 计划、权威
bar 窗口、排除与 coverage 隔离清单。它只绑定声明身份，不读 Base/市场行；
收件方按自身固定清单复核身份与实际过滤证据。

## 禁止

不读物理 Base（P12 读取），不评价胜率/回报，不从 T+1 高低价挑成交，不改
动态阈值，不作正式 H1/daily/live 证据。
