"""Direct DWD projection replay for external-strategy conformance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.backtest.contracts import (
    BacktestRunSpec,
    CorporateAction,
    CorporateActionStep,
    MarketSession,
    OpenExecutionState,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.inputs import CoverageStatus
from quanthus.backtest.policy import ExecutionPolicy
from quanthus.backtest.strategy_replay import (
    PITDailyBar,
    StrategyReplayFrame,
    StrategyReplayResult,
    StrategyReplaySpec,
    run_strategy_replay,
)
from quanthus.candidate_strategy.contract import CandidateManifest
from quanthus.candidate_strategy.quantdinger_adapter import build_quantdinger_adapter
from quanthus.contracts import SnapshotRef

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
EXPECTED_PACKAGE_ID = "dwd-base-202607-v2.1"
EXPECTED_CONTENT_DIGEST = "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
EXPECTED_CORRECTION_ID = "dwd-base-202607-v2.1-correction-20260813-001"


class DwdConformanceError(ValueError):
    """Raised when a direct DWD projection cannot support a comparable replay."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _decimal(value: object) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise DwdConformanceError("DWD projection contains a non-finite decimal")
    return parsed


@dataclass(frozen=True)
class DirectDwdProjectionIdentity:
    package_id: str
    content_digest: str
    manifest_sha256: str
    projection_sha256: str
    listing_id: str
    security_id: str
    venue_id: str
    listing_code: str
    source_symbol: str
    lot_size: int
    price_basis: str
    scope_start: date
    scope_end: date
    row_count: int
    historical_knowledge_from: str
    corporate_action_status: str
    corporate_action_duplicate_groups: int

    @classmethod
    def load(cls, path: Path, projection_path: Path) -> DirectDwdProjectionIdentity:
        raw = json.loads(path.read_text(encoding="utf-8"))
        identity = cls(
            package_id=str(raw["dwd"]["package_id"]),
            content_digest=str(raw["dwd"]["content_digest"]),
            manifest_sha256=str(raw["dwd"]["manifest_sha256"]),
            projection_sha256=str(raw["projection"]["sha256"]),
            listing_id=str(raw["security"]["listing_id"]),
            security_id=str(raw["security"]["security_id"]),
            venue_id=str(raw["security"]["venue_id"]),
            listing_code=str(raw["security"]["listing_code"]),
            source_symbol=str(raw["security"]["source_symbol"]),
            lot_size=int(raw["security"]["lot_size"]),
            price_basis=str(raw["projection"]["price_basis"]),
            scope_start=date.fromisoformat(raw["projection"]["scope_start"]),
            scope_end=date.fromisoformat(raw["projection"]["scope_end"]),
            row_count=int(raw["projection"]["row_count"]),
            historical_knowledge_from=str(raw["projection"]["historical_knowledge_from"]),
            corporate_action_status=str(raw["corporate_actions"]["status"]),
            corporate_action_duplicate_groups=int(
                raw["corporate_actions"]["duplicate_semantic_groups"]
            ),
        )
        identity.validate(projection_path)
        return identity

    def validate(self, projection_path: Path) -> None:
        if self.package_id != EXPECTED_PACKAGE_ID:
            raise DwdConformanceError("projection is not bound to DWD v2.1")
        if self.content_digest != EXPECTED_CONTENT_DIGEST:
            raise DwdConformanceError("DWD v2.1 content digest does not match the frozen package")
        if self.listing_code != "600519" or self.source_symbol != "CNStock:600519.SH":
            raise DwdConformanceError("projection security does not match the frozen candidate")
        if self.lot_size != 100 or self.price_basis != "UNADJUSTED":
            raise DwdConformanceError("projection lot size or price basis is not comparable")
        if self.projection_sha256 != _sha256(projection_path):
            raise DwdConformanceError("DWD projection checksum does not match its identity")


@dataclass(frozen=True)
class DirectDwdRow:
    market_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal
    tradable: bool
    can_buy: bool
    can_sell: bool
    reason_code: str
    upper_price: Decimal | None
    lower_price: Decimal | None
    source_revision: str


@dataclass(frozen=True)
class NormalizedAction:
    action_id: str
    effective_date: date
    known_at: datetime
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    source_revision: str
    source_snapshot_id: str
    evidence_digest: str


def load_normalized_actions(root: Path) -> tuple[NormalizedAction, ...]:
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("kind") != "dwd_base_correction":
        raise DwdConformanceError("corporate actions must come from a DWD correction")
    if manifest.get("correction_id") != EXPECTED_CORRECTION_ID:
        raise DwdConformanceError("unexpected DWD corporate-action correction")
    parent = manifest.get("parent", {})
    if (
        parent.get("package_id") != EXPECTED_PACKAGE_ID
        or parent.get("content_digest") != EXPECTED_CONTENT_DIGEST
    ):
        raise DwdConformanceError("DWD correction parent binding differs")
    normalization = manifest.get("normalization_revision", {})
    if (
        normalization.get("revision") != "corporate_action_replay_2012_20260730_v3"
        or normalization.get("policy_version") != "p1-corporate-action-normalization-20260730-v3"
    ):
        raise DwdConformanceError("DWD correction normalization policy is not v3")
    if (root / "READY").read_text(encoding="ascii").strip() != _sha256(manifest_path):
        raise DwdConformanceError("DWD correction is not READY")
    action_path = root / "payload/replay/corporate_actions_normalized.parquet"
    exception_path = root / "payload/replay/corporate_action_exceptions.parquet"
    inventory = {
        str(item.get("path")): item for item in manifest.get("files", []) if isinstance(item, dict)
    }
    for path in (action_path, exception_path):
        relative = path.relative_to(root).as_posix()
        if inventory.get(relative, {}).get("sha256") != _sha256(path):
            raise DwdConformanceError(f"DWD correction replay checksum failed: {relative}")
    try:
        import pyarrow.parquet as parquet
    except ModuleNotFoundError as exc:
        raise DwdConformanceError("corporate-action loading requires p1 dependencies") from exc
    exceptions = [
        row
        for row in parquet.read_table(exception_path).to_pylist()
        if row["security_code"] == "600519"
    ]
    if exceptions:
        raise DwdConformanceError("600519 is blocked by the corporate-action exception ledger")
    actions = []
    for row in parquet.read_table(action_path).to_pylist():
        if row["security_code"] != "600519":
            continue
        if not date(2012, 1, 4) <= row["effective_date"] <= date(2021, 12, 31):
            continue
        if row["factor_cross_check_status"] != "matched":
            raise DwdConformanceError("600519 corporate action is not factor matched")
        actions.append(
            NormalizedAction(
                action_id=str(row["action_id"]),
                effective_date=row["effective_date"],
                known_at=row["known_at"],
                cash_dividend_per_share=_decimal(row["cash_dividend_per_share"]),
                bonus_share_ratio=_decimal(row["bonus_share_ratio"]),
                source_revision=str(row["source_revision"]),
                source_snapshot_id=str(row["source_snapshot_id"]),
                evidence_digest=str(row["evidence_digest"]),
            )
        )
    dates = [action.effective_date for action in actions]
    if len(dates) != len(set(dates)):
        raise DwdConformanceError("600519 normalized actions are not unique by effective date")
    return tuple(sorted(actions, key=lambda action: (action.effective_date, action.action_id)))


def load_direct_dwd_rows(
    path: Path, identity: DirectDwdProjectionIdentity
) -> tuple[DirectDwdRow, ...]:
    try:
        import pyarrow.parquet as parquet
    except ModuleNotFoundError as exc:
        raise DwdConformanceError("DWD conformance requires the p1 dependency group") from exc
    table = parquet.read_table(path)
    rows: list[DirectDwdRow] = []
    for raw in table.to_pylist():
        rows.append(
            DirectDwdRow(
                market_date=raw["date"],
                open_price=_decimal(raw["open"]),
                high_price=_decimal(raw["high"]),
                low_price=_decimal(raw["low"]),
                close_price=_decimal(raw["close"]),
                volume=_decimal(raw["volume"]),
                turnover=_decimal(raw["turnover"]),
                tradable=bool(raw["is_tradable"]),
                can_buy=bool(raw["can_buy"]),
                can_sell=bool(raw["can_sell"]),
                reason_code=str(raw["reason_code"]),
                upper_price=None if raw["upper_price"] is None else _decimal(raw["upper_price"]),
                lower_price=None if raw["lower_price"] is None else _decimal(raw["lower_price"]),
                source_revision=str(raw["bar_source_revision"]),
            )
        )
    if len(rows) != identity.row_count or not rows:
        raise DwdConformanceError("DWD projection row count does not match its identity")
    dates = tuple(row.market_date for row in rows)
    if dates != tuple(sorted(set(dates))):
        raise DwdConformanceError("DWD projection dates must be unique and ordered")
    if dates[0] != identity.scope_start or dates[-1] != identity.scope_end:
        raise DwdConformanceError("DWD projection date scope does not match its identity")
    return tuple(rows)


def _snapshot(identity: DirectDwdProjectionIdentity, market_date: date) -> SnapshotRef:
    cutoff = datetime.combine(market_date, time(15), tzinfo=MARKET_TIMEZONE)
    return SnapshotRef(
        market_date=market_date,
        knowledge_cutoff=cutoff,
        source_revision=f"{identity.package_id}:retrospective-market-observation",
        snapshot_id=f"dwd-v2-1-600519-{market_date.isoformat()}",
    )


def _pit_bar(row: DirectDwdRow, identity: DirectDwdProjectionIdentity) -> PITDailyBar:
    ohlc_valid = (
        row.low_price <= row.open_price <= row.high_price
        and row.low_price <= row.close_price <= row.high_price
    )
    return PITDailyBar(
        security_code=identity.listing_code,
        exchange="SSE",
        market_date=row.market_date,
        coverage_status=CoverageStatus.TRADING,
        data_valid=ohlc_valid,
        is_executable=ohlc_valid and row.tradable and row.volume > 0 and row.turnover > 0,
        entry_policy_allows=(
            ohlc_valid and row.tradable and row.can_buy and row.volume > 0 and row.turnover > 0
        ),
        reason_code=row.reason_code,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        volume=row.volume,
        amount=row.turnover,
        upper_limit_price=row.upper_price,
        lower_limit_price=row.lower_price,
    )


class DirectDwdMarketView:
    def __init__(
        self,
        identity: DirectDwdProjectionIdentity,
        bars: tuple[PITDailyBar, ...],
        index: int,
    ) -> None:
        self._identity = identity
        self._bars = bars
        self._index = index
        self._snapshot = _snapshot(identity, bars[index].market_date)

    @property
    def snapshot(self) -> SnapshotRef:
        return self._snapshot

    def securities(self) -> tuple[str, ...]:
        return (self._identity.listing_code,)

    def current_bars(self) -> tuple[PITDailyBar, ...]:
        return (self._bars[self._index],)

    def bar(self, security_code: str) -> PITDailyBar:
        if security_code != self._identity.listing_code:
            raise KeyError(security_code)
        return self._bars[self._index]

    def history(self, security_code: str, sessions: int) -> tuple[PITDailyBar, ...]:
        if security_code != self._identity.listing_code:
            raise KeyError(security_code)
        start = max(0, self._index - sessions + 1)
        return self._bars[start : self._index + 1]


class DirectDwdFrameSource:
    def __init__(
        self,
        *,
        identity: DirectDwdProjectionIdentity,
        execution_row: DirectDwdRow,
        actions: Mapping[date, NormalizedAction],
    ) -> None:
        self.identity = identity
        self.execution_row = execution_row
        self.actions_by_date = actions

    def block_reason(self, security_code: str) -> str | None:
        if security_code != self.identity.listing_code:
            return "security_outside_projection"
        return None

    def corporate_actions(
        self,
        security_code: str,
        prior_mark_price: Decimal,
    ) -> tuple[CorporateActionStep, ...]:
        if security_code != self.identity.listing_code:
            raise DwdConformanceError("corporate action security is outside the projection")
        normalized = self.actions_by_date.get(self.execution_row.market_date)
        if normalized is None:
            return ()
        denominator = Decimal("1") + normalized.bonus_share_ratio
        mark = ((prior_mark_price - normalized.cash_dividend_per_share) / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if mark <= 0:
            raise DwdConformanceError("corporate action produced a non-positive mark")
        applied_at = datetime.combine(normalized.effective_date, time(9), tzinfo=MARKET_TIMEZONE)
        return (
            CorporateActionStep(
                action=CorporateAction(
                    action_id=normalized.action_id,
                    security_code=security_code,
                    effective_date=normalized.effective_date,
                    known_at=normalized.known_at,
                    cash_dividend_per_share=normalized.cash_dividend_per_share,
                    bonus_share_ratio=normalized.bonus_share_ratio,
                    source_revision=(
                        f"{normalized.source_revision}|evidence={normalized.evidence_digest}"
                    ),
                    snapshot_id=normalized.source_snapshot_id,
                ),
                applied_at=applied_at,
                post_action_mark_price=mark,
                mark_snapshot_id=(
                    f"{self.identity.package_id}.normalized-action.{normalized.action_id}"
                ),
            ),
        )


def _execution_state(row: DirectDwdRow) -> OpenExecutionState:
    if not row.tradable:
        if row.reason_code == "ZERO_ACTIVITY_OBSERVATION":
            return OpenExecutionState.NON_TRADING_OBSERVED
        return OpenExecutionState.SUSPENDED
    if not row.can_buy and row.can_sell:
        return OpenExecutionState.BUY_BLOCKED_LIMIT_UP
    if row.can_buy and not row.can_sell:
        return OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
    if not row.can_buy and not row.can_sell:
        return OpenExecutionState.MISSING_EVIDENCE
    return OpenExecutionState.EXECUTABLE


def build_direct_dwd_spec(
    *,
    identity: DirectDwdProjectionIdentity,
    rows: tuple[DirectDwdRow, ...],
    manifest: CandidateManifest,
    policy: ExecutionPolicy,
    initial_cash: Decimal,
    scope_start: date,
    scope_end: date,
    actions: tuple[NormalizedAction, ...] = (),
) -> StrategyReplaySpec:
    if (
        manifest.security_code != identity.listing_code
        or manifest.source_symbol != identity.source_symbol
    ):
        raise DwdConformanceError("candidate identity does not match the DWD projection")
    bars = tuple(_pit_bar(row, identity) for row in rows)
    frames: list[StrategyReplayFrame] = []
    actions_by_date = {action.effective_date: action for action in actions}
    for index, (decision_row, execution_row) in enumerate(zip(rows, rows[1:], strict=False)):
        if not scope_start <= decision_row.market_date <= scope_end:
            continue
        decision_view = DirectDwdMarketView(identity, bars, index)
        execution_snapshot = _snapshot(identity, execution_row.market_date)
        frames.append(
            StrategyReplayFrame(
                snapshot=decision_view.snapshot,
                decision_at=decision_view.snapshot.knowledge_cutoff,
                execution_date=execution_row.market_date,
                execution_sessions=(
                    MarketSession(
                        session_date=execution_row.market_date,
                        open_at=datetime.combine(
                            execution_row.market_date, time(9, 30), tzinfo=MARKET_TIMEZONE
                        ),
                        close_at=datetime.combine(
                            execution_row.market_date, time(15), tzinfo=MARKET_TIMEZONE
                        ),
                        security_code=identity.listing_code,
                        open_price=execution_row.open_price,
                        close_price=execution_row.close_price,
                        open_execution_state=_execution_state(execution_row),
                        source_revision=execution_row.source_revision,
                        snapshot_id=execution_snapshot.snapshot_id,
                    ),
                ),
                decision_view=decision_view,
                source=DirectDwdFrameSource(
                    identity=identity,
                    execution_row=execution_row,
                    actions=actions_by_date,
                ),
            )
        )
    if not frames:
        raise DwdConformanceError("requested DWD replay scope produced no frames")
    run = BacktestRunSpec(
        code_version="dwd-direct-conformance-v1",
        parameter_hash=manifest.digest,
        data_snapshot_id=identity.package_id,
        data_content_digest=identity.content_digest,
        data_manifest_sha256=identity.manifest_sha256,
        feature_version=manifest.adapter_contract,
        rule_version=manifest.candidate_version,
        run_id=f"dwd-v2-1-quantdinger-{scope_start}-{scope_end}",
        execution_policy_version=policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version=policy.profile("close-to-next-open-v1").schedule_version,
        official=False,
    )
    return StrategyReplaySpec(
        run=run,
        unit_id="dwd-v2-1-external-conformance",
        initial_cash=initial_cash,
        frames=tuple(frames),
    )


def common_cost_policy(policy: ExecutionPolicy) -> ExecutionPolicy:
    """Match QuantDinger's commission/slippage model for engine comparison."""

    return replace(
        policy,
        policy_version=f"{policy.policy_version}-common-cost-conformance",
        buy_cost=replace(policy.buy_cost, minimum_commission=Decimal("0")),
        sell_cost=replace(
            policy.sell_cost,
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
        ),
    )


def run_direct_dwd_candidate(
    *,
    projection_path: Path,
    identity_path: Path,
    manifest: CandidateManifest,
    policy: ExecutionPolicy,
    initial_cash: Decimal,
    scope_start: date,
    scope_end: date,
    corporate_action_root: Path | None = None,
    common_costs: bool = True,
) -> tuple[StrategyReplayResult, Mapping[str, Any]]:
    identity = DirectDwdProjectionIdentity.load(identity_path, projection_path)
    rows = load_direct_dwd_rows(projection_path, identity)
    replay_policy = common_cost_policy(policy) if common_costs else policy
    actions = (
        () if corporate_action_root is None else load_normalized_actions(corporate_action_root)
    )
    spec = build_direct_dwd_spec(
        identity=identity,
        rows=rows,
        manifest=manifest,
        policy=replay_policy,
        initial_cash=initial_cash,
        scope_start=scope_start,
        scope_end=scope_end,
        actions=actions,
    )
    result = run_strategy_replay(
        BacktestEngine(replay_policy), build_quantdinger_adapter(manifest), spec
    )
    lineage = {
        "package_id": identity.package_id,
        "content_digest": identity.content_digest,
        "manifest_sha256": identity.manifest_sha256,
        "projection_sha256": identity.projection_sha256,
        "price_basis": identity.price_basis,
        "historical_knowledge_from": identity.historical_knowledge_from,
        "point_in_time_publication_proven": False,
        "replay_availability_assumption": "market bar available at its session close",
        "corporate_action_status": identity.corporate_action_status,
        "corporate_action_duplicate_groups": identity.corporate_action_duplicate_groups,
        "normalized_corporate_action_revision": (
            None if corporate_action_root is None else EXPECTED_CORRECTION_ID
        ),
        "cost_model": "common-simplified" if common_costs else policy.policy_version,
    }
    return result, lineage
