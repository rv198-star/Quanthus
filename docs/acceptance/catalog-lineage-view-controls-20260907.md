# Catalog 血缘画布视图控制复验 — 2026-09-07

| 字段 | 值 |
| --- | --- |
| Work item | `P3-DICT-002-C` / #230 |
| Disposition | PASS / ready for release audit |
| Scope | 仅 Web 血缘画布视图管理；不修改目录节点、边、proof level、数据或执行权限 |
| Prior production | `0c671f92e58834d49cdb182d3c58793d052d9814` |

## 1. 变更

用户要求血缘关系图不再随图内容继续撑高页面，并增加常用图层/视图管理能力。本次把
`LineageOverview`、字段焦点图和表级上下文图共用的 Canvas 改成固定高度视口：桌面
520px，窄屏 420px。内容在视口内部滚动/平移。

统一工具栏提供：

- 缩小与放大，范围 45%–180%；
- 当前缩放百分比，点击恢复 100%；
- “适配”按画布和图几何范围计算缩放；
- “居中”优先回到当前 `center` 节点，无中心时回到整图中心；
- 空白区域鼠标/触控拖拽平移。

节点点击和键盘 Enter/Space 导航保持原行为。所有操作仅改变浏览器视口，不修改静态
血缘图内容或查询接口。

## 2. 浏览器实测

`web/tests/e2e/catalog-productization.spec.ts` 使用真实目录查询实现支撑 GET fixture，在
Chromium 实测：

- 1440×1000：画布高度精确 520px；
- 390×844：画布高度精确 420px；
- 放大后缩放百分比变化；100% 恢复精确；
- 适配与焦点居中按钮可操作；
- 放大产生溢出后，空白区域拖拽使内部 `scrollLeft` 发生变化；
- 页面本身无横向溢出；
- 匿名访问仍只展示登录入口。

结果：`3 passed`。

## 3. 工程检查

- Catalog snapshot check：PASS，664 entries / 10 internal gaps，业务图语义摘要未变化；
- Catalog scoped TypeScript：PASS；
- Web ESLint：PASS；
- Web build + Node tests：18/18 PASS；
- 聚焦 Python/文档检查：68 PASS；
- 完整仓库门禁：使用项目虚拟环境执行 `make check`，1590 PASS / 7 个既有环境 skip。

第一次直接使用系统 `python3` 执行 `make check` 时因该解释器未安装 pytest 而在测试启动前
停止；随后使用项目已验收虚拟环境执行同一门禁完整通过。该环境问题不是代码失败。

## 4. 语义审计

变更未触碰 `src/quanthus/catalog_lineage.py`、Catalog API、DWD、Feature/Label/Facts
定义或 Worker。缩放、平移、适配和居中仅在 `web/app/catalog-lineage.tsx` 本地状态中实现。
原字段 proof boundary、精确 technical ID 和表级上下文限制保持不变。

结论：`P3-DICT-002-C` 可重新关闭；`P3-DICT-002-D` 进入提交、生产发布和烟测。
