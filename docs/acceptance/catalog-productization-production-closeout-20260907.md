# Catalog 产品化生产收尾 — 2026-09-07

| 字段 | 结果 |
| --- | --- |
| Work item | `P3-DICT-002` / #232，子项 #228–#231 |
| Disposition | PASS / ACCEPTED，限已声明的目录与静态加工血缘范围 |
| Governing design | `../design/data-indicator-catalog-productization.md` |
| Implementation audit | `catalog-productization-implementation-audit-20260907.md` |
| Runtime revision | `10301cdad7fed730072442da7921d24d0de65018` |
| Previous production | `d47abf8544e9612b75dd77ab2c166a9c75abfda2` |

## 发布与生产证据

实现先经 `quanthus_git_commit` 提交，确认工作区干净后通过 `quanthus_git_push` 快进推送
到 origin/main。随后由 `quanthus_production_deploy` 执行带前版本条件的 dry-run 和正式
API/Web 成对部署；未调用Worker发布、研究任务、数据库迁移或数据修改工具。

```text
API: quanthus-platform-kernel:git-10301cd
sha256:ba8661b06b4859e718802ba77359ecfcfbe5f9cfa908dd84111a4aa5cb4a140c

Web: quanthus-platform-web:git-10301cd
sha256:f8704b876bf0c30ceece102289279b7844234bd84a4e171be41d9a65da7a635e

health: 200 / healthy
ready: 200 / postgres=true
API/Web: healthy / running
```

公网 `https://quanthus.oci.061718.xyz` 核查：

```text
/catalog -> 200；新“查清含义、口径、来源和用途”文案存在
/ready -> ready / postgres=true
/api/platform/v1/catalog/summary（无token）-> 401
/api/platform/v1/catalog/items/dwd_field%3Adwd_security_bar.close/relations（无token）-> 401
/openapi.json -> 4个Catalog路径，全部只有GET
```

生产身份绑定的是上述部署与镜像结果。本次没有提取生产用户凭证，不将受控浏览器GET
测试描述为真实生产登录或PostgreSQL认证读查询。

## 目录与图身份

```text
builder: data-indicator-catalog-builder-v1.2
snapshot: sha256:3dc10fc68d3b612ebd6c4a611aa4745830a2712ba0e7bc446aef28d74b0e5737
catalog entries: 664
internal maintenance gaps: 10（不进入业务summary与首页）
DWD tables / fields: 33 / 524
Chinese shared field terms: 201
graph nodes: 829（含目录、逻辑来源、投影和加工节点）
processing edges: 315（输入→步骤、步骤→输出分段计数）
DWD tables with declared dataset-level sources: 33
```

确定性快照校验在提交前实际执行通过。API组合在启动时重建目录并校验该冻结清单，不维护
另一套可手工修改的公式或数据表。

## 工程与审计结果

完整 `make check`：1584 passed / 7 skipped，另有doctor、编译与离线规划检查PASS。
7项跳过为原有未挂载专家产物或未配置PostgreSQL测试环境，未计为通过。

Web lint、正式构建和18项测试PASS；Chromium桌面1440px、窄屏390px及匿名入口3项PASS。
图与卡片使用真实目录查询实现支撑的受控GET，测试覆盖搜索、折叠、图展开、重聚焦、
全局下钻、键盘操作、无页面异常和无页面横向溢出。

Catalog严格类型检查PASS。全库TypeScript的11项原有错误通过HEAD只读重放确认为基线，
涉及旧测试隐式any与Worker环境类型；未混入本轮修复，也不声称全库类型检查通过。
Python Ruff、shell语法与diff空白检查PASS。

已修复实际浏览器暴露的登录态首屏不一致及全量回归暴露的工单LLD登记遗漏。业务审计
确认成交额不是换手率、指数点位不是每股价格、源单位未知不猜、概念相关不等价、静态
依赖不代表实际消费、指标窗口不代表完整管道准入。

## 结论与上限

A–D有据完成，父工单可最后关闭。当前交付不改变原数仓数据、Feature公式、Facts数值、
策略/专家映射、PIT证明范围或交易权限。全局33表来源覆盖不是524字段全量端到端血缘；
未登记部分仍明确显示“未登记”，不得推断没有依赖。只更新API/Web，不重跑已有研究。
后续收尾提交只同步交付状态和文档，不需要重复构建已验收运行镜像。
