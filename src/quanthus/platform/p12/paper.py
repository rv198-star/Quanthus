"""P12 paper-trading service: real quotes + persistent simulated order book.

One ``PaperService`` per host process binds the admitted quote channel, the
intraday series and every bound account's ``PaperAccount``.  The desktop
endpoints delegate to it; ``build_paper_service`` is called only by the
trusted local assembly, never by the production kernel path.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_paper.quotes import IntradaySeries, QuoteService
from quanthus.shared.fact_contracts import FactError

PAPER_STORE_NAME = "p12-paper.sqlite"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class PaperService:
    """Quotes, session, intraday and per-account simulated order flow."""

    def __init__(
        self,
        *,
        quotes: QuoteService,
        intraday: IntradaySeries,
        accounts: dict[str, PaperAccount],
        match_interval: float = 5.0,
    ) -> None:
        self._quotes = quotes
        self._intraday = intraday
        self._accounts = accounts
        self._match_interval = match_interval
        self._stop = threading.Event()
        self._matcher: threading.Thread | None = None

    # ---- background matching ------------------------------------------------------
    def start(self) -> None:
        """Start the always-on matcher so resting orders fill without a page open."""
        if self._matcher is not None:
            return
        self._stop.clear()
        self._matcher = threading.Thread(
            target=self._match_loop, name="p12-paper-matcher", daemon=True
        )
        self._matcher.start()

    def stop(self) -> None:
        self._stop.set()
        if self._matcher is not None:
            self._matcher.join(timeout=5)
            self._matcher = None

    def _match_loop(self) -> None:
        while not self._stop.wait(self._match_interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a tick failure must not kill the matcher
                continue

    # ---- market reads ------------------------------------------------------------
    def session(self) -> dict[str, Any]:
        return self._quotes.session()

    def quote(self, code: str) -> dict[str, Any]:
        q = self._quotes.quote(code)
        return {
            "security_code": q.security_code,
            "security_name": q.security_name,
            "exchange": q.exchange,
            "quote": q.to_dict(),
            "contract_version": "p12-paper-quote-v1",
        }

    def identity(self, code: str) -> dict[str, Any]:
        return self._quotes.identity(code).to_dict()

    def intraday(self, code: str) -> dict[str, Any]:
        return self._intraday.fetch(code)

    # ---- simulated order flow ------------------------------------------------------
    def _account(self, account_id: str) -> PaperAccount:
        account = self._accounts.get(account_id)
        if account is None:
            raise FactError("ACCOUNT_NOT_ADMITTED", "no paper account bound here")
        return account

    def account_view(self, account_id: str) -> dict[str, Any]:
        account = self._account(account_id)
        account.poll()
        return account.account_view()

    def orders(self, account_id: str) -> dict[str, Any]:
        account = self._account(account_id)
        account.poll()
        return {"orders": account.orders()}

    def submit(self, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
        command = OrderCommand(
            order_id=str(body.get("order_id") or ""),
            side=str(body.get("side") or ""),
            security_code=str(body.get("security_code") or ""),
            quantity=body.get("quantity"),
            limit_price=(
                body.get("limit_price")
                if isinstance(body.get("limit_price"), Decimal)
                else Decimal(str(body.get("limit_price") or "0"))
            ),
        )
        return self._account(account_id).submit(command).to_dict()

    def cancel(self, account_id: str, order_id: str) -> dict[str, Any]:
        return self._account(account_id).cancel(order_id)

    def tick(self) -> dict[str, Any]:
        """Advance every bound account's open orders against fresh quotes."""
        results = {aid: acct.poll() for aid, acct in self._accounts.items()}
        return results


def build_paper_service(
    *,
    state_dir: Path,
    applications: dict[str, Any],
    quotes: QuoteService | None = None,
    intraday: IntradaySeries | None = None,
    now: Callable[[], datetime] | None = None,
) -> PaperService:
    """Assemble the paper service over the bound desktop applications.

    ``applications`` is the account_id -> P12DesktopApplication map produced
    by the trusted assembly.  One shared ``PaperOrderStore`` keeps every
    account's simulated orders in its own ``p12-paper.sqlite`` beside the
    adopted fact store.
    """
    clock = now or _utcnow
    quotes = quotes or QuoteService(now=clock)
    intraday = intraday or IntradaySeries()
    store = PaperOrderStore(Path(state_dir) / PAPER_STORE_NAME, clock=clock)
    accounts = {
        account_id: PaperAccount(app=app, store=store, quotes=quotes, now=clock)
        for account_id, app in applications.items()
    }
    service = PaperService(quotes=quotes, intraday=intraday, accounts=accounts)
    service.start()
    return service
