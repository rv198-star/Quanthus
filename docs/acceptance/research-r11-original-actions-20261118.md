# R11原动作到现有路径的闭合

# R11 018 action binding audit

**唯一结论：** 原P2的REDUCE已是“订单请求数量全量成交、持仓可保留余仓”的公共能力，但305共享funding/whole-account bridge只接受整lot卖出；因此不能直接组成同版资金/整户口径，需要一项窄adapter：把已确认的reduce订单从余量和成本中扣除，保留未卖lot，直到后续余仓退出并满足结算条件，且不引入部分订单成交或通用OMS。

|ID|action|control path|shared candidate|reuse / gap|
|---|---|---|---|---|
|A1|entry buy|runner.evaluate_entry/evaluate_entry_control -> formal_p2_replay TRIAL_ENTRY allocation -> engine.run_buy_session|same decision path may feed FundingBridgeService.enter then submit_buy for one confirmed whole buy|DIRECT_DECISION_PATH; candidate entry/account operation exists for one full lot buy；decision-to-intent/confirmed-fill adapter remains part of U06/U08 integration, not a new capital algorithm|
|A2|held-position reduction with the reduction order fully filled|position_risk.py:249-271 -> formal_p2_replay.py:800-831 calculates requested reduction quantity -> engine.run_sell_sequence -> engine._complete_sell partial_position_sell_fill; residual quantity and proportional cost remain|funding_bridge.py:1177-1194 requires report.quantity == lot.remaining_quantity and line 1252 sets remaining_quantity=0; service lot selection also requires equality at 1499-1508|MATCHING/MATH_ONLY；shared funding/account lot cannot carry residual quantity and proportional cost after a completed reduction order|
|A3|remaining-position full exit after a reduction|formal_p2_replay uses runtime.ledger residual quantity; engine._complete_sell full branch settles at lines 1073-1186|existing shared bridge can sell a full lot and settle when flat, but only after a correct residual lot exists|DEPENDS_ON_A2；the residual lot produced by A2 must preserve identity, chronology, quantity and cost so later full exit can use the existing full-lot path|
|A4|veto or invalid-data does not create a new entry|runner.py:488-506 combines system, PositionRisk veto and constraints; all-scoring abstention maps to NO_ACTION/block-new; formal_p2_replay only allocates TRIAL_ENTRY|no funding/account mutation is required; only TRIAL_ENTRY becomes an entry intent|DIRECT_DECISION_PATH；none for action semantics|
|A5|rejected, unfilled, carried or terminal order under p2_daily_open_no_partial_fill|engine.run_buy_session lines 226-275 and _no_fill_result 1319-1407; run_sell_sequence 589-950 emits cancelled/rejected/carried/filled states|shared bridge records only confirmed posted facts; it does not need to emulate a partial order fill, while the P2 matcher remains authoritative for terminal and carried statuses|DIRECT_P2_MATCHING; event projection needed for U08；no offer here; distinguish whole-order no-fill/carry from a partial-position reduction|
|A6|settlement and late fee|engine full-exit settlement lines 1073-1186|funding_bridge.settle_cycle lines 1519-1579 and apply_late_fee lines 1581-1631 provide atomic shared-cycle settlement/late-fee semantics|DIRECT_AFTER_FLAT；settlement remains correctly blocked while A2 residual quantity exists; no new settlement algorithm|

## Minimal engineering contract

- one confirmed REDUCE fill keeps P2 no-partial-fill order semantics
- decrement residual quantity/cost without zeroing the lot
- retain lot/cycle/source/occurrence identity and audit journal
- block settlement while quantity remains
- allow later full exit and existing settlement/late-fee reuse
- no OMS, market lookup, threshold change or forced sale

## Audit boundary

The hard deadline passed before a public probe, full input rehash, or source-file digest computation. Those items are recorded as not run. No source, shared relay/TPlan/blueprint/issue/adoption/PR, market, native, remote, training, capital, daily, or production write was performed.

## Sol完成的来源核验

Root已完成冻结输入全成员size/SHA复核与12个精确源文件摘要，均与接受305原生检查的相应文件逐字节相同；原P2公开减仓测试已包含在该完整检查。DS2未运行的新probe继续标NOT_RUN。本包只读确认已有路径与窄缺口，不宣称候选资金桥已有余仓能力。详见root-action-source-verification.json。
