# HF 私有维护证据

本说明适用于 Loop3 1.2.0。源码、Issue、PR 和小型控制请求在 GitHub；完整快照、receipt、delivery、日盘点及验收报告在私有 HF dataset。本地 SQLite 继续拥有执行、去重和恢复事实。

## 数据与访问

当前专用数据仓为 `rv192/loop3-maintenance-evidence`，私有，部署时通过 `LOOP3_HF_REPO_ID` 指定。目录按项目、环境、证据类别和内容摘要组织：

```text
wff/oci-dev/receipt/<sha256>.json
wff/oci-dev/delivery/<sha256>.json
quanthus/cn02-dev/receipt/<sha256>.json
aiwork/oci-dev/receipt/<sha256>.json
controller/cloud/report/<sha256>.json
```

HF 文件保存原 JSON 对象。引用同时绑定 dataset、固定 commit revision、路径和原始 UTF-8 内容 SHA256。原 receipt_digest、evidence_digest、report_digest 仍按各自 schema 计算；这两类摘要分别校验文件字节和协议对象。

GitHub 只追加 `<!-- maintenance.evidence-pointer.v1 -->` 小索引，包含项目、环境、请求身份、角色、HF 引用和原生摘要。云端先验证索引，再凭鉴权读取固定 revision 的原文，核对身份、文件 SHA256 和协议摘要。私有 resolve 链接需要鉴权读取工具。

## 凭据与部署

Key 由私有 operator 配置的凭据文件或进程环境提供。仓库只记录变量名，不记录值；Key 不进入命令行参数、GitHub 评论、HF 证据或运行日志。文件以 owner-only 权限保存。

现有接收清单 job 可增加以下私有设置；源代码与凭据分开分发：

```json
{
  "evidence": {
    "repository": "rv192/loop3-maintenance-evidence",
    "credentials_file": "/private/operator/hf.env",
    "proxy_file": "/private/operator/hf-proxy.env"
  }
}
```

凭据文件接受 `HF_TOKEN` / `LOOP3_HF_TOKEN` 等明确别名。接收器仅解析允许的赋值，不执行 shell；代理文件只加载允许的代理变量。WFF 的验证仍使用 Python3.12，接收器兼容条件沿主合同执行。

## 持久发布与恢复

1. 原结果和业务 attempt 先写入本地 SQLite。
2. 向 HF 写入内容寻址文件，使用 `parentCommit` 处理多个项目并发写入；固定 revision GET 读回一致后继续。
3. 小索引在本地 `evidence_references` 中不可变保存，再发布到 GitHub。
4. GET 读回 GitHub 索引，并再次鉴权读取 HF 原文，核对身份和摘要。
5. HF 不可用时保留原 outbox；重试复用原结果。提交确认丢失时先查询相同内容，GitHub 发布确认丢失时复用已保存索引。

HF dataset 必须保持 private。未知后端、权限失败、内容不一致或引用越界会留下可恢复失败，详细正文不会回退发布到 GitHub。

## 云端工具

`scripts/maintenance_evidence_tools.py` 是受限 stdio MCP，适合登记到已有节点的 MCP 连接。它提供三个工具：

- `read_evidence(pointer)`：仅读取登记的三仓项目证据，验证索引、固定版本、文件摘要和对象身份。
- `store_report(project, report)`：保存已签摘要的每日或验收报告，返回持久小索引。跨仓报告使用 controller。
- `read_report(pointer)`：按报告索引读回并核对。

工具不提供 shell、任意 URL 或项目业务操作。Key 留在服务端。服务私有配置包含 dataset、凭据/代理文件和独立 state_dir；state_dir 只保存已返回报告引用的稳定缓存。

```sh
python3 scripts/maintenance_evidence_tools.py --operator-config /private/operator/hf-evidence-tools.json
```

原始 stdout 专用于 MCP JSON-RPC。每天先读取上一轮报告的 HF 索引，再取新现场，判断后将完整报告写入 HF 并读回；GitHub 仅保留状态、关键决定和报告索引。

## 兼容与验收

旧版本已经发布的评论、原生回执和本地账本保持原记录。旧无 HF 配置的历史适配器回归仍受测；当前三仓登记新请求使用 HF 后端和1.2.0。

这次改变了存储与鉴权读取语义。部署后用新 batch/challenge 重新验收三环境，检查去重、后续消费、恢复、HF 权限与固定版本、只发小索引，以及 Web 端实际鉴权读回。未通过的环境沿原账本恢复，新的正式验收结果以该批证据为准。
