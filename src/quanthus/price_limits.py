"""Build the OCI S3 price-limit resolver and integrated execution-day dataset."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any


class PriceLimitBuildError(RuntimeError):
    """Raised when price-limit rules cannot be resolved without silent assumptions."""


@dataclass(frozen=True)
class PriceLimitBuildResult:
    revision: str
    output: Path
    rule_rows: int
    execution_rows: int
    entry_allowed_rows: int
    fail_closed_rows: int
    checksums: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "rule_rows": self.rule_rows,
            "execution_rows": self.execution_rows,
            "entry_allowed_rows": self.entry_allowed_rows,
            "fail_closed_rows": self.fail_closed_rows,
            "checksums": dict(sorted(self.checksums.items())),
        }


@dataclass(frozen=True)
class PriceLimitDecision:
    selected_rule_basis: str
    upper_limit_pct: Decimal | None
    lower_limit_pct: Decimal | None
    upper_limit_price: Decimal | None
    lower_limit_price: Decimal | None
    entry_policy_allows: bool
    is_executable: bool
    reason_code: str


_SUPPORTED_BOARDS = {"SSE_main", "SZ_main", "ChiNext", "STAR"}


def _legacy_board_regimes(policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Translate the immutable v1 shape so historical rebuilds remain reproducible."""

    boards = policy.get("boards")
    if not isinstance(boards, Mapping):
        return []
    required = {
        "SSE_main",
        "SZ_main",
        "ChiNext_pre_2020_08_24",
        "ChiNext_from_2020_08_24",
        "STAR",
    }
    if not required.issubset(boards):
        return []

    def values(key: str) -> tuple[float, float]:
        item = boards[key]
        if not isinstance(item, Mapping):
            raise PriceLimitBuildError(f"boards.{key} must be an object")
        return float(item["normal_pct"]), float(item["st_pct"])

    sse = values("SSE_main")
    sz = values("SZ_main")
    chinext_old = values("ChiNext_pre_2020_08_24")
    chinext_new = values("ChiNext_from_2020_08_24")
    star = values("STAR")
    return [
        {
            "board": "SSE_main",
            "effective_start": "1900-01-01",
            "effective_end": None,
            "normal_pct": sse[0],
            "st_pct": sse[1],
        },
        {
            "board": "SZ_main",
            "effective_start": "1900-01-01",
            "effective_end": None,
            "normal_pct": sz[0],
            "st_pct": sz[1],
        },
        {
            "board": "ChiNext",
            "effective_start": "1900-01-01",
            "effective_end": "2020-08-23",
            "normal_pct": chinext_old[0],
            "st_pct": chinext_old[1],
        },
        {
            "board": "ChiNext",
            "effective_start": "2020-08-24",
            "effective_end": None,
            "normal_pct": chinext_new[0],
            "st_pct": chinext_new[1],
        },
        {
            "board": "STAR",
            "effective_start": "1900-01-01",
            "effective_end": None,
            "normal_pct": star[0],
            "st_pct": star[1],
        },
    ]


def _policy_board_regimes(policy: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = policy.get("board_regimes")
    if raw is None:
        raw = _legacy_board_regimes(policy)
    if not isinstance(raw, list) or not raw:
        raise PriceLimitBuildError("price-limit policy must define board_regimes")

    normalized: list[dict[str, Any]] = []
    by_board: dict[str, list[dict[str, Any]]] = {board: [] for board in _SUPPORTED_BOARDS}
    for index, value in enumerate(raw):
        if not isinstance(value, Mapping):
            raise PriceLimitBuildError(f"board_regimes[{index}] must be an object")
        board = str(value.get("board", ""))
        if board not in _SUPPORTED_BOARDS:
            raise PriceLimitBuildError(f"unsupported board regime: {board}")
        try:
            start = date.fromisoformat(str(value["effective_start"]))
            end_value = value.get("effective_end")
            end = date.fromisoformat(str(end_value)) if end_value is not None else None
            normal_pct = float(value["normal_pct"])
            st_pct = float(value["st_pct"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PriceLimitBuildError(f"invalid board regime at index {index}") from exc
        if end is not None and end < start:
            raise PriceLimitBuildError(f"board regime ends before it starts: {board}")
        if (
            not math.isfinite(normal_pct)
            or not math.isfinite(st_pct)
            or not 0 <= normal_pct <= 100
            or not 0 <= st_pct <= 100
        ):
            raise PriceLimitBuildError(
                f"board regime percentages must be between 0 and 100: {board}"
            )
        item = {
            "board": board,
            "effective_start": start,
            "effective_end": end,
            "normal_pct": normal_pct,
            "st_pct": st_pct,
        }
        normalized.append(item)
        by_board[board].append(item)

    for board, regimes in by_board.items():
        regimes.sort(key=lambda item: item["effective_start"])
        if not regimes or regimes[0]["effective_start"] != date(1900, 1, 1):
            raise PriceLimitBuildError(f"board regimes do not cover supported history: {board}")
        for previous, current in zip(regimes, regimes[1:], strict=False):
            previous_end = previous["effective_end"]
            if previous_end is None or current["effective_start"] != previous_end + timedelta(
                days=1
            ):
                raise PriceLimitBuildError(f"board regimes overlap or contain a gap: {board}")
        if regimes[-1]["effective_end"] is not None:
            raise PriceLimitBuildError(
                f"board regimes must have an open-ended current rule: {board}"
            )
    return tuple(normalized)


def _resolve_board_percentages(
    policy: Mapping[str, Any], *, board: str, market_date: str
) -> tuple[Decimal, Decimal]:
    try:
        session = date.fromisoformat(market_date)
    except ValueError as exc:
        raise PriceLimitBuildError(f"invalid market_date: {market_date}") from exc
    for regime in _policy_board_regimes(policy):
        if regime["board"] != board or session < regime["effective_start"]:
            continue
        end = regime["effective_end"]
        if end is None or session <= end:
            return Decimal(str(regime["normal_pct"])), Decimal(str(regime["st_pct"]))
    raise PriceLimitBuildError(f"no price-limit regime for {board} on {market_date}")


def _sql_regime_case(policy: Mapping[str, Any], field: str) -> str:
    clauses: list[str] = []
    for regime in _policy_board_regimes(policy):
        conditions = [
            f"board = {_sql_string(str(regime['board']))}",
            f"market_date >= DATE {_sql_string(regime['effective_start'].isoformat())}",
        ]
        if regime["effective_end"] is not None:
            conditions.append(
                f"market_date <= DATE {_sql_string(regime['effective_end'].isoformat())}"
            )
        clauses.append("WHEN " + " AND ".join(conditions) + f" THEN {float(regime[field])}")
    return "CASE " + " ".join(clauses) + " ELSE NULL END"


def round_limit_price(reference_price: Decimal, limit_pct: Decimal, *, upper: bool) -> Decimal:
    """Apply exchange-style half-up rounding to a positive A-share limit price."""

    if reference_price <= 0 or limit_pct < 0:
        raise ValueError("reference price must be positive and limit percent non-negative")
    direction = Decimal(1) if upper else Decimal(-1)
    raw = reference_price * (Decimal(1) + direction * limit_pct / Decimal(100))
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def resolve_price_limit_policy(
    *,
    board: str,
    market_date: str,
    status_known: bool,
    is_st_class: bool,
    listing_window_rule: str | None,
    eligible_session_index: int,
    ipo_window_sessions: int,
    observed_tradestatus: str | None,
    prior_trading_close: Decimal | None,
    policy: Mapping[str, Any],
    is_open_session: bool = True,
) -> PriceLimitDecision:
    """Resolve one golden-case decision using the bulk builder's precedence."""

    if not is_open_session:
        return PriceLimitDecision(
            "non_session", None, None, None, None, False, False, "not_open_session"
        )
    normal_pct, st_pct = _resolve_board_percentages(policy, board=board, market_date=market_date)
    in_ipo_window = bool(listing_window_rule and eligible_session_index <= ipo_window_sessions)
    pct: Decimal | None
    if is_st_class:
        basis = "st_overlay"
        pct = st_pct
    elif in_ipo_window:
        basis = "ipo_window"
        pct = Decimal("44") if listing_window_rule == "ipo_main_first_trading_day_44pct" else None
    else:
        basis = "board_baseline"
        pct = normal_pct

    special_uncomputable = in_ipo_window and listing_window_rule in (
        "ipo_main_first_day_legacy_special",
        "ipo_main_first_trading_day_44pct",
    )
    limited = pct is not None
    entry_allowed = (
        status_known
        and not special_uncomputable
        and (not limited or prior_trading_close is not None)
    )
    executable = entry_allowed and observed_tradestatus == "1"
    upper = (
        round_limit_price(prior_trading_close, pct, upper=True)
        if limited and prior_trading_close is not None and not special_uncomputable
        else None
    )
    lower = (
        round_limit_price(prior_trading_close, pct, upper=False)
        if limited and prior_trading_close is not None and not special_uncomputable
        else None
    )
    if observed_tradestatus is None:
        reason = "tradestatus_unknown_fail_closed"
    elif observed_tradestatus != "1":
        reason = "session_not_tradable"
    elif not status_known:
        reason = "st_status_unknown_fail_closed"
    elif in_ipo_window and listing_window_rule == "ipo_main_first_day_legacy_special":
        reason = "legacy_ipo_first_day_rule_not_computable"
    elif in_ipo_window and listing_window_rule == "ipo_main_first_trading_day_44pct":
        reason = "ipo_issue_price_missing_fail_closed"
    elif in_ipo_window and pct is None:
        reason = "ipo_no_limit_session"
    elif limited and prior_trading_close is None and eligible_session_index == 1:
        reason = "first_supported_trade_no_prior_close_fail_closed"
    elif limited and prior_trading_close is None:
        reason = "prior_close_missing_fail_closed"
    elif is_st_class:
        reason = "st_overlay_applied"
    else:
        reason = "price_limit_resolved"
    return PriceLimitDecision(basis, pct, pct, upper, lower, entry_allowed, executable, reason)


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _copy(connection: Any, query: str, path: Path) -> int:
    connection.execute(
        f"COPY ({query}) TO {_sql_string(str(path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    row = connection.execute(f"SELECT count(*) FROM ({query})").fetchone()
    if row is None:
        raise PriceLimitBuildError("row-count query returned no row")
    return int(row[0])


def _schema_hash(connection: Any, path: Path) -> str:
    rows = connection.execute(f"DESCRIBE SELECT * FROM {_source(path)}").fetchall()
    encoded = json.dumps(
        [(str(row[0]), str(row[1]), str(row[2])) for row in rows],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriceLimitBuildError(f"cannot load price-limit policy: {path}") from exc
    if not isinstance(payload.get("policy_version"), str) or not payload["policy_version"].strip():
        raise PriceLimitBuildError("policy_version must be a non-empty string")
    if payload.get("unknown_status_behavior") != "block_new_positions":
        raise PriceLimitBuildError("unknown ST status must block new positions")
    if payload.get("unknown_rule_behavior") != "block_new_positions":
        raise PriceLimitBuildError("unknown price-limit rule must block new positions")
    if payload.get("rounding_rule") != "round_to_tick_0.01_half_up_exchange_style":
        raise PriceLimitBuildError("unsupported price-limit rounding policy")
    _policy_board_regimes(payload)
    return payload


def _validate_inputs(
    rules: Path, identity: Path, s2: Path, output: Path, policy_path: Path
) -> dict[str, Any]:
    required = (
        rules,
        rules.parent / "META.json",
        identity / "daily_universe.parquet",
        identity / "daily_isST.parquet",
        identity / "daily_ohlcv_none.parquet",
        identity / "META.json",
        s2 / "st_status_execution_daily.parquet",
        s2 / "manifest.json",
        policy_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PriceLimitBuildError("required S3 inputs are missing: " + ", ".join(missing))
    if output.exists():
        raise FileExistsError(f"refusing to overwrite S3 revision: {output}")
    meta = json.loads((rules.parent / "META.json").read_text(encoding="utf-8"))
    declared = str(meta.get("sha256", ""))
    if declared and _sha256(rules) != f"sha256:{declared}":
        raise PriceLimitBuildError("price-limit candidate checksum does not match META")
    return meta


def build_price_limit_revision(
    *,
    rules: Path,
    identity_revision: Path,
    s2_revision: Path,
    output: Path,
    revision: str,
    policy_path: Path,
) -> PriceLimitBuildResult:
    """Build an immutable S3 resolver joined to S2 execution state and real sessions."""

    rules = rules.resolve()
    identity = identity_revision.resolve()
    s2 = s2_revision.resolve()
    output = output.resolve()
    policy_path = policy_path.resolve()
    if not revision.strip():
        raise PriceLimitBuildError("revision must not be empty")
    candidate_meta = _validate_inputs(rules, identity, s2, output, policy_path)
    policy = _load_policy(policy_path)
    normal_pct_case = _sql_regime_case(policy, "normal_pct")
    st_pct_case = _sql_regime_case(policy, "st_pct")
    excluded = tuple(str(item) for item in policy.get("excluded_security_ids", []))
    excluded_predicate = (
        "u.security_id NOT IN (" + ", ".join(_sql_string(item) for item in excluded) + ")"
        if excluded
        else "TRUE"
    )

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PriceLimitBuildError("P1 dependencies are missing; run make bootstrap-p1") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        rules_source = _source(rules)
        universe = _source(identity / "daily_universe.parquet")
        provider_daily = _source(identity / "daily_isST.parquet")
        bars = _source(identity / "daily_ohlcv_none.parquet")
        s2_daily = _source(s2 / "st_status_execution_daily.parquet")

        duplicate_rules = int(
            connection.execute(
                f"SELECT count(*) FROM (SELECT interval_id FROM {rules_source} "
                "GROUP BY interval_id HAVING count(*) > 1)"
            ).fetchone()[0]
        )
        if duplicate_rules:
            raise PriceLimitBuildError("candidate contains duplicate interval ids")
        late_rules = int(
            connection.execute(
                f"SELECT count(*) FROM {rules_source} WHERE knowledge_cutoff > effective_start"
            ).fetchone()[0]
        )
        if late_rules:
            raise PriceLimitBuildError("candidate has rules known after their effective start")

        rule_count = _copy(
            connection,
            f"SELECT *, {_sql_string(revision)} AS oci_revision FROM {rules_source} "
            "ORDER BY priority, board, stock_code, effective_start, interval_id",
            staging / "price_limit_regime_pit.parquet",
        )

        query = f"""
            WITH scoped AS (
                SELECT u.*
                FROM {universe} u
                WHERE u.exchange IN ('SSE', 'SZSE')
                  AND u.stock_code NOT LIKE '920%'
                  AND {excluded_predicate}
            ), joined AS (
                SELECT
                    u.*,
                    d.tradestatus,
                    d.raw_shard AS tradestatus_raw_shard,
                    b.close,
                    s.status_label AS st_status_label,
                    s.is_st_class,
                    s.status_known,
                    s.source_market_date AS st_source_market_date,
                    s.revision AS st_revision,
                    CASE
                        WHEN u.exchange = 'SSE' AND u.stock_code LIKE '688%' THEN 'STAR'
                        WHEN u.exchange = 'SZSE' AND (
                            u.stock_code LIKE '300%' OR u.stock_code LIKE '301%'
                            OR u.stock_code LIKE '302%'
                        ) THEN 'ChiNext'
                        WHEN u.exchange = 'SSE' THEN 'SSE_main'
                        ELSE 'SZ_main'
                    END AS board
                FROM scoped u
                LEFT JOIN {provider_daily} d
                  ON d.security_id = u.security_id AND d.date = u.market_date
                LEFT JOIN {bars} b
                  ON b.security_id = u.security_id AND b.date = u.market_date
                LEFT JOIN {s2_daily} s
                  ON s.security_id = u.security_id AND s.execution_date = u.market_date
            ), sequenced AS (
                SELECT
                    *,
                    sum(CASE WHEN tradestatus = '1' THEN 1 ELSE 0 END) OVER (
                        PARTITION BY security_id ORDER BY market_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS eligible_session_index,
                    last_value(CASE WHEN tradestatus = '1' THEN close ELSE NULL END IGNORE NULLS)
                        OVER (
                            PARTITION BY security_id ORDER BY market_date
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                        ) AS prior_trading_close
                FROM joined
            ), with_ipo AS (
                SELECT
                    q.*,
                    i.interval_id AS ipo_interval_id,
                    i.listing_window_rule,
                    CAST(i.no_limit_days AS INTEGER) AS ipo_window_sessions,
                    i.upper_limit_pct AS ipo_upper_pct,
                    i.lower_limit_pct AS ipo_lower_pct,
                    i.knowledge_cutoff AS ipo_knowledge_cutoff,
                    i.source AS ipo_source,
                    CASE
                        WHEN i.interval_id IS NOT NULL
                         AND q.eligible_session_index <= CAST(i.no_limit_days AS INTEGER)
                        THEN true ELSE false
                    END AS in_ipo_window
                FROM sequenced q
                LEFT JOIN {rules_source} i
                  ON i.stock_code = q.stock_code
                 AND i.rule_basis = 'IPO_window'
                 AND q.market_date >= i.effective_start
                 AND (i.effective_end IS NULL OR q.market_date <= i.effective_end)
                 AND i.knowledge_cutoff <= q.market_date
            ), resolved AS (
                SELECT
                    *,
                    CASE
                        WHEN is_st_class AND board IN ('SSE_main', 'SZ_main') THEN 'st_overlay'
                        WHEN is_st_class AND board = 'ChiNext' THEN 'st_overlay'
                        WHEN is_st_class AND board = 'STAR' THEN 'st_overlay'
                        WHEN in_ipo_window THEN 'ipo_window'
                        ELSE 'board_baseline'
                    END AS selected_rule_basis,
                    CASE
                        WHEN is_st_class THEN {st_pct_case}
                        WHEN in_ipo_window THEN ipo_upper_pct
                        ELSE {normal_pct_case}
                    END AS resolved_upper_pct,
                    CASE
                        WHEN is_st_class THEN {st_pct_case}
                        WHEN in_ipo_window THEN ipo_lower_pct
                        ELSE {normal_pct_case}
                    END AS resolved_lower_pct
                FROM with_ipo
            )
            SELECT
                security_id,
                stock_code,
                exchange,
                board,
                CAST(market_date AS DATE) AS execution_date,
                st_status_label,
                is_st_class,
                status_known,
                st_source_market_date,
                st_revision,
                tradestatus AS observed_tradestatus,
                tradestatus_raw_shard,
                CAST(eligible_session_index AS INTEGER) AS eligible_session_index,
                in_ipo_window,
                listing_window_rule,
                ipo_window_sessions,
                selected_rule_basis,
                resolved_upper_pct AS upper_limit_pct,
                resolved_lower_pct AS lower_limit_pct,
                CAST(prior_trading_close AS DECIMAL(18,4)) AS price_reference,
                CASE
                    WHEN in_ipo_window AND listing_window_rule =
                         'ipo_main_first_trading_day_44pct' THEN 'issue_price_missing'
                    WHEN resolved_upper_pct IS NULL AND in_ipo_window THEN 'no_limit'
                    ELSE 'prior_trading_close'
                END AS price_reference_kind,
                CASE
                    WHEN resolved_upper_pct IS NOT NULL
                     AND prior_trading_close IS NOT NULL
                     AND NOT (in_ipo_window AND listing_window_rule =
                              'ipo_main_first_trading_day_44pct')
                    THEN CAST(round(prior_trading_close * (1 + resolved_upper_pct / 100), 2)
                              AS DECIMAL(18,2))
                    ELSE NULL
                END AS upper_limit_price,
                CASE
                    WHEN resolved_lower_pct IS NOT NULL
                     AND prior_trading_close IS NOT NULL
                     AND NOT (in_ipo_window AND listing_window_rule =
                              'ipo_main_first_trading_day_44pct')
                    THEN CAST(round(prior_trading_close * (1 - resolved_lower_pct / 100), 2)
                              AS DECIMAL(18,2))
                    ELSE NULL
                END AS lower_limit_price,
                0.01 AS price_tick,
                'round_to_tick_0.01_half_up_exchange_style' AS rounding_rule,
                CASE
                    WHEN in_ipo_window AND listing_window_rule =
                         'ipo_main_first_day_legacy_special' THEN false
                    ELSE true
                END AS regime_known,
                CASE
                    WHEN NOT coalesce(status_known, false) THEN false
                    WHEN in_ipo_window AND listing_window_rule IN (
                         'ipo_main_first_day_legacy_special',
                         'ipo_main_first_trading_day_44pct') THEN false
                    WHEN resolved_upper_pct IS NOT NULL AND prior_trading_close IS NULL THEN false
                    ELSE true
                END AS entry_policy_allows,
                CASE
                    WHEN tradestatus <> '1' OR tradestatus IS NULL THEN false
                    WHEN NOT coalesce(status_known, false) THEN false
                    WHEN in_ipo_window AND listing_window_rule IN (
                         'ipo_main_first_day_legacy_special',
                         'ipo_main_first_trading_day_44pct') THEN false
                    WHEN resolved_upper_pct IS NOT NULL AND prior_trading_close IS NULL THEN false
                    ELSE true
                END AS is_executable,
                CASE
                    WHEN tradestatus IS NULL THEN 'tradestatus_unknown_fail_closed'
                    WHEN tradestatus <> '1' THEN 'session_not_tradable'
                    WHEN NOT coalesce(status_known, false) THEN 'st_status_unknown_fail_closed'
                    WHEN in_ipo_window AND listing_window_rule =
                         'ipo_main_first_day_legacy_special'
                        THEN 'legacy_ipo_first_day_rule_not_computable'
                    WHEN in_ipo_window AND listing_window_rule =
                         'ipo_main_first_trading_day_44pct'
                        THEN 'ipo_issue_price_missing_fail_closed'
                    WHEN in_ipo_window AND resolved_upper_pct IS NULL THEN 'ipo_no_limit_session'
                    WHEN resolved_upper_pct IS NOT NULL AND prior_trading_close IS NULL
                         AND eligible_session_index = 1
                        THEN 'first_supported_trade_no_prior_close_fail_closed'
                    WHEN resolved_upper_pct IS NOT NULL AND prior_trading_close IS NULL
                        THEN 'prior_close_missing_fail_closed'
                    WHEN is_st_class THEN 'st_overlay_applied'
                    ELSE 'price_limit_resolved'
                END AS reason_code,
                greatest(
                    CAST(market_date AS DATE),
                    coalesce(CAST(ipo_knowledge_cutoff AS DATE), DATE '1900-01-01')
                ) AS knowledge_cutoff,
                ipo_interval_id,
                ipo_source,
                {_sql_string(revision)} AS revision
            FROM resolved
            ORDER BY security_id, market_date, stock_code
        """
        execution_count = _copy(connection, query, staging / "price_limit_execution_daily.parquet")
        duplicate_keys = int(
            connection.execute(
                f"SELECT count(*) FROM (SELECT security_id, execution_date FROM ({query}) "
                "GROUP BY security_id, execution_date HAVING count(*) > 1)"
            ).fetchone()[0]
        )
        if duplicate_keys:
            raise PriceLimitBuildError("S3 resolver produced duplicate execution keys")
        bad_ipo_counts = int(
            connection.execute(
                f"SELECT count(*) FROM ({query}) WHERE in_ipo_window "
                "AND eligible_session_index > ipo_window_sessions"
            ).fetchone()[0]
        )
        if bad_ipo_counts:
            raise PriceLimitBuildError("IPO window exceeds eligible trading-session count")
        bad_unknown = int(
            connection.execute(
                f"SELECT count(*) FROM ({query}) WHERE NOT status_known AND entry_policy_allows"
            ).fetchone()[0]
        )
        if bad_unknown:
            raise PriceLimitBuildError("unknown ST status permits a new entry")
        unknown_regimes = int(
            connection.execute(
                f"SELECT count(*) FROM ({query}) WHERE NOT in_ipo_window "
                "AND (upper_limit_pct IS NULL OR lower_limit_pct IS NULL)"
            ).fetchone()[0]
        )
        if unknown_regimes:
            raise PriceLimitBuildError("policy does not resolve every board/date regime")
        counts = connection.execute(
            f"SELECT count(*) FILTER (WHERE entry_policy_allows), "
            f"count(*) FILTER (WHERE NOT entry_policy_allows) FROM ({query})"
        ).fetchone()
        if counts is None:
            raise PriceLimitBuildError("S3 entry-policy count returned no row")
        entry_allowed = int(counts[0])
        fail_closed = int(counts[1])
        distribution = {
            str(key): int(value)
            for key, value in connection.execute(
                f"SELECT reason_code, count(*) FROM ({query}) GROUP BY reason_code"
            ).fetchall()
        }
    finally:
        connection.close()

    try:
        names = ("price_limit_regime_pit.parquet", "price_limit_execution_daily.parquet")
        checksums = {name: _sha256(staging / name) for name in names}
        connection = duckdb.connect(":memory:")
        try:
            schema_hashes = {name: _schema_hash(connection, staging / name) for name in names}
        finally:
            connection.close()
        manifest = {
            "schema_version": "1.0.0",
            "dataset": "price_limit_execution_daily",
            "revision": revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "policy_version": policy["policy_version"],
            "policy_sha256": _sha256(policy_path),
            "source_revisions": {
                "hermes_rule_candidate": str(candidate_meta.get("revision")),
                "hermes_rule_sha256": _sha256(rules),
                "identity_revision": identity.name,
                "identity_manifest_sha256": _sha256(identity / "META.json"),
                "s2_revision": s2.name,
                "s2_manifest_sha256": _sha256(s2 / "manifest.json"),
            },
            "datasets": {
                name.removesuffix(".parquet"): {
                    "path": name,
                    "rows": rule_count
                    if name.startswith("price_limit_regime")
                    else execution_count,
                    "sha256": checksums[name],
                    "schema_hash": schema_hashes[name],
                }
                for name in names
            },
            "reason_distribution": dict(sorted(distribution.items())),
            "entry_allowed_rows": entry_allowed,
            "fail_closed_rows": fail_closed,
            "constraints": {
                "immutable_revision": True,
                "duplicate_execution_keys": 0,
                "unknown_status_entry_allowed": 0,
                "ipo_window_overrun": 0,
                "unknown_board_date_regimes": 0,
                "non_session_rows": 0,
                "suspension_consumes_ipo_session": False,
                "excluded_security_ids": list(excluded),
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "CHECKSUMS.sha256").write_text(
            "\n".join(
                f"{checksum.removeprefix('sha256:')}  {name}"
                for name, checksum in sorted(checksums.items())
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return PriceLimitBuildResult(
        revision=revision,
        output=output,
        rule_rows=rule_count,
        execution_rows=execution_count,
        entry_allowed_rows=entry_allowed,
        fail_closed_rows=fail_closed,
        checksums=checksums,
    )


def write_price_limit_current(*, revision_dir: Path, pointer: Path) -> None:
    manifest = revision_dir / "manifest.json"
    if not manifest.is_file():
        raise PriceLimitBuildError(f"revision has no manifest: {revision_dir}")
    payload = {
        "dataset": "price_limit_execution_daily",
        "revision": revision_dir.name,
        "revision_path": str(revision_dir.resolve()),
        "manifest_sha256": _sha256(manifest),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    pointer.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, pointer)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
