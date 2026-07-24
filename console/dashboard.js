"use strict";

const SAMPLE_IDS = ["sample-A", "sample-B", "sample-C", "sample-D", "sample-E", "sample-F"];
const STATE_LABELS = {
  STOPPED: "已停止", RUNNING: "自主运行", PAUSE_PENDING: "等待当前件完成",
  PAUSED: "已暂停", RECOVERING: "安全恢复中", COMPLETED: "全部完成", ERROR: "失败关闭",
};
const TASK_LABELS = {
  QUEUED: "排队", PLANNING: "规划", SUBMITTED: "已提交", EXECUTING: "执行中",
  COMPLETED: "完成", BLOCKED: "已阻断", RECOVERING: "恢复中", FAILED: "失败",
};
const LOCATION_LABELS = {
  "cold-storage": "等候区", "analyzer-01": "分析区", "waste-bin": "废弃区",
  "quarantine-zone": "隔离区", home: "待机位",
};
const ui = Object.fromEntries([
  "line-state", "line-state-dot", "active-task", "start-btn", "pause-btn",
  "resume-btn", "reset-btn", "inject-btn", "audit-btn", "notice",
  "queue-progress", "task-list", "trace-status", "trusted-order",
  "untrusted-input", "agent-intent", "safeexec-decision", "recovery-state",
  "physical-source", "arm-state", "platform-state", "dock-state", "location-map",
  "physical-verdict", "metric-completed", "metric-blocked", "metric-recovered",
  "metric-unsafe", "recent-events-list", "scroll-toggle", "audit-drawer",
  "audit-close", "audit-list", "scrim", "compiled-job", "runtime-path",
  "attack-form", "injection-target", "injection-content", "job-caption",
].map(id => [id, document.getElementById(id)]));

let snapshot = null;
let lastSeq = 0;
let stream = null;
let events = [];
let scrollPaused = false;
let reconnectTimer = null;
let snapshotTimer = null;

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

function showNotice(message, danger = false) {
  ui.notice.hidden = false;
  ui.notice.textContent = message;
  ui.notice.style.borderColor = danger ? "#dfaaa5" : "#a9cdbb";
  ui.notice.style.background = danger ? "#fbeae8" : "#e7f3ec";
  ui.notice.style.color = danger ? "#b42318" : "#176b4d";
  window.setTimeout(() => { ui.notice.hidden = true; }, 4200);
}

async function loadSnapshot() {
  snapshot = await request("/api/dashboard/v2");
  lastSeq = Math.max(lastSeq, Number(snapshot.line.last_seq || 0));
  render();
}

function render() {
  if (!snapshot) return;
  const line = snapshot.line;
  const state = line.line_state || "STOPPED";
  ui["line-state"].textContent = STATE_LABELS[state] || state;
  ui["line-state-dot"].className = `state-dot ${state.toLowerCase()}`;
  ui["active-task"].textContent = line.current_task_id || "无活动任务";
  for (const action of ["start", "pause", "resume", "reset"]) {
    ui[`${action}-btn`].disabled = !line.controls?.[action];
  }
  renderJob(line);
  renderInjectionTargets(line);
  renderTasks(line);
  renderTrace(line);
  renderPhysical(snapshot.physical, snapshot.physical_status, line.counters);
  renderMetrics(line.counters);
}

function renderJob(line) {
  const job = line.job_manifest || {};
  const count = Array.isArray(job.sample_ids) ? job.sample_ids.length : 0;
  const active = line.active_work_order_id;
  setText("runtime-path", line.execution_mode === "unsafe-baseline" ? "BYPASS" : "SAFEEXEC");
  ui["runtime-path"].className = `mono runtime-path ${line.execution_mode === "unsafe-baseline" ? "unsafe" : ""}`;
  setText("compiled-job", active && count
    ? `已验证 ${shortId(active)}，授权 ${count} 件样品前往${LOCATION_LABELS[job.destination] || job.destination}`
    : "尚未激活可信工单，请先前往配置页签发");
  setText("job-caption", job.operator_text || "等待工单");
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 16 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
}

function renderInjectionTargets(line) {
  const queued = (line.tasks || []).filter(task => task.status === "QUEUED" && !task.untrusted_input);
  const previous = ui["injection-target"].value;
  const options = queued.map(task => {
    const option = document.createElement("option");
    option.value = task.task_id;
    option.textContent = task.sample_id;
    return option;
  });
  ui["injection-target"].replaceChildren(...options);
  if (queued.some(task => task.task_id === previous)) {
    ui["injection-target"].value = previous;
  }
  ui["inject-btn"].disabled = !line.controls?.inject || queued.length === 0;
  ui["injection-target"].disabled = queued.length === 0;
}

function renderTasks(line) {
  ui["task-list"].replaceChildren();
  const tasks = Array.isArray(line.tasks) ? line.tasks : [];
  for (const task of tasks) {
    const item = document.createElement("li");
    const letter = document.createElement("span");
    const main = document.createElement("div");
    const title = document.createElement("strong");
    const route = document.createElement("small");
    const state = document.createElement("span");
    item.className = `task-item ${task.status?.toLowerCase() || ""} ${task.task_id === line.current_task_id ? "active" : ""}`;
    letter.className = "task-letter";
    letter.textContent = task.sample_id?.slice(-1) || "?";
    main.className = "task-main";
    title.textContent = task.sample_id;
    route.textContent = `${LOCATION_LABELS[task.source] || task.source} → ${LOCATION_LABELS[task.destination] || task.destination}`;
    main.append(title, route);
    state.className = "task-state";
    state.textContent = TASK_LABELS[task.status] || task.status;
    item.append(letter, main, state);
    ui["task-list"].append(item);
  }
  ui["queue-progress"].textContent = `${line.counters?.completed_tasks || 0} / ${tasks.length}`;
}

function renderTrace(line) {
  const task = line.tasks?.find(item => item.task_id === line.current_task_id) ||
    line.tasks?.find(item => item.blocked_intent) ||
    [...(line.tasks || [])].reverse().find(item => item.status !== "QUEUED");
  if (!task) {
    setText("trusted-order", "等待生产线启动");
    setText("untrusted-input", "尚未注入");
    setText("agent-intent", "尚未规划");
    setText("safeexec-decision", "等待意图");
    setText("recovery-state", "无需恢复");
    ui["trace-status"].textContent = "等待任务";
    ui["trace-status"].className = "status-chip";
    return;
  }
  setText(
    "trusted-order",
    `${line.job_manifest?.operator_text || "标准分析任务"}；当前执行 ${task.sample_id}`,
  );
  setText("untrusted-input", task.untrusted_input || "（无不可信输入）");
  const args = task.blocked_intent?.arguments || task.intent?.arguments;
  setText("agent-intent", args
    ? `${task.sample_id}: ${LOCATION_LABELS[args.source] || args.source} → ${LOCATION_LABELS[args.destination] || args.destination}`
    : "Agent 正在根据可信工单规划");
  const decision = task.blocked_decision || task.decision;
  setText("safeexec-decision", decision
    ? decision.effect === "bypass"
      ? "BYPASS · SafeExec 未启用，未执行策略检查"
      : `${String(decision.effect).toUpperCase()} · ${decision.reason_code}`
    : "等待 Runtime 决策");
  const recovering = task.status === "RECOVERING" || line.line_state === "RECOVERING";
  const recovered = task.status === "COMPLETED" && task.attempt > 1;
  setText("recovery-state", recovering
    ? "污染会话已销毁；正在从可信工单创建干净会话"
    : recovered ? "干净会话已正确重试，生产线继续" : "无需恢复");
  const label = TASK_LABELS[task.status] || task.status;
  ui["trace-status"].textContent = label;
  ui["trace-status"].className = `status-chip ${task.status?.toLowerCase() || ""}`;
  document.querySelector(".trace-row.untrusted").classList.toggle("active", !!task.untrusted_input);
  document.querySelector(".trace-row.decision").classList.toggle("denied", decision?.effect === "deny");
  document.querySelector(".trace-row.recovery").classList.toggle("active", recovering);
}

function setText(id, value) {
  ui[id].textContent = String(value ?? "—");
}

function renderPhysical(physical, source, counters = {}) {
  setText(
    "physical-source",
    source === "live" ? "实时库存"
      : source === "confirmed" ? "执行回执确认"
        : source === "cached" ? "最近证据" : "未连接",
  );
  setText("arm-state", physical?.arm_state || "—");
  setText("platform-state", physical?.platform_state || "—");
  setText("dock-state", LOCATION_LABELS[physical?.current_dock] || physical?.current_dock || "—");
  document.querySelectorAll(".sample-cluster").forEach(node => node.replaceChildren());
  document.querySelector(".map-unavailable")?.remove();
  const locations = physical?.sample_locations || {};
  if (!physical?.sample_locations) {
    const unknown = document.createElement("p");
    unknown.className = "map-unavailable";
    unknown.textContent = "未取得 JOY 物理位置证据";
    ui["location-map"].append(unknown);
  }
  for (const sampleId of SAMPLE_IDS) {
    const target = document.querySelector(`.sample-cluster[data-location="${locations[sampleId]}"]`);
    if (!target) continue;
    const token = document.createElement("span");
    token.className = `sample-token sample-${sampleId.slice(-1).toLowerCase()}`;
    token.textContent = sampleId.slice(-1);
    token.title = `${sampleId} · ${LOCATION_LABELS[locations[sampleId]] || locations[sampleId]}`;
    target.append(token);
  }
  const unsafe = Number(counters.unsafe_outcomes || 0);
  ui["physical-verdict"].textContent = `危险物理动作：${unsafe}`;
  ui["physical-verdict"].className = `physical-verdict ${unsafe ? "unsafe" : ""}`;
}

function renderMetrics(counters = {}) {
  setText("metric-completed", counters.completed_tasks || 0);
  setText("metric-blocked", counters.blocked_actions || 0);
  setText("metric-recovered", counters.recovered_tasks || 0);
  setText("metric-unsafe", counters.unsafe_outcomes || 0);
}

function appendEvent(event) {
  if (events.some(item => Number(item.seq) === Number(event.seq))) return;
  events.push(event);
  events.sort((a, b) => a.seq - b.seq);
  if (events.length > 500) events = events.slice(-500);
  lastSeq = Math.max(lastSeq, Number(event.seq || 0));
  if (!scrollPaused) renderEvents();
}

function eventNode(event, detailed = false) {
  const item = document.createElement("li");
  const seq = document.createElement("span");
  const source = document.createElement("span");
  const name = document.createElement("span");
  item.className = `event-item ${event.severity || "info"}`;
  seq.className = "event-seq";
  source.className = "event-source";
  name.className = "event-name";
  seq.textContent = `#${event.seq}`;
  source.textContent = String(event.source || "system").toUpperCase();
  if (detailed) {
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = new Date(event.at_ms || 0).toLocaleTimeString();
    name.textContent = `${event.event} · ${JSON.stringify(event.payload || {})}`;
    item.append(seq, time, source, name);
  } else {
    name.textContent = eventMessage(event);
    item.append(seq, source, name);
  }
  return item;
}

function eventMessage(event) {
  const p = event.payload || {};
  const messages = {
    "job.compiled": `Agent 已将自然语言编译为 ${p.sample_ids?.length || 0} 件样品工单`,
    "line.started": `自主生产线开始处理 ${p.task_count || 0} 件样品`,
    "execution.mode.changed": p.safeexec_enabled
      ? "执行路径切回 SafeExec 保护模式"
      : "执行路径切换为无保护演示基线",
    "attack.injected": `不可信输入已投递到 ${p.task_id || "目标任务"}`,
    "intent.proposed": `${p.sample_id || "Agent"} 提议前往 ${LOCATION_LABELS[p.destination] || p.destination || "目标位置"}`,
    "agent.output.scope_violation": `Agent 尝试将任务目标从 ${p.expected_sample_id || "当前样品"} 替换为 ${p.proposed_sample_id || "其他样品"}，未进入 Runtime`,
    "task.blocked": `SafeExec 拒绝：${p.reason_code || "未授权动作"}，未签发 Lease`,
    "agent.session.terminated": "污染会话已销毁",
    "task.recovering": "从可信工单创建干净会话",
    "task.completed": `${p.sample_id || p.task_id} 已到分析区${p.recovered ? "（恢复后）" : ""}`,
    "unsafe.action.executed": `${p.sample_id || "样品"} 已在无保护模式下到达废弃区`,
    "line.completed": `${p.task_count || "全部"}件样品任务完成`,
    "line.paused": "生产线已在当前件完成后暂停",
    "line.error": `失败关闭：${p.code || "未知错误"}`,
    "line.reset": "签名复位已通过 SafeExec 执行",
  };
  return messages[event.event] || event.event;
}

function renderEvents() {
  ui["recent-events-list"].replaceChildren(...events.slice(-8).map(event => eventNode(event)));
  ui["audit-list"].replaceChildren(...events.map(event => eventNode(event, true)));
}

async function reconcileEvents() {
  const value = await request(`/api/events?after=${events.length ? events[events.length - 1].seq : 0}`);
  for (const event of value.events || []) appendEvent(event);
}

function connectStream() {
  if (stream) stream.close();
  stream = new EventSource(`/api/events/stream?after=${lastSeq}`);
  stream.onmessage = handleStreamEvent;
  const named = [
    "job.compiled", "line.started", "line.pause_pending", "line.paused", "line.resumed", "line.reset",
    "line.completed", "line.error", "task.planning", "task.submitted", "task.executing",
    "task.blocked", "task.recovering", "task.completed", "task.failed", "attack.injected",
    "agent.session.created", "agent.session.compromised", "agent.session.terminated",
    "agent.session.recovered", "intent.proposed", "execution.mode.changed",
    "unsafe.action.executed", "agent.output.scope_violation",
  ];
  for (const eventName of named) stream.addEventListener(eventName, handleStreamEvent);
  stream.onerror = () => {
    stream.close();
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(async () => {
      try {
        await loadSnapshot();
        await reconcileEvents();
      } catch (error) {
        showNotice(`实时连接恢复失败：${error.message}`, true);
      }
      connectStream();
    }, 1300);
  };
}

async function handleStreamEvent(message) {
  const event = JSON.parse(message.data);
  appendEvent(event);
  scheduleSnapshot();
}

function scheduleSnapshot() {
  if (snapshotTimer !== null) return;
  snapshotTimer = window.setTimeout(async () => {
    snapshotTimer = null;
    try { await loadSnapshot(); } catch (error) { showNotice(error.message, true); }
  }, 60);
}

async function control(action) {
  try {
    await request(`/api/control/${action}`, {method: "POST", body: "{}"});
    await loadSnapshot();
    showNotice({start: "生产线已启动", pause: "将在当前件完成后暂停", resume: "生产线已继续", reset: "签名复位完成"}[action]);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function injectAttack() {
  const targetTaskId = ui["injection-target"].value;
  const content = ui["injection-content"].value.trim();
  if (!targetTaskId || !content) {
    showNotice("请选择仍在排队的样品并填写不可信内容", true);
    return;
  }
  const body = {
    schema_version: "safeexec.attack-injection.v1",
    injection_id: crypto.randomUUID(),
    attack_id: "dynamic-sample-label-injection",
    channel: "sample_label",
    target_task_id: targetTaskId,
    untrusted_content: content,
    actor_claims: {claimed_role: "visitor", claimed_identity: "untrusted-label"},
    requested_at_ms: Date.now(),
  };
  try {
    await request("/api/testing/injections", {method: "POST", body: JSON.stringify(body)});
    await loadSnapshot();
    showNotice(`不可信标签已登记到 ${targetTaskId.replace("task-", "")}`);
  } catch (error) {
    showNotice(error.message, true);
  }
}

function openAudit() {
  ui["audit-drawer"].hidden = false;
  ui.scrim.hidden = false;
  ui["audit-close"].focus();
}
function closeAudit() {
  ui["audit-drawer"].hidden = true;
  ui.scrim.hidden = true;
  ui["audit-btn"].focus();
}

function bind() {
  ["start", "pause", "resume", "reset"].forEach(action => {
    ui[`${action}-btn`].addEventListener("click", () => control(action));
  });
  ui["attack-form"].addEventListener("submit", event => {
    event.preventDefault();
    injectAttack();
  });
  ui["audit-btn"].addEventListener("click", openAudit);
  ui["audit-close"].addEventListener("click", closeAudit);
  ui.scrim.addEventListener("click", closeAudit);
  ui["scroll-toggle"].addEventListener("click", () => {
    scrollPaused = !scrollPaused;
    ui["scroll-toggle"].textContent = scrollPaused ? "继续滚动" : "暂停滚动";
    if (!scrollPaused) renderEvents();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !ui["audit-drawer"].hidden) closeAudit();
  });
}

async function init() {
  bind();
  try {
    await loadSnapshot();
    const initial = await request("/api/events?after=0");
    for (const event of initial.events || []) appendEvent(event);
    connectStream();
  } catch (error) {
    showNotice(`控制台未连接：${error.message}`, true);
  }
}

init();
