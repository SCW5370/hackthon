#!/usr/bin/env python3
"""
SafeExec V1 端到端演示
展示完整的 ActionIntent → Runtime → Guard → Executor 流程
"""
import sys
import os
import time
import uuid
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.contracts import ActionIntent, Fact, MissionSpec, Decision
from runtime.policy_engine import PolicyEngine
from runtime.lease_authority import LeaseAuthority
from runtime.fact_hub import FactHub
from runtime.event_ledger import EventLedger
from runtime.state_machine import StateMachine, SystemState
from guard.executor import FakeExecutor
from guard.guard_http import SafeExecGuard


def print_section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def demo_normal_flow():
    """演示 1: 正常流程 - sample-A → analyzer-01"""
    print_section("演示 1: 正常流程")

    # 1. 生成密钥
    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    print(f"✓ 密钥对生成完成")

    # 2. 加载 MissionSpec
    mission = MissionSpec.from_dict({
        "schema_version": "safeexec.mission.v1",
        "mission_id": "mission-lab-001",
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
                    {"key": "camera.healthy", "equals": True, "max_age_ms": 1500}
                ],
            },
        ],
    })
    print(f"✓ MissionSpec 加载完成: {mission.mission_id}")

    # 3. 创建组件
    policy_engine = PolicyEngine(mission)
    lease_authority = LeaseAuthority(private_b64, "x5-runtime-key-01")
    fact_hub = FactHub()
    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    # 4. 添加新鲜的 camera.healthy Fact
    fact_hub.update_fact(Fact(
        key="camera.healthy",
        value=True,
        source="rdk-camera",
        confidence=1.0,
        timestamp=time.time(),
        ttl_ms=1500,
        evidence={"device": "/dev/video0"},
    ))
    print(f"✓ Fact 添加: camera.healthy=True")

    # 5. 创建 ActionIntent
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }
    intent = ActionIntent.from_dict(intent_dict)
    print(f"✓ ActionIntent: {intent.action} {intent.resource['id']} ({intent.arguments['source']} → {intent.arguments['destination']})")

    # 6. Policy Engine 评估
    facts = fact_hub.get_all_facts()
    decision = policy_engine.evaluate(intent, facts)
    print(f"✓ Policy 评估: {decision.effect.value} ({decision.reason_code})")

    # 7. Runtime 签发 Lease
    lease = lease_authority.issue_lease(
        intent=intent,
        decision=decision,
        audience="joy-guard-01",
        mission_id=mission.mission_id,
    )
    print(f"✓ Lease 签发: {lease.lease_id[:16]}... (expires in 5s)")

    # 8. Guard 验证并执行
    result = guard.execute(intent_dict, lease.to_dict())
    print(f"✓ Guard 执行: {result['status']}")
    print(f"  → Executor 调用次数: {executor.call_count}")

    # 9. 验证结果
    assert result["status"] == "executed"
    assert executor.call_count == 1
    print(f"✓ 验证通过: 动作已执行")


def demo_blocked_by_policy():
    """演示 2: Policy 阻断 - 目的地不在 grant 中"""
    print_section("演示 2: Policy 阻断 (NO_MATCHING_GRANT)")

    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    mission = MissionSpec.from_dict({
        "schema_version": "safeexec.mission.v1",
        "mission_id": "mission-lab-001",
        "principal_id": "lab-agent-01",
        "valid_from_ms": int(time.time() * 1000) - 3600000,
        "valid_until_ms": int(time.time() * 1000) + 3600000,
        "grants": [
            {
                "grant_id": "grant-sample-a",
                "action": "lab.sample.transfer",
                "resource": {"type": "lab.sample", "id": "sample-A"},
                "arguments": {"source": "cold-storage", "destination": "analyzer-01"},  # 只允许这个
                "required_facts": [
                    {"key": "camera.healthy", "equals": True, "max_age_ms": 1500}
                ],
            },
        ],
    })

    policy_engine = PolicyEngine(mission)
    lease_authority = LeaseAuthority(private_b64, "x5-runtime-key-01")

    # 尝试转移 sample-A 到 waste-bin (不在 grant 中)
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "waste-bin"},  # 危险操作！
    }
    intent = ActionIntent.from_dict(intent_dict)
    print(f"✓ ActionIntent: {intent.action} → {intent.arguments['destination']}")

    facts = {"camera.healthy": Fact(key="camera.healthy", value=True, source="test", confidence=1.0, timestamp=time.time(), ttl_ms=1500)}
    decision = policy_engine.evaluate(intent, facts)

    print(f"✓ Policy 评估: {decision.effect.value} ({decision.reason_code})")

    # 不应该签发 lease
    if decision.effect.value == "deny":
        print(f"✓ 验证通过: Policy 正确阻断，未签发 Lease")
    else:
        print(f"✗ 错误: 应该阻断但却允许了！")


def demo_blocked_by_stale_fact():
    """演示 3: Fact 过期被阻断"""
    print_section("演示 3: Fact 过期阻断 (FACT_STALE)")

    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    mission = MissionSpec.from_dict({
        "schema_version": "safeexec.mission.v1",
        "mission_id": "mission-lab-001",
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
                    {"key": "camera.healthy", "equals": True, "max_age_ms": 1500}
                ],
            },
        ],
    })

    policy_engine = PolicyEngine(mission)

    # 创建过期的 fact (3 秒前，TTL 是 1.5 秒)
    stale_fact = Fact(
        key="camera.healthy",
        value=True,
        source="test",
        confidence=1.0,
        timestamp=time.time() - 3,  # 3 秒前
        ttl_ms=1500,  # 1.5 秒 TTL
        evidence={},
    )

    intent = ActionIntent.from_dict({
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    })

    facts = {"camera.healthy": stale_fact}
    decision = policy_engine.evaluate(intent, facts)

    print(f"✓ Policy 评估: {decision.effect.value} ({decision.reason_code})")
    assert decision.effect.value == "deny"
    assert decision.reason_code == "FACT_STALE"
    print(f"✓ 验证通过: 过期 Fact 正确阻断")


def demo_replay_attack():
    """演示 4: 重放攻击被阻断"""
    print_section("演示 4: 重放攻击阻断")

    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": str(uuid.uuid4()),
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    # 创建有效的 lease
    lease_authority = LeaseAuthority(private_b64, "x5-runtime-key-01")
    intent = ActionIntent.from_dict(intent_dict)
    decision = Decision.allow(request_id=intent.request_id, matched_grant_id="grant-001", fact_refs=())
    lease = lease_authority.issue_lease(intent, decision, "joy-guard-01", "mission-001")

    # 第一次执行 - 应该成功
    result1 = guard.execute(intent_dict, lease.to_dict())
    print(f"✓ 第 1 次执行: {result1['status']} (Executor 调用: {executor.call_count})")

    # 第二次执行 (重放) - 应该被阻断
    result2 = guard.execute(intent_dict, lease.to_dict())
    print(f"✓ 第 2 次执行 (重放): {result2['status']}")
    print(f"  → 原因: {result2.get('reason_code')}")
    print(f"  → Executor 调用次数仍为: {executor.call_count} (未被再次调用)")

    assert result2["status"] == "blocked"
    assert executor.call_count == 1
    print(f"✓ 验证通过: 重放攻击被正确阻断")


def demo_tampered_intent():
    """演示 5: 篡改 Intent 被发现"""
    print_section("演示 5: 篡改 Intent 检测")

    private_b64, public_b64 = LeaseAuthority.generate_keypair()
    executor = FakeExecutor()
    guard = SafeExecGuard(public_key_b64=public_b64, executor=executor)

    # 创建原始 intent
    intent_dict = {
        "schema_version": "safeexec.action.v1",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "principal_id": "lab-agent-01",
        "issued_at_ms": int(time.time() * 1000),
        "action": "lab.sample.transfer",
        "resource": {"type": "lab.sample", "id": "sample-A"},
        "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    }

    # 签发 lease
    lease_authority = LeaseAuthority(private_b64, "x5-runtime-key-01")
    intent = ActionIntent.from_dict(intent_dict)
    decision = Decision.allow(request_id=intent.request_id, matched_grant_id="grant-001", fact_refs=())
    lease = lease_authority.issue_lease(intent, decision, "joy-guard-01", "mission-001")

    # 篡改 intent - 改成危险目的地
    tampered_intent = dict(intent_dict)
    tampered_intent["arguments"]["destination"] = "waste-bin"

    result = guard.execute(tampered_intent, lease.to_dict())
    print(f"✓ 篡改后执行: {result['status']}")
    print(f"  → 原因: {result.get('reason_code')}")
    print(f"  → Executor 调用次数: {executor.call_count}")

    assert result["status"] == "blocked"
    assert executor.call_count == 0
    print(f"✓ 验证通过: 篡改检测生效")


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║           SafeExec V1 端到端安全演示                      ║
║                                                          ║
║  演示场景:                                               ║
║  1. 正常流程 - Policy 允许 → Lease 签发 → Guard 执行    ║
║  2. Policy 阻断 - 目的地不在 grant 中                   ║
║  3. Fact 过期阻断 - camera.healthy TTL 过期             ║
║  4. 重放攻击阻断 - 同一 Lease 不能使用第二次            ║
║  5. 篡改检测 - Intent 被修改则 Lease 验签失败           ║
╚══════════════════════════════════════════════════════════╝
    """)

    try:
        demo_normal_flow()
        demo_blocked_by_policy()
        demo_blocked_by_stale_fact()
        demo_replay_attack()
        demo_tampered_intent()

        print("\n" + "=" * 60)
        print("  所有演示完成！")
        print("=" * 60)
        print("""
架构总结:
  Lab Agent → ActionIntent → Runtime → Guard → JoyExecutor
                              ↓
                         Policy Engine
                         (精确匹配 grant)
                              ↓
                         Lease Authority
                         (Ed25519 签名)
                              ↓
                         Guard
                         (验签 + 防重放)
                              ↓
                         Executor
                         (执行或阻断)
""")
        return 0

    except Exception as e:
        print(f"\n✗ 演示失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
