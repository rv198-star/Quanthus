"""Local-only P12 desktop server: platform kernel + synthetic runtime + built product web.

  cd product-web && npm ci && npm run build && cd ..
  PYTHONPATH=src python -m quanthus.platform.p12.local_server --state-dir /tmp/p12-local

The kernel uses an in-memory platform store and two seeded local users.  MFA is disabled in
this runtime (enrolment returns an error).  Only the sealed synthetic fixture accounts are
bound; nothing here is reachable from the production ``platform_kernel_from_env``.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from wsgiref.simple_server import make_server

from quanthus.platform.composition import PlatformKernel, create_platform_kernel
from quanthus.platform.p12.assembly import build_synthetic_runtime
from quanthus.platform.p12.http import P12AccountBinding
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root

PRODUCT_ROUTES = ("/", "/research", "/candidates", "/attribution", "/p12")
API_PREFIXES = ("/api/", "/health", "/ready", "/metrics", "/openapi.json")


class LocalMfaDisabledCipher:
    """MFA secrets are not supported in the local synthetic runtime."""

    def encrypt(self, value: str) -> str:
        raise RuntimeError("mfa_disabled_in_local_synthetic_runtime")

    def decrypt(self, value: str) -> str:
        raise RuntimeError("mfa_disabled_in_local_synthetic_runtime")


def build_local_kernel(
    *,
    state_dir: Path,
    users: dict[str, tuple[str, str]],
    clock: Callable[[], datetime] | None = None,
    paper_replay_fixture: Path | None = None,
    replay_tape: dict | None = None,
    replay_day: str | None = None,
    replay_source: str = "历史回放",
    replay_start_at: datetime | None = None,
) -> PlatformKernel:
    """users maps username -> (role, password).

    The dedicated ``SYN-PAPER-A`` paper account is bound for every local user
    and owns the simulated order flow; the sealed ``SYN-P12-*`` demo accounts
    stay readable for internal comparison but take no orders.
    """

    account_users = {
        "SYN-P12-A": frozenset(name for name in users if name != "p12-other"),
        "SYN-P12-B": frozenset(name for name in users if name == "p12-other"),
    }
    runtime = build_synthetic_runtime(
        state_dir=state_dir, account_users=account_users, clock=clock
    )
    # Dedicated paper account on the system clock, empty decision source and
    # its own fact store - the demo synthetic clock and plans never reach it.
    from quanthus.platform.p12.paper import build_paper_service
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        build_paper_application,
        build_quote_live_marks,
    )
    from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

    system_clock = clock
    replay = None
    replay_quotes = None
    if replay_tape is not None:
        from quanthus.p12_paper.replay import Replay, ReplayQuotes

        replay = Replay(
            replay_tape,
            day=replay_day,
            source=replay_source,
            start_at=replay_start_at,
        )
        system_clock = replay.now
        replay_quotes = ReplayQuotes(replay, replay_source)
    if replay_quotes is not None:
        paper_quotes = replay_quotes
        paper_live_marks = build_quote_live_marks(paper_quotes, ttl_seconds=0.0)
        paper_valuation_ref = f"replay-quote:{replay_source}"
    else:
        from quanthus.p12_paper.quotes import QuoteService

        paper_quotes = QuoteService(now=system_clock)
        paper_live_marks = build_quote_live_marks(paper_quotes, ttl_seconds=3.0)
        paper_valuation_ref = "tencent-live-quote"
    paper_decisions = None
    if paper_replay_fixture is not None:
        from quanthus.platform.p12.paper_fixture import PaperReplayDecisionSource

        paper_decisions = PaperReplayDecisionSource.from_path(
            paper_replay_fixture, system_clock
        )
    paper_app = build_paper_application(
        state_dir=state_dir,
        policy=runtime.bindings["SYN-P12-A"].application.policy,
        authorization=SyntheticRuntimeAuthorization(
            "local-paper:v1", frozenset({PAPER_ACCOUNT_ID})
        ),
        now=system_clock,
        decision_source=paper_decisions,
        live_marks=paper_live_marks,
        live_valuation_ref=paper_valuation_ref,
    )
    bindings = dict(runtime.bindings)
    bindings[PAPER_ACCOUNT_ID] = P12AccountBinding(
        application=paper_app,
        display_name="模拟账户",
        allowed_usernames=frozenset(users),
        synthetic=False,
    )
    from quanthus.platform.p12.http import P12Runtime
    runtime = P12Runtime(runtime_ref=runtime.runtime_ref, bindings=bindings)

    paper = build_paper_service(
        state_dir=state_dir,
        applications={PAPER_ACCOUNT_ID: paper_app},
        quotes=paper_quotes,
        intraday=replay,
        daily_kline=replay,
        now=system_clock,
    )
    kernel = create_platform_kernel(
        store=InMemoryPlatformStore(),
        cipher=LocalMfaDisabledCipher(),
        p12_runtime=runtime,
        p12_paper=paper,
    )
    kernel.migrate()
    for username, (role, password) in users.items():
        kernel.identity.create_user(username=username, password=password, role=role)
    return kernel


def static_app(
    api: Callable[..., Iterable[bytes]], web_dist: Path | None
) -> Callable[..., Iterable[bytes]]:
    root = web_dist.resolve() if web_dist else None

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", "/"))
        if path.startswith(API_PREFIXES) or root is None:
            return api(environ, start_response)
        target: Path | None = None
        if path.startswith("/product-assets/"):
            candidate = (root / path[len("/product-assets/") :]).resolve()
            if candidate.is_file() and root in candidate.parents:
                target = candidate
        elif path in PRODUCT_ROUTES or path.startswith(("/p12/", "/candidates/")):
            target = root / "index.html"
        if target is None or not target.is_file():
            start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"Not found"]
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        body = target.read_bytes()
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web-dist", type=Path, default=None)
    parser.add_argument(
        "--clock",
        default=None,
        metavar="ISO8601",
        help=(
            "pin the synthetic clock to one aware instant (for example "
            "2026-11-20T09:30:00+08:00); restart with a later value to advance "
            "the demo to the next market day"
        ),
    )
    parser.add_argument(
        "--paper-replay",
        type=Path,
        default=None,
        metavar="FIXTURE",
        help=(
            "bind the paper account to a guided replay decision fixture "
            "(e.g. config/p12/paper-replay.v1.json) instead of the empty source "
            "so the workbench drives a confirm-or-skip executable task"
        ),
    )
    parser.add_argument(
        "--replay-tape",
        type=Path,
        default=None,
        metavar="TAPE.json",
        help=(
            "serve quotes and the intraday series from a frozen replay tape "
            "(the replay-preview capture shape) so suggestion, quote and match "
            "share one replay clock instead of the live feed"
        ),
    )
    parser.add_argument(
        "--replay-day",
        default=None,
        metavar="YYYY-MM-DD",
        help="trading day the tape replays (required with --replay-tape)",
    )
    parser.add_argument(
        "--replay-source",
        default="历史回放",
        help="label stamped on replay quotes and the intraday series",
    )
    parser.add_argument(
        "--replay-start-at",
        type=datetime.fromisoformat,
        default=None,
        metavar="ISO8601",
        help="start the replay clock at this timeline instant",
    )
    parser.add_argument(
        "--user",
        action="append",
        default=None,
        metavar="USERNAME:ROLE:PASSWORD",
        help=(
            "seed one local user; repeat to seed several. When at least one "
            "--user is given it replaces the default p12-demo/p12-viewer/"
            "p12-other seed set. ROLE must be a registered platform role."
        ),
    )
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("the synthetic local server binds to loopback only")
    web_dist = args.web_dist or find_project_root() / "product-web" / "dist"
    if args.user:
        users = {}
        for spec in args.user:
            parts = spec.split(":", 2)
            if len(parts) != 3 or not all(parts):
                raise SystemExit(
                    "--user expects USERNAME:ROLE:PASSWORD with non-empty fields"
                )
            username, role, password = parts
            if username in users:
                raise SystemExit(f"--user duplicates username {username!r}")
            users[username] = (role, password)
    else:
        users = {
            "p12-demo": ("operator", secrets.token_urlsafe(12)),
            "p12-viewer": ("viewer", secrets.token_urlsafe(12)),
            "p12-other": ("operator", secrets.token_urlsafe(12)),
        }
    replay_tape = None
    if args.replay_tape is not None:
        if not args.replay_day:
            raise SystemExit("--replay-tape requires --replay-day YYYY-MM-DD")
        replay_tape = json.loads(args.replay_tape.read_text(encoding="utf-8"))
    fixed_clock = (
        (lambda: datetime.fromisoformat(args.clock)) if args.clock is not None else None
    )
    kernel = build_local_kernel(
        state_dir=args.state_dir,
        users=users,
        clock=fixed_clock,
        paper_replay_fixture=args.paper_replay,
        replay_tape=replay_tape,
        replay_day=args.replay_day,
        replay_source=args.replay_source,
        replay_start_at=args.replay_start_at,
    )
    credentials = args.state_dir / "local-users.json"
    credentials.write_text(
        json.dumps(
            {name: {"role": role, "password": pw} for name, (role, pw) in users.items()}, indent=2
        ),
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    print(
        json.dumps(
            {
                "url": f"http://{args.host}:{args.port}/p12",
                "credentials_file": str(credentials),
                "web_dist": str(web_dist) if web_dist.exists() else None,
                "state_dir": str(args.state_dir),
                "clock": args.clock,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    application = static_app(kernel.app, web_dist if web_dist.exists() else None)
    with make_server(args.host, args.port, application) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
