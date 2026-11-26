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
from quanthus.decision_api.providers import (
    DecisionProviderExhaustedError,
    DecisionProviderSpec,
    PriorityBackend,
)
from quanthus.decision_api.types import (
    ChoiceOption,
    ChoiceQuestion,
    DecisionDeadline,
    DecisionHttpError,
    DecisionLateAnswerError,
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


def respond_slow_headers(pieces: int, per_piece_delay: float):
    """Send the status line, then stall the remaining header block."""

    def behavior(handler) -> None:
        handler.wfile.write(b"HTTP/1.1 200 OK\r\n")
        handler.wfile.flush()
        for _ in range(pieces):
            time.sleep(per_piece_delay)
            try:
                handler.wfile.write(b"X-Synthetic-Header: pending\r\n")
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        try:
            handler.wfile.write(b"Content-Length: 0\r\n\r\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    return behavior


def respond_close_delimited(payload_at: float, eof_at: float, status: int = 200):
    """HTTP/1.0-style close-delimited body: complete only when EOF arrives."""

    data = json.dumps(ANSWER_BODY).encode("utf-8")

    def behavior(handler) -> None:
        handler.send_response(status)
        handler.end_headers()
        if payload_at:
            time.sleep(payload_at)
        try:
            handler.wfile.write(data)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        remaining = eof_at - payload_at
        if remaining > 0:
            time.sleep(remaining)
        # returning without further writes lets the connection close: EOF

    return behavior


def respond_delayed_error_body(status: int, delay: float):
    """Non-2xx headers immediately; the error body arrives only after delay."""

    data = b'{"error":"synthetic","usage":{"input_tokens":9,"output_tokens":0}}'

    def behavior(handler) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        time.sleep(delay)
        try:
            handler.wfile.write(data)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    return behavior


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
        # the budget is a hard wall-clock bound.  the slack ceiling of
        # 250ms is documented, not generous: exception propagation through
        # the read loop plus scheduler wake latency is observed locally at
        # <= 80ms; a whole-second allowance would hide real overruns.
        self.assertLessEqual(
            elapsed,
            1.2 + 0.25,
            f"trickle stretched the attempt to {elapsed:.3f}s beyond budget + slack ceiling",
        )
        self.assertGreater(elapsed, 0.5, "timeout fired suspiciously early")

    def test_fast_server_inside_budget_succeeds(self) -> None:
        with MockServer() as server:
            server.set(respond_trickle(total_bytes=2048, chunks=4, per_chunk_delay=0.01))
            status, body = UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 5.0)
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 2048)

    def test_trickle_timeout_is_tight_not_second_wide(self) -> None:
        # same trickle as the audit probe; the measured slack over the
        # nominal budget must stay inside scheduler noise, documented as:
        # exception propagation through two frames plus pytest overhead,
        # observed locally at <= 80 ms, hard ceiling 250 ms.
        with MockServer() as server:
            server.set(respond_trickle(total_bytes=12288, chunks=24, per_chunk_delay=0.22))
            measurements = []
            for _ in range(3):
                started = time.monotonic()
                with self.assertRaises(DecisionTimeoutError):
                    UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 1.2)
                measurements.append(time.monotonic() - started)
        for elapsed in measurements:
            self.assertLessEqual(
                elapsed,
                1.2 + 0.25,
                f"trickle return at {elapsed:.3f}s exceeds the 250ms documented slack ceiling",
            )
            self.assertGreaterEqual(elapsed, 1.1, "timeout fired before the body could stall")


class SlowHeaderBudgetTests(unittest.TestCase):
    """The response-header wait is bounded by the same strict budget."""

    def test_slow_headers_time_out_at_the_budget(self) -> None:
        # six header pieces at 0.18s each: headers complete at ~1.1s,
        # well past the 0.6s budget.  The R2 implementation (http.client)
        # could not re-arm the timeout inside getresponse() and returned
        # at 1.11s; the raw-socket transport must cut at the budget.
        with MockServer() as server:
            server.set(respond_slow_headers(pieces=6, per_piece_delay=0.18))
            started = time.monotonic()
            with self.assertRaises(DecisionTimeoutError):
                UrllibJsonTransport().post(server.url, {"probe": 1}, {}, 0.6)
            elapsed = time.monotonic() - started
        self.assertLessEqual(
            elapsed,
            0.6 + 0.25,
            f"slow headers returned at {elapsed:.3f}s, beyond budget + slack ceiling",
        )
        self.assertGreaterEqual(elapsed, 0.5, "header timeout fired suspiciously early")


class CloseDelimitedBudgetTests(unittest.TestCase):
    """HTTP/1.0 close-delimited bodies obey the deadline in both directions."""

    def backend(self, url: str, timeout: float = 5.0) -> OpenAIDecisionsBackend:
        return OpenAIDecisionsBackend(
            HttpBackendConfig(url=url, api_key="test-key", timeout_seconds=timeout)
        )

    def test_close_delimited_body_inside_deadline_succeeds(self) -> None:
        # payload at 0.1s, EOF at 0.2s, deadline 1.0s: a complete,
        # well-formed close-delimited response inside budget is valid.
        with MockServer() as server:
            server.set(respond_close_delimited(payload_at=0.1, eof_at=0.2))
            deadline = DecisionDeadline.after(1.0)
            started = time.monotonic()
            response = self.backend(server.url).decide(small_request(), deadline=deadline)
            elapsed = time.monotonic() - started
        self.assertEqual(len(response.answers), 3)
        self.assertLessEqual(elapsed, 1.0 - 0.05)

    def test_close_delimited_eof_after_deadline_never_delivers(self) -> None:
        # audit reproduction: payload at 0.26s, EOF at 0.82s, deadline
        # 0.65s.  R2 delivered this as a success at 0.84s.  Now the EOF
        # wait must hit the deadline wall and the attempt must surface as
        # a timeout, never as an answer.
        with MockServer() as server:
            server.set(respond_close_delimited(payload_at=0.26, eof_at=0.82))
            deadline = DecisionDeadline.after(0.65)
            started = time.monotonic()
            with self.assertRaises(DecisionTimeoutError):
                self.backend(server.url).decide(small_request(), deadline=deadline)
            elapsed = time.monotonic() - started
        self.assertLessEqual(
            elapsed,
            0.65 + 0.25,
            f"close-delimited EOF wait returned at {elapsed:.3f}s, beyond the deadline wall",
        )
        self.assertGreaterEqual(elapsed, 0.55)

    def test_answer_assembled_after_deadline_is_rejected_with_usage(self) -> None:
        # deterministic backend-level proof of the late-answer gate: a
        # scripted transport that ignores the truncated timeout (as a
        # non-compliant transport would) hands the backend a complete,
        # well-formed body assembled past the deadline.  The backend must
        # reject it as DecisionLateAnswerError, never deliver it, and
        # preserve the wire usage because the vendor did process it.
        raw = json.dumps(ANSWER_BODY)

        def compliant_shape_but_slow(url, payload, headers, timeout):
            time.sleep(0.35)
            return 200, raw

        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(
                url="https://synthetic.invalid/v1/decisions",
                api_key="test-key",
                timeout_seconds=5.0,
            ),
            transport=compliant_shape_but_slow,
        )
        deadline = DecisionDeadline.after(0.3)
        started = time.monotonic()
        with self.assertRaises(DecisionLateAnswerError) as ctx:
            backend.decide(small_request(), deadline=deadline)
        elapsed = time.monotonic() - started
        self.assertIsNotNone(ctx.exception.usage)
        assert ctx.exception.usage is not None
        self.assertEqual(ctx.exception.usage.input_tokens, 10)
        # the rejection happens when the response completes (~0.35s),
        # after the 0.3s deadline: the answer existed only past the wall.
        self.assertGreaterEqual(elapsed, 0.3)


def _loopback_chain(first: MockServer, second: MockServer, first_timeout: float = 2.0):
    """Two-provider chain over real transports on loopback (offline only)."""

    def pair(server: MockServer, name: str, timeout: float):
        spec = DecisionProviderSpec(
            id=name,
            type="openai-ga",
            base_url=f"http://127.0.0.1:{server.port}",
            endpoint_path="/v1/decisions",
            model="test-model",
            api_key_env="SYNTHETIC_OFFLINE",
        )
        config = HttpBackendConfig(url=spec.url, api_key="test-key", timeout_seconds=timeout)
        return (spec, OpenAIDecisionsBackend(config))

    return PriorityBackend((pair(first, "first", first_timeout), pair(second, "second", 2.0)))


class _CountingServer(MockServer):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[float] = []

    def set(self, behavior) -> None:
        def counting(handler) -> None:
            self.hits.append(time.monotonic())
            behavior(handler)

        super().set(counting)


class DelayedErrorBodyChainTests(unittest.TestCase):
    """A deterministic status locked by complete headers never reclassifies."""

    def test_late_401_error_body_never_triggers_failover(self) -> None:
        # audit reproduction: the 401 response headers arrive at once but
        # the error body is delayed 0.9s beyond the first provider's
        # configured 0.5s timeout.  R2 misrecorded this as a timeout and
        # dispatched the second provider.  The status is locked at the
        # headers, so the chain must raise http_401 with zero failovers.
        with _CountingServer() as first, _CountingServer() as second:
            first.set(respond_delayed_error_body(401, delay=0.9))
            second.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            chain = _loopback_chain(first, second, first_timeout=0.5)
            started = time.monotonic()
            with self.assertRaises(DecisionHttpError) as ctx:
                chain.decide(small_request())
            elapsed = time.monotonic() - started
            error = ctx.exception
        self.assertEqual(error.status, 401)
        self.assertEqual(len(second.hits), 0, "deterministic 401 must never fail over")
        outcomes = [record["outcome"] for record in error.attempts]
        self.assertEqual(outcomes[0], "http_401")
        # the rest of the chain stays ledger-visible as never dispatched
        self.assertEqual(outcomes[1:], ["not_dispatched"])
        # the error body was never deliverable inside budget: usage stays
        # unknown (conservative worst-case), the attempt is billable
        self.assertEqual(error.attempts[0]["usage"]["status"], "unknown")
        # the best-effort body read consumed the remaining 0.5s budget;
        # elapsed is bounded by that budget plus scheduler slack
        self.assertLessEqual(elapsed, 0.5 + 0.35, f"return at {elapsed:.3f}s outlived the budget")

    def test_absent_error_body_preserves_status_classification(self) -> None:
        # the connection closes right after the headers with no body at
        # all: the status and stop semantics are unchanged.
        def headers_then_close(handler) -> None:
            handler.send_response(401)
            handler.end_headers()
            # no body, connection closes as the handler returns

        with _CountingServer() as first, _CountingServer() as second:
            first.set(headers_then_close)
            second.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            chain = _loopback_chain(first, second, first_timeout=1.0)
            with self.assertRaises(DecisionHttpError) as ctx:
                chain.decide(small_request())
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(len(second.hits), 0)
        self.assertEqual(ctx.exception.attempts[0]["outcome"], "http_401")

    def test_unparseable_error_body_preserves_status_classification(self) -> None:
        with _CountingServer() as first, _CountingServer() as second:
            first.set(respond_fixed(401, "<html>not json at all</html>"))
            second.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            chain = _loopback_chain(first, second, first_timeout=1.0)
            with self.assertRaises(DecisionHttpError) as ctx:
                chain.decide(small_request())
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(len(second.hits), 0)


class CrossBatchSharedDeadlineTests(unittest.TestCase):
    """One monotonic absolute deadline governs every batch in its window."""

    def test_second_batch_past_deadline_is_never_an_answer(self) -> None:
        # audit reproduction: batch A consumes part of a shared 0.9s
        # deadline; batch B hits a close-delimited response whose EOF
        # completes only past the deadline.  R2 delivered batch B as an
        # answer 0.227s past the wall.  Now batch B must surface as a
        # timeout-class failure, and batch C after expiry must dispatch
        # zero additional wire calls.
        def respond_fixed_after(status, body_text, wait):
            data = body_text.encode("utf-8")

            def behavior(handler) -> None:
                time.sleep(wait)
                handler.send_response(status)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                try:
                    handler.wfile.write(data)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

            return behavior

        with _CountingServer() as first, _CountingServer() as second:
            first.set(respond_fixed_after(200, json.dumps(ANSWER_BODY), wait=0.15))
            second.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            chain = _loopback_chain(first, second)
            absolute = DecisionDeadline.after(0.9)
            reply_a = chain.decide(small_request(), deadline=absolute)
            self.assertEqual(len(reply_a.answers), 3)
            first.set(respond_close_delimited(payload_at=0.26, eof_at=0.96))
            wire_before = len(first.hits) + len(second.hits)
            with self.assertRaises(DecisionProviderExhaustedError) as ctx:
                chain.decide(small_request(), deadline=absolute)
            error_b = ctx.exception
            outcomes_b = [record["outcome"] for record in error_b.attempts]
            self.assertNotIn("answer", outcomes_b)
            # batch B dispatched exactly one wire call (the first provider
            # still had positive budget when the batch started), and the
            # EOF-late response was rejected rather than delivered
            self.assertEqual(
                len(first.hits) + len(second.hits),
                wire_before + 1,
                "batch B should dispatch once, then the deadline must stop the chain",
            )
            self.assertEqual(outcomes_b[0], "timeout")
            self.assertEqual(outcomes_b[1], "not_dispatched")
            # batch C after expiry: no new dispatch at all
            wire_before_c = len(first.hits) + len(second.hits)
            with self.assertRaises(DecisionProviderExhaustedError) as ctx_c:
                chain.decide(small_request(), deadline=absolute)
            self.assertTrue(ctx_c.exception.deadline_stopped)
            self.assertEqual(
                len(first.hits) + len(second.hits),
                wire_before_c,
                "an expired shared deadline must dispatch zero additional wire calls",
            )

    def test_expired_deadline_dispatches_zero_wire_calls(self) -> None:
        with _CountingServer() as first, _CountingServer() as second:
            first.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            second.set(respond_fixed(200, json.dumps(ANSWER_BODY)))
            chain = _loopback_chain(first, second)
            expired = DecisionDeadline.after(0.01)
            time.sleep(0.03)
            with self.assertRaises(DecisionProviderExhaustedError) as ctx:
                chain.decide(small_request(), deadline=expired)
            self.assertTrue(ctx.exception.deadline_stopped)
            self.assertEqual(len(first.hits), 0)
            self.assertEqual(len(second.hits), 0)
            outcomes = [record["outcome"] for record in ctx.exception.attempts]
            self.assertTrue(all(outcome == "not_dispatched" for outcome in outcomes))
            for record in ctx.exception.attempts:
                self.assertEqual(record["usage"]["status"], "zero")


if __name__ == "__main__":
    unittest.main()
