"""
SafeExec V1 Event Ledger
审计日志，记录所有事件
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional

from .contracts import Event


class EventLedger:
    """
    Event Ledger 负责：
    1. 记录所有事件
    2. 提供事件查询 (支持 seq 游标)
    3. 保持事件有序
    """

    def __init__(self, max_events: int = 10000):
        self._events: deque[Event] = deque(maxlen=max_events)
        self._lock = threading.RLock()
        self._seq = 0

    def emit(
        self,
        event: str,
        source: str,
        payload: dict,
        severity: str = "info",
        incident_id: Optional[str] = None,
    ) -> Event:
        """发布一个事件"""
        with self._lock:
            self._seq += 1
            ev = Event(
                seq=self._seq,
                event=event,
                timestamp=time.time(),
                source=source,
                severity=severity,
                incident_id=incident_id,
                payload=payload,
            )
            self._events.append(ev)
            return ev

    def events_after(self, seq: int) -> list[Event]:
        """获取 seq 之后的所有事件"""
        with self._lock:
            return [e for e in self._events if e.seq > seq]

    def get_last_seq(self) -> int:
        """获取最新事件的 seq"""
        with self._lock:
            return self._seq

    def get_all_events(self) -> list[Event]:
        """获取所有事件"""
        with self._lock:
            return list(self._events)


# 避免循环导入
import time
