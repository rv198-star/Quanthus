# R11原U04专家到资金预约：待独立复核的合成工程候选

新增窄application/expert_entry.py，调用原FormalStrategyRunner.evaluate_entry、既有ExpertEntryEligibility.from_formal_strategy_decision与FundingBridgeService.enter。原专家评分/否决、数量/费用计算、资金/事务核心与适配器保持原文件。调用方提供无专家资格的候选，helper从原聚合结果取得资格/上限/摘要；预填资格被拒绝。

真实合成公共链：原stable输出trial_entry/0.20；500000现金和权益、五桶100000、储备0、fraction0.30、测试比例0.04/0.08/0.20、价格10/lot100、原费用0.0003/min5/0.01产生1900股、费用5.70、pending19005.70。预约后现金仍500000，已验证公共SQLite journal包含原专家decision digest与完整entry intent。真实buy后现金480994.30；原命令在后来caller和冷重开后仍返回原quantity/resultdigest，changed-content冲突。

13新增验证覆盖stable、fragile、riskveto、missingrisk、未采用候选、证券/未来expertcutoff/source/coverage/mode/预填资格/风险snapshot时点、真实成交后的原命令回放及冷建。与原R11/U05/专家/平台回归合计106 passed/2.89s。49具名只读文件size/SHA一致，全部既有tracked文件无diff。改动Ruff/format和Python3.12target两文件strictmypy通过，Python3.10语法解析通过；未对新候选运行最终CN02完整检查，留在独立复核之后。旧两周期305的9466/35只覆盖前置源码。

测试比例是合成oracle，不激活历史账户参数案。当前为原U04合成接线候选，原A02专家具体用途、C13多日语义、P10原窗/PIT、U06完整比较/U07整包终审/U08日常使用继续按原清单交付；R12搁置。

原包005于UTC00:09:50冻结，硬截止00:44:50（含模型、发布和打包），35min/240scriptCPU/1GiB/64MiB。跨线程派发被自动审批拒绝后，Sol在独立临时工作树直接推进；源分发仍走GitHub，DS原目录未写。当前脚本/模型/控制完整CPU与费用未统一实测，记unknown_not_zero，保留所有先前失败/成本。证据位于本地continuationrelay/sol-expert-entry-008/public-actuals.json、readonly-verification.json、focused.log、mypy-py312.log与maintenance-exact-base.log。
