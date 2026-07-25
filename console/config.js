"use strict";

const ui = Object.fromEntries([
  "issuer-dot", "issuer-status", "policy-id", "policy-principal",
  "trusted-issuer", "path-chip", "path-description", "protected-mode-btn",
  "unsafe-mode-btn", "unsafe-mode-token", "config-line-state",
  "work-order-form", "work-order-principal", "work-order-validity",
  "work-order-source", "work-order-destination", "work-order-note",
  "issue-work-order-btn", "order-verification", "empty-order", "order-details",
  "active-order-id", "active-order-issuer", "active-order-subject",
  "active-order-expiry", "grant-table-body", "config-notice",
  "work-order-facts",
].map(id => [id, document.getElementById(id)]));

let configuration = null;

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
  ui["config-notice"].hidden = false;
  ui["config-notice"].textContent = message;
  ui["config-notice"].classList.toggle("danger", danger);
  window.setTimeout(() => { ui["config-notice"].hidden = true; }, 5000);
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 22 ? `${text.slice(0, 10)}…${text.slice(-6)}` : text;
}

async function loadConfiguration() {
  configuration = await request("/api/config");
  render();
}

function render() {
  const runtime = configuration.runtime || {};
  const policy = runtime.organization_policy || {};
  const line = configuration.line || {};
  const issuer = configuration.issuer;
  ui["issuer-dot"].className = `state-dot ${issuer ? "running" : "error"}`;
  ui["issuer-status"].textContent = issuer ? "签名密钥就绪" : "签名密钥未配置";
  ui["policy-id"].textContent = policy.mission_id || "—";
  ui["policy-principal"].textContent = policy.principal_id || "—";
  ui["work-order-principal"].value = policy.principal_id || "lab-agent-01";
  ui["trusted-issuer"].textContent =
    (runtime.trusted_work_order_issuers || []).join("、") || "—";
  ui["work-order-facts"].textContent =
    configuration.work_order_fact_mode === "none"
      ? "本轮未启用（感知适配器可插拔）"
      : "camera.healthy ≤ 1.5s";
  ui["config-line-state"].textContent = line.line_state || "UNKNOWN";

  const unsafe = line.execution_mode === "unsafe-baseline";
  ui["path-chip"].textContent = unsafe ? "无保护对照" : "Motion Gate";
  ui["path-chip"].classList.toggle("unsafe", unsafe);
  ui["path-description"].textContent = unsafe
    ? "Agent 动作绕过 Runtime、Lease 与 Guard，直接到达 Legacy Bridge。"
    : "动作经过 Runtime、一次性 Lease 与设备侧 Guard。";
  ui["protected-mode-btn"].setAttribute("aria-pressed", String(!unsafe));
  ui["unsafe-mode-btn"].setAttribute("aria-pressed", String(unsafe));
  const canChangeMode = Boolean(line.controls?.mode);
  const unsafeAvailable = Boolean(line.execution_modes?.["unsafe-baseline"]?.available);
  ui["protected-mode-btn"].disabled = !canChangeMode || !unsafe;
  ui["unsafe-mode-btn"].disabled = !canChangeMode || unsafe || !unsafeAvailable;
  ui["unsafe-mode-token"].disabled = !canChangeMode || unsafe || !unsafeAvailable;

  const canIssue = line.line_state === "STOPPED" && Boolean(issuer);
  ui["issue-work-order-btn"].disabled = !canIssue;
  document.querySelectorAll("#work-order-form input, #work-order-form select, #work-order-form textarea")
    .forEach(control => {
      if (control.id !== "work-order-principal") control.disabled = !canIssue;
    });

  const activeId = configuration.active_work_order_id;
  const active = (configuration.work_orders || [])
    .find(order => order.work_order_id === activeId);
  renderActiveOrder(active);
}

function renderActiveOrder(order) {
  const hasOrder = Boolean(order);
  ui["empty-order"].hidden = hasOrder;
  ui["order-details"].hidden = !hasOrder;
  ui["order-verification"].textContent = hasOrder ? "签名已验证" : "等待工单";
  if (!order) return;
  ui["active-order-id"].textContent = shortId(order.work_order_id);
  ui["active-order-id"].title = order.work_order_id;
  ui["active-order-issuer"].textContent = order.issuer_id;
  ui["active-order-subject"].textContent = order.subject_principal_id;
  ui["active-order-expiry"].textContent =
    new Date(order.valid_until_ms).toLocaleString();
  const rows = (order.grants || []).map(grant => {
    const row = document.createElement("tr");
    const count = order.execution_counts?.[grant.grant_id] || 0;
    const cells = [
      grant.resource?.id,
      grant.action,
      `${grant.arguments?.source} → ${grant.arguments?.destination}`,
      `${count} / ${grant.max_executions}`,
    ];
    for (const value of cells) {
      const cell = document.createElement("td");
      cell.textContent = String(value || "—");
      row.append(cell);
    }
    return row;
  });
  ui["grant-table-body"].replaceChildren(...rows);
}

async function issueWorkOrder() {
  const sampleIds = [...document.querySelectorAll('input[name="sample"]:checked')]
    .map(input => input.value);
  if (!sampleIds.length) {
    showNotice("至少选择一个样品", true);
    return;
  }
  const body = {
    schema_version: "safeexec.work-order-draft.v1",
    sample_ids: sampleIds,
    source: ui["work-order-source"].value,
    destination: ui["work-order-destination"].value,
    subject_principal_id: ui["work-order-principal"].value,
    valid_for_ms: Number(ui["work-order-validity"].value),
    operator_note: ui["work-order-note"].value.trim(),
  };
  ui["issue-work-order-btn"].disabled = true;
  ui["issue-work-order-btn"].textContent = "正在签名";
  try {
    const result = await request("/api/work-orders", {
      method: "POST",
      body: JSON.stringify(body),
    });
    await loadConfiguration();
    showNotice(`可信工单 ${shortId(result.work_order?.work_order_id)} 已验签并激活`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    ui["issue-work-order-btn"].textContent = "签名并激活工单";
    ui["issue-work-order-btn"].disabled =
      configuration?.line?.line_state !== "STOPPED";
  }
}

async function setExecutionMode(mode) {
  const unsafe = mode === "unsafe-baseline";
  const token = ui["unsafe-mode-token"].value;
  if (unsafe && !token) {
    showNotice("请输入对照演示 Token", true);
    ui["unsafe-mode-token"].focus();
    return;
  }
  if (unsafe && !window.confirm(
    "无保护模式会让 Agent 动作绕过 Motion Gate。确认仅用于隔离的 A/B 演示？"
  )) return;
  try {
    await request("/api/control/mode", {
      method: "POST",
      body: JSON.stringify({mode}),
      headers: {"X-Unsafe-Demo-Token": token},
    });
    ui["unsafe-mode-token"].value = "";
    await loadConfiguration();
    showNotice(unsafe ? "已切换到无保护对照" : "已恢复 Motion Gate 保护");
  } catch (error) {
    showNotice(error.message, true);
  }
}

function bind() {
  ui["work-order-form"].addEventListener("submit", event => {
    event.preventDefault();
    issueWorkOrder();
  });
  ui["protected-mode-btn"].addEventListener(
    "click", () => setExecutionMode("protected")
  );
  ui["unsafe-mode-btn"].addEventListener(
    "click", () => setExecutionMode("unsafe-baseline")
  );
}

async function init() {
  bind();
  try {
    await loadConfiguration();
  } catch (error) {
    showNotice(`配置服务未连接：${error.message}`, true);
  }
}

init();
