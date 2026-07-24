"""
SafeExec V1 Fact Hub
存储和管理所有 Fact，支持 TTL 过期
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Callable

from .contracts import Fact


class FactHub:
    """
    Fact Hub 负责：
    1. 存储所有 Fact
    2. 提供 Fact 查询
    3. 支持 Fact 变化的回调订阅
    """

    def __init__(self):
        self._facts: dict[str, Fact] = {}
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[str, any], None]] = []

    def update_fact(self, fact: Fact) -> None:
        """更新一个 Fact"""
        with self._lock:
            old_value = self._facts.get(fact.key)
            old_val = old_value.value if old_value else None

            self._facts[fact.key] = fact

            # 如果关键 Fact 变化，通知订阅者
            if old_val != fact.value and fact.key in {"zone.clear", "camera.healthy"}:
                for callback in self._subscribers:
                    try:
                        callback(fact.key, fact.value)
                    except Exception:
                        pass

    def subscribe(self, callback: Callable[[str, any], None]) -> None:
        """订阅 Fact 变化"""
        with self._lock:
            self._subscribers.append(callback)

    def get_fact(self, key: str) -> Optional[Fact]:
        """获取单个 Fact (自动清理过期)"""
        with self._lock:
            fact = self._facts.get(key)
            if fact is None:
                return None

            # 检查是否过期
            now = time.time()
            if not fact.is_fresh(now):
                # 过期的 Fact 返回 None
                return None

            return fact

    def get_all_facts(self) -> dict[str, Fact]:
        """获取所有未过期的 Fact"""
        now = time.time()
        with self._lock:
            result = {}
            for key, fact in self._facts.items():
                if fact.is_fresh(now):
                    result[key] = fact
            return result

    def get_fact_value(self, key: str, default: any = None) -> any:
        """便捷方法：获取 Fact 值，过期或不存在返回 default"""
        fact = self.get_fact(key)
        if fact is None:
            return default
        return fact.value
