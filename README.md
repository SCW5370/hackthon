# SafeExec V4 — Signed WorkOrder Runtime

> 面向具身 Agent 的零信任执行 Runtime。
> BioLab Agent 持续提出动作，SafeExec 决定动作是否可在真实设备上执行。

## 架构

```
Mac trusted control plane
Config UI :8787/config ── signed WorkOrder ──────────────┐
Dashboard :8787 ── control / audit ────────────────┐     │
                                                   ↓     ↓
RDK X5 edge controller                         Orchestrator :8789
safeexec-agent (LLM / planning) ─ ActionIntent v2 → Runtime :8790
                                                      │ signed Lease
                                                      ↓
Windows device boundary                         Guard :8788 → JOY :18189
                                                      └→ Legacy Bridge :8791
                                                          (A/B demo only)
```

Legacy Bridge 仅用于显式启用的无保护 A/B 演示。默认执行路径始终经过 Runtime、一次性 Lease 与 Guard。
Mac 不运行 Agent 或 Runtime；Windows 不运行 Policy 或业务 Agent。X5 上的
Agent 与 Runtime 使用不同 Linux 服务账号，Agent 无权读取 Lease 私钥。
X5 只从显式允许列表主动发现 Guard 服务，Guard 再在 Windows 本机连接 JOY。
发现设备只更新就绪状态，不会自动授权或执行动作。

## 核心组件

| 组件 | 职责 |
|------|------|
| **OrganizationPolicy** | 长期权限上限，约束允许接入的主体、动作、资源与事实 |
| **WorkOrder Registry** | 验证控制面签名、有效期、精确授权范围与执行预算 |
| **Policy Engine** | 同时匹配 ActionIntent、OrganizationPolicy 与 WorkOrder |
| **Lease Authority** | Ed25519 签名签发 5 秒 Lease |
| **Fact Hub** | 存储摄像头等实时状态，支持 TTL 过期 |
| **Guard** | 验签 + SQLite 防重放 + 执行拦截 |
| **Endpoint Discovery** | X5 主动探测允许列表、校验 Guard audience 与 JOY 深度就绪 |
| **Orchestrator** | 消费已验证工单、驱动 Agent 会话、攻击注入与单次恢复 |
| **Dashboard** | 运行监控与增量审计，不签发可信授权 |
| **Config UI** | 结构化配置并签发可信 WorkOrder |

## 安全机制

1. **双层授权** - 长期 OrganizationPolicy 与短期 WorkOrder 必须同时匹配
2. **签名工单** - Ed25519 验签、主体绑定、有效期与单项执行预算
3. **Lease (5秒)** - 短时有效，且绑定 ActionIntent 与 WorkOrder
4. **SQLite 防重放** - 同一 Lease 只能使用一次
5. **Intent Hash 验证** - 检测动作或 `work_order_id` 篡改

## 快速开始

```bash
# 安装依赖
pip install pynacl pyyaml

# 生成密钥对
python scripts/gen_keys.py --private-key guard/keys/private_key.txt --public-key guard/keys/public_key.txt

# 本地开发模式
SAFEEXEC_GUARD_URL=http://WINDOWS_IP:8788 ./scripts/start_stack.sh

# X5 边缘控制器模式见 docs/e2e-integration.md

# 打开控制台
open http://127.0.0.1:8787
open http://127.0.0.1:8787/config

# 测试
.venv/bin/python -m unittest discover -s tests -v
```

V4 Agent 生产线、Windows Guard 与 JOY 的部署和实机演示步骤见
[`docs/e2e-integration.md`](docs/e2e-integration.md)。

## 数据契约

### WorkOrder（可信控制面 → Runtime）

```json
{
  "schema_version": "safeexec.work-order.v1",
  "work_order_id": "16e25368-0948-4e31-9863-94f6488d31de",
  "issuer_id": "biolab-control-plane",
  "subject_principal_id": "lab-agent-01",
  "valid_until_ms": 1784803600125,
  "grants": [{
    "grant_id": "work-order-sample-a-analysis",
    "action": "lab.sample.transfer",
    "resource": {"type": "lab.sample", "id": "sample-A"},
    "arguments": {"source": "cold-storage", "destination": "analyzer-01"},
    "max_executions": 1
  }],
  "key_id": "biolab-control-plane-key-01",
  "signature": "base64url-ed25519-signature"
}
```

### ActionIntent (Agent → Runtime)

```json
{
  "schema_version": "safeexec.action.v2",
  "request_id": "d7588eb9-f22c-49a7-9814-b46260e19d8e",
  "principal_id": "lab-agent-01",
  "work_order_id": "16e25368-0948-4e31-9863-94f6488d31de",
  "issued_at_ms": 1784800000125,
  "action": "lab.sample.transfer",
  "resource": {"type": "lab.sample", "id": "sample-A"},
  "arguments": {"source": "cold-storage", "destination": "analyzer-01"}
}
```

### ActionLease (Runtime → Guard)

```json
{
  "schema_version": "safeexec.lease.v1",
  "lease_id": "f49fb2ab-853c-4c10-95aa-ef9ae7e34621",
  "request_hash": "sha256:...",
  "expires_at_ms": 1784800005200,
  "signature": "base64url-ed25519-signature"
}
```

## API

### Runtime

- `POST /v1/actions` - Agent 提交动作
- `POST /v1/work-orders` - 注册并验证签名工单
- `GET /v1/work-orders/{id}` - 查询工单与执行预算
- `GET /v1/config` - 查询组织策略与可信签发方
- `POST /v1/facts` - 适配器更新 Fact
- `GET /v1/state` - 当前 Fact 与最近一次动作摘要
- `GET /v1/events` - 审计事件流
- `GET /healthz` - 健康检查
- `GET /readyz` - Runtime、Guard 与设备执行端的深度就绪状态

### Guard

- `POST /v1/execute` - 验证 Lease 后执行
- `GET /v1/events` - Guard 审计事件
- `GET /v1/physical` - JOY 当前物理状态
- `GET /healthz` - 健康检查
- `GET /readyz` - Guard 与本地 JOY RPC 的缓存深度检查

### Orchestrator

- `GET /v1/line/state`
- `GET /v1/preflight`
- `POST /v1/work-orders/activate`
- `POST /v1/control/start|pause|resume|reset`
- `POST /v1/testing/injections`
- `GET /v1/events?after=<seq>`
- `GET /v1/events/stream?after=<seq>`

### Dashboard

- `GET /api/dashboard/v2`（响应 schema 为 `safeexec.dashboard.v4`）
- `GET /api/preflight`
- `GET /api/config`
- `POST /api/work-orders`
- `POST /api/control/start|pause|resume|reset`
- `POST /api/testing/injections`
- `GET /api/events/stream?after=<seq>`

## 测试

X5 A/B 实机验收：

- 保护模式：完成 1、阻断 1、恢复 1、危险动作 0。
- 无保护模式：同一攻击到达 `waste-bin`，危险动作 1。
- 当前测试集：81 项。

## 开发

### 分工

- **你**: Runtime、Policy、Lease、Guard、审计、FakeExecutor
- **对方**: Lab Agent、JoyExecutor、JOY 场景、Dashboard

### 密钥

```
Lease private key:       只保存在 Runtime
Lease public key:        部署到 Windows Guard
WorkOrder private key:   只保存在可信控制面
WorkOrder public key:    只部署到 Runtime
```

**警告: 永远不要把私钥提交到源代码仓库！**
