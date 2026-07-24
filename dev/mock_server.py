#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "console"


class LatestFrame:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.data: bytes | None = None
        self.content_type = "image/jpeg"
        self.timestamp = 0.0

    def update(self, data: bytes, content_type: str) -> None:
        if not data:
            raise ValueError("empty frame")
        if len(data) > 2_000_000:
            raise ValueError("frame exceeds 2 MB")
        with self.lock:
            self.data = data
            self.content_type = content_type
            self.timestamp = time.time()

    def snapshot(self) -> tuple[bytes | None, str, float]:
        with self.lock:
            return self.data, self.content_type, self.timestamp


class DemoState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.seq = 0
        self.events: deque[dict] = deque(maxlen=500)
        self.reset()

    def reset(self) -> None:
        with getattr(self, "lock", threading.Lock()):
            self.status = "READY"
            self.lease_ms = 0
            self.machine = "STOPPED"
            self.facts = {
                "zone.clear": self._fact("zone.clear", True, "mock-vision", 500),
                "camera.healthy": self._fact(
                    "camera.healthy", True, "mock-camera", 1000
                ),
                "system.heartbeat": self._fact(
                    "system.heartbeat", True, "mock-health", 1000
                ),
            }
            if hasattr(self, "events"):
                self.events.clear()
                self.seq = 0
                self.emit("state.changed", "mock-server", {"state": self.status})

    @staticmethod
    def _fact(key: str, value: object, source: str, ttl_ms: int) -> dict:
        return {
            "key": key,
            "value": value,
            "source": source,
            "confidence": 1.0,
            "timestamp": time.time(),
            "ttl_ms": ttl_ms,
            "evidence": {},
        }

    def emit(
        self, event: str, source: str, payload: dict, severity: str = "info"
    ) -> dict:
        self.seq += 1
        item = {
            "seq": self.seq,
            "event": event,
            "timestamp": time.time(),
            "source": source,
            "severity": severity,
            "incident_id": None,
            "payload": payload,
        }
        self.events.append(item)
        return item

    def update_fact(self, fact: dict) -> None:
        required = {"key", "value", "source", "timestamp", "ttl_ms"}
        missing = required.difference(fact)
        if missing:
            raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
        with self.lock:
            previous = self.facts.get(fact["key"])
            self.facts[fact["key"]] = fact
            changed = previous is None or previous.get("value") != fact.get("value")
            if changed:
                severity = (
                    "critical"
                    if fact["key"] in {"zone.clear", "camera.healthy"}
                    and fact.get("value") is not True
                    else "info"
                )
                self.emit("fact.updated", fact["source"], {"fact": fact}, severity)

            # Development-only reaction so the UI can be integrated before the
            # real policy/Lease kernel arrives. This is deliberately fail-safe.
            unsafe = (
                fact["key"] in {"zone.clear", "camera.healthy"}
                and fact.get("value") is not True
            )
            if unsafe and self.status != "SAFE_HOLD":
                self.status = "SAFE_HOLD"
                self.machine = "STOPPED"
                self.lease_ms = 0
                self.emit(
                    "lease.revoked",
                    "mock-core",
                    {"reason": f"{fact['key']}={fact.get('value')}"},
                    "critical",
                )

    def apply_scenario(self, scenario: str) -> None:
        with self.lock:
            if scenario == "normal":
                self.status = "RUNNING"
                self.machine = "RUNNING"
                self.lease_ms = 2000
                self.emit("lease.issued", "mock-core", {"ttl_ms": 2000})
            elif scenario == "intrusion":
                fact = self._fact("zone.clear", False, "mock-vision", 500)
                fact["evidence"] = {"track_ids": [7]}
                self.facts[fact["key"]] = fact
                self.status = "SAFE_HOLD"
                self.machine = "STOPPED"
                self.lease_ms = 0
                self.emit("fact.updated", "mock-vision", {"fact": fact}, "critical")
                self.emit(
                    "lease.revoked",
                    "mock-core",
                    {"reason": "zone.clear=false", "time_to_safe_ms": 286},
                    "critical",
                )
            elif scenario == "camera":
                fact = self._fact("camera.healthy", None, "mock-camera", 1000)
                fact["evidence"] = {"reason": "FRAME_TIMEOUT"}
                self.facts[fact["key"]] = fact
                self.status = "SAFE_HOLD"
                self.machine = "STOPPED"
                self.lease_ms = 0
                self.emit("fact.updated", "mock-camera", {"fact": fact}, "critical")
                self.emit(
                    "lease.revoked",
                    "mock-core",
                    {"reason": "camera.healthy=UNKNOWN"},
                    "critical",
                )
            elif scenario == "degraded":
                self.status = "DEGRADED"
                self.machine = "DEGRADED"
                self.lease_ms = 900
                self.emit(
                    "state.changed",
                    "mock-core",
                    {"state": "DEGRADED", "reason": "system risk"},
                    "warning",
                )
            elif scenario == "reset":
                self.status = "READY"
                self.machine = "STOPPED"
                self.lease_ms = 0
                for key in tuple(self.facts):
                    self.facts[key]["value"] = True
                    self.facts[key]["timestamp"] = time.time()
                self.emit("state.changed", "mock-core", {"state": "READY"})
            else:
                raise ValueError(f"unknown scenario: {scenario}")

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "lease_remaining_ms": self.lease_ms,
                "machine": self.machine,
                "facts": list(self.facts.values()),
                "last_seq": self.seq,
                "server_time": time.time(),
            }

    def events_after(self, seq: int) -> list[dict]:
        with self.lock:
            return [event for event in self.events if event["seq"] > seq]


STATE = DemoState()
FRAME = LatestFrame()


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeExecMock/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(STATE.snapshot())
            return
        if parsed.path == "/api/events":
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            self._json({"events": STATE.events_after(after)})
            return
        if parsed.path == "/api/frame":
            data, content_type, timestamp = FRAME.snapshot()
            if data is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._binary(
                data,
                content_type,
                {"X-Frame-Timestamp": str(timestamp), "Cache-Control": "no-store"},
            )
            return
        if parsed.path in {"/", "/index.html"}:
            self._file(CONSOLE / "index.html")
            return
        requested = (CONSOLE / parsed.path.lstrip("/")).resolve()
        if CONSOLE in requested.parents and requested.is_file():
            self._file(requested)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/frame":
                length = int(self.headers.get("Content-Length", "0"))
                FRAME.update(
                    self.rfile.read(length),
                    self.headers.get("Content-Type", "image/jpeg"),
                )
                self._json({"accepted": True}, HTTPStatus.ACCEPTED)
                return
            payload = self._read_json()
            if parsed.path == "/api/facts":
                STATE.update_fact(payload)
                self._json({"accepted": True}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/mock/scenario":
                STATE.apply_scenario(str(payload.get("scenario", "")))
                self._json(STATE.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        if self.path.startswith(
            ("/api/state", "/api/events", "/api/frame", "/api/facts")
        ):
            return
        print(f"[mock-server] {self.address_string()} {format % args}")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._binary(data, content_type)

    def _binary(
        self,
        data: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SafeExec mock dashboard: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
