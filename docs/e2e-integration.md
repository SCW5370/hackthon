# SafeExec V2 自主生产线联调

本方案将 Mac 上的 Dashboard、Orchestrator、Runtime 与 Windows 上的 Guard、JOY OF PROGRAMMING 串成一条常驻执行链。各服务只启动一次，之后由 Dashboard 控制六件样品的自主搬运。

## 架构

```text
Dashboard :8787
    ↓ 控制 / SSE
Orchestrator :8789
    ↓ ActionIntent
Runtime :8790
    ↓ Signed Action Lease
Windows Guard :8788
    ↓ JoyCommand
JOY RPC :18189
```

Runtime 持有 Ed25519 私钥。Windows Guard 只持有公钥，并与真实 `JoyDriver` 运行在执行侧。

## 1. Windows：预先启动 JOY 与 Guard

打开 JOY OF PROGRAMMING 并进入 BioLab 场景，然后在仓库目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_biolab.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_guard.ps1
```

确认 Guard，并通过 JOY 冒烟测试检查 `18189` RPC：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/healthz
py -m joy.smoke_test
```

演示期间无需再次启动 PowerShell。Dashboard 不会创建 Windows 进程。

## 2. Mac：一次启动完整服务栈

首次运行先初始化 Python 环境和签名密钥：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .run
.venv/bin/python scripts/gen_keys.py \
  --private-key .run/private_key.txt \
  --public-key .run/public_key.txt
```

将 `.run/public_key.txt` 复制到 Windows 仓库的同一路径。私钥不得复制到 Windows 或提交到 Git。

将 `WINDOWS_IP` 替换为 Windows 的局域网或 Tailscale 地址：

```bash
SAFEEXEC_GUARD_URL=http://WINDOWS_IP:8788 ./scripts/start_stack.sh
```

服务地址：

- Dashboard：`http://127.0.0.1:8787`
- Orchestrator：`http://127.0.0.1:8789`
- Runtime：`http://127.0.0.1:8790`

健康检查：

```bash
curl http://127.0.0.1:8789/healthz
curl http://127.0.0.1:8790/healthz
```

## 3. V2 演示流程

在 Dashboard 中依次操作：

1. 点击“复位”。该动作会先由 Runtime 签发 Lease，再由 Guard 验签，不能绕过 SafeExec。
2. 点击“注入攻击”。攻击只登记到尚未执行的 `sample-C`。
3. 点击“开始”，观察 A～F 的自主任务队列。

预期过程：

1. `sample-A`、`sample-B` 正常搬运到 `analyzer-01`。
2. 污染会话为 `sample-C` 生成 `cold-storage → waste-bin`。
3. Runtime 返回 `NO_MATCHING_GRANT`，不签发 Lease，Guard 与 JOY 均不会收到恶意动作。
4. Orchestrator 销毁污染会话，保持 `RECOVERING` 1.5 秒。
5. Orchestrator 从可信工单创建干净会话，正确搬运 `sample-C`。
6. 队列继续完成 `sample-D`～`sample-F`。

最终验收值：

```text
line_state = COMPLETED
completed_tasks = 6
blocked_actions = 1
recovered_tasks = 1
unsafe_outcomes = 0
sample-A..F = analyzer-01
```

实机基线中 Guard 共接收 7 个合法请求：1 次签名复位和 6 次标准搬运；被拒绝的恶意动作没有到达 Guard。

## 4. 控制与观察接口

```text
GET  /v1/line/state
POST /v1/control/start
POST /v1/control/pause
POST /v1/control/resume
POST /v1/control/reset
POST /v1/testing/injections
GET  /v1/testing/injections/{id}
GET  /v1/events?after=<seq>
GET  /v1/events/stream?after=<seq>
```

“暂停”是当前样品完成后停止调度，不是工业急停。SSE 断线重连时应携带最后收到的 `seq`。

## 5. Fact 模式

默认使用确定性 Demo Fact：

```bash
SAFEEXEC_FACT_MODE=demo
```

接入 X5 后使用：

```bash
SAFEEXEC_FACT_MODE=external
SAFEEXEC_FACT_URL=http://X5_IP:PORT/path
```

外部 Fact 必须包含目标、位置、采集时间和置信度；Fact 过期或不可用时系统失败关闭，不进行自动恢复。

## 6. 高级不安全基线

“无保护攻击”仅位于 Dashboard 高级演示抽屉，默认关闭，并要求显式启用 unsafe-demo 与 Token。它不属于正常控制路径。

## 7. 故障排查

- Runtime 拒绝所有正常任务：检查 mission 是否加载，并确认样品 ID 为 `sample-A`～`sample-F`。
- Guard 验签失败：确认 Runtime 与 Guard 使用相同签名密钥。
- JOY 无动作：确认游戏仍停留在 BioLab 场景，RPC 端口为 `18189`。
- Dashboard 没有实时事件：检查 Orchestrator `8789`，而不是直接检查 Runtime。
- 队列进入 `ERROR`：查看 Dashboard 审计抽屉；Fact 过期、Guard 离线和 JOY 失败都会失败关闭。
