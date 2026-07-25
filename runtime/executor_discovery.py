"""Active discovery and readiness tracking for device-side Guards.

Discovery is intentionally limited to an explicit allow-list of endpoints.
Finding a Guard never authorizes or executes an action; it only selects a
healthy transport for the Runtime's existing Policy -> Lease -> Guard path.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def normalize_guard_base_url(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if endpoint.endswith("/v1/execute"):
        endpoint = endpoint[: -len("/v1/execute")]
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid Guard endpoint: {value!r}")
    if parsed.path not in {"", "/"}:
        raise ValueError("Guard candidates must be service base URLs")
    return endpoint


class GuardEndpointDiscovery:
    """Poll configured Guard endpoints and retain the first ready candidate."""

    MAX_CLOCK_SKEW_MS = 2000

    def __init__(
        self,
        candidates: Iterable[str],
        *,
        expected_audience: str = "joy-guard-01",
        interval: float = 2.0,
        timeout: float = 1.5,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        opener: Callable[..., Any] = urlopen,
        autostart: bool = True,
    ) -> None:
        normalized = tuple(
            dict.fromkeys(normalize_guard_base_url(value) for value in candidates)
        )
        if not normalized:
            raise ValueError("at least one Guard candidate is required")
        self.candidates = normalized
        self.expected_audience = expected_audience
        self.interval = interval
        self.timeout = timeout
        self._now_ms = now_ms
        self._opener = opener
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._selected: str | None = None
        self._executing = False
        self._records: dict[str, dict[str, Any]] = {
            endpoint: {
                "endpoint": endpoint,
                "status": "unknown",
                "last_checked_ms": None,
                "last_ready_ms": None,
                "latency_ms": None,
                "clock_skew_ms": None,
                "error": None,
                "guard": None,
            }
            for endpoint in normalized
        }
        self._thread: threading.Thread | None = None
        if autostart:
            self._thread = threading.Thread(
                target=self._run,
                name="safeexec-guard-discovery",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._executing:
                self.refresh()
            self._stop.wait(self.interval)

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(self.timeout + 0.5, 2.0))

    def set_executing(self, value: bool) -> None:
        with self._lock:
            self._executing = value

    def refresh(self, *, blocking: bool = False) -> dict[str, Any]:
        """Refresh readiness.

        Background polling stays non-blocking so overlapping timer ticks do not
        accumulate. Callers on the physical execution path can request a
        blocking refresh to avoid failing closed on a stale unavailable cache
        while another probe is just finishing.
        """
        if not self._refresh_lock.acquire(blocking=blocking):
            return self.snapshot()
        try:
            selected = None
            for endpoint in self.candidates:
                record = self._probe(endpoint)
                with self._lock:
                    self._records[endpoint] = record
                if selected is None and record["status"] == "ready":
                    selected = endpoint
            with self._lock:
                self._selected = selected
            return self.snapshot()
        finally:
            self._refresh_lock.release()

    def _probe(self, endpoint: str) -> dict[str, Any]:
        started = time.monotonic()
        checked_at = self._now_ms()
        previous = self._records[endpoint]
        try:
            request = Request(
                f"{endpoint}/readyz",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with self._opener(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Guard returned non-object readiness")
            audience = value.get("expected_audience")
            if audience != self.expected_audience:
                raise ValueError(
                    f"unexpected Guard audience {audience!r}; "
                    f"expected {self.expected_audience!r}"
                )
            if value.get("ready") is not True or value.get("status") != "ready":
                raise ValueError(
                    str(value.get("error") or "JOY executor is not ready")
                )
            guard_checked_at = value.get("checked_at_ms")
            clock_skew_ms = None
            if isinstance(guard_checked_at, (int, float)):
                clock_skew_ms = abs(self._now_ms() - int(guard_checked_at))
                if clock_skew_ms > self.MAX_CLOCK_SKEW_MS:
                    raise ValueError(
                        "device clock skew "
                        f"{clock_skew_ms}ms exceeds {self.MAX_CLOCK_SKEW_MS}ms"
                    )
            return {
                "endpoint": endpoint,
                "status": "ready",
                "last_checked_ms": checked_at,
                "last_ready_ms": checked_at,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "clock_skew_ms": clock_skew_ms,
                "error": None,
                "guard": value,
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            return {
                "endpoint": endpoint,
                "status": "unavailable",
                "last_checked_ms": checked_at,
                "last_ready_ms": previous.get("last_ready_ms"),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "clock_skew_ms": (
                    clock_skew_ms if "clock_skew_ms" in locals() else None
                ),
                "error": str(exc),
                "guard": None,
            }

    def require_ready(self) -> str:
        with self._lock:
            selected = self._selected
            ready = (
                selected is not None
                and self._records[selected].get("status") == "ready"
            )
        if not ready:
            # Execution authorization must use a fresh observation. If the
            # background poll currently owns the refresh lock, wait for it and
            # immediately probe again rather than denying from stale cache.
            self.refresh(blocking=True)
            with self._lock:
                selected = self._selected
                ready = (
                    selected is not None
                    and self._records[selected].get("status") == "ready"
                )
        if not ready or selected is None:
            raise ConnectionError("no ready Guard + JOY executor was discovered")
        return f"{selected}/v1/execute"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            selected = self._selected
            selected_record = (
                dict(self._records[selected]) if selected is not None else None
            )
            ready = bool(
                selected_record and selected_record.get("status") == "ready"
            )
            status = "executing" if self._executing else (
                "ready" if ready else "discovering"
            )
            return {
                "schema_version": "safeexec.executor-connectivity.v1",
                "status": status,
                "ready": ready or self._executing,
                "selected_endpoint": selected,
                "selected_guard": (
                    selected_record.get("guard") if selected_record else None
                ),
                "candidates": [
                    dict(self._records[endpoint]) for endpoint in self.candidates
                ],
                "auto_execute": False,
                "requires_operator_start": True,
            }
