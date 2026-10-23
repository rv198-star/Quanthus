"""Purpose-specific successor; old admissions cannot select corrected accounting.

The reviewed pure hooks are reused without admitting their generic constructor.
Only this retrospective adapter can bind the correction to the frozen comparison.
"""

from __future__ import annotations

from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_retrospective_replay as legacy

VERSION = "p3-retrospective-accounting-v2"


class CorrectedRetrospectiveEngine(legacy.RetrospectiveEngine):
    runtime_version = VERSION


class CorrectedRetrospectiveRuntime(
    legacy.RetrospectiveRuntime, accounting.AccountingCorrectedReplayRuntime
):
    """Retrospective admission/clock boundary plus reviewed position-cycle hooks.

    The MRO selects RetrospectiveRuntime.__init__ and the accounting-v2 entry and
    return hooks. FormalP2ReplayRuntime still owns matching, fees and cash truth.
    """

    runtime_version = VERSION
    activation_name = "retrospective-execution.v2.json"
    engine_type = CorrectedRetrospectiveEngine

    def _adapter_binding(self) -> dict[str, Any]:
        return {
            "runtime_version": VERSION,
            "accounting_version": accounting.VERSION,
            "code_config_digest": digest_value(self.r2_plan["code_config_members"]),
        }
