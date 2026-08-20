from __future__ import annotations

import pytest

from quanthus.platform import launcher


def test_launcher_drops_privileges_before_exec(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(launcher.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        launcher.os, "setgroups", lambda groups: calls.append(("setgroups", groups))
    )
    monkeypatch.setattr(launcher.os, "setgid", lambda gid: calls.append(("setgid", gid)))
    monkeypatch.setattr(launcher.os, "setuid", lambda uid: calls.append(("setuid", uid)))

    def execvp(program: str, argv: list[str]) -> None:
        calls.append(("execvp", program, tuple(argv)))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(launcher.os, "execvp", execvp)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        launcher.main()

    assert calls == [
        ("setgroups", []),
        ("setgid", 10001),
        ("setuid", 10001),
        (
            "execvp",
            "gunicorn",
            (
                "gunicorn",
                "--bind=0.0.0.0:8080",
                "--workers=1",
                "quanthus.platform.server:create_app()",
            ),
        ),
    ]


def test_launcher_rejects_non_root_start(monkeypatch) -> None:
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 10001)

    with pytest.raises(RuntimeError, match="requires container root"):
        launcher.main()
