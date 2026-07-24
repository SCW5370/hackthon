#!/usr/bin/env python3
"""
SafeExec V1 Lease Authority 测试
"""
import sys
import os
import time
import uuid
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.contracts import ActionIntent, Decision
from runtime.lease_authority import LeaseAuthority, LeaseStore


def create_intent() -> ActionIntent:
    return ActionIntent(
        schema_version="safeexec.action.v1",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        principal_id="lab-agent-01",
        issued_at_ms=1784800000000,
        action="lab.sample.transfer",
        resource={"type": "lab.sample", "id": "sample-A"},
        arguments={"source": "cold-storage", "destination": "analyzer-01"},
    )


def test_key_generation():
    """测试密钥生成"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    assert len(private_b64) > 0
    assert len(public_b64) > 0
    assert private_b64 != public_b64

    print(f"[PASS] test_key_generation: private={private_b64[:20]}... public={public_b64[:20]}...")


def test_lease_issuance():
    """测试 Lease 签发"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    authority = LeaseAuthority(private_b64, "test-key-01")

    intent = create_intent()
    decision = Decision.allow(
        request_id=intent.request_id,
        matched_grant_id="grant-001",
        fact_refs=("camera.healthy@1784800000.125",),
    )

    lease = authority.issue_lease(
        intent=intent,
        decision=decision,
        audience="joy-guard-01",
        mission_id="mission-001",
    )

    assert lease.lease_id is not None
    assert lease.request_id == intent.request_id
    assert lease.audience == "joy-guard-01"
    assert lease.signature is not None
    assert lease.expires_at_ms > lease.issued_at_ms

    print(f"[PASS] test_lease_issuance: lease_id={lease.lease_id}")


def test_lease_verify():
    """测试 Lease 验签"""
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    authority = LeaseAuthority(private_b64, "test-key-01")

    intent = create_intent()
    decision = Decision.allow(
        request_id=intent.request_id,
        matched_grant_id="grant-001",
        fact_refs=(),
    )

    lease = authority.issue_lease(
        intent=intent,
        decision=decision,
        audience="joy-guard-01",
        mission_id="mission-001",
    )

    # 验签
    is_valid, reason = LeaseAuthority.verify_lease(
        lease=lease,
        public_key_b64=public_b64,
        intent_normalized=intent.normalize(),
    )

    assert is_valid, f"Verification failed: {reason}"
    print(f"[PASS] test_lease_verify")


def test_lease_verify_wrong_key():
    """测试用错误公钥验签失败"""
    private_b64, _ = LeaseAuthority.generate_keypair()
    _, wrong_public_b64 = LeaseAuthority.generate_keypair()  # 另一个密钥对

    authority = LeaseAuthority(private_b64, "test-key-01")

    intent = create_intent()
    decision = Decision.allow(
        request_id=intent.request_id,
        matched_grant_id="grant-001",
        fact_refs=(),
    )

    lease = authority.issue_lease(
        intent=intent,
        decision=decision,
        audience="joy-guard-01",
        mission_id="mission-001",
    )

    # 用错误的公钥验签
    is_valid, reason = LeaseAuthority.verify_lease(
        lease=lease,
        public_key_b64=wrong_public_b64,
        intent_normalized=intent.normalize(),
    )

    assert not is_valid
    assert "signature" in reason.lower() or "invalid" in reason.lower()
    print(f"[PASS] test_lease_verify_wrong_key: {reason}")


def test_lease_store_consume():
    """测试 Lease 存储防重放"""
    store = LeaseStore(":memory:")

    lease_id = str(uuid.uuid4())

    # 第一次消费应该成功
    assert store.try_consume(lease_id) is True

    # 第二次消费应该失败 (重放)
    assert store.try_consume(lease_id) is False

    store.close()
    print("[PASS] test_lease_store_consume")


def test_lease_store_replay_protection():
    """测试重放攻击防护"""
    store = LeaseStore(":memory:")

    lease_ids = [str(uuid.uuid4()) for _ in range(10)]

    # 消费所有 lease
    for lid in lease_ids:
        assert store.try_consume(lid) is True

    # 尝试重放所有 lease
    replay_count = 0
    for lid in lease_ids:
        if not store.try_consume(lid):
            replay_count += 1

    assert replay_count == 10
    store.close()
    print(f"[PASS] test_lease_store_replay_protection: {replay_count} replays blocked")


def test_lease_concurrent_consume():
    """测试并发消费 (模拟)"""
    store = LeaseStore(":memory:")
    lease_id = str(uuid.uuid4())

    # 连续消费应该只有一个成功
    results = [store.try_consume(lease_id) for _ in range(5)]
    success_count = sum(1 for r in results if r is True)
    fail_count = sum(1 for r in results if r is False)

    assert success_count == 1
    assert fail_count == 4

    store.close()
    print(f"[PASS] test_lease_concurrent_consume: 1 success, {fail_count} blocked")


def run_all():
    print("=" * 60)
    print("SafeExec V1 Lease Authority Tests")
    print("=" * 60)

    tests = [
        test_key_generation,
        test_lease_issuance,
        test_lease_verify,
        test_lease_verify_wrong_key,
        test_lease_store_consume,
        test_lease_store_replay_protection,
        test_lease_concurrent_consume,
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
