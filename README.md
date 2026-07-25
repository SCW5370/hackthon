# SafeExec V4 — Signed WorkOrder Runtime

> 面向具身 Agent 的零信任执行 Runtime。
> BioLab Agent 持续提出动作，SafeExec 决定动作是否可在真实设备上执行。

## 架构

```
Mac browser (guest experience)        Teammate browser (security wall)
          │ http://X5:8787/experience       │ http://X5:8787/monitor
          └───────────────────┬─────────────┘
          ↓
RDK X5 edge controller          Experience / Monitor / Config :8787
                                Orchestrator :8789
safeexec-agent (LLM / planning) ─ ActionIntent v2 → Runtime :8790
                                                      │ signed Lease
                                                      ↓
Windows device boundary                         Guard :8788 → JOY :18189
                                                      └→ Legacy Bridge :8791
                                                          (A/B demo only)
```

Legacy Bridge 仅用于显式启用的无保护 A/B 演示。默认执行路径始终经过 Runtime、一次性 Lease 与 Guard。
Mac 不运行服务；Windows 不运行 Policy 或业务 Agent。X5 上的
Agent 与 Runtime 使用不同 Linux 服务账号，Agent 无权读取 Lease 私钥。
演示版控制台使用第三个隔离账号 `safeexec-console`，其 WorkOrder 签名私钥
不能被 Agent 或 Runtime 读取；生产部署应改接外部身份系统或 KMS。
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
| **Security Monitor** | 只读安全评分、因果阻断链、攻击矩阵与增量审计 |
| **Experience UI** | 面向评委与参观者的交互演示，以因果路径呈现阻断、恢复与物理结果 |
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

# X5 模式下，三台电脑只通过网络分工，不复制业务服务
open http://safeexec-x5.local:8787/experience
open http://safeexec-x5.local:8787/monitor
open http://safeexec-x5.local:8787/config

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
- `POST /v1/control/continuous` - 在停止且已复位时启用固定实体池循环
- `POST /v1/testing/injections`
- `GET /v1/events?after=<seq>`
- `GET /v1/events/stream?after=<seq>`

### Exhibition Web

- `GET /api/dashboard/v2`（响应 schema 为 `safeexec.dashboard.v4`）
- `GET /api/monitor`（只读观测墙聚合快照）
- `GET /api/preflight`
- `GET /api/config`
- `POST /api/work-orders`
- `POST /api/control/start|pause|resume|reset`
- `POST /api/control/continuous`
- `POST /api/testing/injections`
- `POST /api/experience/challenges` - 运行受限的游客攻击挑战
- `GET /api/experience/challenges` - 查询本次进程内的挑战历史
- `GET /api/experience/challenges/latest`
- `GET /api/events/stream?after=<seq>`

游客挑战固定支持四种路径：提示词注入、模型幻觉、Intent 途中篡改和
Lease 重放。前两类经过真实 Runtime；后两类复用生产 Guard 的签名、哈希绑定
和防重放验证器，但不调用 Executor。

持续模式不在运行时逐件创建或删除 JOY 实体。A～F 会先全部完成搬运并共同
停留在分析区；整批完成后，Orchestrator 才提交一次签名 `lab.line.reset`
动作，经 Policy、Lease、Guard 和 JOY 原子换线。随后六个物理载体一起回到
等候区，并获得下一轮 `LOT-xxxx-X` 逻辑批次。攻击可使用
`target_task_id=next-queued` 原子绑定当时下一件待处理任务。

## 测试

X5 A/B 实机验收：

- 保护模式：完成 1、阻断 1、恢复 1、危险动作 0。
- 无保护模式：同一攻击到达 `waste-bin`，危险动作 1。
- 当前测试集：102 项。

X5 的 Dashboard、Orchestrator 与 Runtime 由 systemd 开机自启并设置为任意
退出后自动拉起。`safeexec-healthcheck.timer` 每 10 秒检查本机健康端点，只有
连续两次失败才重启对应服务；Guard/JOY 等外部执行器离线不会触发 X5 重启，
只会使 Runtime 失败关闭。

Lease 签发后若 Guard 连接中断，Runtime 不会把动作误报为“未执行”，而是标记
`EXECUTION_OUTCOME_UNKNOWN`，保守消耗对应 WorkOrder 预算并把生产线锁定在
`ERROR`。操作员必须依据实时物理状态核对结果，再通过签名复位恢复。

Guard 每次就绪响应都会返回不经缓存的 `server_time_ms`。Runtime 会据此比较
X5 与 Windows Guard 的时钟，偏差超过 2 秒时在 Lease
签发前将执行端标为不可用，避免短时 Lease 因跨设备时钟漂移在 Guard 侧过期。

## 三屏展览分工

- **Windows**：只运行 JOY 仿真、Guard 与本机执行适配，不承载策略和 Web 页面。
- **嘉宾 Mac**：打开 `/experience`，选择攻击并观察“输入—判定—物理结果”。
- **观测电脑**：全屏打开 `/monitor`，只读显示安全评分、阻断层、攻击覆盖与审计证据。

旧工业控制台已移除；根路径直接进入嘉宾体验页。`/config` 仅供赛前签发可信
WorkOrder，演示过程中不需要打开。一次性 reset、smoke 和 probe 脚本仅作为
离线诊断工具保留，不会产生常驻服务或额外窗口。

## 开发

### 分工

- **你**: Runtime、Policy、Lease、Guard、审计、FakeExecutor
- **对方**: Lab Agent、JoyExecutor、JOY 场景、Dashboard

### 密钥

```
Lease private key:       只保存在 Runtime
Lease public key:        部署到 Windows Guard
WorkOrder private key:   只允许可信控制面账号读取（生产环境使用外部 KMS）
WorkOrder public key:    只部署到 Runtime
```

**警告: 永远不要把私钥提交到源代码仓库！**
