# R8固定范围资格验收

基线：ed80dddd1679aa4a3fb3286aea0378a09958abc2；任务#658/#659；里程碑22。
Owner按计划启动R8，起算2026-11-13T06:37:48Z；首2h关口08:37:48Z、整批8h上限14:37:48Z。当前进入原NO_GO分支，未启动正式资金比较。

## 输入与独立方法

复用R7三证券2020年4月63条封存工程复核。manifest SHA256：
`e22c9caa24396b3acf51aaacd18a7f0f26156c6b079647ca4f78d3dc07f19d4a`；rows：
`d2d3b1dadaba476d98ad6295c46ce94564e5e763c7ef361e25822288c76ee7fa`。
精确四文件、READY、所有payload SHA/字节及63唯一键和原决定摘要均已认证。未取新DWD、未来结果或2022+数据。

R7实际双专家卡为1.0.1，semantic digest为
`sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`。
初次字面核对使用旧1.0.0配置；随后确认本次使用的完整entry policy与minimum score和1.0.1相同，单独保留policy-equivalence回执。实际群体、来源和时钟资格仍按1.0.1与新用途独立判断。

主线冻结selection_inspect.py后执行标准库Decimal核对，两名只读独立角色另行认证输入、复算边界和核对原源码。这是选择阶段代数可行性检查，不执行改变守卫的策略变体。

## 两项选择

| 选择 | 已验证事实 | 处置 |
|---|---|---|
| RAW_MARGIN_COMPAT_ENTRY | 现有兼容专家active时输出clip(breakout_margin,-1,1)、confidence1；原等权聚合下63条提议动作变化0 | 停止新增接线/资金比较 |
| LEGACY_INTENSITY_ON_R7_ENTRY | 旧量能合格、短完成episode与R7持续price-only语义不同，最接近3键又失败于P4必要条件 | 停放未准入映射及资金比较 |

13个stable trial_entry均为600519/2020-04-14..30，RSI14为75.586266..90.881448>75，vol20均未超过0.50；原STRATEGY risk veto正确阻新。63条的系统守卫仍因无P4机会资格阻新。

## 三键必要条件排除

| 证券/日期 | prior20已知/未知 | turnover比率上界 | 原门槛 |
|---|---:|---:|---:|
| 000001/2020-04-30 | 20/0 | 1.234691963944 | 1.50 |
| 300750/2020-04-28 | 18/2 | 1.388145214630 | 1.50 |
| 300750/2020-04-29 | 19/1 | 0.729737083938 | 1.50 |

P4要求T turnover / median(previous20 eligible turnovers)>=1.50。合格turnover严格为正；仅在证明中将未知项置0，得到median下界及最有利比率上界。三个上界仍严格失败。未知无效时原资格本就拒绝；这里未生成或修改源行，也未将R7 volume均值量比代替P4 turnover中位数。

原P4还要求quiet(T-1)→active(T)、22连续会话、listing>=120、factor/action边界、隔离、行业集中度、split及精确population。三键已被必要条件排除，停止为它们继续时钟/行业/资金适配。

## 既有能力与历史边界

FormalP2已有5×100000、30%试仓、3..40会话和完整资金/持仓/退出链。R4可复用session/action/P2账本；其单资金、动量排名、次日close退出的replay_arms不同于同用途正式B。P4回溯投影有独立经济可用时间坐标并保留raw knowledge；未来新总体需精确绑定新源。

旧P3-STRUCT-002连续C1-C0收益差-0.00270718、回撤差-0.00934550、2个entry变化，原materiality=false；prefer_continuous来自粗表达对连续表达，保持其真实范围。R4/R6、Head2及原持仓结论和全部曝光/成本保留。

## 结果等级与交付

两项具名选择NO_GO；经济执行blocked/NOT_RUN_CONDITION_NOT_MET，收益效果not_evaluated，原组合保留，R9未触发。蓝图保持模块状态与工程范围不变；追加CP658资格处置及路由记录，下一笔改为合格事件触发，旧R7证据继续绑定原工程底座。
独立资格/可行性审阅已通过；元数据检查、普通main交付、HF/CN02留存和资源收尾按实际回执补记。完整make check仅在证明非docs/planning闭包与已通过的1409执行版逐字节相同时复用；相关元数据和精确base检查重跑。

## 固定资格结果包

私有HF `rv192/quanthus-research-r1@2b1ab15dcedb10b8414ec9f96bacc3fb5dbeae8e`，目录
`runs/RESEARCH-R8-COMBINATION-001/RESEARCH-R8-COMBINATION-001-T1/A1`。14文件/41,286字节全部按固定版本回读并逐字节一致；传输用时11.18秒。manifest SHA256为`a787c216e79995c8cf6d77ce3a822a8d2cb911ca4ea12bfa67df9d5a89b33c20`。源码检查与main交付另记，固定包只承担本次资格处置证据。

## 本地验收绑定

CN02固定候选a31e7164的90项元数据测试通过（pytest3.99秒），两个精确ed80追加历史检查与维护选择通过（selected=[]）；检查工作树干净。日志SHA256为`e931842b948c5bbc119dd6d8b735c8ba7fe6c9abc886385b0fe34df96a751251`。
2350个非docs/planning文件及mode与1409执行版逐项一致，R7完整9231passed/35skipped原日志SHA已在CN02重新认证。因此按既有规范复用完整检查，后继文档索引只作精确base复核。
RAW_MARGIN选择是接口映射的选题级代数诊断，未注册第三位专家或改变正式双专家拓扑，未形成获准的C；LEGACY_INTENSITY也保持未准入公式诊断。

## 实际代码与生命周期交付

PR660已普通合入main `b142a9dcfffa443e5c335048bddde9ae2fdb4410`，父提交为ed80与已审c3eca，树与候选相同。658/659、Feature651、R8里程碑22已实际关闭，R9保持条件待启动。按各自工单追加真实观察checkpoint，原历史和模块分项状态保留。
关闭账本后继仅docs/planning，复用已认证功能闭包和检查，精确base/实际工单再核。临时worktree/分支只保留到本关闭记录合入即释放，最终资源回执存CN02长期根。
