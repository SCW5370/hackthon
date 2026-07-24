"""
SafeExec V1 Policy Engine
精确匹配 ActionIntent 与 MissionSpec grants
"""
from __future__ import annotations

import time
from typing import Optional

from .contracts import (
    ActionIntent,
    MissionSpec,
    Fact,
    Decision,
    Grant,
    RequiredFact,
    Effect,
)


class PolicyEngine:
    """
    Policy Engine 负责：
    1. 验证 principal_id 在 MissionSpec 中
    2. 验证 action + resource + arguments 与 grant 完全匹配
    3. 验证所有 required_facts 满足 (值匹配 + 未过期)
    """

    def __init__(self, mission_spec: MissionSpec):
        self.mission_spec = mission_spec

    def evaluate(
        self,
        intent: ActionIntent,
        facts: dict[str, Fact],
    ) -> Decision:
        """
        评估 ActionIntent 是否允许执行

        返回 Decision:
        - allow: 有匹配的 grant 且所有 fact 满足
        - deny: 某种不满足条件
        """
        now_ms = int(time.time() * 1000)
        now_sec = time.time()

        # 1. 检查 principal_id
        if intent.principal_id != self.mission_spec.principal_id:
            return Decision.deny(
                request_id=intent.request_id,
                reason_code="UNKNOWN_PRINCIPAL",
            )

        # 2. 检查 mission 有效期
        if not self.mission_spec.is_valid_at(now_ms):
            return Decision.deny(
                request_id=intent.request_id,
                reason_code="MISSION_EXPIRED",
            )

        # 3. 查找匹配的 grant (精确匹配 action + resource + arguments)
        matched_grant: Optional[Grant] = None
        for grant in self.mission_spec.grants:
            if self._grant_matches_intent(grant, intent):
                matched_grant = grant
                break

        if matched_grant is None:
            return Decision.deny(
                request_id=intent.request_id,
                reason_code="NO_MATCHING_GRANT",
            )

        # 4. 验证所有 required_facts
        fact_refs: list[str] = []
        for req_fact in matched_grant.required_facts:
            fact = facts.get(req_fact.key)

            # Fact 缺失
            if fact is None:
                return Decision.deny(
                    request_id=intent.request_id,
                    reason_code="FACT_MISSING",
                )

            # Fact must satisfy both the publisher TTL and the stricter
            # policy-owned max age. A publisher cannot extend its own trust.
            fact_age_ms = (now_sec - fact.timestamp) * 1000
            if not fact.is_fresh(now_sec) or fact_age_ms > req_fact.max_age_ms:
                return Decision.deny(
                    request_id=intent.request_id,
                    reason_code="FACT_STALE",
                )

            # Fact 值不匹配
            if fact.value != req_fact.equals:
                return Decision.deny(
                    request_id=intent.request_id,
                    reason_code="FACT_MISMATCH",
                )

            # 记录 fact ref
            fact_refs.append(f"{req_fact.key}@{fact.timestamp:.3f}")

        # 所有检查通过
        return Decision.allow(
            request_id=intent.request_id,
            matched_grant_id=matched_grant.grant_id,
            fact_refs=tuple(fact_refs),
        )

    def _grant_matches_intent(self, grant: Grant, intent: ActionIntent) -> bool:
        """检查 grant 是否与 intent 完全匹配"""
        # 验证 action
        if grant.action != intent.action:
            return False

        # 验证 resource
        if grant.resource != intent.resource:
            return False

        # 验证 arguments
        if grant.arguments != intent.arguments:
            return False

        return True
