"""
SafeExec V1 Executor Interface
定义 Executor.execute() 接口和 FakeExecutor
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.contracts import ActionIntent


class BaseExecutor(ABC):
    """Executor 抽象基类"""

    @abstractmethod
    def execute(self, intent: dict) -> dict:
        """
        执行 ActionIntent

        Args:
            intent: ActionIntent 字典

        Returns:
            ExecutionReceipt 字典:
            {
                "status": "executed" | "failed",
                "message": str,
                "executed_at_ms": int,
            }
        """
        pass

    def stop(self):
        """停止 Executor"""
        pass


class FakeExecutor(BaseExecutor):
    """
    假的 Executor - 用于测试和演示
    只记录调用，不真正执行动作
    """

    def __init__(self):
        self._call_count = 0
        self._calls: list[dict] = []

    @property
    def call_count(self) -> int:
        return self._call_count

    def get_calls(self) -> list[dict]:
        return list(self._calls)

    def reset(self):
        self._call_count = 0
        self._calls = []

    def execute(self, intent: dict) -> dict:
        self._call_count += 1

        # 提取关键字段
        command_id = intent.get("request_id", "unknown")
        original_action = intent.get("action", "")
        # 映射到 JoyCommand action
        action = "TRANSFER" if original_action == "lab.sample.transfer" else original_action
        resource_id = intent.get("resource", {}).get("id", "")
        source = intent.get("arguments", {}).get("source", "")
        destination = intent.get("arguments", {}).get("destination", "")

        receipt = {
            "status": "executed",
            "command_id": command_id,
            "action": action,
            "sample_id": resource_id,
            "source": source,
            "destination": destination,
            "executed_at_ms": int(time.time() * 1000),
            "message": f"FakeExecutor: {action} {resource_id} from {source} to {destination}",
        }

        self._calls.append({
            "intent": intent,
            "receipt": receipt,
            "received_at": time.time(),
        })

        print(f"[FakeExecutor] #{self._call_count}: {action} {resource_id} ({source} → {destination})")

        return receipt


class JoyExecutor(BaseExecutor):
    """
    真正的 JoyExecutor - 集成到 JOY 机械臂

    映射:
    command_id = ActionIntent.request_id
    action = "TRANSFER"
    sample_id = ActionIntent.resource.id
    source = ActionIntent.arguments.source
    destination = ActionIntent.arguments.destination
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18189,
        *,
        timeout: float = 120.0,
        backend: Any | None = None,
    ):
        if backend is None:
            from joy.driver import JoyDriver
            from joy.safeexec_adapter import JoyExecutor as JoyBackend

            driver = JoyDriver(host, port).connect()
            backend = JoyBackend(driver, timeout=timeout)
        self._backend = backend
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def execute(self, intent: dict) -> dict:
        self._call_count += 1
        receipt = self._backend.execute(intent)
        if receipt.get("state") != "succeeded":
            raise RuntimeError(
                f"JOY execution failed: "
                f"{receipt.get('error_code')}: {receipt.get('error')}"
            )
        return {
            "status": "executed",
            "command_id": intent.get("request_id"),
            "message": "Transferred by the live JOY BioLab executor",
            "executed_at_ms": receipt["finished_at_ms"],
            "receipt": receipt,
        }

    def stop(self):
        driver = getattr(self._backend, "driver", None)
        if driver is not None:
            driver.disconnect()
