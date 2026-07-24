// SafeExec V1 Dashboard — Robot Action Firewall
// Polls /api/dashboard/v1 and renders all 5 areas

const els = {
  // Header
  scenarioName: document.querySelector("#scenario-name"),
  resultBanner: document.querySelector("#result-banner"),
  resultIcon: document.querySelector("#result-icon"),
  resultTitle: document.querySelector("#result-title"),
  resultDetail: document.querySelector("#result-detail"),
  modeBadge: document.querySelector("#mode-badge"),
  runId: document.querySelector("#run-id"),
  // P1
  inputTask: document.querySelector("#input-task"),
  inputUntrusted: document.querySelector("#input-untrusted"),
  // P2
  badgeCompromised: document.querySelector("#badge-compromised"),
  agentSample: document.querySelector("#agent-sample"),
  agentSource: document.querySelector("#agent-source"),
  agentDest: document.querySelector("#agent-dest"),
  agentDestChip: document.querySelector("#agent-dest-chip"),
  agentFingerprint: document.querySelector("#agent-fingerprint"),
  // P3 Chain
  chainAgent: document.querySelector("#chain-agent"),
  chainAgentStatus: document.querySelector("#chain-agent-status"),
  chainRuntime: document.querySelector("#chain-runtime"),
  chainRuntimeStatus: document.querySelector("#chain-runtime-status"),
  chainRuntimeSub: document.querySelector("#chain-runtime-sub"),
  chainLease: document.querySelector("#chain-lease"),
  chainLeaseStatus: document.querySelector("#chain-lease-status"),
  chainLeaseSub: document.querySelector("#chain-lease-sub"),
  chainGuard: document.querySelector("#chain-guard"),
  chainGuardStatus: document.querySelector("#chain-guard-status"),
  chainGuardSub: document.querySelector("#chain-guard-sub"),
  chainJoy: document.querySelector("#chain-joy"),
  chainJoyStatus: document.querySelector("#chain-joy-status"),
  chainJoySub: document.querySelector("#chain-joy-sub"),
  // P4 Physical
  unsafeDot: document.querySelector("#unsafe-dot"),
  unsafeLabel: document.querySelector("#unsafe-label"),
  physArm: document.querySelector("#phys-arm"),
  physPlatform: document.querySelector("#phys-platform"),
  physDock: document.querySelector("#phys-dock"),
  sampleALoc: document.querySelector("#sample-a-loc"),
  sampleAMarker: document.querySelector("#sample-a-bar"),
  sampleBLoc: document.querySelector("#sample-b-loc"),
  sampleBMarker: document.querySelector("#sample-b-bar"),
  // P5 Timeline
  timelineList: document.querySelector("#timeline-list"),
  // A/B
  abRedResult: document.querySelector("#ab-red-result"),
  abRedDetail: document.querySelector("#ab-red-detail"),
  abBlueResult: document.querySelector("#ab-blue-result"),
  abBlueDetail: document.querySelector("#ab-blue-detail"),
};

// ─── Init ──────────────────────────────────────────────────────
async function init() {
  bindEvents();
  await refresh();
  setInterval(refresh, 800);
}

// ─── Events ─────────────────────────────────────────────────────
function bindEvents() {
  document.querySelector(".scenario-selector").addEventListener("click", async e => {
    const btn = e.target.closest("[data-scenario]");
    if (!btn) return;
    document.querySelectorAll(".scenario-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    await fetch("/api/dashboard/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: btn.dataset.scenario }),
    });
    await refresh();
  });
}

// ─── Refresh ────────────────────────────────────────────────────
async function refresh() {
  try {
    const res = await fetch("/api/dashboard/v1", { cache: "no-store" });
    const data = await res.json();
    renderDashboard(data);
  } catch (err) {
    console.error("刷新失败:", err);
  }
}

// ─── Render: Full Dashboard ─────────────────────────────────────
function renderDashboard(d) {
  renderHeader(d);
  renderInput(d);
  renderAgent(d);
  renderChain(d);
  renderPhysical(d);
  renderTimeline(d);
  renderComparison(d);
}

// ─── Render: Header ─────────────────────────────────────────────
function renderHeader(d) {
  els.scenarioName.textContent = d.scenario === "prompt-injection"
    ? "提示词注入攻击" : "正常作业";
  els.runId.textContent = d.run_id || "—";

  // Result banner
  const state = d.result?.state || "pending";
  els.resultBanner.className = `result-banner ${state}`;
  const iconMap = {
    blocked: "🛡️",
    executed: "⚠️",
    allowed: "✅",
    pending: "⏳",
  };
  els.resultIcon.textContent = iconMap[state] || "—";
  els.resultTitle.textContent = d.result?.title || "—";
  els.resultDetail.textContent = d.result?.detail || "—";
}

// ─── Render: P1 — Input Evidence ────────────────────────────────
function renderInput(d) {
  els.inputTask.textContent = d.input?.operator_task || "—";
  const untrusted = d.input?.untrusted_content || "";
  els.inputUntrusted.textContent = untrusted || "（无不可信内容）";
}

// ─── Render: P2 — Agent Intent ─────────────────────────────────
function renderAgent(d) {
  const agent = d.agent || {};
  els.agentSample.textContent = agent.sample_id || "—";
  els.agentSource.textContent = agent.source || "—";
  els.agentDest.textContent = agent.destination || "—";
  els.agentFingerprint.textContent = agent.semantic_fingerprint || "—";

  const compromised = !!agent.compromised;
  els.badgeCompromised.classList.toggle("show", compromised);
  els.agentDestChip.classList.toggle("danger", compromised);
}

// ─── Render: P3 — Execution Chain ───────────────────────────────
function renderChain(d) {
  const agent = d.agent || {};
  const runtime = d.runtime || {};
  const lease = d.lease || {};
  const guard = d.guard || {};
  const result = d.result || {};

  // Agent — always "passed" when there's a result
  setChainNode(els.chainAgent, els.chainAgentStatus, null,
    result.state !== "pending" ? "SUBMITTED" : "PENDING",
    agent.requested_action || "—");

  // Runtime
  if (runtime.effect === "allow") {
    setChainNode(els.chainRuntime, els.chainRuntimeStatus, "state-passed",
      "ALLOWED", runtime.reason_code || "GRANT_MATCHED");
    setChainNode(els.chainLease, els.chainLeaseStatus, "state-passed",
      "ISSUED", lease.lease_id ? lease.lease_id.slice(0, 12) + "…" : "—");
  } else if (runtime.effect === "deny") {
    setChainNode(els.chainRuntime, els.chainRuntimeStatus, "state-blocked",
      "DENIED", runtime.reason_code || "—");
    setChainNode(els.chainLease, els.chainLeaseStatus, "state-pending",
      "NOT ISSUED", "—");
  } else {
    setChainNode(els.chainRuntime, els.chainRuntimeStatus, "state-pending",
      "PENDING", "—");
    setChainNode(els.chainLease, els.chainLeaseStatus, "state-pending",
      "PENDING", "—");
  }

  // Guard
  if (guard.verification === "passed") {
    setChainNode(els.chainGuard, els.chainGuardStatus, "state-passed",
      "VERIFIED", "signature ok");
  } else if (guard.verification === "blocked") {
    setChainNode(els.chainGuard, els.chainGuardStatus, "state-blocked",
      "BLOCKED", guard.executor_called === false ? "no execute" : "BLOCKED");
  } else if (guard.reached) {
    setChainNode(els.chainGuard, els.chainGuardStatus, "state-reached",
      "REACHED", "—");
  } else {
    setChainNode(els.chainGuard, els.chainGuardStatus, "state-pending",
      "NOT REACHED", "—");
  }

  // JOY
  if (result.state === "executed") {
    setChainNode(els.chainJoy, els.chainJoyStatus, "state-danger",
      "EXECUTED", "physical changed");
  } else if (result.state === "blocked") {
    setChainNode(els.chainJoy, els.chainJoyStatus, "state-passed",
      "IDLE", "blocked by guard");
  } else if (guard.executor_called) {
    setChainNode(els.chainJoy, els.chainJoyStatus, "state-reached",
      "EXECUTING", "in progress");
  } else {
    setChainNode(els.chainJoy, els.chainJoyStatus, "state-pending",
      "IDLE", "no request");
  }
}

function setChainNode(node, statusEl, stateClass, label, sub) {
  node.className = `chain-node ${stateClass || ""}`;
  statusEl.textContent = label;
  const subEl = node.querySelector(".chain-sub");
  if (subEl) subEl.textContent = sub || "";
}

// ─── Render: P4 — Physical State ───────────────────────────────
function renderPhysical(d) {
  const phys = d.physical || {};

  els.physArm.textContent = phys.arm_state || "—";
  els.physPlatform.textContent = phys.platform_state || "—";
  els.physDock.textContent = phys.current_dock || "—";

  const unsafe = !!phys.unsafe_outcome;
  els.unsafeDot.className = `dot ${unsafe ? "danger" : "safe"}`;
  els.unsafeLabel.className = unsafe ? "danger" : "";
  els.unsafeLabel.textContent = unsafe ? "危险" : "安全";

  // Sample A
  const locA = phys.sample_locations?.["sample-A"] || "cold-storage";
  els.sampleALoc.textContent = locA;
  els.sampleAMarker.className = `sample-marker ${locA === "waste-bin" ? "waste-bin" : locA === "analyzer-01" ? "analyzer" : ""}`;

  // Sample B
  const locB = phys.sample_locations?.["sample-B"] || "cold-storage";
  els.sampleBLoc.textContent = locB;
  els.sampleBMarker.className = `sample-marker ${locB === "waste-bin" ? "waste-bin" : locB === "analyzer-01" ? "analyzer" : ""}`;
}

// ─── Render: P5 — Timeline ───────────────────────────────────────
function renderTimeline(d) {
  const entries = d.timeline || [];
  if (!entries.length) {
    els.timelineList.innerHTML = '<div class="timeline-empty">等待事件…</div>';
    return;
  }
  els.timelineList.innerHTML = entries.map(e => {
    const time = new Date(e.at_ms || 0).toLocaleTimeString();
    const statusClass = e.status || "info";
    return `
      <div class="timeline-item status-${statusClass}">
        <span class="timeline-time">${time}</span>
        <span class="timeline-source ${e.source || "system"}">${(e.source || "sys").toUpperCase()}</span>
        <span class="timeline-message">${e.message || e.event || ""}</span>
      </div>
    `;
  }).join("");
}

// ─── Render: A/B Comparison ─────────────────────────────────────
function renderComparison(d) {
  const comp = d.comparison || {};

  // Red baseline
  const unprotected = comp.unprotected || {};
  const upResult = unprotected.result || "—";
  const upClass = upResult.includes("unsafe") ? 'color:var(--neon-red)' :
                  upResult.includes("safe")   ? 'color:var(--neon-green)' : '';
  els.abRedResult.textContent = upResult.replace(/_/g, " ");
  els.abRedResult.style.cssText = upClass;
  els.abRedDetail.textContent = `sample-A → ${unprotected.sample_A_final || "—"}`;

  // Blue SafeExec
  const protected_ = comp.protected || {};
  const pResult = protected_.result || "—";
  const pClass = pResult === "blocked" ? 'color:var(--neon-blue)' :
                 pResult === "executed" ? 'color:var(--neon-red)' :
                 pResult === "allowed" ? 'color:var(--neon-green)' : '';
  els.abBlueResult.textContent = pResult.replace(/_/g, " ");
  els.abBlueResult.style.cssText = pClass;
  els.abBlueDetail.textContent = `sample-A → ${protected_.sample_A_final || "—"}`;
}

// ─── Boot ───────────────────────────────────────────────────────
init();
