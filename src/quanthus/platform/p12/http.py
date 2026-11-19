"""Thin HTTP adapter for the P12 desktop: authorization, account binding, DTO mapping.

The platform transport authenticates the bearer session and checks the RBAC permission
before calling into this module.  Here the account binding comes from the trusted runtime
assembly; a mode or account in the request never grants access.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from quanthus.platform.identity import AuthenticatedPrincipal
from quanthus.platform.p12.errors import error_dto

if TYPE_CHECKING:  # the application closure loads only when a runtime is assembled
    from quanthus.product_daily.application.desktop import P12DesktopApplication

_LOGGER = logging.getLogger("quanthus.platform.p12")
_STATUS = {
    "INVALID_REPORT_SHAPE": 422,
    "SCHEMA_VERSION_MISMATCH": 422,
    "UNSUPPORTED_FACT": 422,
    "UNAUTHORIZED_CONTEXT": 403,
    "ACCOUNT_NOT_ADMITTED": 403,
    "FACT_NOT_FOUND": 404,
    "PLAN_NOT_FOUND": 404,
    "P12_RUNTIME_NOT_CONFIGURED": 503,
}


@dataclass(frozen=True)
class P12AccountBinding:
    application: P12DesktopApplication
    display_name: str
    allowed_usernames: frozenset[str]
    synthetic: bool


@dataclass(frozen=True)
class P12Runtime:
    """Trusted account bindings produced only by an explicit assembly."""

    runtime_ref: str
    bindings: Mapping[str, P12AccountBinding]

    def binding_for(self, principal: AuthenticatedPrincipal, account_id: str) -> P12AccountBinding:
        from quanthus.shared.fact_contracts import FactError

        binding = self.bindings.get(account_id)
        if binding is None or principal.username not in binding.allowed_usernames:
            raise FactError("ACCOUNT_NOT_ADMITTED", "account is not admitted to this user")
        return binding


Result = tuple[int, dict[str, Any]]


def _transport() -> Any:
    from quanthus.platform.p12 import transport

    return transport


class P12Endpoints:
    def __init__(self, runtime: P12Runtime | None) -> None:
        self._runtime = runtime

    def _run(self, request_id: str, call: Callable[[P12Runtime], dict[str, Any]]) -> Result:
        if self._runtime is None:
            return 503, error_dto(
                "P12_RUNTIME_NOT_CONFIGURED",
                "no trusted P12 account runtime is assembled in this environment",
                request_id,
            )
        from quanthus.platform.p12.transport import OutboundContractError
        from quanthus.platform.p12.validation import ContractViolation
        from quanthus.shared.fact_contracts import FactError

        try:
            return 200, call(self._runtime)
        except (FactError, ContractViolation) as exc:
            code = str(exc.reason_code)
            status = _STATUS.get(code, 409)
            detail = str(exc).split(": ", 1)[-1]
            return status, error_dto(code, detail, request_id)
        except OutboundContractError:
            _LOGGER.exception("p12_outbound_contract_violation")
            return 500, error_dto(
                "UNCLASSIFIED_FACT_ERROR", "server response violated the contract", request_id
            )

    @staticmethod
    def _scope_query(binding: P12AccountBinding, query_string: str) -> None:
        requested = urllib.parse.parse_qs(query_string).get("mode")
        if requested and requested != [binding.application.scope.mode]:
            from quanthus.shared.fact_contracts import FactError

            raise FactError("UNAUTHORIZED_CONTEXT", "requested mode differs from the binding")

    def accounts(self, principal: AuthenticatedPrincipal, request_id: str) -> Result:
        def call(runtime: P12Runtime) -> dict[str, Any]:
            entries = [
                {
                    "account_id": account_id,
                    "mode": binding.application.scope.mode,
                    "display_name": binding.display_name,
                    "synthetic": binding.synthetic,
                }
                for account_id, binding in sorted(runtime.bindings.items())
                if principal.username in binding.allowed_usernames
            ]
            payload: dict[str, Any] = _transport().accounts_dto(entries)
            return payload

        return self._run(request_id, call)

    def _read(
        self,
        principal: AuthenticatedPrincipal,
        account_id: str,
        query_string: str,
        request_id: str,
        reader: Callable[[P12DesktopApplication], dict[str, Any]],
    ) -> Result:
        def call(runtime: P12Runtime) -> dict[str, Any]:
            binding = runtime.binding_for(principal, account_id)
            self._scope_query(binding, query_string)
            return reader(binding.application)

        return self._run(request_id, call)

    def workbench(
        self, principal: AuthenticatedPrincipal, account_id: str, query: str, request_id: str
    ) -> Result:
        return self._read(
            principal,
            account_id,
            query,
            request_id,
            lambda app: _transport().workbench_dto(app.workbench(app.scope)),
        )

    def records(
        self, principal: AuthenticatedPrincipal, account_id: str, query: str, request_id: str
    ) -> Result:
        return self._read(
            principal,
            account_id,
            query,
            request_id,
            lambda app: _transport().records_dto(app.records(app.scope)),
        )

    def funding_rules(
        self, principal: AuthenticatedPrincipal, account_id: str, query: str, request_id: str
    ) -> Result:
        return self._read(
            principal,
            account_id,
            query,
            request_id,
            lambda app: _transport().funding_rules_dto(app.funding_rules(app.scope)),
        )

    def submit_fill(
        self,
        principal: AuthenticatedPrincipal,
        account_id: str,
        body: dict[str, Any],
        request_id: str,
    ) -> Result:
        def call(runtime: P12Runtime) -> dict[str, Any]:
            binding = runtime.binding_for(principal, account_id)
            command = _transport().parse_submit_fill(body)
            app = binding.application
            payload: dict[str, Any] = _transport().outcome_dto(app.submit_fill(app.scope, command))
            return payload

        return self._run(request_id, call)

    def correct_fill(
        self,
        principal: AuthenticatedPrincipal,
        account_id: str,
        body: dict[str, Any],
        request_id: str,
    ) -> Result:
        def call(runtime: P12Runtime) -> dict[str, Any]:
            binding = runtime.binding_for(principal, account_id)
            command = _transport().parse_correct_fill(body)
            app = binding.application
            payload: dict[str, Any] = _transport().outcome_dto(app.correct_fill(app.scope, command))
            return payload

        return self._run(request_id, call)
