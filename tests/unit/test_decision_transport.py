"""Live-transport contract tests against a local loopback mock server.

These tests exercise the real UrllibJsonTransport end to end -- real
sockets, real HTTP parsing -- while never leaving loopback.  They pin the
two review-critical transport properties:

* non-2xx responses preserve their HTTP status regardless of body shape
  (JSON object, JSON array, empty, HTML), so failover classification is
  purely status-driven;
* one strict wall-clock budget covers the full body read, so a server
  trickling one chunk at a time cannot stretch the attempt beyond its
  budget.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import threading
import time
import unittest

from quanthus.decision_api.backends import (
    HttpBackendConfig,
    OpenAIDecisionsBackend,
    UrllibJsonTransport,
)
from quanthus.decision_api.types import (
    ChoiceOption,
    ChoiceQuestion,
    DecisionHttpError,
    DecisionRequest,
    DecisionTimeoutError,
    PredicateQuestion,
    ScoreLevel,
    ScoreQuestion,
)


def small_request() -> DecisionRequest:
    return DecisionRequest(
        model="test-model",
        input="synthetic evidence",
        questions=(
            PredicateQuestion(instructions="p?", name="p"),
            ChoiceQuestion(
                instructions="c?",
                name="c",
                choices=(ChoiceOption("A"), ChoiceOption("B")),
            ),
            ScoreQuestion(
                instructions="s?",
                name="s",
                levels=(ScoreLevel("L"), ScoreLevel("H")),
            ),
        ),
    )


ANSWER_BODY = {
    "answers": [
        {"name": "p", "probability": 0.5, "type": "predicate"},
        {
            "choice": "A",
            "confidence": 0.6,
            "name": "c",
            "probabilities": [
                {"probability": 0.6, "value": "A"},
                {"probability": 0.4, "value": "B"},
            ],
            "type": "choice",
        },
        {
            "confidence": 0.5,
            "name": "s",
            "probabilities": [
                {"label": "L", "probability": 0.5, "value": 0},
                {"label": "H", "probability": 0.5, "value": 1},
            ],
            "score": 1.0,
            "type": "score",
        },
    ],
    "model": "test-model",
    "usage": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
}


class _ProgrammableHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        behavior = self.server.behavior  # type: ignore[attr-defined]
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            behavior(self)

    def log_message(self, *args) -> None:
        pass


def respond_fixed(status: int, body_text: str):
    payload = body_text.encode("utf-8")

    def behavior(handler) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    return behavior


def respond_trickle(total_bytes: int, chunks: int, per_chunk_delay: float):
    payload = json.dumps(ANSWER_BODY).encode("utf-8")
    while len(payload) < total_bytes:
        payload += payload
    payload = payload[:total_bytes]
    piece = max(1, len(payload) // chunks)

    def behavior(handler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        for offset in range(0, len(payload), piece):
            try:
                handler.wfile.write(payload[offset : offset + piece])
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(per_chunk_delay)

    return behavior


class MockServer:
    """Programmable loopback HTTP server; behavior is swappable per request."""

    def __init__(self) -> None:
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProgrammableHandler)
        self._server.daemon_threads = True
        self._server.behavior = respond_fixed(200, json.dumps(ANSWER_BODY))  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )
        self._thread.start()

    def set(self, behavior) -> None:
        self._server.behavior = behavior  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/decisions"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> MockServer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


BODY_SHAPES = {
    "json_object": '{"error": "fixture"}',
    "json_array": '[{"error": 1}]',
    "empty": "",
    "html": "<html><body>Bad Gateway</body></html>",
}


class TransportRawBehaviorTests(unittest.TestCase):
    """The real transport returns (status, raw text) without judging the body."""

    def test_success_body_round_trips_verbatim(self) -> None:
        raw = json.dumps(ANSWER_BODY)
        with MockServer() as server:
            server.set(respond_fixed(200, raw))
            status, body = UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 5.0)
        self.assertEqual(status, 200)
        self.assertEqual(body, raw)

    def test_status_matrix_all_body_shapes_preserve_status(self) -> None:
        statuses = (400, 401, 403, 404, 408, 409, 422, 425, 429, 500, 502, 503, 504, 520, 524, 529)
        matrix: dict[str, dict[str, int]] = {}
        with MockServer() as server:
            for status in statuses:
                matrix[str(status)] = {}
                for shape, raw in BODY_SHAPES.items():
                    server.set(respond_fixed(status, raw))
                    seen, body = UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 5.0)
                    self.assertEqual(seen, status, f"status={status} shape={shape}")
                    self.assertEqual(body, raw, f"status={status} shape={shape}")
                    matrix[str(status)][shape] = seen
        # the full 16x4 matrix ran and every cell preserved its status
        self.assertEqual(len(matrix), 16)
        for outcomes in matrix.values():
            self.assertEqual(len(outcomes), 4)


class BackendClassificationTests(unittest.TestCase):
    """Classification is status-first through the real transport."""

    def backend(self, url: str, timeout: float = 5.0) -> OpenAIDecisionsBackend:
        return OpenAIDecisionsBackend(
            HttpBackendConfig(url=url, api_key="test-key", timeout_seconds=timeout)
        )

    def test_deterministic_statuses_raise_http_error_whatever_the_body(self) -> None:
        with MockServer() as server:
            for status in (400, 401, 403, 404, 422, 520, 524, 529):
                for shape, raw in BODY_SHAPES.items():
                    server.set(respond_fixed(status, raw))
                    with self.assertRaises(DecisionHttpError) as ctx:
                        self.backend(server.url).decide(small_request())
                    self.assertEqual(ctx.exception.status, status, f"status={status} shape={shape}")
                    self.assertEqual(ctx.exception.body, raw[:400])

    def test_transient_statuses_raise_http_error_with_status_too(self) -> None:
        with MockServer() as server:
            for status in (408, 409, 425, 429, 500, 502, 503, 504):
                server.set(respond_fixed(status, ""))
                with self.assertRaises(DecisionHttpError) as ctx:
                    self.backend(server.url).decide(small_request())
                self.assertEqual(ctx.exception.status, status)
                self.assertNotIsInstance(ctx.exception, DecisionTimeoutError)

    def test_billed_failure_usage_extracted_from_error_body(self) -> None:
        raw = json.dumps({"error": "throttled", "usage": {"input_tokens": 64, "output_tokens": 0}})
        with MockServer() as server:
            server.set(respond_fixed(503, raw))
            with self.assertRaises(DecisionHttpError) as ctx:
                self.backend(server.url).decide(small_request())
        self.assertIsNotNone(ctx.exception.usage)
        assert ctx.exception.usage is not None
        self.assertEqual(ctx.exception.usage.input_tokens, 64)

    def test_error_body_html_leaves_usage_unknown(self) -> None:
        with MockServer() as server:
            server.set(respond_fixed(502, "<html>upstream proxy error</html>"))
            with self.assertRaises(DecisionHttpError) as ctx:
                self.backend(server.url).decide(small_request())
        self.assertIsNone(ctx.exception.usage)

    def test_success_parses_through_real_transport(self) -> None:
        with MockServer() as server:
            server.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            response = self.backend(server.url).decide(small_request())
        self.assertEqual(len(response.answers), 3)
        self.assertEqual(response.usage.input_tokens, 10)


class TrickleBudgetTests(unittest.TestCase):
    """A slow-trickle server cannot stretch the attempt beyond its budget."""

    def test_trickling_body_times_out_inside_budget(self) -> None:
        # 24 chunks of a 12 KiB body, each delayed 0.22s after flush: the
        # full body would take ~5.3s, well beyond the 1.2s budget.
        with MockServer() as server:
            server.set(respond_trickle(total_bytes=12288, chunks=24, per_chunk_delay=0.22))
            started = time.monotonic()
            with self.assertRaises(DecisionTimeoutError):
                UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 1.2)
            elapsed = time.monotonic() - started
        # the budget is a hard wall-clock bound: elapsed may exceed the
        # nominal budget only by one in-flight socket timeout quantum
        self.assertLess(elapsed, 1.2 + 1.0, f"trickle stretched the attempt to {elapsed:.2f}s")
        self.assertGreater(elapsed, 0.5, "timeout fired suspiciously early")

    def test_fast_server_inside_budget_succeeds(self) -> None:
        with MockServer() as server:
            server.set(respond_trickle(total_bytes=2048, chunks=4, per_chunk_delay=0.01))
            status, body = UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 5.0)
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 2048)


if __name__ == "__main__":
    unittest.main()
