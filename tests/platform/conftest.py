from __future__ import annotations

import io
import json
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from wsgiref.util import setup_testing_defaults

import pytest

from quanthus.platform.composition import PlatformKernel, create_platform_kernel
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.strategy_attribution_demo import (
    SyntheticAttributionExecutor,
    synthetic_run_request,
)
from quanthus.strategy_attribution_pipeline import AttributionPipeline
from quanthus.strategy_attribution_publication import AttributionPublisher


class TestCipher:
    def encrypt(self, value: str) -> str:
        return "encrypted:" + value[::-1]

    def decrypt(self, value: str) -> str:
        if not value.startswith("encrypted:"):
            raise ValueError("invalid ciphertext")
        return value.removeprefix("encrypted:")[::-1]


@pytest.fixture(scope="module")
def attribution_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("platform-attribution")
    request = synthetic_run_request()
    result = AttributionPipeline(root / "runs").run(request, SyntheticAttributionExecutor())
    return AttributionPublisher(root / "releases").publish(request, result).root


@pytest.fixture()
def kernel(attribution_release: Path) -> PlatformKernel:
    store = InMemoryPlatformStore()
    observed = create_platform_kernel(
        store=store,
        cipher=TestCipher(),
        attribution_root=attribution_release.parent,
    )
    observed.migrate()
    observed.identity.create_user(
        username="admin",
        password="Admin-password-123",
        role="admin",
    )
    return observed


def wsgi_request(
    kernel: PlatformKernel,
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    body = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else b""
    )
    environ: dict[str, Any] = {}
    setup_testing_defaults(environ)
    parsed_path = urllib.parse.urlsplit(path)
    environ.update(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": parsed_path.path,
            "QUERY_STRING": parsed_path.query,
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(body),
        }
    )
    for key, value in (headers or {}).items():
        environ["HTTP_" + key.upper().replace("-", "_")] = value
    captured: dict[str, Any] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    response_body = b"".join(kernel.app(environ, start_response))
    content_type = captured["headers"].get("Content-Type", "")
    decoded: Any = (
        json.loads(response_body) if content_type.startswith("application/json") else response_body
    )
    return int(captured["status"].split(" ", 1)[0]), captured["headers"], decoded


def login(
    kernel: PlatformKernel,
    *,
    username: str = "admin",
    password: str = "Admin-password-123",
) -> str:
    status, _, payload = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/auth/login",
        payload={"username": username, "password": password},
    )
    assert status == 200
    return str(payload["token"])
