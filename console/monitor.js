"use strict";

const ATTACK_LABELS = {
  "prompt-injection": "提示词注入",
  "model-hallucination": "模型幻觉",
  "intent-tampering": "指令篡改",
  "lease-replay": "Lease 重放",
};

const EVENT_LABELS = {
  "work_order.activated": "可信工单已激活",
  "line.started": "自主产线启动",
  "line.paused": "产线协作暂停",
  "line.resumed": "产线继续运行",
  "line.batch.completed": "整批处理完成",
  "line.batch.refreshing": "签名换线正在执行",
  "line.batch.refreshed": "下一批已进入等候区",
  "line.cycle.completed": "批次周期完成",
  "attack.injected": "不可信输入进入 Agent",
  "intent.proposed": "Agent 提出动作意图",
  "task.submitted": "意图提交 Runtime",
  "task.executing": "Lease 已通过 Guard",
  "task.completed": "可信物理动作完成",
  "task.blocked": "越权动作被阻断",
  "task.recovering": "从可信工单恢复",
  "agent.session.compromised": "污染会话已识别",
  "agent.session.terminated": "污染会话已销毁",
  "agent.session.recovered": "干净会话恢复成功",
  "line.error": "产线失败关闭",
  "line.reset": "签名复位完成",
};

const SECURITY_EVENTS = new Set([
  "attack.injected",
  "task.blocked",
  "agent.session.compromised",
  "agent.session.terminated",
  "task.recovering",
  "agent.session.recovered",
  "line.error",
]);

let monitor = null;
let events = [];
let challenges = [];
let lastSeq = 0;
let eventStream = null;
let snapshotTimer = null;
let reconnectTimer = null;
let newestRenderedSeq = 0;

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = String(value ?? "—");
}

async function request(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function componentsFromDashboard(dashboard) {
  const preflight = dashboard?.preflight || {};
  const raw = preflight.components || {};
  return {
    orchestrator: Boolean(raw.orchestrator?.ready),
    runtime: Boolean(raw.runtime?.ready),
    guard: Boolean(raw.guard?.ready),
    joy: Boolean(raw.joy?.ready),
  };
}

function calculateAssurance(dashboard) {
  const line = dashboard?.line || {};
  const counters = line.counters || {};
  const components = componentsFromDashboard(dashboard);
  const readyCount = Object.values(components).filter(Boolean).length;
  const health = readyCount * 10;
  const unsafe = Number(counters.unsafe_outcomes || 0) === 0 ? 30 : 0;
  const protectedMode = line.execution_mode === "protected" ? 10 : 0;
  const trustedOrder = line.active_work_order_id ? 10 : 0;
  const boundary = protectedMode + trustedOrder;
  const blocked = Number(counters.blocked_actions || 0);
  const recovered = Number(counters.recovered_tasks || 0);
  const recovery = blocked === 0
    ? 10
    : Math.round(clamp(recovered / blocked, 0, 1) * 10);
  return {
    score: health + unsafe + boundary + recovery,
    health,
    unsafe,
    boundary,
    recovery,
    readyCount,
    components,
  };
}

function renderAssurance(dashboard) {
  const value = calculateAssurance(dashboard);
  const lineFailed = dashboard?.line?.line_state === "ERROR";
  const circumference = 2 * Math.PI * 72;
  const ring = byId("score-ring");
  ring.style.strokeDashoffset = String(
    circumference * (1 - value.score / 100),
  );
  ring.style.stroke = lineFailed
    ? "var(--warning)"
    : value.score >= 90
    ? "var(--trusted)"
    : value.score >= 70
      ? "var(--warning)"
      : "var(--blocked)";
  setText("assurance-score", value.score);
  setText(
    "assurance-grade",
    lineFailed
      ? "故障中，安全边界保持"
      : value.score >= 90
        ? "证据链完整"
        : value.score >= 70
          ? "存在降级项"
          : "需要处置",
  );
  setText("factor-health", `${value.health} / 40`);
  setText("factor-unsafe", `${value.unsafe} / 30`);
  setText("factor-boundary", `${value.boundary} / 20`);
  setText("factor-recovery", `${value.recovery} / 10`);
  setText("component-summary", `${value.readyCount} / 4`);

  Object.entries(value.components).forEach(([name, ready]) => {
    const row = document.querySelector(`[data-component="${name}"]`);
    if (!row) return;
    row.classList.toggle("is-ready", ready);
    row.classList.toggle("is-down", !ready);
    row.querySelector("strong").textContent = ready ? "READY" : "OFFLINE";
  });
}

function latestEvent(...names) {
  const accepted = new Set(names);
  return [...events].reverse().find((event) => accepted.has(event.event)) || null;
}

function clearFlowClasses() {
  document.querySelectorAll(".flow-node").forEach((node) => {
    node.classList.remove("is-active", "is-passed", "is-blocked");
  });
  document.querySelectorAll(".flow-link").forEach((node) => {
    node.classList.remove("is-passed", "is-blocked");
  });
}

function markFlowThrough(layer) {
  const order = ["agent", "runtime", "lease", "guard", "device"];
  const index = order.indexOf(layer);
  order.forEach((name, nodeIndex) => {
    const node = document.querySelector(`[data-layer="${name}"]`);
    if (nodeIndex < index) node?.classList.add("is-passed");
    if (nodeIndex === index) node?.classList.add("is-active");
  });
  const links = [
    "agent-runtime",
    "runtime-lease",
    "lease-guard",
    "guard-device",
  ];
  links.forEach((name, linkIndex) => {
    if (linkIndex < index) {
      document.querySelector(`[data-link="${name}"]`)?.classList.add("is-passed");
    }
  });
}

function markBlocked(layer) {
  const order = ["agent", "runtime", "lease", "guard", "device"];
  const index = order.indexOf(layer);
  order.forEach((name, nodeIndex) => {
    const node = document.querySelector(`[data-layer="${name}"]`);
    if (nodeIndex < index) node?.classList.add("is-passed");
    if (nodeIndex === index) node?.classList.add("is-blocked");
  });
  const blockedLink = layer === "runtime"
    ? "runtime-lease"
    : layer === "guard"
      ? "guard-device"
      : null;
  if (blockedLink) {
    document.querySelector(`[data-link="${blockedLink}"]`)?.classList.add("is-blocked");
  }
}

function normalizedChallenge(challenge) {
  if (!challenge) return null;
  if (challenge.status !== "armed") return challenge;
  const blockedEvent = events.find((event) => (
    event.event === "task.blocked"
    && Number(event.at_ms) >= Number(challenge.created_at_ms || 0)
  ));
  if (!blockedEvent) return challenge;
  return {
    ...challenge,
    status: "blocked",
    blocked_at: "runtime",
    reason_code: blockedEvent.payload?.reason_code || "NO_MATCHING_GRANT",
    physical_outcome: "no-change",
  };
}

function currentSecurityDecision() {
  const lastChallenge = normalizedChallenge(challenges.at(-1));
  const securityEvent = [...events].reverse().find((event) => (
    SECURITY_EVENTS.has(event.event)
  ));
  if (
    lastChallenge
    && Number(lastChallenge.created_at_ms || 0)
      >= Number(securityEvent?.at_ms || 0) - 2000
  ) {
    return { challenge: lastChallenge, event: securityEvent };
  }
  return { challenge: null, event: securityEvent };
}

function renderCausalFlow(dashboard) {
  const line = dashboard?.line || {};
  const physical = dashboard?.physical || {};
  const current = (line.tasks || []).find(
    (task) => task.task_id === line.current_task_id,
  );
  const lineState = String(line.line_state || "UNKNOWN");
  const stateNode = document.querySelector(".line-state");
  stateNode.classList.toggle("is-running", ["RUNNING", "PAUSE_PENDING", "RECOVERING"].includes(lineState));
  stateNode.classList.toggle("is-error", lineState === "ERROR");
  setText("line-state-label", lineState);
  setText("agent-state", current ? current.lot_id || current.sample_id : "等待任务");
  setText("device-state", `${physical.arm_state || "UNKNOWN"} · ${physical.current_dock || "—"}`);

  clearFlowClasses();
  const stage = byId("causal-stage");
  stage.classList.toggle("is-flowing", lineState === "RUNNING");

  const decision = currentSecurityDecision();
  const challenge = decision.challenge;
  const event = decision.event;
  const decisionBox = byId("live-decision");
  decisionBox.classList.remove("is-blocked");

  if (lineState === "ERROR") {
    const lastError = line.last_error || {};
    const errorText = String(lastError.detail || "执行链异常，系统已失败关闭。");
    const guardDenied = /Guard|LEASE_|REQUEST_HASH|REPLAY|CONSUMED/i.test(
      `${lastError.code || ""} ${errorText}`,
    );
    markBlocked(guardDenied ? "guard" : "runtime");
    decisionBox.classList.add("is-blocked");
    setText("decision-kicker", "异常动作 · 失败关闭");
    setText(
      "decision-title",
      guardDenied
        ? "Device Guard 拒绝执行，等待签名复位"
        : "SafeExec Runtime 中止链路，等待人工处置",
    );
    setText("decision-detail", errorText);
    setText(
      "decision-code",
      lastError.reason_code
        || (errorText.match(/[A-Z][A-Z0-9_]{3,}/)?.[0])
        || lastError.code
        || "FAIL_CLOSED",
    );
    setText("physical-outcome", "NO CHANGE");
    setText("runtime-state", guardDenied ? "策略已允许" : "失败关闭");
    setText("lease-state", guardDenied ? "凭证无效" : "停止签发");
    setText("guard-state", guardDenied ? "验证拒绝" : "未调用");
    return;
  }

  if (challenge?.status === "blocked") {
    const layer = challenge.blocked_at === "guard" ? "guard" : "runtime";
    markBlocked(layer);
    decisionBox.classList.add("is-blocked");
    setText("decision-kicker", `${ATTACK_LABELS[challenge.attack_type] || "攻击"} · 已阻断`);
    setText("decision-title", `执行权在 ${layer === "guard" ? "Device Guard" : "SafeExec Runtime"} 被截断`);
    setText("decision-detail", challenge.detail || "动作没有获得抵达设备的权限。");
    setText("decision-code", challenge.reason_code || "DENIED");
    setText("physical-outcome", "NO CHANGE");
    setText("runtime-state", layer === "runtime" ? "策略拒绝" : "Lease 已签发");
    setText("lease-state", layer === "runtime" ? "未签发" : "哈希已绑定");
    setText("guard-state", layer === "guard" ? "验证拒绝" : "未调用");
    return;
  }

  if (event?.event === "task.blocked") {
    markBlocked("runtime");
    decisionBox.classList.add("is-blocked");
    setText("decision-kicker", "越权意图 · 已阻断");
    setText("decision-title", "Runtime 拒绝签发 Action Lease");
    setText("decision-detail", "污染会话无法把未授权动作传递到设备边界。");
    setText("decision-code", event.payload?.reason_code || "NO_MATCHING_GRANT");
    setText("physical-outcome", "NO CHANGE");
    setText("runtime-state", "策略拒绝");
    setText("lease-state", "未签发");
    setText("guard-state", "未调用");
    return;
  }

  if (current?.status === "EXECUTING" || current?.status === "COMPLETED") {
    markFlowThrough("device");
    document.querySelector('[data-layer="device"]')?.classList.add("is-passed");
    setText("runtime-state", "策略允许");
    setText("lease-state", "已签发 · 单次");
    setText("guard-state", "验签通过");
    setText("decision-kicker", "可信动作 · 执行中");
    setText("decision-title", `${current.lot_id || current.sample_id} 获得一次性执行权`);
    setText("decision-detail", "Intent 与 Lease 哈希一致，设备侧 Guard 已消费凭证。");
    setText("decision-code", "ALLOW");
    setText("physical-outcome", physical.arm_state === "MOVING" ? "IN MOTION" : "VERIFIED");
    return;
  }

  if (current) {
    const layer = current.status === "SUBMITTED" ? "runtime" : "agent";
    markFlowThrough(layer);
    setText("runtime-state", current.status === "SUBMITTED" ? "正在评估" : "等待意图");
    setText("lease-state", "未签发");
    setText("guard-state", "等待凭证");
    setText("decision-kicker", "正常任务 · 正在评估");
    setText("decision-title", `${current.lot_id || current.sample_id} 正在生成结构化动作`);
    setText("decision-detail", "动作只有通过授权边界后才能抵达 JOY。");
    setText("decision-code", current.status || "PLANNING");
    setText("physical-outcome", "PENDING");
    return;
  }

  markFlowThrough("agent");
  setText("runtime-state", "等待意图");
  setText("lease-state", "未签发");
  setText("guard-state", "等待凭证");
  setText("decision-kicker", "等待安全事件");
  setText("decision-title", "可信链路正在监控每一个动作意图");
  setText("decision-detail", "攻击发生时，这里会显示阻断层、原因代码和物理结果。");
  setText("decision-code", "READY");
  setText("physical-outcome", "NO CHANGE");
}

function renderBatch(dashboard) {
  const line = dashboard?.line || {};
  const physical = dashboard?.physical || line.physical_evidence || {};
  const locations = physical.sample_locations || {};
  const tasks = line.tasks || [];
  const track = byId("batch-track");
  const samples = ["sample-A", "sample-B", "sample-C", "sample-D", "sample-E", "sample-F"];
  const nodes = samples.map((sampleId) => {
    const task = tasks.find((item) => item.sample_id === sampleId) || {};
    const location = locations[sampleId] || "unknown";
    const item = document.createElement("li");
    item.classList.toggle("is-analyzed", location === "analyzer-01");
    item.classList.toggle("is-current", task.task_id === line.current_task_id);
    item.classList.toggle("is-blocked", task.status === "BLOCKED" || task.status === "RECOVERING");
    const dot = document.createElement("i");
    const title = document.createElement("strong");
    const state = document.createElement("small");
    title.textContent = task.lot_id || sampleId;
    state.textContent = location === "analyzer-01"
      ? "分析区"
      : task.task_id === line.current_task_id
        ? "搬运中"
        : "等候区";
    item.append(dot, title, state);
    return item;
  });
  track.replaceChildren(...nodes);
  const analyzer = Object.values(locations).filter((value) => value === "analyzer-01").length;
  const storage = Object.values(locations).filter((value) => value === "cold-storage").length;
  setText("analyzer-count", analyzer);
  setText("storage-count", storage);
  setText("batch-label", `Cycle ${line.entity_pool?.active_cycle || 1}`);
  byId("batch-progress").style.width = `${clamp(analyzer / 6, 0, 1) * 100}%`;
}

function challengeStateByType(type) {
  const candidates = challenges
    .filter((challenge) => challenge.attack_type === type)
    .map(normalizedChallenge);
  return candidates.at(-1) || null;
}

function renderThreats(dashboard) {
  const line = dashboard?.line || {};
  let challengedBlocked = 0;
  Object.keys(ATTACK_LABELS).forEach((type) => {
    const row = document.querySelector(`[data-attack="${type}"]`);
    const challenge = challengeStateByType(type);
    row.classList.remove("is-blocked", "is-armed");
    const status = row.querySelector("strong");
    const layer = row.querySelector("small");
    if (challenge?.status === "blocked") {
      row.classList.add("is-blocked");
      status.textContent = challenge.reason_code || "BLOCKED";
      layer.textContent = challenge.blocked_at === "guard" ? "Guard" : "Runtime";
      challengedBlocked += 1;
    } else if (challenge?.status === "armed") {
      row.classList.add("is-armed");
      status.textContent = "ARMED";
    } else {
      status.textContent = "WAITING";
    }
  });

  const counters = line.counters || {};
  const allowed = Number(counters.completed_tasks || 0);
  const orchestratedBlocked = Number(counters.blocked_actions || 0);
  const blocked = Math.max(orchestratedBlocked, challengedBlocked);
  const unsafe = Number(counters.unsafe_outcomes || 0);
  const total = Math.max(1, allowed + blocked + unsafe);
  setText("blocked-total", `${blocked} BLOCKED`);
  setText("allowed-count", allowed);
  setText("blocked-count", blocked);
  setText("unsafe-count", unsafe);
  setText("outcome-total", `${allowed + blocked + unsafe} decisions`);
  byId("allowed-bar").style.width = `${allowed / total * 100}%`;
  byId("blocked-bar").style.width = `${blocked / total * 100}%`;
  byId("unsafe-bar").style.width = `${unsafe / total * 100}%`;
}

function renderSparkline() {
  const now = Date.now();
  const bins = Array(10).fill(0);
  events.forEach((event) => {
    const age = now - Number(event.at_ms || 0);
    if (age < 0 || age >= 60_000) return;
    const index = clamp(9 - Math.floor(age / 6000), 0, 9);
    bins[index] += 1;
  });
  const max = Math.max(1, ...bins);
  const points = bins.map((value, index) => {
    const x = index / 9 * 320;
    const y = 68 - value / max * 58;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  byId("spark-line").setAttribute("points", points.join(" "));
  byId("spark-area").setAttribute(
    "d",
    `M 0 72 L ${points.join(" L ")} L 320 72 Z`,
  );
  const recentCount = bins.reduce((sum, value) => sum + value, 0);
  setText("events-per-minute", recentCount);
  setText("last-seq", lastSeq);
}

function eventEvidence(event) {
  const payload = event.payload || {};
  return payload.reason_code
    || payload.destination
    || payload.lot_id
    || payload.task_id
    || payload.strategy
    || payload.path
    || "verified event";
}

function renderAudit() {
  const container = byId("audit-rows");
  const recent = events.slice(-7).reverse();
  const nodes = recent.map((event) => {
    const row = document.createElement("div");
    row.className = "audit-row";
    row.setAttribute("role", "row");
    if (["critical", "error"].includes(event.severity) || event.event === "task.blocked") {
      row.classList.add("is-critical");
    }
    if (event.event.includes("recover")) row.classList.add("is-recovery");
    if (Number(event.seq) > newestRenderedSeq) row.classList.add("is-new");
    const values = [
      `#${event.seq}`,
      new Date(Number(event.at_ms)).toLocaleTimeString("zh-CN", { hour12: false }),
      event.source || "unknown",
      EVENT_LABELS[event.event] || event.event,
      eventEvidence(event),
    ];
    values.forEach((value) => {
      const cell = document.createElement("span");
      cell.setAttribute("role", "cell");
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  });
  container.replaceChildren(...nodes);
  newestRenderedSeq = Math.max(newestRenderedSeq, ...recent.map((event) => Number(event.seq || 0)));
}

function renderVerdict(dashboard) {
  const line = dashboard?.line || {};
  const counters = line.counters || {};
  const decision = currentSecurityDecision();
  const verdict = byId("wall-verdict");
  verdict.classList.remove("is-blocked", "is-warning");
  if (line.line_state === "ERROR" || Number(counters.unsafe_outcomes || 0) > 0) {
    verdict.classList.add("is-warning");
    setText("wall-verdict-label", "执行链需要人工处置");
  } else if (
    decision.challenge?.status === "blocked"
    || decision.event?.event === "task.blocked"
  ) {
    verdict.classList.add("is-blocked");
    setText("wall-verdict-label", "越权动作已被截断");
  } else if (line.line_state === "RUNNING") {
    setText("wall-verdict-label", "可信动作持续执行");
  } else {
    setText("wall-verdict-label", "安全链路就绪");
  }
}

function renderCounters(dashboard) {
  const counters = dashboard?.line?.counters || {};
  setText("completed-total", counters.completed_tasks || 0);
  setText("recovered-total", counters.recovered_tasks || 0);
  setText("unsafe-total", counters.unsafe_outcomes || 0);
}

function renderAll() {
  const dashboard = monitor?.dashboard || {};
  renderAssurance(dashboard);
  renderCausalFlow(dashboard);
  renderBatch(dashboard);
  renderThreats(dashboard);
  renderSparkline();
  renderAudit();
  renderVerdict(dashboard);
  renderCounters(dashboard);
}

function appendEvents(incoming) {
  const known = new Set(events.map((event) => Number(event.seq)));
  incoming.forEach((event) => {
    const seq = Number(event.seq || 0);
    if (!seq || known.has(seq)) return;
    events.push(event);
    known.add(seq);
  });
  events.sort((a, b) => Number(a.seq) - Number(b.seq));
  if (events.length > 300) events = events.slice(-300);
  lastSeq = Math.max(lastSeq, ...events.map((event) => Number(event.seq || 0)));
}

async function loadMonitor() {
  const value = await request("/api/monitor");
  monitor = value;
  challenges = Array.isArray(value.challenges) ? value.challenges : [];
  renderAll();
}

async function reconcileEvents() {
  const value = await request(`/api/events?after=${lastSeq}`);
  appendEvents(value.events || []);
}

function handleStreamEvent(message) {
  if (!message.data) return;
  try {
    appendEvents([JSON.parse(message.data)]);
    renderAll();
  } catch (error) {
    showNotice(`审计事件解析失败：${error.message}`);
  }
}

function connectStream() {
  if (eventStream) eventStream.close();
  eventStream = new EventSource(`/api/events/stream?after=${lastSeq}`);
  eventStream.onmessage = handleStreamEvent;
  Object.keys(EVENT_LABELS).forEach((eventName) => {
    eventStream.addEventListener(eventName, handleStreamEvent);
  });
  eventStream.onerror = () => {
    eventStream.close();
    clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(async () => {
      try {
        await reconcileEvents();
        await loadMonitor();
      } catch (error) {
        showNotice(`实时链路恢复失败：${error.message}`);
      }
      connectStream();
    }, 1500);
  };
}

function showNotice(message) {
  const notice = byId("monitor-notice");
  notice.textContent = message;
  notice.hidden = false;
  window.setTimeout(() => { notice.hidden = true; }, 5000);
}

function updateClock() {
  byId("wall-clock").textContent = new Date().toLocaleTimeString("zh-CN", {
    hour12: false,
  });
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await document.documentElement.requestFullscreen();
    }
  } catch (error) {
    showNotice(`无法切换全屏：${error.message}`);
  }
}

async function boot() {
  updateClock();
  window.setInterval(updateClock, 1000);
  byId("fullscreen-toggle").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    setText("fullscreen-toggle", document.fullscreenElement ? "退出全屏" : "全屏");
  });
  try {
    const [monitorValue, eventValue] = await Promise.all([
      request("/api/monitor"),
      request("/api/events?after=0"),
    ]);
    monitor = monitorValue;
    challenges = Array.isArray(monitorValue.challenges)
      ? monitorValue.challenges
      : [];
    appendEvents(eventValue.events || []);
    renderAll();
    connectStream();
    snapshotTimer = window.setInterval(async () => {
      try {
        await loadMonitor();
      } catch (error) {
        showNotice(`状态刷新失败：${error.message}`);
      }
    }, 4000);
  } catch (error) {
    showNotice(`观测墙启动失败：${error.message}`);
  }
}

window.addEventListener("beforeunload", () => {
  eventStream?.close();
  clearInterval(snapshotTimer);
  clearTimeout(reconnectTimer);
});

boot();
