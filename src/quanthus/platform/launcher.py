"""Drop the Platform API runtime to its unprivileged uid before starting Gunicorn."""

from __future__ import annotations

import os

API_UID = 10001
API_GID = 10001


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("platform launcher requires container root before privilege drop")
    os.setgroups([])
    os.setgid(API_GID)
    os.setuid(API_UID)
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "--bind=0.0.0.0:8080",
            "--workers=1",
            "quanthus.platform.server:create_app()",
        ],
    )


if __name__ == "__main__":
    main()
