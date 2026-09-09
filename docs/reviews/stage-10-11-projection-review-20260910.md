# Stage 10/11 Projection Contract 三视角评审

| Field | Value |
| --- | --- |
| Review scope | `P3-STAGE-002` projection semantics and qualification plan |
| Provenance | 单一助手按量化、短线操作、基金三个独立视角分别审查；不是三位真人专家签字 |
| Inputs | `P3-STAGE-001-H` mixed closeout; `P3-STRUCT-005` Support Evolution Facts acceptance |
| Decision | `PASS_FOR_FORMAL_PROJECTION_CONTRACT` |
| Claim ceiling | 只批准 development Projection Contract；不批准 primary-stage、LabelCard、日更或交易 |

## 1. 量化视角

**结论：PASS。**

当前数据已经稳定证明 Stage-10 context 和 Stage-11 rebound predicate 的可重复计算，但没有证明
必须把它们立即压成一个互斥的单值分类。把 `{10,11}` 作为候选命题集合显式输出，比为了满足
`primary_stage` 接口而提前制造仲裁规则更稳健。

量化侧要求：

- 5日、2%、5%继续作为版本化 projection policy，不进入 Support Evolution Fact identity；
- Stage-10 confirmation ref必须绑定准确的事实 revision；
- Stage-11只消费 prior Stage-10 confirmation，不做当前深度二次证明；
- 新旧 predicate 必须逐观察点一致；
- qualification 只重放 immutable artifacts，不增加一次市场结果访问；
- primary-stage compression 单独立项，不能在本里程碑内顺手决定。

## 2. 短线操作视角

**结论：PASS，且层级模型比两个互斥布尔更符合交易语言。**

“结构已经坏掉”和“坏掉以后正在反抽”本来就可以同时为真：前者是背景，后者是局部动作。
如果为了显示 Stage 11 而删除 Stage-10 failure context，业务会失去最关键的因果锚点；如果要求
Stage 11 当天重新满足 -2% 深度，则反抽越接近支撑反而越容易被错误取消。

短线侧接受：

```text
Stage-10 context
└─ Stage-11 rebound condition
```

但不接受本轮直接决定“反抽结束后一定回10”或“一定转12”。这些需要新的下行推进/反抽结束
事实与状态转移证据。

## 3. 基金 / 中期结构视角

**结论：PASS。**

Stage 10 更适合作为中期的结构风险背景，Stage 11 是这个背景中的恢复/反抽描述。组合或风控
消费者未来很可能希望同时知道：

- 关键支撑是否已经被确认破坏；
- 当前是否存在明显反弹；
- 是否已经真正 reclaim 原支撑。

因此保留两个命题比只给一个阶段数字的信息量更高。基金侧反对在没有消费者需求和外样本
证据时提前做单值压缩。

## 4. 综合决定

三视角共同批准以下边界：

1. Stage 10 = persistent structural-failure **context proposition**；
2. Stage 11 = nested current rebound **condition proposition**；
3. `{10,11}` candidate set 是合法输出；
4. `primary_stage` 在本里程碑必须始终为 null；
5. reclaim/censor 清除当前候选，但不回写历史确认；
6. Stage-11 predicate 允许随当前 rebound 变化而 true/false，这不自动定义 primary-state transition；
7. 资格复现必须只读两份已接受 F9 artifact，不重新打开 DWD；
8. 通过后只能称为 **Formal Projection Contract**，不能称 QUALIFIED LabelCard。

这次评审不修改 Stage 5/6/7 的 `INCONCLUSIVE_PARKED` disposition。
