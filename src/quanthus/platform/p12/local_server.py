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
    )
    from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

    system_clock = clock
    paper_app = build_paper_application(
        state_dir=state_dir,
        policy=runtime.bindings["SYN-P12-A"].application.policy,
        authorization=SyntheticRuntimeAuthorization(
            "local-paper:v1", frozenset({PAPER_ACCOUNT_ID})
        ),
        now=system_clock,
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
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("the synthetic local server binds to loopback only")
    web_dist = args.web_dist or find_project_root() / "product-web" / "dist"
    users = {
        "p12-demo": ("operator", secrets.token_urlsafe(12)),
        "p12-viewer": ("viewer", secrets.token_urlsafe(12)),
        "p12-other": ("operator", secrets.token_urlsafe(12)),
    }
    fixed_clock = (
        (lambda: datetime.fromisoformat(args.clock)) if args.clock is not None else None
    )
    kernel = build_local_kernel(state_dir=args.state_dir, users=users, clock=fixed_clock)
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
