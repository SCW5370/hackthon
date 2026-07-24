#!/usr/bin/env python3
"""
SafeExec V1 契约测试
测试 ActionIntent, MissionSpec, Fact, Decision, Lease
"""
import sys
import os
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.contracts import (
    ActionIntent,
    MissionSpec,
    Fact,
    Decision,
    ActionLease,
    ALLOWED_ACTION,
    ALLOWED_RESOURCE_IDS,
    ALLOWED_POSITIONS,
)


def test_action_intent_valid():
    """测试有效的 ActionIntent"""
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": ALLOWED_ACTION,
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    intent = ActionIntent.from_dict(intent_dict)

    assert intent.schema_version == "safeexec.action.v1"
    assert intent.action == "lab.sample.transfer"
    assert intent.resource["id"] == "sample-A"
    assert intent.arguments["destination"] == "analyzer-01"

    print("[PASS] test_action_intent_valid")


def test_action_intent_unknown_field():
    """测试未知字段被拒绝"""
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": ALLOWED_ACTION,
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
        "extra_field": "should fail",  # 未知字段
    }

    try:
        ActionIntent.from_dict(intent_dict)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown fields" in str(e)
        print(f"[PASS] test_action_intent_unknown_field: {e}")


def test_action_intent_invalid_action():
    """测试非法 action 被拒绝"""
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "robot.arm.pick",  # 非法 action
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    try:
        ActionIntent.from_dict(intent_dict)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Invalid action" in str(e)
        print(f"[PASS] test_action_intent_invalid_action: {e}")


def test_action_intent_time_too_old():
    """测试请求时间太旧被拒绝"""
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000) - 20000,  # 20 秒前 > 10 秒限制
        "action": ALLOWED_ACTION,
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    try:
        ActionIntent.from_dict(intent_dict)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "time" in str(e).lower()
        print(f"[PASS] test_action_intent_time_too_old: {e}")


def test_action_intent_normalize():
    """测试 Intent 规范化哈希"""
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),  # 使用当前时间
        "action": ALLOWED_ACTION,
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    intent = ActionIntent.from_dict(intent_dict)
    hash1 = intent.compute_hash()

    # 再次哈希应该相同 (规范化)
    intent2 = ActionIntent.from_dict(intent_dict)
    hash2 = intent2.compute_hash()

    assert hash1 == hash2
    assert hash1.startswith("sha256:")
    print(f"[PASS] test_action_intent_normalize: hash={hash1[:30]}...")


def test_mission_spec():
    """测试 MissionSpec 解析"""
    mission_dict = {
        "schema_version": "safeexec.mission.v1",
        "mission_id": "mission-001",
        "principal_id": "lab-agent-01",
        "valid_from_ms": int(time.time() * 1000) - 1000,
        "valid_until_ms": int(time.time() * 1000) + 3600000,
        "grants": [
            {
                "grant_id": "grant-001",
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": "sample-A"},
                "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
                "required_facts": [
                    {"key": "camera.healthy", "equals": True, "max_age_ms": 1500}
                ],
            }
        ],
    }

    mission = MissionSpec.from_dict(mission_dict)

    assert mission.principal_id == "lab-agent-01"
    assert len(mission.grants) == 1
    assert mission.grants[0].grant_id == "grant-001"
    assert mission.is_valid_at(int(time.time() * 1000))

    print("[PASS] test_mission_spec")


def test_fact_ttl():
    """测试 Fact TTL 过期"""
    fact = Fact(
        key="camera.healthy",
        value=True,
        source="test",
        confidence=1.0,
        timestamp=time.time() - 2,  # 2 秒前
        ttl_ms=1000,  # 1 秒 TTL
        evidence={},
    )

    assert fact.is_fresh(time.time()) is False  # 已过期
    print("[PASS] test_fact_ttl")


def test_decision_allow():
    """测试 Decision 允许"""
    decision = Decision.allow(
        request_id="req-001",
        matched_grant_id="grant-001",
        fact_refs=("camera.healthy@1784800000.125",),
    )

    assert decision.effect.value == "allow"
    assert decision.reason_code == "GRANT_MATCHED"
    assert decision.matched_grant_id == "grant-001"

    print("[PASS] test_decision_allow")


def test_decision_deny():
    """测试 Decision 拒绝"""
    decision = Decision.deny(
        request_id="req-001",
        reason_code="FACT_MISSING",
    )

    assert decision.effect.value == "deny"
    assert decision.reason_code == "FACT_MISSING"
    assert decision.matched_grant_id is None

    print("[PASS] test_decision_deny")


def run_all():
    print("=" * 60)
    print("SafeExec V1 Contracts Tests")
    print("=" * 60)

    tests = [
        test_action_intent_valid,
        test_action_intent_unknown_field,
        test_action_intent_invalid_action,
        test_action_intent_time_too_old,
        test_action_intent_normalize,
        test_mission_spec,
        test_fact_ttl,
        test_decision_allow,
        test_decision_deny,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
