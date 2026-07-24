"""
SafeExec V1 State Machine
系统安全状态管理
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Optional

import time


class SystemState(str, Enum):
    LOCKED = "LOCKED"       # 默认锁定，无执行权
    READY = "READY"         # 条件满足，等待动作
    RUNNING = "RUNNING"     # Lease 有效，动作执行中
    DEGRADED = "DEGRADED"   # 风险上升，限制动作或等待恢复
    SAFE_HOLD = "SAFE_HOLD" # Lease 已撤销，执行器进入安全保持


class StateMachine:
    """
    State Machine 负责：
    1. 管理系统状态
    2. 状态转换验证
    3. 触发状态变化事件
    """

    # 合法的状态转换
    VALID_TRANSITIONS = {
        SystemState.LOCKED: {SystemState.READY},
        SystemState.READY: {SystemState.RUNNING, SystemState.LOCKED},
        SystemState.RUNNING: {SystemState.DEGRADED, SystemState.SAFE_HOLD},
        SystemState.DEGRADED: {SystemState.RUNNING, SystemState.SAFE_HOLD, SystemState.LOCKED},
        SystemState.SAFE_HOLD: {SystemState.LOCKED},  # 只能人工复位
    }

    def __init__(self, event_emitter=None):
        self._state = SystemState.LOCKED
        self._lock = threading.RLock()
        self._event_emitter = event_emitter  # 回调函数

    @property
    def state(self) -> SystemState:
        with self._lock:
            return self._state

    def transition(self, new_state: SystemState, reason: str = "") -> bool:
        """
        尝试状态转换
        Returns: True if transition succeeded
        """
        with self._lock:
            if new_state not in self.VALID_TRANSITIONS.get(self._state, set()):
                return False

            old_state = self._state
            self._state = new_state

            # 触发事件
            if self._event_emitter:
                try:
                    self._event_emitter(
                        "state.changed",
                        "state_machine",
                        {
                            "old_state": old_state.value,
                            "new_state": new_state.value,
                            "reason": reason,
                        },
                        "warning" if new_state in {SystemState.DEGRADED, SystemState.SAFE_HOLD} else "info",
                    )
                except Exception:
                    pass

            return True

    def reset(self) -> bool:
        """人工复位到 LOCKED"""
        return self.transition(SystemState.LOCKED, "manual_reset")

    def to_ready(self) -> bool:
        """转换到 READY"""
        return self.transition(SystemState.READY, "conditions_met")

    def to_running(self) -> bool:
        """转换到 RUNNING"""
        return self.transition(SystemState.RUNNING, "lease_issued")

    def to_degraded(self, reason: str) -> bool:
        """转换到 DEGRADED"""
        return self.transition(SystemState.DEGRADED, reason)

    def to_safe_hold(self, reason: str) -> bool:
        """转换到 SAFE_HOLD"""
        return self.transition(SystemState.SAFE_HOLD, reason)

    def snapshot(self) -> dict:
        """获取状态快照"""
        with self._lock:
            return {
                "state": self._state.value,
                "timestamp": time.time(),
            }
