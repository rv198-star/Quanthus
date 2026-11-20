"""Manifest-driven, dependency-free WSGI transport for the Platform Kernel."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from quanthus.data_catalog import DataCatalogError, DataIndicatorCatalog
from quanthus.expert_evaluation.product_view import (
    ProductExpertEvidence,
    ProductExpertEvidenceError,
)
from quanthus.forward_validation.product_view import (
    ForwardProductViewError,
    ForwardValidationProductView,
)
from quanthus.platform.candidate_approval import (
    CandidateApprovalService,
    public_candidate_approval_event,
)
from quanthus.platform.identity import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthorizationError,
    IdentityService,
    MfaRequiredError,
    public_user,
)
from quanthus.platform.jobs import (
    ATTRIBUTION_VERIFICATION_JOB,
    CANDIDATE_REPLAY_JOB,
    STRUCTURE_MVP_JOB,
    DurableJobQueue,
    JobNotCancelableError,
    LocalJobRunner,
    public_job,
)
from quanthus.platform.manifest import FeatureManifest
from quanthus.platform.observability import JsonFormatter, Metrics, request_id, request_id_context
from quanthus.platform.p12.http import P12Endpoints, P12Runtime
from quanthus.platform.persistence import (
    DuplicateRecordError,
    IdempotencyConflictError,
    PlatformStore,
)
from quanthus.platform.ports import GateQueryPort
from quanthus.platform.product_query import ProductCandidateEvidence, ProductResearchCatalog

OPENAPI_VERSION = "3.1.0"
MAX_BODY_BYTES = 64 * 1024
_STRUCTURE_MVP_HEALTH_PROBE_ENV = "QUANTHUS_STRUCTURE_MVP_HEALTH_PROBE"
_STRUCTURE_MVP_EVENT_RUN_ROOT = Path("/data/event-runs")
_STRUCTURE_MVP_SEED_RECEIPT = _STRUCTURE_MVP_EVENT_RUN_ROOT / "structure-qualification-seed.json"
_STRUCTURE_MVP_RECEIPT_MAX_BYTES = 64 * 1024
_STRUCTURE_MVP_REVIEW_MAX_BYTES = 2 * 1024 * 1024
_STRUCTURE_MVP_JOB_RE = re.compile(r"^job_[0-9a-f]{32}$")
_STRUCTURE_FACTS_HEALTH_PROBE_ENV = "QUANTHUS_STRUCTURE_FACTS_HEALTH_PROBE"
_STRUCTURE_FACTS_RUN_CONFIG = "config/structure-facts-run.development.v1.json"
_STRUCTURE_FACTS_SECURITY_ORDER = ("000001", "300750", "600519")
_STRUCTURE_FACTS_REVIEW_MAX_BYTES = 2 * 1024 * 1024
_STRUCTURE_FACTS_REVIEW_TARGET = 12
StartResponse = Callable[[str, list[tuple[str, str]]], object]
Handler = Callable[["HttpRequest", Mapping[str, str]], "HttpResponse"]


@dataclass(frozen=True)
class HttpRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    request_id: str
    query_string: str = ""

    def json_object(self) -> dict[str, Any]:
        try:
            value: Any = json.loads(self.body) if self.body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str
    pattern: re.Pattern[str]
    handler: Handler
    public: bool


def _json_response(payload: Mapping[str, Any], status: int = 200) -> HttpResponse:
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return HttpResponse(
        status=status,
        body=body,
        headers=(("Content-Type", "application/json"),),
    )


def _text_response(body: str, content_type: str = "text/plain") -> HttpResponse:
    return HttpResponse(
        status=200,
        body=body.encode("utf-8"),
        headers=(("Content-Type", f"{content_type}; charset=utf-8"),),
    )


def _error(reason: str, status: int, observed_request_id: str) -> HttpResponse:
    return _json_response(
        {"error": {"reason": reason, "request_id": observed_request_id}},
        status,
    )


def _bearer_token(observed_request: HttpRequest) -> str:
    value = observed_request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("authentication_required")
    return token


def _principal(
    identity: IdentityService,
    observed_request: HttpRequest,
    permission: str,
) -> AuthenticatedPrincipal:
    principal = identity.authenticate(_bearer_token(observed_request))
    identity.require(principal, permission)
    return principal


def _structure_mvp_review_sample(
    event_run_root: Path,
    job_id: str,
) -> list[dict[str, Any]]:
    if _STRUCTURE_MVP_JOB_RE.fullmatch(job_id) is None:
        return []
    run_id = f"run_{job_id.removeprefix('job_')}"
    root = event_run_root.resolve()
    path = (root / "structure-runs" / run_id / "structure-adm.jsonl").resolve()
    if root not in path.parents:
        return []
    try:
        if not path.is_file() or path.stat().st_size > _STRUCTURE_MVP_REVIEW_MAX_BYTES:
            return []
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return []
    if any(not isinstance(row, dict) for row in rows):
        return []
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    fields = (
        "market_date",
        "security_code",
        "state",
        "close",
        "prior_high20",
        "prior_low20",
        "volume_ratio20",
        "reason_code",
        "knowledge_cutoff",
    )
    for row in rows:
        state = row.get("state")
        if not isinstance(state, str):
            return []
        count = counts.get(state, 0)
        if count >= 2:
            continue
        if any(field not in row for field in fields):
            return []
        selected.append({field: row[field] for field in fields})
        counts[state] = count + 1
    return sorted(
        selected,
        key=lambda row: (str(row["state"]), str(row["market_date"]), str(row["security_code"])),
    )


def _structure_facts_review_sample(
    event_run_root: Path,
    job_id: str,
    security_order: tuple[str, ...],
) -> list[dict[str, Any]]:
    if _STRUCTURE_MVP_JOB_RE.fullmatch(job_id) is None or not security_order:
        return []
    run_id = f"run_{job_id.removeprefix('job_')}"
    root = event_run_root.resolve()
    path = (root / "structure-runs" / run_id / "structure-facts.jsonl").resolve()
    if root not in path.parents:
        return []
    try:
        if not path.is_file() or path.stat().st_size > _STRUCTURE_FACTS_REVIEW_MAX_BYTES:
            return []
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return []
    if any(not isinstance(row, dict) for row in rows):
        return []
    rows = sorted(
        rows,
        key=lambda row: (str(row.get("security_code", "")), str(row.get("market_date", ""))),
    )
    by_security = {
        code: [row for row in rows if row.get("security_code") == code]
        for code in security_order
    }
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()

    def add_first(code: str, predicate: Callable[[dict[str, Any]], bool]) -> None:
        for row in by_security[code]:
            if not predicate(row):
                continue
            key = (str(row.get("security_code")), str(row.get("market_date")))
            if key not in selected_keys:
                selected.append(row)
                selected_keys.add(key)
            return

    for code in security_order:
        add_first(
            code,
            lambda row: row.get("episode_status") == "active"
            and row.get("episode_age_sessions") == 0,
        )
        add_first(
            code,
            lambda row: row.get("episode_status") == "active"
            and isinstance(row.get("episode_age_sessions"), int)
            and int(row["episode_age_sessions"]) >= 1,
        )
        add_first(code, lambda row: row.get("episode_status") == "ended")

    fill_buckets = {
        code: [
            row
            for row in by_security[code]
            if row.get("available") is True
            and (str(row.get("security_code")), str(row.get("market_date")))
            not in selected_keys
        ]
        for code in security_order
    }
    fill_index = 0
    while len(selected) < _STRUCTURE_FACTS_REVIEW_TARGET:
        added = False
        for code in security_order:
            bucket = fill_buckets[code]
            if fill_index >= len(bucket):
                continue
            row = bucket[fill_index]
            key = (str(row.get("security_code")), str(row.get("market_date")))
            if key not in selected_keys:
                selected.append(row)
                selected_keys.add(key)
                added = True
                if len(selected) >= _STRUCTURE_FACTS_REVIEW_TARGET:
                    break
        if not added:
            break
        fill_index += 1

    fields = (
        "market_date",
        "security_code",
        "dwd_security_id",
        "snapshot_id",
        "knowledge_cutoff",
        "source_close",
        "available",
        "availability_reason",
        "history_reset",
        "source_window_start",
        "source_window_end",
        "rolling_resistance20",
        "rolling_support20",
        "resistance_margin",
        "support_margin",
        "volume_ratio20",
        "episode_id",
        "episode_status",
        "episode_termination_reason",
        "attempt_at",
        "attempt_snapshot_id",
        "breakout_level",
        "episode_support_level",
        "attempt_volume_ratio20",
        "episode_anchor_window_start",
        "episode_anchor_window_end",
        "breakout_margin",
        "episode_support_margin",
        "post_attempt_hold_count",
        "episode_age_sessions",
    )
    projected: list[dict[str, Any]] = []
    for row in selected[:_STRUCTURE_FACTS_REVIEW_TARGET]:
        if any(field not in row for field in fields):
            return []
        payload = {field: row[field] for field in fields}
        canonical = json.dumps(
            row,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["row_digest"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        projected.append(payload)
    return projected


def _compile_path(path: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"<([a-z][a-z0-9_]*)>", path):
        parts.append(re.escape(path[cursor : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    parts.append(re.escape(path[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


class PlatformHttpApp:
    """Eager WSGI application whose complete route set comes from one manifest."""

    def __init__(
        self,
        *,
        manifest: FeatureManifest,
        store: PlatformStore,
        identity: IdentityService,
        jobs: LocalJobRunner,
        replay_jobs: DurableJobQueue,
        gate_query: GateQueryPort,
        product_catalog: ProductResearchCatalog,
        candidate_evidence: ProductCandidateEvidence,
        expert_evidence: ProductExpertEvidence,
        forward_validation: ForwardValidationProductView,
        data_catalog: DataIndicatorCatalog,
        candidate_approvals: CandidateApprovalService,
        metrics: Metrics,
        p12_runtime: P12Runtime | None = None,
        p12_paper=None,
    ) -> None:
        self.manifest = manifest
        self._store = store
        self._identity = identity
        self._p12 = P12Endpoints(p12_runtime, p12_paper)
        self._jobs = jobs
        self._replay_jobs = replay_jobs
        self._gate_query = gate_query
        self._product_catalog = product_catalog
        self._candidate_evidence = candidate_evidence
        self._expert_evidence = expert_evidence
        self._forward_validation = forward_validation
        self._data_catalog = data_catalog
        self._candidate_approvals = candidate_approvals
        self._metrics = metrics
        self._routes: list[Route] = []
        self._logger = logging.getLogger("quanthus.platform.http")
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)
            self._logger.propagate = False
        self._register_routes()
        registered = {route.operation_id for route in self._routes}
        if registered != set(self.manifest.routes):
            missing = sorted(set(self.manifest.routes).difference(registered))
            unexpected = sorted(registered.difference(self.manifest.routes))
            raise RuntimeError(
                f"manifest and HTTP composition differ: missing={missing}, unexpected={unexpected}"
            )

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: StartResponse,
    ) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        headers = self._headers(environ)
        observed_request_id = request_id(headers.get("x-request-id"))
        token = request_id_context.set(observed_request_id)
        started = time.perf_counter()
        route: Route | None = None
        response: HttpResponse
        try:
            body = self._body(environ)
            observed_request = HttpRequest(
                method=method,
                path=path,
                headers=headers,
                body=body,
                request_id=observed_request_id,
                query_string=str(environ.get("QUERY_STRING", "")),
            )
            route, params = self._match(method, path)
            if route is None:
                response = _error("route_not_found", 404, observed_request_id)
            else:
                response = route.handler(observed_request, params)
        except MfaRequiredError as exc:
            response = _error(str(exc), 428, observed_request_id)
        except AuthenticationError as exc:
            response = _error(str(exc), 401, observed_request_id)
        except AuthorizationError as exc:
            response = _error(str(exc), 403, observed_request_id)
        except DuplicateRecordError as exc:
            response = _error(str(exc), 409, observed_request_id)
        except ValueError as exc:
            response = _error(str(exc), 400, observed_request_id)
        except Exception:
            self._logger.exception("platform_request_failed")
            response = _error("internal_error", 500, observed_request_id)
        route_label = route.path if route else "unmatched"
        duration_ms = round((time.perf_counter() - started) * 1000)
        self._metrics.observe(
            method=method,
            route=route_label,
            status=response.status,
            duration_ms=duration_ms,
        )
        self._logger.info(
            "platform_request method=%s route=%s status=%s duration_ms=%s",
            method,
            route_label,
            response.status,
            duration_ms,
            extra={"request_id": observed_request_id},
        )
        request_id_context.reset(token)
        status_line = f"{response.status} {HTTPStatus(response.status).phrase}"
        response_headers = [
            *response.headers,
            ("Content-Length", str(len(response.body))),
            ("X-Request-ID", observed_request_id),
        ]
        start_response(status_line, response_headers)
        return [response.body]

    @staticmethod
    def _headers(environ: Mapping[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").lower()] = str(value)
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = str(environ["CONTENT_TYPE"])
        return headers

    @staticmethod
    def _body(environ: Mapping[str, Any]) -> bytes:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        stream = environ.get("wsgi.input")
        body = stream.read(length) if stream is not None and length else b""
        if not isinstance(body, bytes) or len(body) != length:
            raise ValueError("incomplete request body")
        return body

    def _match(self, method: str, path: str) -> tuple[Route | None, Mapping[str, str]]:
        for route in self._routes:
            if route.method != method:
                continue
            match = route.pattern.fullmatch(path)
            if match is not None:
                return route, match.groupdict()
        return None, {}

    def _add(
        self,
        operation_id: str,
        path: str,
        method: str,
        handler: Handler,
        *,
        public: bool = False,
    ) -> None:
        if operation_id not in self.manifest.routes:
            return
        self._routes.append(
            Route(
                operation_id=operation_id,
                method=method,
                path=path,
                pattern=_compile_path(path),
                handler=handler,
                public=public,
            )
        )

    def openapi_document(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        for route in self._routes:
            openapi_path = re.sub(r"<([a-z][a-z0-9_]*)>", r"{\1}", route.path)
            paths.setdefault(openapi_path, {})[route.method.lower()] = {
                "operationId": route.operation_id,
                "responses": {"200": {"description": "Success"}},
                "security": [] if route.public else [{"bearerAuth": []}],
            }
        return {
            "openapi": OPENAPI_VERSION,
            "info": {"title": "Quanthus Platform API", "version": "1.0.0"},
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                }
            },
            "paths": dict(sorted(paths.items())),
        }

    def _register_routes(self) -> None:
        self._add("system.health", "/health", "GET", self._health, public=True)
        self._add("system.ready", "/ready", "GET", self._ready, public=True)
        self._add("system.metrics", "/metrics", "GET", self._platform_metrics, public=True)
        self._add("system.openapi", "/openapi.json", "GET", self._openapi, public=True)
        self._add("auth.login", "/api/platform/v1/auth/login", "POST", self._login, public=True)
        self._add("auth.logout", "/api/platform/v1/auth/logout", "POST", self._logout)
        self._add("auth.me", "/api/platform/v1/auth/me", "GET", self._me)
        self._add("auth.mfa.enroll", "/api/platform/v1/auth/mfa/enroll", "POST", self._enroll_mfa)
        self._add(
            "jobs.submit-candidate-replay",
            "/api/platform/v1/product/candidate-replays",
            "POST",
            self._submit_candidate_replay,
        )
        self._add(
            "jobs.list-candidate-replays",
            "/api/platform/v1/product/candidate-replays",
            "GET",
            self._list_candidate_replays,
        )
        self._add(
            "auth.mfa.confirm", "/api/platform/v1/auth/mfa/confirm", "POST", self._confirm_mfa
        )
        self._add("auth.mfa.disable", "/api/platform/v1/auth/mfa", "DELETE", self._disable_mfa)
        self._add("admin.users.list", "/api/platform/v1/admin/users", "GET", self._list_users)
        self._add("admin.users.create", "/api/platform/v1/admin/users", "POST", self._create_user)
        self._add("quanthus.gates.read", "/api/platform/v1/quanthus/gates", "GET", self._read_gates)
        self._add(
            "quanthus.product.catalog.read",
            "/api/platform/v1/product/catalog",
            "GET",
            self._read_product_catalog,
        )
        self._add(
            "quanthus.catalog.summary.read",
            "/api/platform/v1/catalog/summary",
            "GET",
            self._read_data_catalog_summary,
        )
        self._add(
            "quanthus.catalog.search.read",
            "/api/platform/v1/catalog/search",
            "GET",
            self._search_data_catalog,
        )
        self._add(
            "quanthus.catalog.item.read",
            "/api/platform/v1/catalog/items/<entry_id>",
            "GET",
            self._read_data_catalog_item,
        )
        self._add(
            "quanthus.catalog.relations.read",
            "/api/platform/v1/catalog/items/<entry_id>/relations",
            "GET",
            self._read_data_catalog_relations,
        )
        self._add(
            "quanthus.product.candidate.read",
            "/api/platform/v1/product/candidates/<strategy_id>",
            "GET",
            self._read_product_candidate,
        )
        self._add(
            "quanthus.product.candidate-review.write",
            "/api/platform/v1/product/candidates/<strategy_id>/reviews",
            "POST",
            self._record_product_candidate_review,
        )
        self._add(
            "quanthus.product.candidate-governance.write",
            "/api/platform/v1/product/candidates/<strategy_id>/governance",
            "POST",
            self._record_product_candidate_governance,
        )
        self._add(
            "jobs.submit-attribution-verification",
            "/api/platform/v1/jobs/attribution-verifications",
            "POST",
            self._submit_attribution_verification,
        )
        self._add("jobs.read", "/api/platform/v1/jobs/<job_id>", "GET", self._read_job)
        self._add("jobs.cancel", "/api/platform/v1/jobs/<job_id>/cancel", "POST", self._cancel_job)
        self._add("p12.accounts.list", "/api/p12/v1/accounts", "GET", self._p12_accounts)
        self._add(
            "p12.workbench.read",
            "/api/p12/v1/accounts/<account_id>/workbench",
            "GET",
            self._p12_workbench,
        )
        self._add(
            "p12.records.read",
            "/api/p12/v1/accounts/<account_id>/records",
            "GET",
            self._p12_records,
        )
        self._add(
            "p12.funding.read",
            "/api/p12/v1/accounts/<account_id>/funding-rules",
            "GET",
            self._p12_funding_rules,
        )
        self._add(
            "p12.reports.submit",
            "/api/p12/v1/accounts/<account_id>/execution-reports",
            "POST",
            self._p12_submit_fill,
        )
        self._add(
            "p12.facts.correct",
            "/api/p12/v1/accounts/<account_id>/fact-corrections",
            "POST",
            self._p12_correct_fill,
        )
        self._add(
            "p12.order_events.submit",
            "/api/p12/v1/accounts/<account_id>/order-events",
            "POST",
            self._p12_submit_order_event,
        )
        self._add(
            "p12.paper.session",
            "/api/p12/v1/market/session",
            "GET",
            self._p12_paper_session,
        )
        self._add(
            "p12.paper.quote",
            "/api/p12/v1/securities/<code>/quote",
            "GET",
            self._p12_paper_quote,
        )
        self._add(
            "p12.paper.identity",
            "/api/p12/v1/securities/<code>/identity",
            "GET",
            self._p12_paper_identity,
        )
        self._add(
            "p12.paper.intraday",
            "/api/p12/v1/securities/<code>/intraday",
            "GET",
            self._p12_paper_intraday,
        )
        self._add(
            "p12.paper.account",
            "/api/p12/v1/accounts/<account_id>/paper",
            "GET",
            self._p12_paper_account,
        )
        self._add(
            "p12.paper.orders",
            "/api/p12/v1/accounts/<account_id>/sim-orders",
            "GET",
            self._p12_paper_orders,
        )
        self._add(
            "p12.paper.submit",
            "/api/p12/v1/accounts/<account_id>/sim-orders",
            "POST",
            self._p12_paper_submit,
        )
        self._add(
            "p12.paper.cancel",
            "/api/p12/v1/accounts/<account_id>/sim-orders/<order_id>/cancel",
            "POST",
            self._p12_paper_cancel,
        )

    def _p12_accounts(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.accounts(principal, observed_request.request_id)
        return _json_response(payload, status)

    def _p12_workbench(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.workbench(
            principal,
            params["account_id"],
            observed_request.query_string,
            observed_request.request_id,
        )
        return _json_response(payload, status)

    def _p12_records(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.records(
            principal,
            params["account_id"],
            observed_request.query_string,
            observed_request.request_id,
        )
        return _json_response(payload, status)

    def _p12_funding_rules(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.funding_rules(
            principal,
            params["account_id"],
            observed_request.query_string,
            observed_request.request_id,
        )
        return _json_response(payload, status)

    def _p12_submit_fill(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:report")
        status, payload = self._p12.submit_fill(
            principal,
            params["account_id"],
            observed_request.json_object(),
            observed_request.request_id,
            observed_request.query_string,
        )
        return _json_response(payload, status)

    def _p12_correct_fill(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:report")
        status, payload = self._p12.correct_fill(
            principal,
            params["account_id"],
            observed_request.json_object(),
            observed_request.request_id,
            observed_request.query_string,
        )
        return _json_response(payload, status)

    def _p12_submit_order_event(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:report")
        status, payload = self._p12.submit_order_event(
            principal,
            params["account_id"],
            observed_request.json_object(),
            observed_request.request_id,
            observed_request.query_string,
        )
        return _json_response(payload, status)

    def _p12_paper_session(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_session(principal, observed_request.request_id)
        return _json_response(payload, status)

    def _p12_paper_quote(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_quote(
            principal, params["code"], observed_request.request_id
        )
        return _json_response(payload, status)

    def _p12_paper_identity(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_identity(
            principal, params["code"], observed_request.request_id
        )
        return _json_response(payload, status)

    def _p12_paper_intraday(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_intraday(
            principal, params["code"], observed_request.request_id
        )
        return _json_response(payload, status)

    def _p12_paper_account(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_account(
            principal, params["account_id"], observed_request.request_id
        )
        return _json_response(payload, status)

    def _p12_paper_orders(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:read")
        status, payload = self._p12.paper_orders(
            principal, params["account_id"], observed_request.request_id
        )
        return _json_response(payload, status)

    def _p12_paper_submit(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:report")
        status, payload = self._p12.paper_submit(
            principal,
            params["account_id"],
            observed_request.json_object(),
            observed_request.request_id,
        )
        return _json_response(payload, status)

    def _p12_paper_cancel(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "p12:report")
        status, payload = self._p12.paper_cancel(
            principal,
            params["account_id"],
            params["order_id"],
            observed_request.request_id,
        )
        return _json_response(payload, status)

    def _structure_mvp_qualification_health(self) -> dict[str, Any] | None:
        enabled = os.environ.get(_STRUCTURE_MVP_HEALTH_PROBE_ENV, "0").strip()
        if enabled != "1":
            return None
        try:
            if (
                not _STRUCTURE_MVP_SEED_RECEIPT.is_file()
                or _STRUCTURE_MVP_SEED_RECEIPT.stat().st_size
                > _STRUCTURE_MVP_RECEIPT_MAX_BYTES
            ):
                return {"status": "seed_receipt_unavailable"}
            receipt = json.loads(_STRUCTURE_MVP_SEED_RECEIPT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "seed_receipt_invalid"}
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema_version")
            != "quanthus.structure-mvp-qualification-seed.v1"
            or receipt.get("status") not in {"submitted", "rejected"}
        ):
            return {"status": "seed_receipt_invalid"}
        if receipt["status"] == "rejected":
            return {
                "status": "seed_rejected",
                "code_revision": receipt.get("code_revision"),
                "reason": receipt.get("reason"),
            }
        job_ids = receipt.get("job_ids")
        if (
            not isinstance(job_ids, list)
            or len(job_ids) != 2
            or any(not isinstance(item, str) for item in job_ids)
        ):
            return {"status": "seed_receipt_invalid"}
        runs: list[dict[str, Any]] = []
        for job_id in job_ids:
            job = self._replay_jobs.get(job_id)
            if job is None or job.kind != STRUCTURE_MVP_JOB:
                runs.append({"job_id": job_id, "state": "missing"})
                continue
            item: dict[str, Any] = {
                "job_id": job.job_id,
                "state": job.state,
                "stage": job.stage,
                "progress": job.progress,
            }
            if job.error_reason is not None:
                item["error_reason"] = job.error_reason
            if job.result_ref:
                for key in (
                    "result_digest",
                    "adm_manifest_digest",
                    "row_count",
                    "state_counts",
                    "decision_counts",
                    "p2_probe_count",
                    "p2_fill_count",
                ):
                    if key in job.result_ref:
                        item[key] = job.result_ref[key]
            runs.append(item)
        response: dict[str, Any] = {
            "status": "running"
            if any(item.get("state") in {"queued", "running"} for item in runs)
            else "complete",
            "code_revision": receipt.get("code_revision"),
            "runs": runs,
        }
        if len(runs) == 2 and all(item.get("state") == "succeeded" for item in runs):
            result_match = runs[0].get("result_digest") == runs[1].get("result_digest")
            adm_match = runs[0].get("adm_manifest_digest") == runs[1].get(
                "adm_manifest_digest"
            )
            response["reproduced"] = bool(result_match and adm_match)
            response["result_digest"] = (
                runs[0].get("result_digest") if result_match else None
            )
            response["adm_manifest_digest"] = (
                runs[0].get("adm_manifest_digest") if adm_match else None
            )
            if result_match and adm_match:
                response["row_count"] = runs[0].get("row_count")
                response["state_counts"] = runs[0].get("state_counts")
                response["decision_counts"] = runs[0].get("decision_counts")
                response["p2_probe_count"] = runs[0].get("p2_probe_count")
                response["p2_fill_count"] = runs[0].get("p2_fill_count")
                response["review_sample"] = _structure_mvp_review_sample(
                    _STRUCTURE_MVP_EVENT_RUN_ROOT,
                    str(runs[0]["job_id"]),
                )
        return response

    def _structure_facts_qualification_health(self) -> dict[str, Any] | None:
        enabled = os.environ.get(_STRUCTURE_FACTS_HEALTH_PROBE_ENV, "0").strip()
        if enabled != "1":
            return None
        security_codes = _STRUCTURE_FACTS_SECURITY_ORDER
        jobs = [
            job
            for job in self._replay_jobs.list(STRUCTURE_MVP_JOB, 50)
            if job.input_ref.get("run_config") == _STRUCTURE_FACTS_RUN_CONFIG
            and job.idempotency_key.startswith("structure-facts-qualification-v1-")
        ]
        grouped: dict[str, dict[str, Any]] = {}
        revision_order: list[str] = []
        for job in jobs:
            revision = job.input_ref.get("code_revision")
            parts = job.idempotency_key.split("-")
            if not isinstance(revision, str) or len(parts) < 6 or parts[4] not in {"a", "b"}:
                continue
            if revision not in grouped:
                grouped[revision] = {}
                revision_order.append(revision)
            grouped[revision].setdefault(parts[4], job)
        revision = next(
            (item for item in revision_order if set(grouped[item]) == {"a", "b"}),
            None,
        )
        if revision is None:
            return {"status": "runs_unavailable"}
        pair = (grouped[revision]["a"], grouped[revision]["b"])
        result_keys = (
            "result_digest",
            "adm_manifest_digest",
            "manifest_sha256",
            "row_count",
            "fact_counts",
            "renderer_counts",
            "consumer_counts",
            "p2_probe_count",
            "p2_fill_count",
        )
        runs: list[dict[str, Any]] = []
        for job in pair:
            item: dict[str, Any] = {
                "job_id": job.job_id,
                "state": job.state,
                "stage": job.stage,
                "progress": job.progress,
            }
            if job.error_reason is not None:
                item["error_reason"] = job.error_reason
            if job.result_ref:
                for key in result_keys:
                    if key in job.result_ref:
                        item[key] = job.result_ref[key]
            runs.append(item)
        response: dict[str, Any] = {
            "status": "running"
            if any(item["state"] in {"queued", "running"} for item in runs)
            else "complete",
            "code_revision": revision,
            "runs": runs,
        }
        if all(item["state"] == "succeeded" for item in runs):
            reproduced = all(runs[0].get(key) == runs[1].get(key) for key in result_keys)
            response["reproduced"] = reproduced
            if reproduced:
                for key in result_keys:
                    response[key] = runs[0].get(key)
                response["review_sample"] = _structure_facts_review_sample(
                    _STRUCTURE_MVP_EVENT_RUN_ROOT,
                    str(pair[0].job_id),
                    security_codes,
                )
        return response

    def _health(self, _request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        payload: dict[str, Any] = {
            "status": "healthy",
            "role": "api",
            "manifest": self.manifest.version,
        }
        qualification = self._structure_mvp_qualification_health()
        if qualification is not None:
            payload["structure_mvp_qualification"] = qualification
        facts_qualification = self._structure_facts_qualification_health()
        if facts_qualification is not None:
            payload["structure_facts_qualification"] = facts_qualification
        try:
            payload["forward_validation"] = self._forward_validation.health_summary()
        except ForwardProductViewError as exc:
            payload["forward_validation"] = {
                "state": "projection_unavailable",
                "reason": exc.reason_code,
            }
        return _json_response(payload)

    def _ready(self, _request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        available = self._store.ready()
        return _json_response(
            {
                "status": "ready" if available else "unavailable",
                "checks": {"postgres": available},
            },
            200 if available else 503,
        )

    def _platform_metrics(self, _request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        return _text_response(self._metrics.render())

    def _openapi(self, _request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        return _json_response(self.openapi_document())

    def _login(self, observed_request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        payload = observed_request.json_object()
        result = self._identity.login(
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            mfa_code=str(payload["mfa_code"]) if payload.get("mfa_code") is not None else None,
            request_id=observed_request.request_id,
        )
        return _json_response(
            {
                "token": result.token,
                "expires_at": result.expires_at.isoformat(),
                "user": {
                    "user_id": result.principal.user_id,
                    "username": result.principal.username,
                    "role": result.principal.role,
                },
            }
        )

    def _logout(self, observed_request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        token = _bearer_token(observed_request)
        self._identity.authenticate(token)
        self._identity.logout(token, request_id=observed_request.request_id)
        return _json_response({"revoked": True})

    def _me(self, observed_request: HttpRequest, _params: Mapping[str, str]) -> HttpResponse:
        principal = self._identity.authenticate(_bearer_token(observed_request))
        user = self._store.get_user(principal.user_id)
        if user is None:
            raise AuthenticationError("invalid_or_expired_session")
        return _json_response(
            {
                "user_id": principal.user_id,
                "username": principal.username,
                "role": principal.role,
                "mfa_enabled": user.mfa_enabled,
            }
        )

    def _enroll_mfa(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = self._identity.authenticate(_bearer_token(observed_request))
        enrollment = self._identity.begin_mfa(principal)
        return _json_response(
            {"secret": enrollment.secret, "provisioning_uri": enrollment.provisioning_uri}
        )

    def _confirm_mfa(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = self._identity.authenticate(_bearer_token(observed_request))
        payload = observed_request.json_object()
        self._identity.confirm_mfa(
            principal,
            str(payload.get("code", "")),
            request_id=observed_request.request_id,
        )
        return _json_response({"mfa_enabled": True})

    def _disable_mfa(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = self._identity.authenticate(_bearer_token(observed_request))
        payload = observed_request.json_object()
        self._identity.disable_mfa(
            principal,
            str(payload.get("code", "")),
            request_id=observed_request.request_id,
        )
        return _json_response({"mfa_enabled": False})

    def _list_users(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "users:read")
        return _json_response({"users": [public_user(user) for user in self._store.list_users()]})

    def _create_user(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "users:write")
        payload = observed_request.json_object()
        user = self._identity.create_user(
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            role=str(payload.get("role", "")),
            actor_user_id=principal.user_id,
            request_id=observed_request.request_id,
        )
        return _json_response(public_user(user), 201)

    def _read_gates(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "gates:read")
        return _json_response(self._gate_query.read().to_dict())

    @staticmethod
    def _catalog_query_values(observed_request: HttpRequest) -> dict[str, str]:
        parsed = urllib.parse.parse_qs(
            observed_request.query_string,
            keep_blank_values=True,
            strict_parsing=False,
        )
        allowed = {"q", "kind", "domain", "family", "status", "limit"}
        if set(parsed).difference(allowed) or any(len(values) != 1 for values in parsed.values()):
            raise ValueError("catalog_query_parameters_invalid")
        return {key: values[0] for key, values in parsed.items()}

    def _read_data_catalog_summary(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "catalog:read")
        return _json_response(self._data_catalog.summary())

    def _search_data_catalog(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "catalog:read")
        values = self._catalog_query_values(observed_request)
        try:
            limit = int(values.get("limit", "30"))
        except ValueError as exc:
            raise ValueError("catalog_limit_invalid") from exc
        result = self._data_catalog.search(
            values.get("q", ""),
            kind=values.get("kind") or None,
            domain=values.get("domain") or None,
            family=values.get("family") or None,
            status=values.get("status") or None,
            limit=limit,
        )
        return _json_response(result)

    def _read_data_catalog_item(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "catalog:read")
        entry_id = urllib.parse.unquote(params["entry_id"])
        try:
            return _json_response(self._data_catalog.item(entry_id))
        except DataCatalogError as exc:
            if str(exc) == "catalog_entry_not_found":
                return _error(str(exc), 404, observed_request.request_id)
            raise

    def _read_data_catalog_relations(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "catalog:read")
        entry_id = urllib.parse.unquote(params["entry_id"])
        query = urllib.parse.parse_qs(observed_request.query_string, keep_blank_values=True)
        if set(query) - {"depth", "limit", "asset_scope"} or any(
            len(value) != 1 for value in query.values()
        ):
            raise ValueError("catalog_query_parameters_invalid")
        try:
            depth = int(query.get("depth", ["2"])[0])
            limit = int(query.get("limit", ["60"])[0])
        except ValueError as exc:
            raise ValueError("catalog_lineage_bounds_invalid") from exc
        asset_scope = query.get("asset_scope", ["full"])[0]
        if asset_scope not in {"full", "focus"}:
            raise ValueError("catalog_asset_lineage_scope_invalid")
        try:
            return _json_response(
                self._data_catalog.relations(
                    entry_id, depth=depth, limit=limit, asset_scope=asset_scope
                )
            )
        except DataCatalogError as exc:
            if str(exc) == "catalog_entry_not_found":
                return _error(str(exc), 404, observed_request.request_id)
            raise

    def _read_product_catalog(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "product:read")
        catalog = self._product_catalog.read()
        for candidate in catalog["strategies"]:
            evidence = self._candidate_evidence.candidate_summary(candidate["strategy_id"])
            if evidence is None:
                raise ValueError("candidate evidence does not exist")
            self._apply_evidence_summary(candidate, evidence)
            candidate["approval"] = self._candidate_approval(candidate, evidence)
        return _json_response(catalog)

    def _candidate_approval(
        self, candidate: Mapping[str, Any], evidence: Mapping[str, Any]
    ) -> dict[str, object]:
        return self._candidate_approvals.state(
            str(candidate["strategy_id"]),
            str(candidate["version"]),
            str(evidence["source_artifact_sha256"]),
        )

    @staticmethod
    def _apply_evidence_summary(candidate: dict[str, Any], evidence: Mapping[str, Any]) -> None:
        summary = evidence.get("summary")
        if not isinstance(summary, Mapping):
            return
        candidate.update(
            {
                "operations": summary["operations"],
                "closed_trades": summary["closed_trades"],
                "total_return": summary["total_return"],
                "max_drawdown": summary["max_drawdown"],
            }
        )

    def _candidate_identity(self, raw_strategy_id: str) -> tuple[str, dict[str, Any]]:
        strategy_id = urllib.parse.unquote(raw_strategy_id)
        candidate = self._product_catalog.candidate(strategy_id)
        if candidate is None:
            raise ValueError("candidate does not exist")
        return strategy_id, candidate

    def _read_product_candidate(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        _principal(self._identity, observed_request, "product:read")
        strategy_id, candidate = self._candidate_identity(params["strategy_id"])
        evidence = self._candidate_evidence.candidate(strategy_id)
        if evidence is None:
            raise ValueError("candidate evidence does not exist")
        self._apply_evidence_summary(candidate, evidence)
        approval = self._candidate_approval(candidate, evidence)
        active_run_id = next(
            (
                item.run_id
                for item in self._store.candidate_active_runs()
                if item.strategy_id == strategy_id and item.strategy_version == candidate["version"]
            ),
            None,
        )
        run_history = []
        for run in self._store.candidate_runs(strategy_id, candidate["version"]):
            item = next(
                value
                for value in run.candidates
                if value.get("candidate_id") == strategy_id
                and value.get("candidate_version") == candidate["version"]
            )
            run_history.append(
                {
                    "run_id": run.run_id,
                    "job_id": run.job_id,
                    "created_at": run.created_at.isoformat(),
                    "result_sha256": run.result_sha256,
                    "evidence_digest": item.get("artifact_sha256"),
                    "result_digest": item.get("result_digest"),
                    "active": run.run_id == active_run_id,
                    "active_switch_applied": run.active_switch_applied,
                    "summary": item.get("summary", {}),
                }
            )
        try:
            expert_evidence = self._expert_evidence.candidate(
                strategy_id, str(candidate["version"])
            )
        except ProductExpertEvidenceError as exc:
            return _error(exc.reason_code, 503, observed_request.request_id)
        forward_validation = self._forward_validation.candidate(
            strategy_id, str(candidate["version"])
        )
        return _json_response(
            {
                "candidate": candidate,
                "evidence": evidence,
                "expert_evidence": expert_evidence,
                "forward_validation": forward_validation,
                "approval": approval,
                "run_history": run_history,
            }
        )

    def _record_product_candidate_review(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "product:review")
        strategy_id, candidate = self._candidate_identity(params["strategy_id"])
        evidence = self._candidate_evidence.candidate(strategy_id)
        if evidence is None:
            raise ValueError("candidate evidence does not exist")
        payload = observed_request.json_object()
        event = self._candidate_approvals.record_review(
            strategy_id=strategy_id,
            strategy_version=candidate["version"],
            evidence_digest=str(evidence["source_artifact_sha256"]),
            action=str(payload.get("action", "")),
            note=str(payload.get("note", "")),
            principal=principal,
            request_id=observed_request.request_id,
        )
        return _json_response(
            {
                "event": public_candidate_approval_event(event),
                "approval": self._candidate_approval(candidate, evidence),
            },
            201,
        )

    def _record_product_candidate_governance(
        self, observed_request: HttpRequest, params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "product:govern")
        strategy_id, candidate = self._candidate_identity(params["strategy_id"])
        evidence = self._candidate_evidence.candidate(strategy_id)
        if evidence is None:
            raise ValueError("candidate evidence does not exist")
        payload = observed_request.json_object()
        event = self._candidate_approvals.record_governance(
            strategy_id=strategy_id,
            strategy_version=candidate["version"],
            evidence_digest=str(evidence["source_artifact_sha256"]),
            action=str(payload.get("action", "")),
            note=str(payload.get("note", "")),
            principal=principal,
            request_id=observed_request.request_id,
        )
        return _json_response(
            {
                "event": public_candidate_approval_event(event),
                "approval": self._candidate_approval(candidate, evidence),
            },
            201,
        )

    def _submit_attribution_verification(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "jobs:run")
        payload = observed_request.json_object()
        job, created = self._jobs.submit(
            kind=ATTRIBUTION_VERIFICATION_JOB,
            actor_user_id=principal.user_id,
            idempotency_key=observed_request.headers.get("idempotency-key", ""),
            input_ref={"release_id": str(payload.get("release_id", ""))},
            execute=False,
        )
        job = self._jobs.dispatch(job.job_id)
        return _json_response(public_job(job), 201 if created else 200)

    def _submit_candidate_replay(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "jobs:run")
        payload = observed_request.json_object()
        reason = str(payload.get("reason", "")).strip()
        if not 1 <= len(reason) <= 500:
            return _error("replay_reason_required", 400, observed_request.request_id)
        active_runs = {
            f"{item.strategy_id}@{item.strategy_version}": item.run_id
            for item in self._store.candidate_active_runs()
        }
        candidate_identities = (
            f"{item['strategy_id']}@{item['version']}"
            for item in self._product_catalog.read()["strategies"]
            if item.get("replay_submission_eligible", True) is True
        )
        input_ref = {
            "scope_start": "2012-01-04",
            "scope_end": "2021-12-30",
            "provider_config": "config/replay-data-provider.development.v2.json",
            "candidate_set": "current-five",
            "reason": reason,
            "expected_active_runs": {
                identity: active_runs.get(identity) for identity in candidate_identities
            },
        }
        try:
            job, created = self._replay_jobs.submit(
                kind=CANDIDATE_REPLAY_JOB,
                actor_user_id=principal.user_id,
                idempotency_key=observed_request.headers.get("idempotency-key", ""),
                input_ref=input_ref,
                concurrency_key="candidate-set:current-five:development",
            )
        except IdempotencyConflictError as exc:
            return _error(str(exc), 409, observed_request.request_id)
        except DuplicateRecordError:
            return _error("candidate_replay_already_active", 409, observed_request.request_id)
        return _json_response(public_job(job), 201 if created else 200)

    def _list_candidate_replays(
        self, observed_request: HttpRequest, _params: Mapping[str, str]
    ) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "jobs:read")
        jobs = self._replay_jobs.list(CANDIDATE_REPLAY_JOB)
        if principal.role != "admin":
            jobs = tuple(job for job in jobs if job.actor_user_id == principal.user_id)
        return _json_response({"jobs": [public_job(job) for job in jobs]})

    def _read_job(self, observed_request: HttpRequest, params: Mapping[str, str]) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "jobs:read")
        job = self._replay_jobs.get(params["job_id"])
        if job is None:
            return _error("job_not_found", 404, observed_request.request_id)
        if principal.role != "admin" and job.actor_user_id != principal.user_id:
            raise AuthorizationError("permission_denied")
        return _json_response(public_job(job))

    def _cancel_job(self, observed_request: HttpRequest, params: Mapping[str, str]) -> HttpResponse:
        principal = _principal(self._identity, observed_request, "jobs:run")
        job = self._replay_jobs.get(params["job_id"])
        if job is None:
            return _error("job_not_found", 404, observed_request.request_id)
        if principal.role != "admin" and job.actor_user_id != principal.user_id:
            raise AuthorizationError("permission_denied")
        try:
            return _json_response(public_job(self._replay_jobs.cancel(job.job_id)))
        except JobNotCancelableError as exc:
            return _error(str(exc), 409, observed_request.request_id)


def create_http_app(
    *,
    manifest: FeatureManifest,
    store: PlatformStore,
    identity: IdentityService,
    jobs: LocalJobRunner,
    replay_jobs: DurableJobQueue,
    gate_query: GateQueryPort,
    product_catalog: ProductResearchCatalog,
    candidate_evidence: ProductCandidateEvidence,
    expert_evidence: ProductExpertEvidence,
    forward_validation: ForwardValidationProductView,
    data_catalog: DataIndicatorCatalog,
    candidate_approvals: CandidateApprovalService,
    metrics: Metrics,
    p12_runtime: P12Runtime | None = None,
    p12_paper=None,
) -> PlatformHttpApp:
    return PlatformHttpApp(
        manifest=manifest,
        store=store,
        identity=identity,
        jobs=jobs,
        replay_jobs=replay_jobs,
        gate_query=gate_query,
        product_catalog=product_catalog,
        candidate_evidence=candidate_evidence,
        expert_evidence=expert_evidence,
        forward_validation=forward_validation,
        data_catalog=data_catalog,
        candidate_approvals=candidate_approvals,
        metrics=metrics,
        p12_runtime=p12_runtime,
        p12_paper=p12_paper,
    )
