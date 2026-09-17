"""Prospective-validation contracts and dependency-gated runtime surfaces.

The current runtime surface exposes only the P6-GOV-002 disabled rehearsal. Official prospective
execution still requires the later paper, Web, and activation gates.
"""

from quanthus.forward_validation.contracts import (
    ForwardValidationContractError,
    P6EntryProposal,
)
from quanthus.forward_validation.data_boundary import (
    DwdVisibilityReceipt,
    P6AccessLedgerEntry,
    P6ActivationBoundary,
    P6CorrectionDisposition,
    P6DataBoundaryPolicy,
    P6OwnerValueDecision,
    P6ReleaseAdmission,
    ProspectiveDwdRelease,
    classify_release_admission,
    load_data_boundary_policy,
    load_owner_value_decision,
    load_visibility_receipt,
    make_access_ledger_entry,
    make_correction_disposition,
    verify_prospective_dwd_release,
)
from quanthus.forward_validation.loss_attribution import (
    MaterialLossAttribution,
    classify_loss,
    review_ready,
)
from quanthus.forward_validation.paper_ledger import (
    P6PaperPolicy,
    PaperLedgerReconciliation,
    PaperLedgerState,
    initial_paper_ledger,
    load_paper_policy,
    reconcile_paper_ledger,
)
from quanthus.forward_validation.paper_projection import (
    PaperPendingIntent,
    PaperSettlement,
    SealedAggregateDecision,
    project_paper_intent,
    settle_paper_intent,
)
from quanthus.forward_validation.scorecards import (
    ForwardRoleScorecard,
    P6ScorecardPolicy,
    RoleMetricPolicy,
    build_role_scorecard,
    load_scorecard_policy,
)
from quanthus.forward_validation.runner import (
    ForwardCycleJobInput,
    ForwardRun,
    P6ForwardWorkerConfig,
    load_forward_worker_config,
    make_disabled_rehearsal_input,
    run_disabled_rehearsal,
    verify_forward_run,
)
from quanthus.forward_validation.validation import load_entry_proposal

__all__ = [
    "DwdVisibilityReceipt",
    "ForwardValidationContractError",
    "P6AccessLedgerEntry",
    "P6ActivationBoundary",
    "P6CorrectionDisposition",
    "P6DataBoundaryPolicy",
    "P6EntryProposal",
    "P6OwnerValueDecision",
    "P6PaperPolicy",
    "P6ReleaseAdmission",
    "P6ScorecardPolicy",
    "ProspectiveDwdRelease",
    "RoleMetricPolicy",
    "ForwardRoleScorecard",
    "ForwardCycleJobInput",
    "ForwardRun",
    "P6ForwardWorkerConfig",
    "MaterialLossAttribution",
    "PaperLedgerReconciliation",
    "PaperLedgerState",
    "PaperPendingIntent",
    "PaperSettlement",
    "SealedAggregateDecision",
    "build_role_scorecard",
    "classify_loss",
    "classify_release_admission",
    "load_data_boundary_policy",
    "load_entry_proposal",
    "load_forward_worker_config",
    "initial_paper_ledger",
    "load_owner_value_decision",
    "load_paper_policy",
    "load_scorecard_policy",
    "load_visibility_receipt",
    "make_access_ledger_entry",
    "make_correction_disposition",
    "make_disabled_rehearsal_input",
    "project_paper_intent",
    "reconcile_paper_ledger",
    "review_ready",
    "run_disabled_rehearsal",
    "settle_paper_intent",
    "verify_forward_run",
    "verify_prospective_dwd_release",
]
