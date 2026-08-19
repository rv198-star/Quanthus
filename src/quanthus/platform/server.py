"""Administrative and development entry point for the Platform Kernel."""

from __future__ import annotations

import argparse
import json
from wsgiref.simple_server import make_server

from quanthus.platform.composition import platform_kernel_from_env
from quanthus.platform.persistence import DuplicateRecordError


def create_app():  # type: ignore[no-untyped-def]
    """Gunicorn factory; use one worker while the local JobRunner is active."""
    kernel = platform_kernel_from_env()
    kernel.migrate()
    kernel.start()
    return kernel.app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Quanthus Platform Kernel")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    bootstrap = subparsers.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--password", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    kernel = platform_kernel_from_env()
    applied = kernel.migrate()
    if args.command == "migrate":
        print(json.dumps({"applied": list(applied)}, ensure_ascii=True, sort_keys=True))
        return 0
    if args.command == "bootstrap-admin":
        try:
            user = kernel.identity.create_user(
                username=args.username,
                password=args.password,
                role="admin",
            )
        except DuplicateRecordError:
            print(json.dumps({"created": False, "reason": "already_exists"}, sort_keys=True))
            return 2
        print(json.dumps({"created": True, "user_id": user.user_id}, sort_keys=True))
        return 0
    kernel.start()
    with make_server(args.host, args.port, kernel.app) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
