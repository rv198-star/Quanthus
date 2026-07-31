"use client";

import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BadgeCheck,
  Braces,
  Check,
  ChevronRight,
  CircleHelp,
  Copy,
  Download,
  FileJson,
  FlaskConical,
  GitCompareArrows,
  Layers3,
  Route,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import {
  type ChangeEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import demoArtifact from "@/public/data/attribution-demo.v1.json";
import type {
  AttributionClaim,
  AttributionWorkspace,
  MetricEffect,
} from "./types";
import {
  componentPublicationState,
  importWorkspaceDocument,
  isSyntheticFixture,
  workspaceSourceTrust,
} from "./artifact-envelope";
import {
  displayArtifactTerm,
  displayBoolean,
  displayComponentDescription,
  displayComponentLabel,
  displayConditionLabel,
  displayContextLabel,
  displayFormattedValue,
  displayInterventionLabel,
  displayMetricLabel,
  displayOperationLabel,
} from "./display-terms";
import { loadPublishedWorkspaceCatalog } from "./published-workspace-catalog";
import {
  createWorkspaceCatalogEntry,
  groupWorkspaceCatalog,
  removeWorkspaceCatalogEntry,
  upsertWorkspaceCatalogEntries,
  type PublishedReleaseReference,
  type WorkspaceCatalogEntry,
} from "./workspace-catalog";

type ViewId = "overview" | "modules" | "risk" | "evidence";

const demoWorkspace = demoArtifact as unknown as AttributionWorkspace;
const demoArtifactText = `${JSON.stringify(demoArtifact, null, 2)}\n`;

const views = [
  { id: "overview", label: "结论总览", icon: Layers3 },
  { id: "modules", label: "模块归因", icon: GitCompareArrows },
  { id: "risk", label: "风险与情景", icon: ShieldCheck },
  { id: "evidence", label: "方法与证据", icon: Braces },
] as const;

const capabilityRegistry = [
  ["baseline_replay", "基线重放", "可从精确清单复现策略结果"],
  ["component_map", "组件映射", "可分析到策略根以下的原生边界"],
  ["legal_intervention", "合法干预", "存在策略原生的移除或中和语义"],
  ["decision_trace", "决策轨迹", "可定位首次发生分歧的决策"],
  ["opportunity_trace", "机会轨迹", "可分析被拒绝或挤出的机会"],
  ["condition_map", "条件切片", "可按预先声明的状态或阶段分析"],
  ["training_replay", "训练重放", "可比较预先声明的再训练过程"],
] as const;

const classificationText: Record<string, string> = {
  beneficial: "有帮助",
  harmful: "有拖累",
  negligible: "影响很小",
  tradeoff: "有取舍",
  inconclusive: "证据不足",
  unoriented: "仅记录变化",
};

const statusText: Record<string, string> = {
  valid: "有效",
  invalid: "无效",
  unavailable: "不可用",
};

const evidenceText: Record<string, string> = {
  accounting: "账务归集",
  association: "相关关系",
  sensitivity: "敏感性分析",
  counterfactual_replay: "冻结历史全量重放",
};

const actionText: Record<string, [string, string]> = {
  no_strategy_action_synthetic_fixture: [
    "不产生策略动作",
    "合成证据只验证合同与界面",
  ],
};

const validationText: Record<string, string> = {
  baseline_reproduced: "基线已复现",
  paired_comparable: "成对比较一致",
  interaction_checked: "交互已检查",
  condition_checked: "条件切片已检查",
  stability_checked: "稳定性已检查",
  independent_evaluation_completed: "独立评估已完成",
};

const syntheticValidationText: Record<string, string> = {
  baseline_reproduced: "合成基线可重复生成",
  paired_comparable: "合成配对结构一致",
  interaction_checked: "合成交互结构已检查",
  condition_checked: "合成条件切片已检查",
  stability_checked: "合成稳定性结构已检查",
  independent_evaluation_completed: "合成独立评估结构已完成",
};

const componentKindText: Record<string, string> = {
  strategy_root: "策略根",
  native_expert: "原生专家",
  feature_group: "特征组",
  native_router: "状态路由",
  policy_risk_gate: "风险门",
  native_allocator: "资金分配",
};

function shortId(value: string, start = 12, end = 8) {
  if (value.length <= start + end + 3) return value;
  return `${value.slice(0, start)}…${value.slice(-end)}`;
}

function displayDate(value: string) {
  return value.replaceAll("-", ".");
}

function classTone(value: string) {
  if (value === "beneficial" || value === "valid") return "positive";
  if (value === "harmful" || value === "invalid") return "negative";
  if (value === "tradeoff") return "warning";
  return "neutral";
}

function displayEvidenceType(value: string, synthetic: boolean) {
  if (synthetic && value === "counterfactual_replay") return "合成全量重放";
  return evidenceText[value] ?? "未登记的证据类型";
}

function workspaceComponentLabel(
  workspace: AttributionWorkspace,
  componentId: string,
  fallback?: string,
) {
  const nativeLabel = workspace.adapter.components.find(
    (component) => component.component_id === componentId,
  )?.label;
  return displayComponentLabel(componentId, fallback ?? nativeLabel);
}

function workspaceDisplayTitle(entry: WorkspaceCatalogEntry) {
  return isSyntheticFixture(entry.workspace)
    ? "归因机制虚构样例"
    : entry.workspace.title;
}

function workspaceDisplaySubtitle(entry: WorkspaceCatalogEntry) {
  return isSyntheticFixture(entry.workspace)
    ? "人为构造的六个月重放，只用于验证归因合同与界面。"
    : entry.workspace.subtitle;
}

function workspaceDisplayPeriod(entry: WorkspaceCatalogEntry) {
  const claim = entry.workspace.claims[0];
  if (!claim) return "无已发布声明";
  return `${displayDate(claim.method_evidence.period_start)} 至 ${displayDate(
    claim.method_evidence.period_end,
  )}`;
}

function MetricDirectionIcon({ classification }: { classification: string }) {
  if (classification === "beneficial") return <ArrowUpRight aria-hidden="true" />;
  if (classification === "harmful") return <ArrowDownRight aria-hidden="true" />;
  return <Route aria-hidden="true" />;
}

function claimSentence(claim: AttributionClaim, synthetic: boolean) {
  const summary = claim.decision_summary;
  const evidence = displayEvidenceType(summary.evidence_type, synthetic);
  const classification =
    classificationText[summary.classification] ?? "未识别结论";
  if (summary.status !== "valid") {
    return `内核将这条声明标记为“${statusText[summary.status] ?? "未识别状态"}”，因此工作台不展示方向性结论。`;
  }
  const prefix = synthetic ? "虚构示例输出" : "内核结论";
  return `${prefix}：${displayComponentLabel(claim.subject_component_id, claim.subject_label)} 在这次${evidence}中表现为“${classification}”。`;
}

function StatusPill({
  value,
  synthetic = false,
}: {
  value: string;
  synthetic?: boolean;
}) {
  const label =
    synthetic && value === "valid"
      ? "结构校验通过"
      : statusText[value] ?? classificationText[value] ?? "未识别状态";
  return (
    <span
      className={`status-pill tone-${classTone(value)}`}
      title={label === "未识别状态" ? `原始代码：${value}` : undefined}
    >
      <span className="status-dot" />
      {synthetic && classificationText[value] ? `示例：${label}` : label}
    </span>
  );
}

function DigestValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copyDigest() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <span className="digest-value">
      <code title={value}>{shortId(value, 16, 10)}</code>
      <button
        type="button"
        className="icon-button icon-button-small"
        onClick={copyDigest}
        title={copied ? "已复制" : "复制完整摘要"}
        aria-label={copied ? "已复制摘要" : "复制完整摘要"}
      >
        {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </button>
    </span>
  );
}

function MetricComparison({
  effect,
  synthetic = false,
}: {
  effect: MetricEffect;
  synthetic?: boolean;
}) {
  return (
    <article className={`metric-card tone-border-${classTone(effect.classification)}`}>
      <header>
        <div>
          <p className="eyebrow">{synthetic ? "虚构示例指标" : "归因指标"}</p>
          <h3>{displayMetricLabel(effect.metric_id, effect.label)}</h3>
        </div>
        <span className={`metric-icon tone-${classTone(effect.classification)}`}>
          <MetricDirectionIcon classification={effect.classification} />
        </span>
      </header>
      <div className="metric-values">
        <div>
          <span>保留模块</span>
          <strong>{displayFormattedValue(effect.display.baseline)}</strong>
        </div>
        <ChevronRight aria-hidden="true" />
        <div>
          <span>执行干预</span>
          <strong>{displayFormattedValue(effect.display.intervention)}</strong>
        </div>
      </div>
      <footer>
        <StatusPill value={effect.classification} synthetic={synthetic} />
        <span className="effect-value">
          有利方向 {effect.display.oriented_effect
            ? displayFormattedValue(effect.display.oriented_effect)
            : "未定义"}
        </span>
      </footer>
    </article>
  );
}

function EmptyEvidence({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty-evidence">
      <CircleHelp aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}

function OverviewView({
  claim,
  synthetic,
}: {
  claim?: AttributionClaim;
  synthetic: boolean;
}) {
  if (!claim) {
    return <EmptyEvidence>该产物没有可展示的归因声明。</EmptyEvidence>;
  }
  const nextAction = actionText[claim.decision_summary.next_action_code];
  return (
    <>
      <section className="baseline-band" aria-labelledby="baseline-title">
        <div className="section-heading baseline-heading">
          <div>
            <p className="eyebrow">
              {synthetic ? "虚构示例基线" : "冻结策略基线"}
            </p>
            <h2 id="baseline-title">先确认我们在解释什么结果</h2>
          </div>
          <p>
            {displayDate(claim.method_evidence.period_start)} — {displayDate(claim.method_evidence.period_end)}
          </p>
        </div>
        <div className="baseline-metrics">
          {claim.decision_summary.primary_effects.map((metric) => (
            <div key={metric.metric_id} className="baseline-metric">
              <span>{displayMetricLabel(metric.metric_id, metric.label)}</span>
              <strong>{displayFormattedValue(metric.display.baseline)}</strong>
              <small>{synthetic ? "虚构数值" : "基线结果"}</small>
            </div>
          ))}
          <div className="baseline-metric baseline-status">
            <span>声明状态</span>
            <strong>
              {synthetic && claim.decision_summary.status === "valid"
                ? "结构校验通过"
                : statusText[claim.decision_summary.status] ?? "未识别状态"}
            </strong>
            <small>
              {displayEvidenceType(
                claim.decision_summary.evidence_type,
                synthetic,
              )}
            </small>
          </div>
        </div>
      </section>

      <section className="decision-section" aria-labelledby="decision-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              第一层 · {synthetic ? "示例摘要" : "决策摘要"}
            </p>
            <h2 id="decision-title">
              {displayComponentLabel(claim.subject_component_id, claim.subject_label)}
            </h2>
          </div>
          <div className="status-group">
            <StatusPill value={claim.decision_summary.status} synthetic={synthetic} />
            <StatusPill
              value={claim.decision_summary.classification}
              synthetic={synthetic}
            />
          </div>
        </div>
        <div className="plain-conclusion">
          <BadgeCheck aria-hidden="true" />
          <div>
            <strong>{claimSentence(claim, synthetic)}</strong>
            <p>
              下方差值均来自归因产物，不代表该模块“占有”了总收益中的某个比例。
            </p>
          </div>
        </div>
        <div className="metric-grid">
          {claim.decision_summary.primary_effects.map((effect) => (
            <MetricComparison
              key={effect.metric_id}
              effect={effect}
              synthetic={synthetic}
            />
          ))}
        </div>
      </section>

      <section className="explanation-strip" aria-label="证据边界">
        <div>
          <FlaskConical aria-hidden="true" />
          <span>
            <strong>
              {displayEvidenceType(
                claim.decision_summary.evidence_type,
                synthetic,
              )}
            </strong>
            {synthetic
              ? "只验证虚构数据下的合同链路"
              : "只说明这次冻结历史模拟中的变化"}
          </span>
        </div>
        <div>
          <ShieldCheck aria-hidden="true" />
          <span>
            <strong>查看原始验证标记</strong>
            证据等级由内核发布，查看器不升级
          </span>
        </div>
        <div>
          <Route aria-hidden="true" />
          <span>
            <strong>{nextAction?.[0] ?? "内核发布的下一动作"}</strong>
            {nextAction?.[1] ?? "当前动作没有登记中文说明"}
          </span>
        </div>
      </section>
    </>
  );
}

function ModulesView({
  workspace,
  selectedClaimId,
  onSelect,
  synthetic,
}: {
  workspace: AttributionWorkspace;
  selectedClaimId?: string;
  onSelect: (claimId: string) => void;
  synthetic: boolean;
}) {
  const unpublishedComponentIds = workspace.adapter.components
    .map((component) => component.component_id)
    .filter(
      (componentId) =>
        componentPublicationState(workspace, componentId) === "unpublished",
    );

  return (
    <section aria-labelledby="module-table-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">成对干预结果</p>
          <h2 id="module-table-title">模块不是按“贡献率”排名</h2>
        </div>
        <p>每行保留各自指标向量与证据状态</p>
      </div>
      <div className="table-shell">
        <table className="claims-table">
          <thead>
            <tr>
              <th>原生模块</th>
              <th>结论</th>
              <th>内核发布的主指标：保留 / 干预</th>
              <th>证据</th>
              <th><span className="sr-only">查看</span></th>
            </tr>
          </thead>
          <tbody>
            {workspace.claims.map((claim) => (
              <tr
                key={claim.claim_id}
                className={
                  selectedClaimId === claim.claim_id ? "selected-row" : undefined
                }
              >
                <td>
                  <strong>
                    {workspaceComponentLabel(
                      workspace,
                      claim.subject_component_id,
                      claim.subject_label,
                    )}
                  </strong>
                </td>
                <td>
                  <StatusPill value={claim.classification} synthetic={synthetic} />
                </td>
                <td>
                  {claim.decision_summary.primary_effects.length ? (
                    <span className="published-primary-effects">
                      {claim.decision_summary.primary_effects.map((effect) => (
                        <span key={effect.metric_id}>
                          <strong>
                            {displayMetricLabel(effect.metric_id, effect.label)}
                          </strong>
                          <span className="comparison-inline">
                            {displayFormattedValue(effect.display.baseline)}
                            <ChevronRight aria-hidden="true" />
                            {displayFormattedValue(effect.display.intervention)}
                          </span>
                        </span>
                      ))}
                    </span>
                  ) : "未发布"}
                </td>
                <td>{displayEvidenceType(claim.evidence_type, synthetic)}</td>
                <td>
                  <button
                    type="button"
                    className="icon-button"
                    title={`查看 ${workspaceComponentLabel(workspace, claim.subject_component_id, claim.subject_label)}`}
                    aria-label={`查看 ${workspaceComponentLabel(workspace, claim.subject_component_id, claim.subject_label)}`}
                    onClick={() => onSelect(claim.claim_id)}
                  >
                    <ChevronRight aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="unavailable-band">
        <AlertTriangle aria-hidden="true" />
        <div>
          <strong>明确不可归因的组件</strong>
          <p>
            {workspace.unavailable_component_ids.length
              ? workspace.unavailable_component_ids
                  .map((componentId) => workspaceComponentLabel(workspace, componentId))
                  .join(" · ")
              : "当前产物未列出。"}
          </p>
        </div>
      </div>
      <div className="unpublished-band">
        <CircleHelp aria-hidden="true" />
        <div>
          <strong>尚无已发布归因声明</strong>
          <p>
            {unpublishedComponentIds.length
              ? unpublishedComponentIds
                  .map((componentId) => workspaceComponentLabel(workspace, componentId))
                  .join(" · ")
              : "当前原生组件均已有声明或明确标为不可归因。"}
          </p>
        </div>
      </div>
    </section>
  );
}

function RiskView({
  claim,
  synthetic,
}: {
  claim?: AttributionClaim;
  synthetic: boolean;
}) {
  if (!claim) return <EmptyEvidence>该产物没有可展示的归因声明。</EmptyEvidence>;

  return (
    <>
      <section aria-labelledby="risk-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">第二层 · 原因分析</p>
            <h2 id="risk-title">
              {displayComponentLabel(claim.subject_component_id, claim.subject_label)}的风险与机会
            </h2>
          </div>
          <StatusPill value={claim.classification} synthetic={synthetic} />
        </div>
        {claim.reason_analysis.metric_effects.length ? (
          <div className="metric-grid">
            {claim.reason_analysis.metric_effects.map((metric) => (
              <MetricComparison
                key={metric.metric_id}
                effect={metric}
                synthetic={synthetic}
              />
            ))}
          </div>
        ) : (
          <EmptyEvidence>
            该声明没有发布风险或机会指标；工作台不会根据收益差自行拆分。
          </EmptyEvidence>
        )}
        <p className="analysis-guardrail">
          这里保留内核发布的开放指标向量；损失、机会成本和净收益不会在查看器中相加。
        </p>
      </section>

      <section className="analysis-section" aria-labelledby="condition-title">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">预声明条件切片</p>
            <h2 id="condition-title">在哪些情景下有效</h2>
          </div>
        </div>
        {claim.reason_analysis.condition_effects.length ? (
          <div className="condition-grid">
            {claim.reason_analysis.condition_effects.map((condition) => (
              <article className="condition-card" key={condition.condition_id}>
                <header>
                  <div>
                    <h3>
                      {displayConditionLabel(condition.condition_id, condition.label)}
                    </h3>
                  </div>
                  <span>{condition.sample_count} 个样本</span>
                </header>
                <div className="compact-metric-list">
                  {condition.metric_effects.map((metric) => (
                    <div key={metric.metric_id}>
                      <span>{displayMetricLabel(metric.metric_id, metric.label)}</span>
                      <strong>
                        {displayFormattedValue(metric.display.baseline)} →{" "}
                        {displayFormattedValue(metric.display.intervention)}
                      </strong>
                      <em className={`text-${classTone(metric.classification)}`}>
                        {metric.display.oriented_effect
                          ? displayFormattedValue(metric.display.oriented_effect)
                          : "方向未定义"}
                      </em>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyEvidence>该声明未发布条件切片，不能推断它在哪种市场状态下更有效。</EmptyEvidence>
        )}
      </section>

      <section className="analysis-section" aria-labelledby="interaction-title">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">成对交互检查</p>
            <h2 id="interaction-title">是否依赖其他模块共同工作</h2>
          </div>
        </div>
        {claim.reason_analysis.interaction_effects.length ? (
          <div className="table-shell">
            <table className="interaction-table">
              <thead>
                <tr>
                  <th>配对模块</th>
                  <th>指标</th>
                  <th>原始交互</th>
                  <th>有利方向</th>
                  <th>是否显著越过阈值</th>
                </tr>
              </thead>
              <tbody>
                {claim.reason_analysis.interaction_effects.map((effect) => (
                  <tr key={`${effect.partner_component_id}-${effect.metric_id}`}>
                    <td>
                      {displayComponentLabel(effect.partner_component_id)}
                    </td>
                    <td>{displayMetricLabel(effect.metric_id, effect.label)}</td>
                    <td>{displayFormattedValue(effect.display.raw)}</td>
                    <td>
                      {effect.display.oriented
                        ? displayFormattedValue(effect.display.oriented)
                        : "未定义"}
                    </td>
                    <td>{effect.material ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyEvidence>该声明未发布交互实验，单模块效果不能相加。</EmptyEvidence>
        )}
      </section>
    </>
  );
}

function EvidenceRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="evidence-row">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function EvidenceView({
  workspace,
  claim,
  synthetic,
  publication,
}: {
  workspace: AttributionWorkspace;
  claim?: AttributionClaim;
  synthetic: boolean;
  publication?: PublishedReleaseReference;
}) {
  const capabilities = new Set(workspace.adapter.capabilities);
  const registeredCapabilities = new Set(capabilityRegistry.map(([id]) => id as string));
  const extensionCapabilities = workspace.adapter.capabilities.filter(
    (capability) => !registeredCapabilities.has(capability),
  );
  const question = claim
    ? workspace.questions.find((item) => item.digest === claim.method_evidence.question_digest)
    : undefined;
  const baselineExperiment = claim
    ? workspace.experiments.find(
        (item) => item.digest === claim.method_evidence.baseline_experiment_digest,
      )
    : undefined;
  const interventionExperiment = claim
    ? workspace.experiments.find(
        (item) => item.digest === claim.method_evidence.intervention_experiment_digest,
      )
    : undefined;
  return (
    <>
      <section aria-labelledby="capability-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">适配器能力协商</p>
            <h2 id="capability-title">策略声明什么，内核才分析什么</h2>
          </div>
          <p>未声明能力只会缩小归因范围</p>
        </div>
        <div className="capability-grid">
          {capabilityRegistry.map(([id, label, description]) => {
            const available = capabilities.has(id);
            return (
              <div className={`capability-item ${available ? "available" : "absent"}`} key={id}>
                {available ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}
                <div>
                  <strong>{label}</strong>
                  <p>{description}</p>
                  <code>{id}</code>
                </div>
              </div>
            );
          })}
          {extensionCapabilities.map((capability) => (
            <div className="capability-item available" key={capability}>
              <Check aria-hidden="true" />
              <div>
                <strong>扩展能力</strong>
                <p>由当前适配器原样声明，查看器未附加语义。</p>
                <code>{capability}</code>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="analysis-section" aria-labelledby="topology-title">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">开放组件拓扑</p>
            <h2 id="topology-title">来自策略的原生边界</h2>
          </div>
        </div>
        <div className="topology-list">
          {workspace.adapter.components.map((component) => (
            <div className="topology-row" key={component.component_id}>
              <span className="topology-kind">
                {componentKindText[component.kind] ?? "未登记的组件类型"}
              </span>
              <div>
                <strong>
                  {displayComponentLabel(component.component_id, component.label)}
                </strong>
                <code>{component.component_id}</code>
              </div>
              <p>
                {displayComponentDescription(
                  component.component_id,
                  component.description,
                )}
              </p>
              <span className="dependency">
                {component.depends_on.length
                  ? `依赖 ${component.depends_on
                      .map((componentId) =>
                        workspaceComponentLabel(workspace, componentId),
                      )
                      .join("、")}`
                  : "根组件"}
                <small>
                  {component.legal_interventions.length
                    ? component.legal_interventions
                        .map(
                          (item) =>
                            `${displayOperationLabel(item.operation)}：${displayArtifactTerm(item.fallback_semantics)}`,
                        )
                        .join(" · ")
                    : "无已声明合法干预"}
                </small>
              </span>
            </div>
          ))}
        </div>
        <div className="joint-intervention-section">
          <h3>适配器声明的联合干预</h3>
          {workspace.adapter.legal_joint_interventions.length ? (
            <div className="joint-intervention-list">
              {workspace.adapter.legal_joint_interventions.map((joint) => (
                <div key={joint.digest}>
                  <span>
                    <strong>联合干预定义</strong>
                    <code>{joint.joint_spec_version}</code>
                  </span>
                  <span>
                    <small>组件</small>
                    <p>
                      {joint.component_ids
                        .map((componentId) =>
                          workspaceComponentLabel(workspace, componentId),
                        )
                        .join(" → ")}
                    </p>
                  </span>
                  <span>
                    <small>干预顺序</small>
                    <p>
                      {joint.intervention_ids
                        .map(displayInterventionLabel)
                        .join(" → ")}
                    </p>
                  </span>
                  <span>
                    <small>兼容与冲突规则</small>
                    <p>
                      {displayArtifactTerm(joint.compatibility_semantics)}{" "}
                      {displayArtifactTerm(joint.conflict_rule)}
                    </p>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted-copy">适配器未声明合法联合干预。</p>
          )}
        </div>
      </section>

      <section className="analysis-section evidence-section" aria-labelledby="evidence-title">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">第三层 · 方法与证据</p>
            <h2 id="evidence-title">身份、实验与语义摘要</h2>
          </div>
          {claim && <StatusPill value={claim.status} synthetic={synthetic} />}
        </div>
        <div className="evidence-columns">
          <div>
            <h3>工作区与适配器</h3>
            <EvidenceRow label="工作区"><code>{workspace.workspace_id}</code></EvidenceRow>
            <EvidenceRow label="数据格式版本"><code>{workspace.schema}</code></EvidenceRow>
            <EvidenceRow label="数据来源">
              <span>{displayArtifactTerm(workspace.source_kind)}</span>
            </EvidenceRow>
            <EvidenceRow label="工作区摘要"><DigestValue value={workspace.digest} /></EvidenceRow>
            {publication && (
              <>
                <EvidenceRow label="发布标识"><code>{publication.releaseId}</code></EvidenceRow>
                <EvidenceRow label="运行标识"><code>{publication.runId}</code></EvidenceRow>
                <EvidenceRow label="发布摘要"><DigestValue value={publication.releaseDigest} /></EvidenceRow>
                <EvidenceRow label="正式策略证据">
                  <span>{displayBoolean(publication.formalStrategyEvidence)}</span>
                </EvidenceRow>
              </>
            )}
            <EvidenceRow label="策略版本"><code>{workspace.adapter.strategy_version}</code></EvidenceRow>
            <EvidenceRow label="适配器版本"><code>{workspace.adapter.adapter_version}</code></EvidenceRow>
            <EvidenceRow label="能力清单版本"><code>{workspace.adapter.capability_manifest_version}</code></EvidenceRow>
            <EvidenceRow label="适配器摘要"><DigestValue value={workspace.adapter.digest} /></EvidenceRow>
            <EvidenceRow label="原生拓扑摘要"><DigestValue value={workspace.adapter.native_topology_digest} /></EvidenceRow>
          </div>
          <div>
            <h3>当前归因声明</h3>
            {claim ? (
              <>
                <EvidenceRow label="声明"><code>{claim.claim_id}</code></EvidenceRow>
                <EvidenceRow label="问题"><code>{claim.question_id}</code></EvidenceRow>
                <EvidenceRow label="干预"><code>{claim.intervention_id}</code></EvidenceRow>
                <EvidenceRow label="问题摘要"><DigestValue value={claim.method_evidence.question_digest} /></EvidenceRow>
                <EvidenceRow label="上下文摘要"><DigestValue value={claim.method_evidence.context_digest} /></EvidenceRow>
                <EvidenceRow label="声明期间"><code>{claim.method_evidence.period_start} / {claim.method_evidence.period_end}</code></EvidenceRow>
                <EvidenceRow label="证据类型">
                  <span>{displayEvidenceType(claim.evidence_type, synthetic)}</span>{" "}
                  <code>{claim.evidence_type}</code>
                </EvidenceRow>
                <EvidenceRow label="基线实验"><code title={claim.method_evidence.baseline_experiment_id}>{shortId(claim.method_evidence.baseline_experiment_id, 22, 8)}</code></EvidenceRow>
                <EvidenceRow label="干预实验"><code title={claim.method_evidence.intervention_experiment_id}>{shortId(claim.method_evidence.intervention_experiment_id, 22, 8)}</code></EvidenceRow>
                <EvidenceRow label="基线实验摘要"><DigestValue value={claim.method_evidence.baseline_experiment_digest} /></EvidenceRow>
                <EvidenceRow label="干预实验摘要"><DigestValue value={claim.method_evidence.intervention_experiment_digest} /></EvidenceRow>
                <EvidenceRow label="声明摘要"><DigestValue value={claim.digest} /></EvidenceRow>
              </>
            ) : (
              <EmptyEvidence>没有当前声明。</EmptyEvidence>
            )}
          </div>
        </div>
        {claim && (
          <div className="question-manifest">
            <h3>预声明问题</h3>
            {question ? (
              <>
                <div className="question-grid">
                <EvidenceRow label="估计对象">
                  <span>{displayArtifactTerm(question.estimand_kind)}</span>
                </EvidenceRow>
                <EvidenceRow label="分析角色">
                  <span>{displayArtifactTerm(question.analysis_role)}</span>
                </EvidenceRow>
                <EvidenceRow label="样本角色">
                  <span>{displayArtifactTerm(question.sample_role)}</span>
                </EvidenceRow>
                <EvidenceRow label="目标总体">
                  <span>{displayArtifactTerm(question.target_population)}</span>
                </EvidenceRow>
                <EvidenceRow label="主指标">
                  <span>
                    {question.primary_metric_ids
                      .map((metricId) => displayMetricLabel(metricId))
                      .join(" · ")}
                  </span>
                </EvidenceRow>
                <EvidenceRow label="干预语义">
                  <span>{displayArtifactTerm(question.intervention_semantics)}</span>
                </EvidenceRow>
                <EvidenceRow label="多重比较">
                  <span>{displayArtifactTerm(question.multiple_comparison_rule)}</span>
                </EvidenceRow>
                <EvidenceRow label="分类规则">
                  <span>{displayArtifactTerm(question.classification_rule)}</span>
                </EvidenceRow>
                <EvidenceRow label="决策规则">
                  <span>{displayArtifactTerm(question.decision_rule)}</span>
                </EvidenceRow>
                <EvidenceRow label="本次动作">
                  <span>
                    {displayArtifactTerm(claim.decision_summary.next_action_code)}
                  </span>
                </EvidenceRow>
                <EvidenceRow label="结果读取前已绑定">
                  <span>{displayBoolean(question.bound_before_outcomes)}</span>
                </EvidenceRow>
                <EvidenceRow label="不确定性分析">
                  <span>
                    {question.uncertainty_required
                      ? displayArtifactTerm(question.uncertainty_method)
                      : `本次未要求；${displayArtifactTerm(question.uncertainty_method)}`}
                  </span>
                </EvidenceRow>
                <EvidenceRow label="稳定性分析">
                  <span>
                    {question.stability_required
                      ? displayArtifactTerm(question.stability_method)
                      : `本次未要求；${displayArtifactTerm(question.stability_method)}`}
                  </span>
                </EvidenceRow>
                </div>
                {(question.known_assumptions.length > 0 ||
                  question.exclusions.length > 0 ||
                  question.stop_conditions.length > 0) && (
                  <div className="question-boundaries">
                    <h4>假设、排除项与停止条件</h4>
                    <ul className="plain-list">
                      {[
                        ...question.known_assumptions,
                        ...question.exclusions,
                        ...question.stop_conditions,
                      ].map((item) => (
                        <li key={item}>{displayArtifactTerm(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : (
              <EmptyEvidence>当前声明的问题清单无法解析，查看器不会补造方法描述。</EmptyEvidence>
            )}
          </div>
        )}
        {claim && (
          <div className="manifest-details-grid">
            <details className="manifest-details">
              <summary>
                <ChevronRight aria-hidden="true" />
                基线实验约束
              </summary>
              {baselineExperiment ? (
                <div className="manifest-content">
                  <EvidenceRow label="资金约束"><span>{displayBoolean(baselineExperiment.capital_constrained)}</span></EvidenceRow>
                  <EvidenceRow label="时点合规校验"><span>{displayBoolean(baselineExperiment.pit_validated)}</span></EvidenceRow>
                  <EvidenceRow label="样本分区已授权"><span>{displayBoolean(baselineExperiment.partition_authorized)}</span></EvidenceRow>
                  <EvidenceRow label="结果已复现"><span>{displayBoolean(baselineExperiment.reproduced)}</span></EvidenceRow>
                  <EvidenceRow label="资金路径已核对"><span>{displayBoolean(baselineExperiment.reconciled)}</span></EvidenceRow>
                  <div className="context-bindings">
                    {Object.entries(baselineExperiment.context_bindings).map(([key, value]) => (
                      <span key={key}><code>{displayContextLabel(key)}</code><strong>{value}</strong></span>
                    ))}
                  </div>
                </div>
              ) : (
                <EmptyEvidence>基线实验清单不可解析。</EmptyEvidence>
              )}
            </details>
            <details className="manifest-details">
              <summary>
                <ChevronRight aria-hidden="true" />
                干预实验约束
              </summary>
              {interventionExperiment ? (
                <div className="manifest-content">
                  <EvidenceRow label="干预">
                    <span>{interventionExperiment.intervention_ids.map(displayInterventionLabel).join(" · ")}</span>
                  </EvidenceRow>
                  <EvidenceRow label="改变组件">
                    <span>{interventionExperiment.changed_component_ids.map((componentId) => workspaceComponentLabel(workspace, componentId)).join(" · ")}</span>
                  </EvidenceRow>
                  <EvidenceRow label="资金约束"><span>{displayBoolean(interventionExperiment.capital_constrained)}</span></EvidenceRow>
                  <EvidenceRow label="时点合规校验"><span>{displayBoolean(interventionExperiment.pit_validated)}</span></EvidenceRow>
                  <EvidenceRow label="样本分区已授权"><span>{displayBoolean(interventionExperiment.partition_authorized)}</span></EvidenceRow>
                  <EvidenceRow label="结果已复现"><span>{displayBoolean(interventionExperiment.reproduced)}</span></EvidenceRow>
                  <EvidenceRow label="资金路径已核对"><span>{displayBoolean(interventionExperiment.reconciled)}</span></EvidenceRow>
                </div>
              ) : (
                <EmptyEvidence>干预实验清单不可解析。</EmptyEvidence>
              )}
            </details>
          </div>
        )}
        {claim && (
          <div className="validation-block">
            <div>
              <h3>验证标记</h3>
              <div className="tag-list">
                {claim.method_evidence.validation_flags.map((flag) => (
                  <span key={flag}>
                    <Check aria-hidden="true" />
                    {(synthetic ? syntheticValidationText[flag] : validationText[flag]) ??
                      validationText[flag] ??
                      flag}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <h3>限制与失败原因</h3>
              {claim.limitations.length || claim.method_evidence.reason_codes.length ? (
                <ul className="plain-list">
                  {[...claim.limitations, ...claim.method_evidence.reason_codes].map((item) => (
                    <li key={item}>{displayArtifactTerm(item)}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted-copy">产物未声明限制或失败原因。</p>
              )}
            </div>
            <div className="supporting-evidence-block">
              <h3>支撑证据摘要</h3>
              {claim.method_evidence.supporting_evidence_digests.length ? (
                <div className="digest-list">
                  {claim.method_evidence.supporting_evidence_digests.map((item) => (
                    <DigestValue key={item} value={item} />
                  ))}
                </div>
              ) : (
                <p className="muted-copy">产物未列出额外支撑证据摘要。</p>
              )}
            </div>
          </div>
        )}
      </section>
    </>
  );
}

function EmptyWorkspaceView({
  onImport,
  onShowSynthetic,
  publicationState,
}: {
  onImport: () => void;
  onShowSynthetic: () => void;
  publicationState: "loading" | "ready" | "partial" | "unavailable";
}) {
  const publicationStateText = {
    loading: "正在读取",
    ready: "0 份",
    partial: "部分产物不可读取",
    unavailable: "目录不可读取",
  }[publicationState];
  return (
    <main className="empty-workspace">
      <section className="empty-workspace-copy" aria-labelledby="empty-workspace-title">
        <p className="eyebrow">当前数据状态</p>
        <h1 id="empty-workspace-title">尚未接入真实归因结果</h1>
        <p>
          正式策略、专家模块和回测归因产物目前都还没有接入。此前页面中的
          99% 收益、5% 回撤以及模块名称全部是人为编造的合同样例，不代表
          Quanthus 的任何实际策略表现。
        </p>
        <div className="empty-workspace-actions">
          <button type="button" className="primary-button" onClick={onImport}>
            <Upload aria-hidden="true" />
            导入归因产物
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={onShowSynthetic}
          >
            <FlaskConical aria-hidden="true" />
            查看虚构样例
          </button>
        </div>
      </section>
      <section className="connection-status" aria-label="归因系统接入状态">
        <div>
          <span>归因合同原型</span>
          <strong>已实现</strong>
        </div>
        <div>
          <span>正式策略适配器</span>
          <strong>尚未接入</strong>
        </div>
        <div>
          <span>已发布归因产物</span>
          <strong>{publicationStateText}</strong>
        </div>
      </section>
    </main>
  );
}

export function AttributionWorkbench() {
  const [catalogEntries, setCatalogEntries] = useState<WorkspaceCatalogEntry[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [selectedClaimId, setSelectedClaimId] = useState("");
  const [view, setView] = useState<ViewId>("overview");
  const [importError, setImportError] = useState<string | null>(null);
  const [publicationState, setPublicationState] = useState<
    "loading" | "ready" | "partial" | "unavailable"
  >("loading");
  const fileInput = useRef<HTMLInputElement>(null);
  const importSequence = useRef(0);

  const activeEntry = useMemo(
    () => catalogEntries.find((entry) => entry.entryId === selectedWorkspaceId),
    [catalogEntries, selectedWorkspaceId],
  );
  const workspace = activeEntry?.workspace ?? demoWorkspace;
  const artifactText = activeEntry?.artifactText ?? demoArtifactText;
  const selectedClaim = useMemo(
    () => workspace.claims.find((claim) => claim.claim_id === selectedClaimId),
    [selectedClaimId, workspace.claims],
  );
  const strategyGroups = useMemo(
    () => groupWorkspaceCatalog(catalogEntries),
    [catalogEntries],
  );
  const hasLoadedWorkspace = activeEntry !== undefined;
  const sourceTrust = workspaceSourceTrust(
    workspace,
    activeEntry?.origin ?? "synthetic",
  );
  const isSynthetic = sourceTrust.syntheticFixture;
  const isFormalStrategyEvidence =
    activeEntry?.publication?.formalStrategyEvidence === true;

  useEffect(() => {
    let cancelled = false;
    loadPublishedWorkspaceCatalog()
      .then((result) => {
        if (cancelled) return;
        setPublicationState(result.errors.length ? "partial" : "ready");
        if (result.errors.length) {
          setImportError(`部分已发布归因产物未载入：${result.errors.join("；")}`);
        }
        if (!result.entries.length) return;
        const firstPublished = result.entries[0];
        setCatalogEntries((current) =>
          upsertWorkspaceCatalogEntries(current, result.entries),
        );
        setSelectedWorkspaceId((current) => current || firstPublished.entryId);
        setSelectedClaimId(
          (current) =>
            current || firstPublished.workspace.claims[0]?.claim_id || "",
        );
      })
      .catch(() => {
        if (!cancelled) setPublicationState("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function showSyntheticFixture() {
    const entry = createWorkspaceCatalogEntry(
      demoWorkspace,
      demoArtifactText,
      "synthetic",
    );
    setCatalogEntries((current) =>
      upsertWorkspaceCatalogEntries(current, [entry]),
    );
    setSelectedWorkspaceId(entry.entryId);
    setSelectedClaimId(demoWorkspace.claims[0]?.claim_id ?? "");
    setImportError(null);
    setView("overview");
  }

  function clearWorkspace() {
    if (!activeEntry || activeEntry.origin === "published") return;
    const remaining = removeWorkspaceCatalogEntry(
      catalogEntries,
      activeEntry.entryId,
    );
    const nextEntry = remaining[0];
    setCatalogEntries(remaining);
    setSelectedWorkspaceId(nextEntry?.entryId ?? "");
    setSelectedClaimId(nextEntry?.workspace.claims[0]?.claim_id ?? "");
    setImportError(null);
    setView("overview");
  }

  async function importArtifact(event: ChangeEvent<HTMLInputElement>) {
    const files = [...(event.target.files ?? [])];
    event.target.value = "";
    if (!files.length) return;

    const importedEntries: WorkspaceCatalogEntry[] = [];
    const errors: string[] = [];
    for (const file of files) {
      const text = await file.text();
      const result = importWorkspaceDocument({ workspace, artifactText }, text);
      if (!result.ok) {
        errors.push(`${file.name}：${result.error}`);
        continue;
      }
      importedEntries.push(
        createWorkspaceCatalogEntry(
          result.document.workspace,
          result.document.artifactText,
          "imported",
          `imported:${++importSequence.current}:${result.document.workspace.digest}`,
        ),
      );
    }

    const lastImported = importedEntries.at(-1);
    if (importedEntries.length) {
      setCatalogEntries((current) =>
        upsertWorkspaceCatalogEntries(current, importedEntries),
      );
      setSelectedWorkspaceId(lastImported?.entryId ?? "");
      setSelectedClaimId(lastImported?.workspace.claims[0]?.claim_id ?? "");
      setView("overview");
    }
    setImportError(errors.length ? errors.join("；") : null);
  }

  function downloadArtifact() {
    const blob = new Blob([artifactText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${workspace.workspace_id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function selectClaim(claimId: string, nextView?: ViewId) {
    setSelectedClaimId(claimId);
    if (nextView) setView(nextView);
  }

  function selectWorkspace(entry: WorkspaceCatalogEntry) {
    setSelectedWorkspaceId(entry.entryId);
    setSelectedClaimId(entry.workspace.claims[0]?.claim_id ?? "");
    setView("overview");
  }

  return (
    <div className="workbench-shell">
      <header className={`topbar ${hasLoadedWorkspace ? "" : "topbar-empty"}`}>
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <div>
            <strong>Quanthus</strong>
            <span>策略归因分析</span>
          </div>
        </div>
        <span className="catalog-summary">
          {hasLoadedWorkspace
            ? `${strategyGroups.length} 个策略 · ${catalogEntries.length} 个回测批次`
            : "尚未接入正式归因数据"}
        </span>
        <div className="topbar-actions">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            multiple
            onChange={importArtifact}
            className="sr-only"
            aria-label="导入归因工作区 JSON"
          />
          <button type="button" className="secondary-button" onClick={() => fileInput.current?.click()}>
            <Upload aria-hidden="true" />导入
          </button>
          {hasLoadedWorkspace ? (
            <>
              {activeEntry?.origin !== "published" && (
                <button type="button" className="icon-button" onClick={clearWorkspace} title="移除当前回测批次" aria-label="移除当前回测批次">
                  <X aria-hidden="true" />
                </button>
              )}
              <button type="button" className="icon-button" onClick={downloadArtifact} title="下载当前原始产物" aria-label="下载当前原始产物">
                <Download aria-hidden="true" />
              </button>
            </>
          ) : (
            <button type="button" className="secondary-button" onClick={showSyntheticFixture}>
              <FlaskConical aria-hidden="true" />虚构样例
            </button>
          )}
        </div>
      </header>

      {importError && (
        <div className="import-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <span>{importError}</span>
          <button type="button" className="icon-button icon-button-small" onClick={() => setImportError(null)} aria-label="关闭导入错误">
            <X aria-hidden="true" />
          </button>
        </div>
      )}

      {!hasLoadedWorkspace ? (
        <EmptyWorkspaceView
          onImport={() => fileInput.current?.click()}
          onShowSynthetic={showSyntheticFixture}
          publicationState={publicationState}
        />
      ) : (
      <div className="analysis-workspace">
        <aside className="strategy-sidebar">
          <div className="catalog-heading">
            <div>
              <span>归因目录</span>
              <strong>策略与回测批次</strong>
            </div>
            <small>{catalogEntries.length}</small>
          </div>
          <div className="strategy-catalog">
            {strategyGroups.map((group) => {
              const firstEntry = group.entries[0];
              if (!firstEntry) return null;
              return (
                <section className="strategy-group" key={group.strategyId}>
                  <header>
                    <strong>{workspaceDisplayTitle(firstEntry)}</strong>
                    <code>{group.strategyId}</code>
                  </header>
                  <div className="run-list">
                    {group.entries.map((entry) => (
                      <button
                        type="button"
                        key={entry.entryId}
                        className={
                          activeEntry?.entryId === entry.entryId
                            ? "selected"
                            : undefined
                        }
                        onClick={() => selectWorkspace(entry)}
                      >
                        <span className="run-marker" aria-hidden="true" />
                        <span>
                          <strong>{workspaceDisplayPeriod(entry)}</strong>
                          <small>
                            版本 {entry.workspace.adapter.strategy_version} ·{" "}
                            {entry.workspace.claims.length} 条声明 · 批次{" "}
                            {shortId(entry.workspace.digest, 10, 6)}
                          </small>
                        </span>
                        <span
                          className={`catalog-origin ${entry.origin}`}
                          title={
                            entry.origin === "published"
                              ? "服务端已验证发布"
                              : entry.origin === "synthetic"
                                ? "内置虚构样例"
                                : "浏览器导入，未验证"
                          }
                        >
                          {entry.origin === "published" ? (
                            <BadgeCheck aria-hidden="true" />
                          ) : entry.origin === "synthetic" ? (
                            <FlaskConical aria-hidden="true" />
                          ) : (
                            <AlertTriangle aria-hidden="true" />
                          )}
                        </span>
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
          <div className="viewer-boundary">
            <FileJson aria-hidden="true" />
            <p>目录只组织独立归因产物，不合并不同策略的证据。</p>
          </div>
        </aside>

        <section className="strategy-analysis">
          <header className="workspace-context">
            <div className="workspace-context-copy">
              <div className="source-line">
                {sourceTrust.serverVerifiedPublication && (
                  <span className="source-badge formal">
                    <BadgeCheck aria-hidden="true" />
                    服务端已验证发布
                  </span>
                )}
                {isSynthetic && (
                  <span className="source-badge synthetic">
                    <FlaskConical aria-hidden="true" />
                    {sourceTrust.serverVerifiedPublication
                      ? "合成夹具 · 非真实策略证据"
                      : "内置虚构样例 · 非真实回测"}
                  </span>
                )}
                {sourceTrust.unverifiedImport && (
                  <span className="source-badge unverified">
                    <AlertTriangle aria-hidden="true" />
                    未验证的导入产物
                  </span>
                )}
                {sourceTrust.serverVerifiedPublication &&
                  !isSynthetic &&
                  !isFormalStrategyEvidence && (
                  <span className="source-badge nonformal">
                    <AlertTriangle aria-hidden="true" />
                    尚非正式策略证据
                  </span>
                )}
              </div>
              <p className="eyebrow">当前策略与回测批次</p>
              <h1>{activeEntry ? workspaceDisplayTitle(activeEntry) : workspace.title}</h1>
              <p>
                {activeEntry
                  ? workspaceDisplaySubtitle(activeEntry)
                  : workspace.subtitle}
              </p>
            </div>
            <div className="workspace-context-controls">
              <label className="mobile-workspace-select">
                <span>策略与回测批次</span>
                <select
                  value={selectedWorkspaceId}
                  onChange={(event) => {
                    const entry = catalogEntries.find(
                      (item) => item.entryId === event.target.value,
                    );
                    if (entry) selectWorkspace(entry);
                  }}
                >
                  {strategyGroups.map((group) => {
                    const firstEntry = group.entries[0];
                    if (!firstEntry) return null;
                    return (
                      <optgroup
                        key={group.strategyId}
                        label={workspaceDisplayTitle(firstEntry)}
                      >
                        {group.entries.map((entry) => (
                          <option key={entry.entryId} value={entry.entryId}>
                            {workspaceDisplayPeriod(entry)} · 版本{" "}
                            {entry.workspace.adapter.strategy_version} · 批次{" "}
                            {entry.workspace.digest.slice(-6)}
                          </option>
                        ))}
                      </optgroup>
                    );
                  })}
                </select>
              </label>
              <div className="context-fact">
                <span>策略版本</span>
                <strong>{workspace.adapter.strategy_version}</strong>
              </div>
              <div className="context-fact">
                <span>分析区间</span>
                <strong>
                  {selectedClaim
                    ? `${displayDate(selectedClaim.method_evidence.period_start)} 至 ${displayDate(selectedClaim.method_evidence.period_end)}`
                    : "无已发布声明"}
                </strong>
              </div>
              <label className="claim-select">
                <span>分析对象</span>
                <select
                  value={selectedClaimId}
                  disabled={!workspace.claims.length}
                  onChange={(event) => selectClaim(event.target.value)}
                >
                  {workspace.claims.map((claim) => (
                    <option key={claim.claim_id} value={claim.claim_id}>
                      {workspaceComponentLabel(
                        workspace,
                        claim.subject_component_id,
                        claim.subject_label,
                      )}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </header>

          <nav className="view-tabs local-view-tabs" role="tablist" aria-label="当前策略归因视图">
            {views.map(({ id, label, icon: ViewIcon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={view === id}
                className={view === id ? "active" : undefined}
                onClick={() => setView(id)}
              >
                <ViewIcon aria-hidden="true" />
                {label}
              </button>
            ))}
          </nav>

          <main className="workspace-main" role="tabpanel">
          {isSynthetic && (
            <div className="synthetic-notice">
              <FlaskConical aria-hidden="true" />
              <div>
                <strong>
                  {sourceTrust.serverVerifiedPublication
                    ? "服务端已验证的合成夹具，非真实策略证据"
                    : "当前是内置虚构样例，不是真实回测"}
                </strong>
                <p>
                  {sourceTrust.serverVerifiedPublication
                    ? "服务端验证只确认发布链路和产物身份。日期、模块和全部数值均为人为编造；99% 收益与 5% 回撤不代表 Quanthus 的任何策略结果。"
                    : "日期、模块和全部数值均为人为编造；99% 收益与 5% 回撤不代表 Quanthus 的任何策略结果。"}
                </p>
              </div>
            </div>
          )}
          {view === "overview" && (
            <OverviewView claim={selectedClaim} synthetic={isSynthetic} />
          )}
          {view === "modules" && (
            <ModulesView
              workspace={workspace}
              selectedClaimId={selectedClaimId}
              onSelect={(claimId) => selectClaim(claimId, "overview")}
              synthetic={isSynthetic}
            />
          )}
          {view === "risk" && (
            <RiskView claim={selectedClaim} synthetic={isSynthetic} />
          )}
          {view === "evidence" && (
            <EvidenceView
              workspace={workspace}
              claim={selectedClaim}
              synthetic={isSynthetic}
              publication={activeEntry?.publication}
            />
          )}
        </main>
        </section>
      </div>
      )}
    </div>
  );
}
