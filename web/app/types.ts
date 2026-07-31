export type ClaimStatus = "valid" | "invalid" | "unavailable";

export type ClaimClassification = string;

export interface InterventionDescriptor {
  intervention_id: string;
  intervention_version: string;
  operation: string;
  fallback_semantics: string;
  digest: string;
}

export interface ComponentDescriptor {
  component_id: string;
  component_version: string;
  label: string;
  kind: string;
  role: string;
  description: string;
  depends_on: string[];
  legal_interventions: InterventionDescriptor[];
  digest: string;
}

export interface JointInterventionDescriptor {
  joint_spec_id: string;
  joint_spec_version: string;
  intervention_ids: string[];
  component_ids: string[];
  compatibility_semantics: string;
  conflict_rule: string;
  digest: string;
}

export interface AdapterManifest {
  adapter_id: string;
  adapter_version: string;
  capability_manifest_version: string;
  strategy_id: string;
  strategy_version: string;
  native_topology_digest: string;
  capabilities: string[];
  components: ComponentDescriptor[];
  legal_joint_interventions: JointInterventionDescriptor[];
  digest: string;
}

export interface ExperimentManifest {
  experiment_id: string;
  schema_version: string;
  question_id: string;
  question_digest: string;
  role: string;
  strategy_id: string;
  strategy_version: string;
  adapter_id: string;
  adapter_version: string;
  capability_manifest_version: string;
  capability_manifest_digest: string;
  native_topology_digest: string;
  context_digest: string;
  context_bindings: Record<string, string>;
  artifact_digest: string;
  native_trace_digest: string | null;
  metric_calculator_id: string;
  metric_calculator_version: string;
  metric_spec_digests: Record<string, string>;
  period_start: string;
  period_end: string;
  intervention_ids: string[];
  changed_component_ids: string[];
  capital_constrained: boolean;
  reproduced: boolean;
  reconciled: boolean;
  pit_validated: boolean;
  partition_authorized: boolean;
  question_bound_before_outcomes: boolean;
  decision_trace_reconciled: boolean | null;
  opportunity_trace_reconciled: boolean | null;
  interaction_spec_digest: string | null;
  metrics: Record<string, string>;
  digest: string;
}

export interface MetricDisplay {
  baseline: string;
  intervention: string;
  raw_effect: string;
  oriented_effect: string | null;
}

export interface MetricEffect {
  metric_id: string;
  label: string;
  unit: string;
  preference: string;
  baseline_value: string;
  intervention_value: string;
  raw_effect: string;
  oriented_effect: string | null;
  materiality: string;
  classification: string;
  uncertainty_evidence_digest: string | null;
  display: MetricDisplay;
}

export interface ConditionEffect {
  condition_id: string;
  label: string;
  sample_count: number;
  metric_effects: MetricEffect[];
}

export interface InteractionEffect {
  interaction_id: string;
  interaction_spec_digest: string;
  partner_component_id: string;
  partner_experiment_id: string;
  partner_experiment_digest: string;
  joint_experiment_id: string;
  joint_experiment_digest: string;
  metric_id: string;
  label: string;
  raw_interaction: string;
  oriented_interaction: string | null;
  material: boolean;
  display: {
    raw: string;
    oriented: string | null;
  };
}

export interface MetricSpecArtifact {
  metric_id: string;
  label: string;
  unit: string;
  definition_version: string;
  definition_digest: string;
  preference: string;
  materiality: string;
  display: string;
  display_decimals: number;
  target_min: string | null;
  target_max: string | null;
  digest: string;
}

export interface ArtifactVerificationReceipt {
  schema_version: string;
  receipt_id: string;
  verifier_id: string;
  verifier_version: string;
  artifact_digest: string;
  artifact_kind: string;
  subject_id: string;
  producer_id: string;
  producer_version: string;
  parent_digests: string[];
  verified_facts: Record<string, boolean>;
  passed: boolean;
  reason_codes: string[];
  digest: string;
}

export interface SemanticArtifactEnvelope {
  schema_version: string;
  artifact_id: string;
  artifact_kind: string;
  producer_id: string;
  producer_version: string;
  source_kind: string;
  parent_digests: string[];
  payload: Record<string, unknown>;
  digest: string;
}

export interface AttributionQuestionArtifact {
  question_id: string;
  question_version: string;
  subject_component_id: string;
  intervention_id: string;
  intervention_spec_digest: string;
  hypothesis_family_id: string;
  estimand_kind: string;
  intervention_semantics: string;
  target_population: string;
  sample_role: string;
  analysis_role: string;
  multiple_comparison_rule: string;
  classification_rule: string;
  decision_rule: string;
  decision_actions: Record<string, string>;
  uncertainty_required: boolean;
  uncertainty_method: string;
  stability_required: boolean;
  stability_method: string;
  bound_before_outcomes: boolean;
  primary_metric_ids: string[];
  metric_specs: MetricSpecArtifact[];
  required_capabilities: string[];
  condition_specs: Array<Record<string, unknown>>;
  interaction_specs: Array<Record<string, unknown>>;
  known_assumptions: string[];
  exclusions: string[];
  stop_conditions: string[];
  digest: string;
}

export interface AnalysisCheckArtifact {
  kind: string;
  method: string;
  completed: boolean;
  passed: boolean;
  digest: string;
  [key: string]: unknown;
}

export interface ConditionComparisonArtifact {
  condition_id: string;
  condition_spec_digest: string;
  question_digest: string;
  adapter_capability_digest: string;
  context_digest: string;
  period_start: string;
  period_end: string;
  label: string;
  sample_count: number;
  baseline_metrics: Record<string, string>;
  intervention_metrics: Record<string, string>;
  baseline_experiment_digest: string;
  intervention_experiment_digest: string;
  reconciled: boolean;
  digest: string;
}

export interface InteractionComparisonArtifact {
  interaction_id: string;
  interaction_spec_digest: string;
  partner_component_id: string;
  baseline_experiment_digest: string;
  subject_experiment_digest: string;
  partner_experiment_id: string;
  partner_experiment_digest: string;
  joint_experiment_id: string;
  joint_experiment_digest: string;
  digest: string;
}

export interface DecisionSummary {
  headline_code: string;
  classification: ClaimClassification;
  status: ClaimStatus;
  next_action_code: string;
  primary_effects: MetricEffect[];
  evidence_type: string;
  validation_flags: string[];
  largest_caveat: string | null;
}

export interface ReasonAnalysis {
  metric_effects: MetricEffect[];
  condition_effects: ConditionEffect[];
  interaction_effects: InteractionEffect[];
  limitations: string[];
}

export interface MethodEvidence {
  question_digest: string;
  strategy_id: string;
  strategy_version: string;
  adapter_id: string;
  adapter_version: string;
  capability_manifest_version: string;
  capability_manifest_digest: string;
  native_topology_digest: string;
  context_digest: string;
  period_start: string;
  period_end: string;
  baseline_experiment_id: string;
  intervention_experiment_id: string;
  baseline_experiment_digest: string;
  intervention_experiment_digest: string;
  baseline_artifact_digest: string;
  intervention_artifact_digest: string;
  evidence_type: string;
  method_registry_version: string;
  applied_method_ids: string[];
  status: ClaimStatus;
  validation_flags: string[];
  reason_codes: string[];
  analysis_checks: AnalysisCheckArtifact[];
  verification_receipts: ArtifactVerificationReceipt[];
  supporting_evidence_digests: string[];
}

export interface AttributionClaim {
  claim_id: string;
  schema_version: string;
  question_id: string;
  subject_component_id: string;
  subject_label: string;
  intervention_id: string;
  primary_metric_ids: string[];
  strategy_id: string;
  strategy_version: string;
  adapter_id: string;
  adapter_version: string;
  capability_manifest_version: string;
  capability_manifest_digest: string;
  native_topology_digest: string;
  context_digest: string;
  period_start: string;
  period_end: string;
  status: ClaimStatus;
  classification: ClaimClassification;
  next_action_code: string;
  evidence_type: string;
  method_registry_version: string;
  applied_method_ids: string[];
  validation_flags: string[];
  reason_codes: string[];
  limitations: string[];
  metric_effects: MetricEffect[];
  condition_effects: ConditionEffect[];
  interaction_effects: InteractionEffect[];
  analysis_checks: AnalysisCheckArtifact[];
  verification_receipts: ArtifactVerificationReceipt[];
  supporting_evidence_digests: string[];
  decision_summary: DecisionSummary;
  reason_analysis: ReasonAnalysis;
  method_evidence: MethodEvidence;
  question_digest: string;
  baseline_experiment_id: string;
  intervention_experiment_id: string;
  baseline_experiment_digest: string;
  intervention_experiment_digest: string;
  baseline_artifact_digest: string;
  intervention_artifact_digest: string;
  digest: string;
}

export interface AttributionWorkspace {
  schema: string;
  workspace_id: string;
  title: string;
  subtitle: string;
  source_kind: string;
  adapter: AdapterManifest;
  baseline: ExperimentManifest;
  questions: AttributionQuestionArtifact[];
  experiments: ExperimentManifest[];
  condition_comparisons: ConditionComparisonArtifact[];
  interaction_comparisons: InteractionComparisonArtifact[];
  analysis_checks: AnalysisCheckArtifact[];
  artifacts: SemanticArtifactEnvelope[];
  claims: AttributionClaim[];
  unavailable_component_ids: string[];
  digest: string;
}
