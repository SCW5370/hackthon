"""High-level JOY client API for the SafeExec integration boundary."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .command import JoyCommand
from .locations import resolve_location


class JoyDriver:
    """Named-location API over the in-level DataExchange RPC endpoint."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18189,
        *,
        exchange: Any | None = None,
        sim_env: Any | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._exchange = exchange
        self._sim_env = sim_env
        self._connected = exchange is not None

    def connect(self) -> "JoyDriver":
        if self._connected:
            return self
        if self._sim_env is None:
            try:
                from pyjop import SimEnv
            except ImportError as exc:
                raise RuntimeError(
                    "pyjop is required for a live JOY connection; "
                    "run joy.probe_env with the JOY Python or install pyjop"
                ) from exc
            self._sim_env = SimEnv
        if not self._sim_env.connect(self.host, self.port):
            raise ConnectionError(f"could not connect to JOY at {self.host}:{self.port}")
        if self._exchange is None:
            from pyjop import DataExchange

            self._exchange = DataExchange.find("safeexec_exchange")
        if self._exchange is None:
            raise ConnectionError("safeexec_exchange was not found in the active level")
        self._connected = True
        return self

    def health(self) -> dict[str, Any]:
        return self._rpc("health")

    def get_inventory(self) -> dict[str, Any]:
        return self._rpc("inventory")

    def get_arm_state(self) -> str:
        return str(self.health()["arm_state"])

    def transfer(
        self,
        sample_id: str,
        source: str,
        destination: str,
        *,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        # Resolve both names locally so raw coordinates cannot enter the RPC.
        resolve_location(sample_id, source)
        resolve_location(sample_id, destination)
        command = JoyCommand(
            command_id=command_id or f"cmd-{uuid.uuid4().hex[:12]}",
            action="TRANSFER",
            sample_id=sample_id,
            source=source,
            destination=destination,
        )
        return self._rpc("transfer", command.to_dict())

    def pause(self) -> dict[str, Any]:
        return self._rpc("pause")

    def resume(self) -> dict[str, Any]:
        return self._rpc("resume")

    def reset(self) -> dict[str, Any]:
        return self._rpc("reset")

    def disconnect(self) -> None:
        """Close a live pyjop client.

        pyjop 1.0.3 terminates the current Python process after disconnecting,
        so command-line entry points call this only after flushing their result.
        """

        if self._sim_env is not None and self._connected:
            try:
                self._sim_env.disconnect()
            except SystemExit:
                # pyjop 1.0.3 implements disconnect by raising SystemExit after
                # closing its socket. Library callers should still regain control.
                pass
        self._connected = False

    def _rpc(self, func_name: str, *args: Any) -> Any:
        if not self._connected or self._exchange is None:
            raise RuntimeError("JoyDriver.connect() must be called first")

        old_result = None
        try:
            if "rpc_result" in self._exchange.get_keys():
                old_result = self._exchange.get_data("rpc_result")
        except (AttributeError, TypeError):
            pass

        result = self._exchange.rpc(func_name, *args)
        stale_pyjop_result = (
            isinstance(old_result, dict)
            and old_result.get("func_name") == func_name
            and old_result.get("value") == result
        )
        if result not in ("", None) and not stale_pyjop_result:
            return result

        # pyjop 1.0.3 can return before an event reply arrives. Poll its public
        # DataExchange data key as a compatibility path.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if "rpc_result" in self._exchange.get_keys():
                payload = self._exchange.get_data("rpc_result")
                if (
                    isinstance(payload, dict)
                    and payload != old_result
                    and payload.get("func_name") == func_name
                ):
                    return payload.get("value")
            time.sleep(0.05)
        raise TimeoutError(f"JOY RPC {func_name!r} did not return within 8 seconds")
