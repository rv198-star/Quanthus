"""Explicit scoped consumer over the retained, versioned capital runtime.

The caller supplies the exact accepted RestructuringRuntime. This adapter owns
only the successor's event/frame scope and conservative pre-allocation filter.
Cash, fills, fees, actions, allocation, held management and exits stay inherited.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

from quanthus.aggregator import GovernedAction
from quanthus.expert_evaluation.serialization import digest_value as digest

VERSION = "entry-march-capital-v1"
ARMS = ("C0", "C1", "C2")


def scoped_panel_type(parent: type) -> type:
    """Project exact parent+patch values before materializing a bounded SQL table."""

    class ScopedPanel(parent):  # type: ignore[misc]
        def __init__(
            self,
            path: Path,
            report: Any,
            *,
            patch: Path,
            selected: tuple[str, ...],
            start: date,
            end: date,
        ) -> None:
            import tempfile

            import duckdb

            if not selected or len(set(selected)) != len(selected) or start > end:
                raise ValueError("ENTRY_CAPITAL_PANEL_SCOPE")
            self.path, self.report = path.resolve(), report
            self._spill = tempfile.TemporaryDirectory(prefix="entry-march-panel-")
            self._connection = duckdb.connect(
                config={
                    "memory_limit": "512MB",
                    "threads": 1,
                    "max_temp_directory_size": "64MB",
                    "temp_directory": self._spill.name,
                    "autoinstall_known_extensions": False,
                    "autoload_known_extensions": False,
                }
            )
            try:
                self._connection.execute("SET timezone='UTC'")
                self._connection.execute(
                    "CREATE TABLE feature_panel AS SELECT * FROM ("
                    "SELECT * FROM read_parquet(?) WHERE security_id<>'000792' "
                    "UNION ALL SELECT * FROM read_parquet(?)) "
                    "WHERE security_id IN (" + ",".join("?" for _ in selected) + ") "
                    "AND market_date BETWEEN ? AND ?",
                    [str(self.path), str(patch), *selected, start, end],
                )
                self._connection.execute(
                    "CREATE UNIQUE INDEX feature_panel_key "
                    "ON feature_panel (market_date,security_id)"
                )
            except BaseException:
                self.close()
                raise

    return ScopedPanel


def filtered_decision(
    decision: Any, *, arm: str, prediction: dict[str, Any], request_digest: str
) -> Any:
    """Withhold only; a model can never re-enable a rejected expert decision."""
    if arm not in ARMS or type(prediction["c0_allowed"]) is not bool:
        raise ValueError("ENTRY_CAPITAL_ARM_OR_PREDICTION")
    original = decision.action.final_action is GovernedAction.TRIAL_ENTRY
    if original != prediction["c0_allowed"]:
        raise ValueError("ENTRY_CAPITAL_ORIGINAL_B_ELIGIBILITY_CHANGED")
    allowed = prediction[{"C0": "c0_allowed", "C1": "c1_allowed", "C2": "c2_allowed"}[arm]]
    if type(allowed) is not bool or allowed and not original:
        raise ValueError("ENTRY_CAPITAL_CANNOT_PROMOTE_REJECTION")
    reason = (
        "original_B_preserved"
        if arm == "C0"
        else "unchanged_R0_withheld"
        if arm == "C1"
        else "raw7_withheld"
    )
    action = replace(
        decision.action,
        final_action=(decision.action.final_action if allowed else GovernedAction.NO_ACTION),
        action_policy_version=decision.action.action_policy_version + "/" + VERSION,
        action_policy_digest=digest(
            {"parent": decision.action.action_policy_digest, "request": request_digest, "arm": arm}
        ),
        reason_codes=decision.action.reason_codes + ((reason,) if original and not allowed else ()),
    )
    return replace(decision, intervention_id=VERSION + "/" + arm, action=action)


def runtime_type(parent: type) -> type:
    """Compose without editing/import-substituting the old frozen producer."""

    class MarchRuntime(parent):  # type: ignore[misc]
        frames: Any

        def __init__(
            self,
            *args: Any,
            predictions: list[dict[str, Any]],
            successor_request: dict[str, Any],
            **kwargs: Any,
        ) -> None:
            self.successor_request = deepcopy(successor_request)
            self.successor_digest = digest(self.successor_request)
            self.entry_predictions = {
                r["opportunity"]["event_id"]: deepcopy(r) for r in predictions
            }
            if len(predictions) != 87 or len(self.entry_predictions) != 87:
                raise ValueError("ENTRY_CAPITAL_EXACT87")
            super().__init__(*args, **kwargs)
            position = self.registry.card.semantic_sections["position_and_execution"]
            if (
                int(position["unit_count"]) != 5
                or Decimal(str(position["unit_notional"])) != 100000
            ):
                raise ValueError("ENTRY_CAPITAL_UNCHANGED_UNITS")
            # Parent initialization opens its immutable feature lane and creates lazy
            # calendar frames; it runs no portfolio. The new experiment explicitly
            # projects those frames before the first run_variant/initial-cash state.
            self.decision_start = date(2021, 4, 2)
            self.decision_end = date(2021, 12, 30)
            self.execution_end = date(2021, 12, 31)
            self.frames = tuple(
                f
                for f in self.frames
                if self.decision_start <= f.snapshot.market_date <= self.decision_end
            )
            if (
                not self.frames
                or self.frames[0].snapshot.market_date != self.decision_start
                or self.frames[-1].execution_date != self.execution_end
            ):
                raise ValueError("ENTRY_CAPITAL_SCOPED_CALENDAR")

        def _open_data_inputs(self) -> None:
            # Retained parent __init__ dispatches here after source/preparation
            # admission. Reuse immutable features, not its all-history SQL copy.
            import pyarrow.parquet as pq  # type: ignore[import-untyped]

            from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
            from quanthus.backtest.contracts import MARKET_TIMEZONE
            from quanthus.contracts import SnapshotRef

            h = import_module("quanthus.expert_strategy.p3_historical_robustness")
            validate_feature_patch = import_module(
                "quanthus.expert_strategy.p3_restructuring_preparation"
            ).validate_feature_patch

            self.data_snapshot_id = self.snapshot_id
            self.data_content_digest = self.prerequisite_digest
            self.data_manifest_sha256 = self.effective_source_sha
            self.frames = [
                h.HistoricalFrame(
                    SnapshotRef(day, self.info, self.effective_source_sha, self.snapshot_id),
                    datetime.combine(day, time(15), tzinfo=MARKET_TIMEZONE),
                    following,
                    h._HistoricalFrameSource(self.reader, following),
                )
                for day, following in self.reader._date_pairs(date(2021, 4, 2), date(2021, 12, 30))
            ]
            report = BaselineFeaturePanelReport(
                **{
                    **self.preparation["feature_report"],
                    "scope_start": date.fromisoformat(
                        self.preparation["feature_report"]["scope_start"]
                    ),
                    "scope_end": date.fromisoformat(
                        self.preparation["feature_report"]["scope_end"]
                    ),
                }
            )
            original = self.prepared / "features.parquet"
            validate_feature_patch(original, pq.read_table(self.feature_patch), ("000792",))
            selected = tuple(sorted({e.panel_security_id for e in self.events}))
            self.panel = self.stack.enter_context(
                scoped_panel_type(h.DayPanel)(
                    original,
                    report,
                    patch=self.feature_patch,
                    selected=selected,
                    start=date(2021, 4, 2),
                    end=date(2021, 12, 30),
                )
            )

        def _load_event_population(self, event_root: Path) -> Any:
            events, mapping = super()._load_event_population(event_root)
            selected = tuple(e for e in events if e.record.event_id in self.entry_predictions)
            if {e.record.event_id for e in selected} != set(self.entry_predictions):
                raise ValueError("ENTRY_CAPITAL_EVENT_BIJECTION")
            for item in selected:
                prediction = self.entry_predictions[item.record.event_id]
                if (
                    item.panel_security_id != prediction["opportunity"]["security_code"]
                    or item.record.identity.signal_date.isoformat()
                    != prediction["opportunity"]["signal_date"]
                ):
                    raise ValueError("ENTRY_CAPITAL_EVENT_COORDINATE")
            return selected, mapping

        def _validate_intervention_id(self, intervention_id: str | None) -> None:
            if intervention_id not in ARMS:
                raise ValueError("ENTRY_CAPITAL_ARM")

        @staticmethod
        def _variant_id(intervention_id: str | None) -> str:
            if intervention_id not in ARMS:
                raise ValueError("ENTRY_CAPITAL_ARM")
            return "variant." + str(intervention_id)

        def _held_intervention_id(self, intervention_id: str | None) -> str:
            self._validate_intervention_id(intervention_id)
            return "intervention.position-risk-base-policy"

        def _evaluate_entry_decision(
            self, runner: Any, *, event_id: str, intervention_id: str | None, **kwargs: Any
        ) -> Any:
            self._validate_intervention_id(intervention_id)
            original = super()._evaluate_entry_decision(
                runner, event_id=event_id, intervention_id="B", **kwargs
            )
            return filtered_decision(
                original,
                arm=str(intervention_id),
                prediction=self.entry_predictions[event_id],
                request_digest=self.successor_digest,
            )

        def _metrics_intervention_id(self, intervention_id: str | None) -> None:
            self._validate_intervention_id(intervention_id)
            # Compute own-arm metrics; paired successor analysis is separate and
            # must never read the old full-market B equity as its comparator.
            return None

        def _code_version(self) -> str:
            return str(super()._code_version()) + ":" + VERSION + ":" + self.successor_digest

        def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
            return {
                **super()._parameter_payload(intervention_id),
                "successor_request_digest": self.successor_digest,
                "successor_scope": ["2021-04-02", "2021-12-30", "2021-12-31"],
                "predictions_digest": digest(self.entry_predictions),
                "new_empty_portfolios": True,
            }

        def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
            return {
                **super()._manifest_metadata(intervention_id),
                "purpose": VERSION,
                "successor_request_digest": self.successor_digest,
                "successor_arm": intervention_id,
                "old_B_fills_inherited": False,
                "historical_PIT_claim": False,
                "consumer_admission": "NOT_GRANTED",
            }

    return MarchRuntime
