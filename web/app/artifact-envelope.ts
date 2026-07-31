import type {
  AttributionClaim,
  AttributionWorkspace,
  ClaimStatus,
  ConditionEffect,
  InteractionEffect,
  MetricEffect,
} from "./types";

export const ATTRIBUTION_WORKSPACE_SCHEMA =
  "quanthus.strategy-attribution-workspace.v1";

export interface WorkspaceDocument {
  workspace: AttributionWorkspace;
  artifactText: string;
}

export interface WorkspaceSourceTrust {
  syntheticFixture: boolean;
  unverifiedImport: boolean;
  serverVerifiedPublication: boolean;
}

export type WorkspaceTrustOrigin = "published" | "synthetic" | "imported";

export type WorkspaceImportResult =
  | { ok: true; document: WorkspaceDocument }
  | { ok: false; document: WorkspaceDocument; error: string };

export type ComponentPublicationState =
  | "claimed"
  | "unavailable"
  | "unpublished";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function hasStrings(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => typeof value[field] === "string");
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isClaimStatus(value: unknown): value is ClaimStatus {
  return value === "valid" || value === "invalid" || value === "unavailable";
}

function isMetricEffect(value: unknown): value is MetricEffect {
  if (!isRecord(value) || !isRecord(value.display)) return false;
  return (
    hasStrings(value, [
      "metric_id",
      "label",
      "unit",
      "preference",
      "baseline_value",
      "intervention_value",
      "raw_effect",
      "materiality",
      "classification",
    ]) &&
    isNullableString(value.oriented_effect) &&
    isNullableString(value.uncertainty_evidence_digest) &&
    hasStrings(value.display, ["baseline", "intervention", "raw_effect"]) &&
    isNullableString(value.display.oriented_effect)
  );
}

function isConditionEffect(value: unknown): value is ConditionEffect {
  return (
    isRecord(value) &&
    hasStrings(value, ["condition_id", "label"]) &&
    typeof value.sample_count === "number" &&
    Array.isArray(value.metric_effects) &&
    value.metric_effects.every(isMetricEffect)
  );
}

function isInteractionEffect(value: unknown): value is InteractionEffect {
  if (!isRecord(value) || !isRecord(value.display)) return false;
  return (
    hasStrings(value, [
      "interaction_id",
      "interaction_spec_digest",
      "partner_component_id",
      "partner_experiment_id",
      "partner_experiment_digest",
      "joint_experiment_id",
      "joint_experiment_digest",
      "metric_id",
      "label",
      "raw_interaction",
    ]) &&
    isNullableString(value.oriented_interaction) &&
    typeof value.material === "boolean" &&
    typeof value.display.raw === "string" &&
    isNullableString(value.display.oriented)
  );
}

function isDecisionSummary(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "headline_code",
      "classification",
      "evidence_type",
      "next_action_code",
    ]) &&
    isClaimStatus(value.status) &&
    Array.isArray(value.primary_effects) &&
    value.primary_effects.every(isMetricEffect) &&
    isStringArray(value.validation_flags) &&
    isNullableString(value.largest_caveat)
  );
}

function isReasonAnalysis(value: unknown): boolean {
  return (
    isRecord(value) &&
    Array.isArray(value.metric_effects) &&
    value.metric_effects.every(isMetricEffect) &&
    Array.isArray(value.condition_effects) &&
    value.condition_effects.every(isConditionEffect) &&
    Array.isArray(value.interaction_effects) &&
    value.interaction_effects.every(isInteractionEffect) &&
    isStringArray(value.limitations)
  );
}

function isMethodEvidence(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "question_digest",
      "strategy_id",
      "strategy_version",
      "adapter_id",
      "adapter_version",
      "capability_manifest_version",
      "capability_manifest_digest",
      "native_topology_digest",
      "context_digest",
      "period_start",
      "period_end",
      "baseline_experiment_id",
      "intervention_experiment_id",
      "baseline_experiment_digest",
      "intervention_experiment_digest",
      "baseline_artifact_digest",
      "intervention_artifact_digest",
      "evidence_type",
      "method_registry_version",
    ]) &&
    isClaimStatus(value.status) &&
    isStringArray(value.applied_method_ids) &&
    isStringArray(value.validation_flags) &&
    isStringArray(value.reason_codes) &&
    Array.isArray(value.analysis_checks) &&
    value.analysis_checks.every(isAnalysisCheck) &&
    Array.isArray(value.verification_receipts) &&
    value.verification_receipts.every(isVerificationReceipt) &&
    isStringArray(value.supporting_evidence_digests)
  );
}

function isClaim(value: unknown): value is AttributionClaim {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, [
      "claim_id",
      "schema_version",
      "question_id",
      "subject_component_id",
      "subject_label",
      "intervention_id",
      "strategy_id",
      "strategy_version",
      "adapter_id",
      "adapter_version",
      "capability_manifest_version",
      "capability_manifest_digest",
      "native_topology_digest",
      "context_digest",
      "period_start",
      "period_end",
      "classification",
      "next_action_code",
      "evidence_type",
      "method_registry_version",
      "question_digest",
      "baseline_experiment_id",
      "intervention_experiment_id",
      "baseline_experiment_digest",
      "intervention_experiment_digest",
      "baseline_artifact_digest",
      "intervention_artifact_digest",
      "digest",
    ]) &&
    isClaimStatus(value.status) &&
    isStringArray(value.primary_metric_ids) &&
    isStringArray(value.applied_method_ids) &&
    isStringArray(value.validation_flags) &&
    isStringArray(value.reason_codes) &&
    isStringArray(value.limitations) &&
    isStringArray(value.supporting_evidence_digests) &&
    Array.isArray(value.analysis_checks) &&
    value.analysis_checks.every(isAnalysisCheck) &&
    Array.isArray(value.verification_receipts) &&
    value.verification_receipts.every(isVerificationReceipt) &&
    Array.isArray(value.metric_effects) &&
    value.metric_effects.every(isMetricEffect) &&
    Array.isArray(value.condition_effects) &&
    value.condition_effects.every(isConditionEffect) &&
    Array.isArray(value.interaction_effects) &&
    value.interaction_effects.every(isInteractionEffect) &&
    isDecisionSummary(value.decision_summary) &&
    isReasonAnalysis(value.reason_analysis) &&
    isMethodEvidence(value.method_evidence)
  );
}

function isIntervention(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "intervention_id",
      "intervention_version",
      "operation",
      "fallback_semantics",
      "digest",
    ])
  );
}

function isComponent(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "component_id",
      "component_version",
      "label",
      "kind",
      "role",
      "description",
      "digest",
    ]) &&
    isStringArray(value.depends_on) &&
    Array.isArray(value.legal_interventions) &&
    value.legal_interventions.every(isIntervention)
  );
}

function isJointIntervention(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "joint_spec_id",
      "joint_spec_version",
      "compatibility_semantics",
      "conflict_rule",
      "digest",
    ]) &&
    isStringArray(value.intervention_ids) &&
    isStringArray(value.component_ids)
  );
}

function isAdapter(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "adapter_id",
      "adapter_version",
      "capability_manifest_version",
      "strategy_id",
      "strategy_version",
      "native_topology_digest",
      "digest",
    ]) &&
    isStringArray(value.capabilities) &&
    Array.isArray(value.components) &&
    value.components.every(isComponent) &&
    Array.isArray(value.legal_joint_interventions) &&
    value.legal_joint_interventions.every(isJointIntervention)
  );
}

function isExperiment(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "experiment_id",
      "schema_version",
      "question_id",
      "question_digest",
      "role",
      "strategy_id",
      "strategy_version",
      "adapter_id",
      "adapter_version",
      "capability_manifest_version",
      "capability_manifest_digest",
      "native_topology_digest",
      "context_digest",
      "artifact_digest",
      "metric_calculator_id",
      "metric_calculator_version",
      "period_start",
      "period_end",
      "digest",
    ]) &&
    isRecord(value.context_bindings) &&
    Object.values(value.context_bindings).every((item) => typeof item === "string") &&
    isNullableString(value.native_trace_digest) &&
    isNullableString(value.interaction_spec_digest) &&
    isStringRecord(value.metric_spec_digests) &&
    isStringArray(value.intervention_ids) &&
    isStringArray(value.changed_component_ids) &&
    [
      "capital_constrained",
      "reproduced",
      "reconciled",
      "pit_validated",
      "partition_authorized",
      "question_bound_before_outcomes",
    ].every((field) => typeof value[field] === "boolean") &&
    (value.decision_trace_reconciled === null ||
      typeof value.decision_trace_reconciled === "boolean") &&
    (value.opportunity_trace_reconciled === null ||
      typeof value.opportunity_trace_reconciled === "boolean") &&
    isRecord(value.metrics) &&
    Object.values(value.metrics).every((item) => typeof item === "string")
  );
}

function isQuestion(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "question_id",
      "question_version",
      "subject_component_id",
      "intervention_id",
      "intervention_spec_digest",
      "hypothesis_family_id",
      "estimand_kind",
      "intervention_semantics",
      "target_population",
      "sample_role",
      "analysis_role",
      "multiple_comparison_rule",
      "classification_rule",
      "decision_rule",
      "uncertainty_method",
      "stability_method",
      "digest",
    ]) &&
    typeof value.uncertainty_required === "boolean" &&
    typeof value.stability_required === "boolean" &&
    typeof value.bound_before_outcomes === "boolean" &&
    isStringRecord(value.decision_actions) &&
    isStringArray(value.primary_metric_ids) &&
    Array.isArray(value.metric_specs) &&
    value.metric_specs.every(isMetricSpec) &&
    isStringArray(value.required_capabilities) &&
    Array.isArray(value.condition_specs) &&
    value.condition_specs.every(isRecord) &&
    Array.isArray(value.interaction_specs) &&
    value.interaction_specs.every(isRecord) &&
    isStringArray(value.known_assumptions) &&
    isStringArray(value.exclusions) &&
    isStringArray(value.stop_conditions)
  );
}

function isMetricSpec(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "metric_id",
      "label",
      "unit",
      "definition_version",
      "definition_digest",
      "preference",
      "materiality",
      "display",
      "digest",
    ]) &&
    typeof value.display_decimals === "number" &&
    isNullableString(value.target_min) &&
    isNullableString(value.target_max)
  );
}

function isStringRecord(value: unknown): boolean {
  return (
    isRecord(value) &&
    Object.values(value).every((item) => typeof item === "string")
  );
}

function isBooleanRecord(value: unknown): boolean {
  return (
    isRecord(value) &&
    Object.values(value).every((item) => typeof item === "boolean")
  );
}

function isVerificationReceipt(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "schema_version",
      "receipt_id",
      "verifier_id",
      "verifier_version",
      "artifact_digest",
      "artifact_kind",
      "subject_id",
      "producer_id",
      "producer_version",
      "digest",
    ]) &&
    isStringArray(value.parent_digests) &&
    isBooleanRecord(value.verified_facts) &&
    typeof value.passed === "boolean" &&
    isStringArray(value.reason_codes)
  );
}

function isSemanticArtifact(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "schema_version",
      "artifact_id",
      "artifact_kind",
      "producer_id",
      "producer_version",
      "source_kind",
      "digest",
    ]) &&
    isStringArray(value.parent_digests) &&
    isRecord(value.payload)
  );
}

function isConditionComparison(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "condition_id",
      "condition_spec_digest",
      "question_digest",
      "adapter_capability_digest",
      "context_digest",
      "period_start",
      "period_end",
      "label",
      "baseline_experiment_digest",
      "intervention_experiment_digest",
      "digest",
    ]) &&
    typeof value.sample_count === "number" &&
    typeof value.reconciled === "boolean" &&
    isStringRecord(value.baseline_metrics) &&
    isStringRecord(value.intervention_metrics)
  );
}

function isInteractionComparison(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, [
      "interaction_id",
      "interaction_spec_digest",
      "partner_component_id",
      "baseline_experiment_digest",
      "subject_experiment_digest",
      "partner_experiment_id",
      "partner_experiment_digest",
      "joint_experiment_id",
      "joint_experiment_digest",
      "digest",
    ])
  );
}

function isAnalysisCheck(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasStrings(value, ["kind", "method", "digest"]) &&
    typeof value.completed === "boolean" &&
    typeof value.passed === "boolean"
  );
}

export function parseWorkspaceArtifact(text: string): AttributionWorkspace {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("文件不是有效的 JSON");
  }

  if (
    !isRecord(parsed) ||
    parsed.schema !== ATTRIBUTION_WORKSPACE_SCHEMA ||
    !hasStrings(parsed, [
      "workspace_id",
      "title",
      "subtitle",
      "source_kind",
      "digest",
    ]) ||
    !isAdapter(parsed.adapter) ||
    !isExperiment(parsed.baseline) ||
    !Array.isArray(parsed.questions) ||
    !parsed.questions.every(isQuestion) ||
    !Array.isArray(parsed.experiments) ||
    !parsed.experiments.every(isExperiment) ||
    !Array.isArray(parsed.condition_comparisons) ||
    !parsed.condition_comparisons.every(isConditionComparison) ||
    !Array.isArray(parsed.interaction_comparisons) ||
    !parsed.interaction_comparisons.every(isInteractionComparison) ||
    !Array.isArray(parsed.analysis_checks) ||
    !parsed.analysis_checks.every(isAnalysisCheck) ||
    !Array.isArray(parsed.artifacts) ||
    !parsed.artifacts.every(isSemanticArtifact) ||
    !Array.isArray(parsed.claims) ||
    !parsed.claims.every(isClaim) ||
    !isStringArray(parsed.unavailable_component_ids)
  ) {
    throw new Error("文件不是受支持的 Quanthus 归因工作区 v1 产物");
  }
  return parsed as unknown as AttributionWorkspace;
}

export function importWorkspaceDocument(
  current: WorkspaceDocument,
  text: string,
): WorkspaceImportResult {
  try {
    return {
      ok: true,
      document: { workspace: parseWorkspaceArtifact(text), artifactText: text },
    };
  } catch (error) {
    return {
      ok: false,
      document: current,
      error: error instanceof Error ? error.message : "无法读取归因产物",
    };
  }
}

export function componentPublicationState(
  workspace: AttributionWorkspace,
  componentId: string,
): ComponentPublicationState {
  if (workspace.unavailable_component_ids.includes(componentId)) {
    return "unavailable";
  }
  if (workspace.claims.some((claim) => claim.subject_component_id === componentId)) {
    return "claimed";
  }
  return "unpublished";
}

export function isSyntheticFixture(workspace: AttributionWorkspace): boolean {
  return workspace.source_kind === "synthetic_fixture_not_strategy_evidence";
}

export function workspaceSourceTrust(
  workspace: AttributionWorkspace,
  origin: WorkspaceTrustOrigin,
): WorkspaceSourceTrust {
  return {
    syntheticFixture: isSyntheticFixture(workspace),
    unverifiedImport: origin === "imported",
    serverVerifiedPublication: origin === "published",
  };
}
