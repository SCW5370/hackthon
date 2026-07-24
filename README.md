# SafeExec V1 - Robot Action Safety Runtime

> 多 Agent 机器人的动作信任层
> Factory Agent 负责提出"要做什么"，SafeExec 负责决定"这个动作现在能不能执行"。

## 架构

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Lab Agent     │ ──▶ │  SafeExec       │ ──▶ │  Guard          │
│  (ActionIntent) │     │  Runtime        │     │  (Lease 验签)   │
└─────────────────┘     │  (Policy+Lease) │     └────────┬────────┘
                        └─────────────────┘              │
                                                         ▼
                                                  ┌───────────────┐
                                                  │ JoyExecutor   │
                                                  │ (机械臂控制)   │
                                                  └───────────────┘
```

## 核心组件

| 组件 | 职责 |
|------|------|
| **Policy Engine** | 精确匹配 ActionIntent 与 MissionSpec grants |
| **Lease Authority** | Ed25519 签名签发 5 秒 Lease |
| **Fact Hub** | 存储摄像头等实时状态，支持 TTL 过期 |
| **Guard** | 验签 + SQLite 防重放 + 执行拦截 |

## 安全机制

1. **Policy Engine** - 精确匹配 grant，不允许未授权动作
2. **Lease (5秒)** - 短时有效，过期自动失效
3. **Ed25519 签名** - 防篡改
4. **SQLite 防重放** - 同一 Lease 只能使用一次
5. **Intent Hash 验证** - 检测篡改

## 快速开始

```bash
# 安装依赖
pip install pynacl pyyaml

# 生成密钥对
python scripts/gen_keys.py --private-key guard/keys/private_key.txt --public-key guard/keys/public_key.txt

# 运行演示
python scripts/demo.py

# 运行测试
python tests/test_contracts.py
python tests/test_policy.py
python tests/test_lease.py
python tests/test_guard.py
```

真实 Agent、Windows Guard 与 JOY 的部署和 A/B 演示步骤见
[`docs/e2e-integration.md`](docs/e2e-integration.md)。

## 数据契约

### ActionIntent (Agent → Runtime)

```json
{
  "schema_version": "safeexec.action.v1",
  "request_id": "d7588eb9-f22c-49a7-9814-b46260e19d8e",
  "principal_id": "lab-agent-01",
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
- `POST /v1/facts` - 适配器更新 Fact
- `GET /v1/state` - 当前 Fact 与最近一次动作摘要
- `GET /v1/events` - 审计事件流
- `GET /healthz` - 健康检查

### Guard

- `POST /v1/execute` - 验证 Lease 后执行
- `GET /v1/events` - Guard 审计事件
- `GET /v1/physical` - JOY 当前物理状态
- `GET /healthz` - 健康检查

### Dashboard

- `GET /api/dashboard/v1` - 聚合 Runtime、Guard 与 JOY 的只读展示数据
- `POST /api/dashboard/scenario` - 启动受保护演示；无保护基线默认禁用
- 默认端口：Dashboard `8787`、Runtime `8790`、Guard `8788`

## 测试

```
cd /opt/safeexec_new
python tests/test_contracts.py   # 9 tests
python tests/test_policy.py      # 7 tests
python tests/test_lease.py       # 7 tests
python tests/test_guard.py       # 10 tests
```

**总计: 33 tests, 全部通过**

## 开发

### 分工

- **你**: Runtime、Policy、Lease、Guard、审计、FakeExecutor
- **对方**: Lab Agent、JoyExecutor、JOY 场景、Dashboard

### 密钥

```
Private key:  只保存在 X5 Runtime
Public key:   部署到 Windows Guard
```

**警告: 永远不要把私钥提交到源代码仓库！**
