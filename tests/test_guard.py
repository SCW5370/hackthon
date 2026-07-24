#!/usr/bin/env python3
"""
SafeExec V1 Guard 测试
端到端集成测试
"""
import sys
import os
import time
import uuid
import tempfile
import threading
import http.server
import socketserver
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.contracts import ActionIntent, Decision
from runtime.lease_authority import LeaseAuthority
from guard.verifier import LeaseVerifier
from guard.executor import FakeExecutor, JoyExecutor
from guard.guard_http import SafeExecGuard


# ============================================================
# 测试夹具
# ==========================================================

def create_valid_intent():
    """创建有效的 ActionIntent"""
    return {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }


def create_valid_lease(intent, private_key_b64):
    """创建有效的 Lease"""
    authority = LeaseAuthority(private_key_b64, "test-key-01")

    action_intent = ActionIntent.from_dict(intent)
    decision = Decision.allow(
        request_id=action_intent.request_id,
        matched_grant_id="grant-001",
        fact_refs=("camera.healthy@1784800000.125",),
    )

    lease = authority.issue_lease(
        intent=action_intent,
        decision=decision,
        audience="joy-guard-01",
        mission_id="mission-001",
    )

    return lease.to_dict()


# ============================================================
# Guard Verifier 测试
# ==========================================================

def test_guard_verify_valid():
    """测试有效 Lease 验证通过"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    verifier = LeaseVerifier(public_b64, "joy-guard-01")

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    is_valid, reason, receipt = verifier.verify_and_consume(intent, lease)

    assert is_valid, f"Should be valid: {reason}"
    assert receipt is not None
    assert receipt["status"] == "verified"

    verifier.close()
    print("[PASS] test_guard_verify_valid")


def test_guard_verify_invalid_signature():
    """测试无效签名被拒绝"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    wrong_private_b64, _ = LeaseAuthority.generate_keypair()  # 错误的密钥

    verifier = LeaseVerifier(public_b64, "joy-guard-01")

    intent = create_valid_intent()
    # 用错误的私钥签发
    lease = create_valid_lease(intent, wrong_private_b64)

    is_valid, reason, receipt = verifier.verify_and_consume(intent, lease)

    assert not is_valid
    assert "signature" in reason.lower() or "invalid" in reason.lower()
    assert receipt is None

    verifier.close()
    print("[PASS] test_guard_verify_invalid_signature")


def test_guard_verify_replay():
    """测试重放攻击被阻止"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    verifier = LeaseVerifier(public_b64, "joy-guard-01")

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    # 第一次验证
    is_valid1, reason1, receipt1 = verifier.verify_and_consume(intent, lease)
    assert is_valid1, f"First verification should pass: {reason1}"

    # 第二次验证 (重放) 应该失败
    is_valid2, reason2, receipt2 = verifier.verify_and_consume(intent, lease)
    assert not is_valid2
    assert "consumed" in reason2.lower() or "replay" in reason2.lower()

    verifier.close()
    print("[PASS] test_guard_verify_replay")


def test_guard_verify_wrong_audience():
    """测试 audience 不匹配被拒绝"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    verifier = LeaseVerifier(public_b64, "joy-guard-01")  # 期望 joy-guard-01

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    # Runtime 签发给不同的 audience
    authority = LeaseAuthority(private_b64, "test-key-01")
    action_intent = ActionIntent.from_dict(intent)
    decision = Decision.allow(
        request_id=action_intent.request_id,
        matched_grant_id="grant-001",
        fact_refs=(),
    )
    lease_obj = authority.issue_lease(
        intent=action_intent,
        decision=decision,
        audience="wrong-audience",  # 错误的 audience
        mission_id="mission-001",
    )
    lease = lease_obj.to_dict()

    is_valid, reason, receipt = verifier.verify_and_consume(intent, lease)

    assert not is_valid
    assert "audience" in reason.lower()

    verifier.close()
    print("[PASS] test_guard_verify_wrong_audience")


def test_guard_verify_tampered_intent():
    """测试篡改 intent 被发现"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    verifier = LeaseVerifier(public_b64, "joy-guard-01")

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    # 篡改 intent
    intent["arguments"]["destination"] = "waste-bin"  # 改成危险目的地

    is_valid, reason, receipt = verifier.verify_and_consume(intent, lease)

    assert not is_valid
    assert "hash" in reason.lower() or "mismatch" in reason.lower()

    verifier.close()
    print("[PASS] test_guard_verify_tampered_intent")


# ============================================================
# FakeExecutor 测试
# ==========================================================

def test_fake_executor():
    """测试 FakeExecutor 记录调用"""
    executor = FakeExecutor()

    intent = create_valid_intent()
    receipt = executor.execute(intent)

    assert receipt["status"] == "executed"
    assert executor.call_count == 1
    assert receipt["action"] == "TRANSFER"
    assert receipt["sample_id"] == "sample-A"

    print(f"[PASS] test_fake_executor: {receipt}")


def test_fake_executor_call_tracking():
    """测试 FakeExecutor 调用追踪"""
    executor = FakeExecutor()

    for i in range(5):
        intent = create_valid_intent()
        executor.execute(intent)

    assert executor.call_count == 5
    assert len(executor.get_calls()) == 5

    executor.reset()
    assert executor.call_count == 0

    print("[PASS] test_fake_executor_call_tracking")


# ============================================================
# SafeExecGuard 集成测试
# ==========================================================

def test_safeexec_guard_full_flow():
    """测试完整的 Guard 流程"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    result = guard.execute(intent, lease)

    assert result["status"] == "executed"
    assert executor.call_count == 1

    events = guard.get_events()
    event_names = [e["event"] for e in events]
    assert "guard.accepted" in event_names
    assert "executor.completed" in event_names

    print(f"[PASS] test_safeexec_guard_full_flow: events={event_names}")


def test_safeexec_guard_blocked():
    """测试 Guard 阻断无效请求"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    wrong_private_b64, _ = LeaseAuthority.generate_keypair()

    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    intent = create_valid_intent()
    # 用错误的私钥签发
    lease = create_valid_lease(intent, wrong_private_b64)

    result = guard.execute(intent, lease)

    assert result["status"] == "blocked"
    assert executor.call_count == 0  # Executor 不应被调用

    events = guard.get_events()
    event_names = [e["event"] for e in events]
    assert "guard.blocked" in event_names

    print(f"[PASS] test_safeexec_guard_blocked: reason={result.get('reason_code')}")


def test_safeexec_guard_replay_blocked():
    """测试 Guard 阻止重放"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    intent = create_valid_intent()
    lease = create_valid_lease(intent, private_b64)

    # 第一次成功
    result1 = guard.execute(intent, lease)
    assert result1["status"] == "executed"
    assert executor.call_count == 1

    # 第二次被阻止
    result2 = guard.execute(intent, lease)
    assert result2["status"] == "blocked"
    assert executor.call_count == 1  # 仍然是 1

    print("[PASS] test_safeexec_guard_replay_blocked")


# ============================================================
# 运行所有测试
# ==========================================================

def run_all():
    print("=" * 60)
    print("SafeExec V1 Guard Integration Tests")
    print("=" * 60)

    tests = [
        # Guard Verifier 测试
        test_guard_verify_valid,
        test_guard_verify_invalid_signature,
        test_guard_verify_replay,
        test_guard_verify_wrong_audience,
        test_guard_verify_tampered_intent,
        # FakeExecutor 测试
        test_fake_executor,
        test_fake_executor_call_tracking,
        # SafeExecGuard 集成测试
        test_safeexec_guard_full_flow,
        test_safeexec_guard_blocked,
        test_safeexec_guard_replay_blocked,
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
