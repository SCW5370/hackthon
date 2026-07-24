# SafeExec V3 — Agent-driven BioLab Line

> 面向具身 Agent 的零信任执行 Runtime。
> BioLab Agent 持续提出动作，SafeExec 决定动作是否可在真实设备上执行。

## 架构

```
Dashboard :8787 → Orchestrator :8789 → Runtime :8790
                                      ↓
                               Guard :8788 → JOY :18189
```

## 核心组件

| 组件 | 职责 |
|------|------|
| **Policy Engine** | 精确匹配 ActionIntent 与 MissionSpec grants |
| **Lease Authority** | Ed25519 签名签发 5 秒 Lease |
| **Fact Hub** | 存储摄像头等实时状态，支持 TTL 过期 |
| **Guard** | 验签 + SQLite 防重放 + 执行拦截 |
| **Orchestrator** | 自然语言工单、动态队列、Agent 会话、攻击注入与单次恢复 |
| **Dashboard** | 控制常驻生产线并按事件序号增量展示证据 |

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

# Windows 先常驻启动 JOY 与 Guard；Mac 一次启动三服务
SAFEEXEC_GUARD_URL=http://100.123.243.7:8788 ./scripts/start_stack.sh

# 打开控制台
open http://127.0.0.1:8787

# 测试
.venv/bin/python -m unittest discover -s tests -v
```

V3 Agent 生产线、Windows Guard 与 JOY 的部署和实机演示步骤见
[`docs/e2e-integration.md`](docs/e2e-integration.md)。

## 数据契约

### JobManifest（Function Calling → Orchestrator）

```json
{
  "schema_version": "safeexec.job-manifest.v1",
  "job_id": "16e25368-0948-4e31-9863-94f6488d31de",
  "operator_text": "把距离机械臂最近的四个样品运送到分析区",
  "sample_ids": ["sample-C", "sample-A", "sample-E", "sample-D"],
  "source": "cold-storage",
  "destination": "analyzer-01",
  "selection_strategy": "nearest"
}
```

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

### Orchestrator

- `GET /v1/line/state`
- `POST /v1/agent/commands`
- `POST /v1/control/start|pause|resume|reset`
- `POST /v1/testing/injections`
- `GET /v1/events?after=<seq>`
- `GET /v1/events/stream?after=<seq>`

### Dashboard

- `GET /api/dashboard/v2`（响应 schema 为 `safeexec.dashboard.v3`）
- `POST /api/agent/commands`
- `POST /api/control/start|pause|resume|reset`
- `POST /api/testing/injections`
- `GET /api/events/stream?after=<seq>`

## 测试

动态四样品实机基线：完成 4、阻断 1、恢复 1、危险动作 0；攻击可绑定
任意尚未执行的任务。默认六样品工单仍保留用于兼容 V2 验收。

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
