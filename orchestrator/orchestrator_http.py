"""HTTP and SSE interface for the persistent BioLab Orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .service import (
    HttpRuntimeClient,
    HttpUnsafeExecutorClient,
    LineOrchestrator,
    OrchestratorError,
)
from .job_compiler import DeterministicJobCompiler, OpenAIJobCompiler
from .action_provider import OpenAIActionProvider


class OrchestratorServer(ThreadingHTTPServer):
    orchestrator: LineOrchestrator

    def handle_error(self, request: object, client_address: object) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecOrchestrator/2.0"

    @property
    def app(self) -> LineOrchestrator:
        return self.server.orchestrator  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/healthz":
                self._json(self.app.health())
            elif parsed.path == "/v1/line/state":
                self._json(self.app.snapshot())
            elif parsed.path == "/v1/events":
                after = self._after(parsed.query)
                self._json(self.app.events_after(after))
            elif parsed.path == "/v1/events/stream":
                self._stream(self._after(parsed.query))
            elif parsed.path.startswith("/v1/testing/injections/"):
                injection_id = parsed.path.rsplit("/", 1)[-1]
                self._json(self.app.get_injection(injection_id))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except OrchestratorError as exc:
            self._error(exc.status, str(exc))
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            data = self._body()
            if parsed.path == "/v1/control/start":
                self._require_empty(data)
                self._json(self.app.start())
            elif parsed.path == "/v1/control/pause":
                self._require_empty(data)
                self._json(self.app.pause())
            elif parsed.path == "/v1/control/resume":
                self._require_empty(data)
                self._json(self.app.resume())
            elif parsed.path == "/v1/control/reset":
                self._require_empty(data)
                self._json(self.app.reset())
            elif parsed.path == "/v1/control/mode":
                if set(data) != {"mode"} or not isinstance(data["mode"], str):
                    raise ValueError("mode endpoint expects exactly one string field")
                self._json(
                    self.app.set_execution_mode(
                        data["mode"],
                        self.headers.get("X-Unsafe-Demo-Token", ""),
                    )
                )
            elif parsed.path == "/v1/testing/injections":
                self._json(self.app.register_injection(data), HTTPStatus.ACCEPTED)
            elif parsed.path == "/v1/agent/commands":
                self._json(
                    self.app.compile_operator_command(data),
                    HTTPStatus.CREATED,
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except OrchestratorError as exc:
            self._error(exc.status, str(exc))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _stream(self, after: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        cursor = after
        try:
            while True:
                result = self.app.wait_for_events(cursor, timeout=10.0)
                events = result["events"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    block = (
                        f"id: {event['seq']}\n"
                        f"event: {event['event']}\n"
                        f"data: {payload}\n\n"
                    )
                    self.wfile.write(block.encode("utf-8"))
                    cursor = int(event["seq"])
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    @staticmethod
    def _after(query: str) -> int:
        value = int(parse_qs(query).get("after", ["0"])[0])
        if value < 0:
            raise ValueError("after must be non-negative")
        return value

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("request body must be an object")
        return value

    @staticmethod
    def _require_empty(value: dict) -> None:
        if value:
            raise ValueError("control endpoint expects an empty object")

    def _json(self, value: dict, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8790")
    parser.add_argument(
        "--legacy-url",
        default=os.getenv("LAB_LEGACY_URL", "http://127.0.0.1:8791"),
    )
    parser.add_argument("--fact-mode", choices=("demo", "external"), default="demo")
    parser.add_argument("--recovery-delay", type=float, default=1.5)
    parser.add_argument("--enable-testing", action="store_true")
    parser.add_argument(
        "--agent-provider",
        choices=("replay", "openai"),
        default=os.getenv("SAFEEXEC_AGENT_PROVIDER", "replay"),
    )
    parser.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL", ""))
    parser.add_argument("--llm-api-key", default=os.getenv("LLM_API_KEY", ""))
    parser.add_argument("--llm-model", default=os.getenv("LLM_MODEL", ""))
    parser.add_argument("--enable-unsafe-demo", action="store_true")
    args = parser.parse_args()

    job_compiler = (
        OpenAIJobCompiler(
            base_url=args.llm_base_url,
            api_key=args.llm_api_key,
            model=args.llm_model,
        )
        if args.agent_provider == "openai"
        else DeterministicJobCompiler()
    )
    server = OrchestratorServer((args.host, args.port), Handler)
    action_provider = (
        OpenAIActionProvider(
            base_url=args.llm_base_url,
            api_key=args.llm_api_key,
            model=args.llm_model,
        )
        if args.agent_provider == "openai"
        else None
    )
    server.orchestrator = LineOrchestrator(
        HttpRuntimeClient(args.runtime_url),
        provider=action_provider,
        job_compiler=job_compiler,
        unsafe_executor=(
            HttpUnsafeExecutorClient(
                args.legacy_url,
                os.getenv("LAB_LEGACY_TOKEN", ""),
            )
            if args.enable_unsafe_demo
            else None
        ),
        unsafe_demo_enabled=args.enable_unsafe_demo,
        unsafe_demo_token=os.getenv("LAB_LEGACY_TOKEN", ""),
        fact_mode=args.fact_mode,
        recovery_delay=args.recovery_delay,
        testing_enabled=args.enable_testing,
    )
    print(f"SafeExec Orchestrator listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
