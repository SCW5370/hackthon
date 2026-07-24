# SafeExec V0 — 危险仓储态势感知 项目发现文档

## 1. 项目定位与愿景

**项目名称**: SafeExec Robot Action Firewall — 危险仓储态势感知系统
**当前版本**: V0 Demo（黑客松演示版）
**核心故事**: 在多 Agent 仓储环境中，构建一个"机器人动作防火墙"，通过 Fact + Policy + State Machine + Lease + Executor Gateway 的架构，实时监控、拦截越权动作，保障人机混合作业安全。

**目标用户**: 仓储安全值班员 / 运营管理员
**核心差异化**: 传统安全监控是被动的日志告警；SafeExec 是主动拦截——通过 Action Lease 机制在动作执行前就阻断风险。

---

## 2. 系统架构（当前实现）

### 2.1 技术架构

```
[模拟前端 Dashboard] ←→ [Python HTTP Server (:8787)] ←→ [态势引擎 + LLM 播报生成]
                                      ↓
                              [audit.jsonl 持久化日志]
```

**前端**: 纯 HTML/CSS/JS，无框架，地址 `http://127.0.0.1:8787/`
**后端**: Python 标准库 `http.server` + `ThreadingHTTPServer`，单文件 `dev/mock_server.py`
**配置**: `config/demo.json`（区域定义、机器人参数、动作标签）
**日志**: `logs/audit.jsonl`（追加写，每行一个 JSON）

### 2.2 核心数据模型

#### RobotState（机器人状态）
| 字段 | 含义 |
|------|------|
| id / name / type | 标识：forklift-07 / 叉车-07 / forklift |
| x, y, theta | 地图坐标（归一化 0~1） |
| speed_mps | 当前速度（米/秒） |
| payload_kg / height_m | 负载 / 举升高度 |
| action | 当前动作（move/stop/forklift-raise/...） |
| zone_id | 当前所在区域 |
| status | normal / warning / critical |
| violations[] | 关联违规 ID 列表 |
| control_action | 系统自动下发指令（emergency_stop / force_slow_down / ...） |
| trajectory | 轨迹点序列（最多30个） |

#### Zone（区域配置）
| 字段 | 含义 |
|------|------|
| id | 区域唯一标识 |
| risk_level | low / medium / high / critical |
| polygon | 归一化坐标多边形 [[x,y], ...] |
| rules.speed_limit | 限速（m/s） |
| rules.allowed_actions | 允许动作列表 |
| rules.forbidden | 是否全面禁止 |
| rules.max_stay_ms | 最大停留时间 |
| rules.height_limit | 限高 |

#### Violation（违规记录）
| 字段 | 含义 |
|------|------|
| id | 违规唯一 ID（v1, v2, ...） |
| kind | zone_intrusion / speeding / overload / unauthorized_action / ... |
| level | critical / warning |
| title | 简短标题 |
| message | 格式化描述（含数字中文语音化） |
| narrative | AI 生成的语音播报文案 |
| summary | AI 生成的一句话摘要 |
| advice | AI 生成的处置建议 |
| severity_label | 高危 / 中危 / 低危 |

#### Fact（感知事实）
| 字段 | 含义 |
|------|------|
| key | zone.clear / camera.healthy / system.heartbeat / robot.pose / ... |
| value | true / false / null（unknown） |
| source | 数据来源（mock-vision / mock-camera / ...） |
| confidence | 置信度 |
| ttl_ms | 过期时间（毫秒） |
| evidence | 附加证据（robot_id / reason 等） |

### 2.3 态势引擎（SituationEngine）

8 种违规检测规则：
1. **zone_intrusion** — 闯入 foridden=true 区域
2. **speeding** — 区域超速（超过限速+0.05）
3. **overload** — 负载超过额定值
4. **overheight** — 举升超高
5. **unauthorized_action** — 执行了区域不允许的动作
6. **long_stay_in_danger** — 危险区超长停留
7. **camera_unhealthy** — 摄像头异常
8. **zone_not_clear** — 监控区域检测到入侵/障碍物

违规不自动过期，必须操作员确认（ACK）后才清除。

### 2.4 AI 文案生成（NarrativeAgent）

**优先路径**: 调用外部 LLM（支持 ANTHROPIC_API_KEY 环境变量）
**备用路径**: 规则模板 fallback（8 种违规类型 × 中英文数字语音化）

数字语音化规则：0.4 → "零点四"，1.8 → "一点八"

### 2.5 系统状态机

```
READY → RUNNING（正常作业）→ SAFE_HOLD（违规检测）→ DEGRADED（系统降级）→ EMERGENCY（紧急停止）
       ↓
    任何违规 → SAFE_HOLD
    操作员 e_stop → EMERGENCY
```

### 2.6 API 列表

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/state` | GET | 全系统快照 |
| `/api/events?after=N` | GET | 获取 seq>N 的新事件 |
| `/api/config` | GET | 加载配置 |
| `/api/mock/scenario` | POST | 触发模拟场景 |
| `/api/command` | POST | 发送操作员命令 |
| `/api/ai/narrative` | POST | AI 文案生成（调试用） |
| `/api/facts` | POST | 更新感知事实 |
| `/api/frame` | GET/POST | 视频帧（预留） |

### 2.7 8 个模拟场景

| 场景 ID | 触发条件 | 违规 |
|----------|----------|------|
| normal | 重置，状态恢复 | 无 |
| intrusion | 叉车闯化学品区 + zone.clear=false | zone_intrusion + unauthorized_action |
| forklift_overload | 叉车超载1300kg | overload + overheight |
| agv_speeding_with_person | AGV在人机区1.8m/s + 人员0.6m内 | person_nearby_fast |
| arm_high_voltage | 机械臂滞留高压区12秒 | long_stay_in_danger |
| camera | 摄像头超时 | camera_unhealthy |
| fire_lane_intrusion | AGV占消防通道 | zone_intrusion |
| degraded | 系统降级模式 | 系统状态变为 DEGRADED |

---

## 3. 当前 Dashboard 能力

### 已实现
- ✅ 顶部 KPI 栏（态势评分/系统状态/在线机器人/活跃告警）
- ✅ KPI 指标行（6项：态势评分/严重告警/警告/机器人总数/禁区侵入/摄像头）
- ✅ 机器人状态卡片矩阵（含违规列表、自动控制指令显示）
- ✅ SVG 仓储风险地图（区域多边形+机器人定位+轨迹+人员标记）
- ✅ 实时告警表格（7列：时间/等级/类型/机器人/区域/描述/操作确认）
- ✅ 事件时间线（最近12条，含事件类型解析）
- ✅ 操作员控制面板（紧急停止/确认告警/系统复位）
- ✅ CrashLab 场景模拟按钮栏（Shift+D 切换）
- ✅ AI 告警弹窗（narrative + AI 建议，语音播报 + Browser Notification）
- ✅ 自动安全控制可视化（robot.control_action 字段驱动红色"系统自动指令"标签）
- ✅ 数字中文语音化（TTS 播报友好）
- ✅ LLM 文案生成（Claude API + 规则模板 fallback）
- ✅ 持久化审计日志（logs/audit.jsonl，每事件一条 JSONL）

---

## 4. 当前 Dashboard 缺失（对照产品架构）

### 4.1 Multi-Agent Console（P0 — 核心缺失）
产品架构 L3 核心：Safety Supervisor Agent ↔ Factory Task Agent 的结构化对话面板。当前完全没有。

### 4.2 红蓝双产线 Digital Twin（P0 — 核心缺失）
产品架构核心特性：Baseline（直接执行）vs SafeExec（Lease 拦截）对比视图。当前只有单张地图。

### 4.3 Lease 可视化（P0）
产品核心概念：Lease（时间限制授权令牌）。当前只显示 `lease_remaining_ms`，没有独立面板。

### 4.4 Action Workflow 状态机（P1）
产品要求：PROPOSED → EVALUATING → AUTHORIZED → EXECUTING → COMPLETED 的动作生命周期可视化。当前没有。

### 4.5 CrashLab 攻击注入面板（P1）
当前场景模拟命名不清晰，没有体现"攻击与异常注入实验室"的定位。

### 4.6 因果链可视化（P1）
Fact → Policy → State → Lease → Gateway → Action 的横向流程图，当前只是文字时间线。

### 4.7 系统健康指标（P2）
内存/心跳/RPS/P95 延迟 的实时图表，当前没有。

---

## 5. 产品架构关键概念（对照参考）

### 5.1 Multi-Agent 控制流
```
Factory Task Agent（任务规划）
    ↓ PROPOSE action
Safety Supervisor Agent（安全监督）
    ↓ REQUEST authorization
SafeExec Runtime（执行引擎）
    ↓ EVALUATE Fact + Policy
    → ALLOW/DENY
Safety Supervisor
    → confirm / REPLAN
```

### 5.2 Action Lease 机制
- Lease 是时间有限的授权令牌
- 动作执行前必须持有有效 Lease
- Lease 可被 Runtime 实时撤销
- 撤销触发条件：Fact 变更 / Policy 违规 / 健康检查失败

### 5.3 Red/Blue 双产线
| 红色 Baseline | 蓝色 SafeExec |
|--------------|--------------|
| Factory Agent 直接控制 | Factory Agent 只能 PROPOSE |
| 无 Lease 约束 | 动作必须持有 Lease |
| 异常仅记录日志 | 异常实时改变权限 |
| 人工事后复盘 | Supervisor 实时拦截 |

### 5.4 CrashLab 攻击类型
越权动作 / 人员闯入危险区 / 摄像头遮挡 / 内存异常 / 受控 DoS / 摄像头越权读取

---

## 6. 技术债务与风险

1. **mock_server.py 是单线程 + 文件锁混用**：多线程场景下 `RLock` 正确但 `reset()` 中使用 `getattr(self, "lock", ...)` 容易出现竞态
2. **NarrativeAgent 每次请求都重新实例化 API**：应该复用 session
3. **audit.jsonl 无轮转**：长时间运行文件会持续膨胀
4. **地图坐标全靠归一化**：实际部署需要坐标映射层
5. **无身份认证**：操作员命令无权限验证
6. **前端无错误边界**：网络异常时 dashboard 直接显示 ERR 无重试

---

## 7. 部署状态

- 服务地址：`http://127.0.0.1:8787/`
- 配置文件：`config/demo.json`
- 审计日志：`logs/audit.jsonl`（自动创建）
- 进程管理：直接 `python3 dev/mock_server.py &`，无 systemd
- LLM：通过环境变量 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 配置

---

## 8. 后续开发建议（按优先级）

### P0（黑客松冲刺必做）
1. **Multi-Agent Console** — Safety Supervisor ↔ Factory Agent 对话面板，最核心的故事
2. **红蓝双产线视图** — 评委一眼看懂对比效果
3. **Lease 可视化面板** — 产品核心概念显性化

### P1（强化说服力）
4. **Action Workflow 状态卡片** — 动作生命周期可见
5. **CrashLab 攻击面板重组** — 体现主动防御能力
6. **因果链流程图** — 展示决策透明度

### P2（加分项）
7. **系统健康指标折线图** — 内存/心跳/RPS
8. **前端美化升级** — 工业控制台风格字体/动效
