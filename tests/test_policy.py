#!/usr/bin/env python3
"""
SafeExec V1 Policy Engine 测试
"""
import sys
import os
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.contracts import ActionIntent, MissionSpec, Fact, Effect
from runtime.policy_engine import PolicyEngine


def create_mission() -> MissionSpec:
    """创建测试用 MissionSpec"""
    mission_dict = {
        "schema_version": "safeexec.mission.v1",
        "mission_id": "mission-test-001",
        "principal_id": "lab-agent-01",
        "valid_from_ms": int(time.time() * 1000) - 3600000,
        "valid_until_ms": int(time.time() * 1000) + 3600000,
        "grants": [
            {
                "grant_id": "grant-sample-a",
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": "sample-A"},
                "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
                "required_facts": [
                    {"key": "camera.healthy", "equals": True, "max_age_ms": 1500},
                ],
            },
        ],
    }
    return MissionSpec.from_dict(mission_dict)


def create_intent(
    resource_id="sample-A",
    source="cold-storage",
    destination="analyzer-01",
) -> ActionIntent:
    return ActionIntent(
        schema_version="safeexec.action.v1",
        request_id=str(uuid.uuid4()),
        principal_id="lab-agent-01",
        issued_at_ms=int(time.time() * 1000),
        action="lab.sample.transfer",
        resource={"type": "lab.sample", "id": resource_id},
        arguments={"source": source, "destination": destination},
    )


def create_fresh_fact(key: str, value=True) -> Fact:
    return Fact(
        key=key,
        value=value,
        source="test",
        confidence=1.0,
        timestamp=time.time(),
        ttl_ms=1500,
        evidence={},
    )


def test_policy_allow():
    """测试完全匹配的请求被允许"""
    mission = create_mission()
    engine = PolicyEngine(mission)
    intent = create_intent()
    facts = {"camera.healthy": create_fresh_fact("camera.healthy", True)}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.ALLOW
    assert decision.matched_grant_id == "grant-sample-a"
    print("[PASS] test_policy_allow")


def test_policy_unknown_principal():
    """测试未知 principal 被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)

    intent = ActionIntent(
        schema_version="safeexec.action.v1",
        request_id=str(uuid.uuid4()),
        principal_id="unknown-agent",  # 未知
        issued_at_ms=int(time.time() * 1000),
        action="lab.sample.transfer",
        resource={"type": "lab.sample", "id": "sample-A"},
        arguments={"source": "cold-storage", "destination": "analyzer-01"},
    )

    facts = {"camera.healthy": create_fresh_fact("camera.healthy", True)}
    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "UNKNOWN_PRINCIPAL"
    print("[PASS] test_policy_unknown_principal")


def test_policy_no_matching_grant():
    """测试无匹配 grant 被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)

    # sample-B 不在 grant 中
    intent = create_intent(resource_id="sample-B")
    facts = {"camera.healthy": create_fresh_fact("camera.healthy", True)}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "NO_MATCHING_GRANT"
    print("[PASS] test_policy_no_matching_grant")


def test_policy_fact_missing():
    """测试缺少 fact 被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)
    intent = create_intent()

    # 没有 camera.healthy fact
    facts = {}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "FACT_MISSING"
    print("[PASS] test_policy_fact_missing")


def test_policy_fact_stale():
    """测试 fact 过期被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)
    intent = create_intent()

    # 过期的 fact
    stale_fact = Fact(
        key="camera.healthy",
        value=True,
        source="test",
        confidence=1.0,
        timestamp=time.time() - 3,  # 3 秒前
        ttl_ms=1500,  # 1.5 秒 TTL
        evidence={},
    )
    facts = {"camera.healthy": stale_fact}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "FACT_STALE"
    print("[PASS] test_policy_fact_stale")


def test_policy_fact_mismatch():
    """测试 fact 值不匹配被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)
    intent = create_intent()

    # camera.healthy = False
    facts = {"camera.healthy": create_fresh_fact("camera.healthy", False)}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "FACT_MISMATCH"
    print("[PASS] test_policy_fact_mismatch")


def test_policy_destination_mismatch():
    """测试目标位置不匹配被拒绝"""
    mission = create_mission()
    engine = PolicyEngine(mission)
    intent = create_intent(destination="waste-bin")  # 不是 analyzer-01
    facts = {"camera.healthy": create_fresh_fact("camera.healthy", True)}

    decision = engine.evaluate(intent, facts)

    assert decision.effect == Effect.DENY
    assert decision.reason_code == "NO_MATCHING_GRANT"
    print("[PASS] test_policy_destination_mismatch")


def run_all():
    print("=" * 60)
    print("SafeExec V1 Policy Engine Tests")
    print("=" * 60)

    tests = [
        test_policy_allow,
        test_policy_unknown_principal,
        test_policy_no_matching_grant,
        test_policy_fact_missing,
        test_policy_fact_stale,
        test_policy_fact_mismatch,
        test_policy_destination_mismatch,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
