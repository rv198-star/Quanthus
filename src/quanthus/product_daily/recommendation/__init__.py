"""R11 daily advice-package producer (proposal, not adopted).

This package turns an injected daily-facts view plus an injected account-facts view into a
versioned decision-set package whose shape matches the P12 `decision_sets` contract.  It does
not bind the package to any account, does not publish outside the given directory, and does not
declare any real-use qualification.
"""

from quanthus.product_daily.recommendation.admission import (
    AdmissionDisposition,
    MemberClock,
    assess_members,
)
from quanthus.product_daily.recommendation.contracts import (
    DailyAdvicePackage,
    DailyFactsView,
    Decision,
    DecisionSet,
    EntryPlan,
    MemberEvidence,
    PublishReceipt,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
    CandidateEvaluationReceipt,
    CandidateSignal,
    DailyAdviceProducer,
    ExpertBundle,
    ProducerResult,
)
from quanthus.product_daily.recommendation.publish import (
    decision_set_digests,
    file_sha256,
    p12_package_digests,
    package_digests,
    publish_package,
)

__all__ = [
    "AccountFactsView",
    "AdmissionDisposition",
    "AdvicePolicy",
    "CandidateEvaluationReceipt",
    "CandidateSignal",
    "DailyAdvicePackage",
    "DailyAdviceProducer",
    "DailyFactsView",
    "Decision",
    "DecisionSet",
    "EntryPlan",
    "ExpertBundle",
    "MemberClock",
    "MemberEvidence",
    "PublishReceipt",
    "ProducerResult",
    "assess_members",
    "decision_set_digests",
    "file_sha256",
    "p12_package_digests",
    "package_digests",
    "publish_package",
]
