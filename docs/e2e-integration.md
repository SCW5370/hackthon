# SafeExec × BioLab Agent × JOY 联调

## 最终部署边界

```text
Mac / RDK X5                         Windows + JOY

Lab Agent
  -> Runtime :8790
       Policy + Fact + Lease
             -> Tailscale -> Guard :8788
                                Lease 验签 / 防重放
                                      -> JoyExecutor
                                           -> JOY :18189
```

Runtime 持有 Ed25519 私钥。Windows Guard 只持有公钥，并与真实
`JoyDriver` 运行在 JOY 的嵌入式 Python 中。

## 初始化

Mac：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .run
.venv/bin/python scripts/gen_keys.py \
  --private-key .run/private_key.txt \
  --public-key .run/public_key.txt
```

将 `.run/public_key.txt` 复制到 Windows 仓库的 `.run/public_key.txt`。
私钥不得复制到 Windows 或提交到 Git。

Windows 已启动 JOY 和 `BioLab_Guardian.py` 后：

```powershell
.\scripts\install_windows_guard_deps.ps1
.\scripts\start_windows_guard_interactive.ps1
```

验证：

```bash
curl http://WINDOWS_TAILSCALE_IP:8788/healthz
```

## 启动 Runtime

```bash
.venv/bin/python -m runtime.runtime_http \
  --mission config/mission.yaml \
  --key .run/private_key.txt \
  --guard-url http://WINDOWS_TAILSCALE_IP:8788/v1/execute \
  --host 127.0.0.1 \
  --port 8790
```

另开终端启动真实 Dashboard（`8787` 仅作为展示层，不持有私钥，也不执行动作）：

```bash
SAFEEXEC_GUARD_URL=http://WINDOWS_TAILSCALE_IP:8788 \
  .venv/bin/python dev/dashboard_server.py
```

## 受保护正常动作

```bash
.venv/bin/python scripts/post_demo_fact.py
SAFEEXEC_RUNTIME_URL=http://127.0.0.1:8790 \
  .venv/bin/python -m lab_agent run \
  --mode replay --scenario normal --transport safeexec
```

预期：Policy 返回 `GRANT_MATCHED`，Guard 返回 `executed`，JOY 将
`sample-A` 从 `cold-storage` 移至 `analyzer-01`。

## 受保护的 Prompt Injection

先复位 JOY：

```powershell
.\scripts\reset_biolab_interactive.ps1
```

然后提交同一业务任务下被劫持的 Agent 意图：

```bash
SAFEEXEC_RUNTIME_URL=http://127.0.0.1:8790 \
  .venv/bin/python -m lab_agent run \
  --mode replay --scenario prompt-injection --transport safeexec
```

预期：Agent 明确生成 `cold-storage -> waste-bin`，Runtime 返回
`NO_MATCHING_GRANT`；Guard 和 JoyExecutor 均不被调用，样品保持原位。

## 无保护对照（仅演示）

该路径故意绕过 Runtime、Lease 和 Guard。用完必须关闭。

Windows：

```powershell
.\scripts\start_windows_legacy_bridge_interactive.ps1 `
  -Token YOUR_ONE_TIME_DEMO_TOKEN
```

Mac：

```bash
LAB_LEGACY_URL=http://WINDOWS_TAILSCALE_IP:8791 \
LAB_LEGACY_TOKEN=YOUR_ONE_TIME_DEMO_TOKEN \
  .venv/bin/python -m lab_agent run \
  --mode replay --scenario prompt-injection \
  --transport legacy --confirm-unsafe-demo
```

预期：相同恶意意图被直接执行，`sample-A` 到达 `waste-bin`，
`unsafe_outcome=true`。

演示结束：

```powershell
.\scripts\reset_biolab_interactive.ps1
.\scripts\stop_windows_legacy_bridge.ps1
```

## 当前数据契约

- Agent → Runtime：`safeexec.action.v1`
- Runtime → Guard：`safeexec.lease.v1`
- JOY 执行回执：`safeexec.execution.v1`
- Agent 只输出命名业务对象和区域，不能输出关节角或世界坐标。
