"""Opt-in, job-local physical input view for Base 2.4 and canonical CN02 Daily.

This is a warehouse reader, not the legacy seven-dataset research projection.
It preserves native fields/Decimals and knowledge intervals. It never activates
P3/P6, infers missing availability, merges revisions, or rewrites historical Runs.
Qualify once per Job and share the descriptor; independent acceptance opens anew.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import daily_base24
from .base22_sources import _fingerprint, physical_path
from .native_base24_reader import read_native_tables

VERSION = "quanthus.base24-input-view.v1"
TABLES = (
    "dwd_security",
    "dwd_listing",
    "dwd_trading_venue",
    "dwd_trading_calendar",
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_price_limit_regime",
    "dwd_listing_status_interval",
    "dwd_security_identifier",
    "dwd_corporate_action",
    "dwd_corporate_action_term",
    "dwd_adjustment_factor",
    "dwd_quality_exception",
    "dwd_company_industry_membership",
    "dwd_industry_node",
    "dwd_industry_scheme",
)
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


class InputViewError(ValueError):
    """An explicit source binding, scope, or already-qualified file changed."""


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ReadScope:
    business_start: date
    business_end: date
    knowledge_cutoff: datetime

    def __post_init__(self) -> None:
        if (
            type(self.business_start) is not date
            or type(self.business_end) is not date
            or self.business_start > self.business_end
            or self.knowledge_cutoff.tzinfo is None
            or self.knowledge_cutoff.utcoffset() is None
        ):
            raise InputViewError("INPUT_VIEW_EXPLICIT_DATES_AND_AWARE_CUTOFF_REQUIRED")


@dataclass(frozen=True)
class DailySelection:
    root: Path
    market_date: date
    manifest_sha256: str


@dataclass(frozen=True)
class _File:
    path: Path
    fingerprint: tuple[Any, ...]


@dataclass(frozen=True)
class InputView:
    scope: ReadScope
    identity: str
    receipt_json: str
    _files: Mapping[str, tuple[_File, ...]]

    def receipt(self) -> dict[str, Any]:
        """Return a detached safe identity; mutation cannot change this descriptor."""
        return dict(json.loads(self.receipt_json))

    def iter_batches(
        self,
        table: str,
        *,
        columns: tuple[str, ...] | None = None,
        batch_size: int = 8192,
        all_versions_for_governance: bool = False,
    ) -> Iterator[Any]:
        """Stream native rows in scope, without coercion or a legacy fallback.

        Default visibility is [knowledge_from, knowledge_to). Unknown starts do
        not pass. Explicit governance mode retains unknown/late/replaced rows;
        it is not a research/PIT admission. Business intervals overlap the scope;
        observation dates use Shanghai, independently of the UTC timestamp date.
        No source priority or deduplication is invented for overlapping versions.
        """
        pa = importlib.import_module("pyarrow")
        pc = importlib.import_module("pyarrow.compute")
        pq = importlib.import_module("pyarrow.parquet")

        if table not in self._files:
            raise InputViewError("INPUT_VIEW_TABLE_NOT_BOUND")
        if type(batch_size) is not int or not 1 <= batch_size <= 65536:
            raise InputViewError("INPUT_VIEW_BATCH_BOUND_INVALID")
        for member in self._files[table]:
            physical_path(member.path)
            if _fingerprint(member.path) != member.fingerprint:
                raise InputViewError("INPUT_VIEW_MEMBER_CHANGED")
            parquet = pq.ParquetFile(member.path)
            schema = parquet.schema_arrow
            requested = schema.names if columns is None else list(columns)
            if len(requested) != len(set(requested)) or not set(requested) <= set(schema.names):
                raise InputViewError("INPUT_VIEW_COLUMNS_INVALID")
            temporal = _temporal_columns(table, schema.names)
            needed = set(requested) | set(temporal) | {"knowledge_from", "knowledge_to"}
            if table == "dwd_security_bar":
                needed |= {"interval_size", "price_basis"}
            if not needed <= set(schema.names):
                raise InputViewError("INPUT_VIEW_REQUIRED_NATIVE_FIELDS_ABSENT")
            for batch in parquet.iter_batches(
                columns=[name for name in schema.names if name in needed],
                batch_size=batch_size,
                use_threads=False,
            ):
                mask = _business_mask(batch, temporal, self.scope)
                if table == "dwd_security_bar":
                    daily = pc.equal(batch.column("interval_size"), "1d")
                    if pc.any(
                        pc.and_(
                            daily,
                            pc.fill_null(
                                pc.not_equal(batch.column("price_basis"), "UNADJUSTED"), True
                            ),
                        )
                    ).as_py():
                        raise InputViewError("INPUT_VIEW_CANONICAL_DAILY_BASIS_REQUIRED")
                    mask = pc.and_(mask, daily)
                if not all_versions_for_governance:
                    cutoff = pa.scalar(
                        self.scope.knowledge_cutoff, type=schema.field("knowledge_from").type
                    )
                    known = pc.less_equal(batch.column("knowledge_from"), cutoff)
                    until = batch.column("knowledge_to")
                    known = pc.and_(
                        known,
                        pc.or_(pc.is_null(until), pc.fill_null(pc.greater(until, cutoff), False)),
                    )
                    mask = pc.and_(mask, known)
                filtered = batch.filter(pc.fill_null(mask, False))
                if filtered.num_rows:
                    yield filtered.select(requested)
            if _fingerprint(member.path) != member.fingerprint:
                raise InputViewError("INPUT_VIEW_MEMBER_CHANGED")


def _temporal_columns(table: str, names: list[str]) -> tuple[str, ...]:
    if table == "dwd_trading_calendar":
        return ("trade_date",)
    if table in {"dwd_security_bar", "dwd_tradability_state"}:
        return ("observation_start",)
    if table == "dwd_quality_exception":
        return ("affected_effective_at",)
    if "effective_from" in names:
        return ("effective_from", "effective_to")
    return ("effective_at",)


def _business_mask(batch: Any, columns: tuple[str, ...], scope: ReadScope) -> Any:
    pa = importlib.import_module("pyarrow")
    pc = importlib.import_module("pyarrow.compute")

    def day(name: str) -> Any:
        value = batch.column(name)
        if pa.types.is_timestamp(value.type):
            if value.type.tz is None:
                raise InputViewError("INPUT_VIEW_AWARE_NATIVE_CLOCK_REQUIRED")
            value = pc.local_timestamp(pc.cast(value, pa.timestamp("us", tz="Asia/Shanghai")))
        return pc.cast(value, pa.date32())

    start = day(columns[0])
    mask = pc.less_equal(start, scope.business_end)
    if len(columns) == 1:
        return pc.and_(mask, pc.greater_equal(start, scope.business_start))
    # Compare interval endpoints as instants to Shanghai midnight. Converting an
    # exclusive effective_to to a date would wrongly discard same-day overlap.
    from datetime import time
    from zoneinfo import ZoneInfo

    endpoint = batch.column(columns[1])
    midnight = datetime.combine(scope.business_start, time(), ZoneInfo("Asia/Shanghai"))
    return pc.and_(
        mask,
        pc.or_(
            pc.is_null(endpoint),
            pc.fill_null(pc.greater(endpoint, pa.scalar(midnight, type=endpoint.type)), False),
        ),
    )


def open_input_view(
    *,
    base_root: Path,
    base_config: Mapping[str, Any],
    scope: ReadScope,
    code_revision: str,
    policy_digests: Mapping[str, str],
    daily: tuple[DailySelection, ...] = (),
) -> InputView:
    """Verify source bytes once; expose raw 16-table inputs, never admit a consumer.

    Each Daily revision is explicitly pinned. Its original prior is retained;
    a different selected prior revision is disclosed, not silently rebound into
    an accepted P6 chain. Corporate-action normalization/industry authority and
    consumer-specific universe/PIT/entry gates remain separate business steps.
    """
    if (
        not re.fullmatch(r"[0-9a-f]{40}", code_revision)
        or not policy_digests
        or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not _SHA.fullmatch(v)
            for k, v in policy_digests.items()
        )
    ):
        raise InputViewError("INPUT_VIEW_COMPLETE_CODE_AND_POLICY_IDENTITY_REQUIRED")
    dates = [selection.market_date for selection in daily]
    if (
        dates != sorted(set(dates))
        or any(day <= date(2026, 9, 30) or day > scope.business_end for day in dates)
        or any(not _SHA.fullmatch(x.manifest_sha256) for x in daily)
    ):
        raise InputViewError("INPUT_VIEW_DAILY_SELECTION_INVALID")
    if scope.business_end > date(2026, 9, 30) and (not dates or dates[-1] != scope.business_end):
        raise InputViewError("INPUT_VIEW_REQUESTED_TAIL_NOT_BOUND")
    native = read_native_tables(base_root, base_config, TABLES)
    paths = {table: list(native.table_paths[table]) for table in TABLES}
    paths["dwd_security_bar"] = [
        path for path in paths["dwd_security_bar"] if "interval_size=1d" in path.parts
    ]
    if not paths["dwd_security_bar"]:
        raise InputViewError("INPUT_VIEW_NATIVE_DAILY_PARTITIONS_ABSENT")
    receipts = []
    prior_core = None
    for selection in daily:
        root = physical_path(selection.root)
        receipt = daily_base24.verify(root)
        manifest = json.loads((root / "manifest.json").read_bytes())
        core = json.loads((root / "members/core/manifest.json").read_bytes())
        if (
            receipt["manifest_sha256"] != selection.manifest_sha256
            or receipt["market_date"] != selection.market_date.isoformat()
            or any(
                manifest["base"].get(k) != native.binding[k]
                for k in ("manifest_sha256", "content_digest", "materialization_sha256")
            )
        ):
            raise InputViewError("INPUT_VIEW_DAILY_PIN_OR_BASE_DIFFERS")
        for table in set(TABLES) & set(receipt["table_paths"]):
            relative = receipt["table_paths"][table]
            if table == "dwd_security_bar":
                relative = ["payload/1d.parquet"]
            paths[table].extend(root / name for name in relative)
        references = []
        # Core references contain identities first observed after the Base
        # cutoff. They are already verified members of this canonical Daily,
        # not old-root fallbacks; omitting them strands otherwise valid bars.
        for member in manifest.get("files", []):
            parts = Path(member["path"]).parts
            if (
                len(parts) == 5
                and parts[:3] == ("members", "core", "reference")
                and parts[3] in {"dwd_security", "dwd_listing", "dwd_security_identifier"}
                and parts[4] == "part-00000.parquet"
            ):
                table = parts[3]
                paths[table].append(physical_path(root / member["path"]))
                references.append({"table": table, **member})
        original_prior = core.get("prior")
        linked = original_prior.get("manifest_sha256") if original_prior else None
        receipts.append(
            {
                "market_date": receipt["market_date"],
                "manifest_sha256": receipt["manifest_sha256"],
                "core_manifest_sha256": manifest["identity"]["core"],
                "reference_members": references,
                "original_prior": original_prior,
                "selected_previous_core": prior_core,
                "prior_relation": "FIRST_SELECTED_DATE"
                if prior_core is None
                else ("MATCH" if linked == prior_core else "DIFFERENT_REVISION_OR_GAP"),
            }
        )
        prior_core = manifest["identity"]["core"]
    files = {t: tuple(_File(p, _fingerprint(p)) for p in values) for t, values in paths.items()}
    receipt = {
        "schema_version": VERSION,
        "purpose": "p10_prebusiness_input_alignment",
        "base": dict(native.binding),
        "base_config_digest": _digest(dict(base_config)),
        "Daily": receipts,
        "code_revision": code_revision,
        "reader_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "policy_digests": dict(policy_digests),
        "scope": {
            "business_start": scope.business_start.isoformat(),
            "business_end": scope.business_end.isoformat(),
            "knowledge_cutoff": scope.knowledge_cutoff.isoformat(),
            "universe": "ALL_SOURCE_LISTINGS_NO_BUSINESS_SELECTION",
        },
        "table_file_counts": {t: len(values) for t, values in files.items()},
        "revision_merge": "NONE_NATIVE_KNOWLEDGE_VERSIONS_RETAINED",
        "consumer_admission": "NOT_GRANTED",
        "P6_chain_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "legacy_seven_dataset_equivalence": False,
        "coverage_or_historical_PIT_qualification": "NOT_GRANTED_BY_PHYSICAL_VIEW",
        "unknown_knowledge": "excluded_by_as_of_read_retained_in_governance_read",
    }
    return InputView(
        scope, _digest(receipt), json.dumps(receipt, sort_keys=True), MappingProxyType(files)
    )
