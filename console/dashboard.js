const stateValue = document.querySelector("#state-value");
const leaseValue = document.querySelector("#lease-value");
const machineValue = document.querySelector("#machine-value");
const factsList = document.querySelector("#facts-list");
const eventsList = document.querySelector("#events-list");
const machineStage = document.querySelector("#machine-stage");
const machineLabel = document.querySelector("#machine-label");
const leaseTrack = document.querySelector("#lease-track");
const cameraBadge = document.querySelector("#camera-badge");
const personBox = document.querySelector("#person-box");
const liveFrame = document.querySelector("#live-frame");
const visionMessage = document.querySelector("#vision-message");
let lastSeq = 0;
let frameTimes = [];

function valueClass(value) {
  if (value === true) return "ok";
  if (value === false) return "bad";
  return "unknown";
}

function valueLabel(value) {
  if (value === true) return "TRUE";
  if (value === false) return "FALSE";
  return "UNKNOWN";
}

function renderState(state) {
  stateValue.textContent = state.status;
  leaseValue.textContent = state.lease_remaining_ms
    ? `${state.lease_remaining_ms}ms`
    : "NONE";
  machineValue.textContent = state.machine;
  machineLabel.textContent = state.machine;
  machineStage.className = `machine-stage ${state.machine.toLowerCase()}`;
  leaseTrack.style.width = `${Math.min(100, state.lease_remaining_ms / 20)}%`;

  factsList.innerHTML = state.facts
    .map(
      (fact) => `
        <div class="fact">
          <strong>${fact.key}</strong>
          <b class="${valueClass(fact.value)}">${valueLabel(fact.value)}</b>
          <small>${fact.source} · TTL ${fact.ttl_ms}ms</small>
        </div>`,
    )
    .join("");

  const camera = state.facts.find((fact) => fact.key === "camera.healthy");
  cameraBadge.textContent = camera ? valueLabel(camera.value) : "NO FACT";
  cameraBadge.style.color =
    camera?.value === true ? "var(--green)" : camera?.value === false ? "var(--red)" : "var(--muted)";

  const zone = state.facts.find((fact) => fact.key === "zone.clear");
  personBox.style.display = zone?.value === false ? "block" : "none";
}

function appendEvents(events) {
  for (const event of events) {
    lastSeq = Math.max(lastSeq, event.seq);
    const item = document.createElement("li");
    const time = new Date(event.timestamp * 1000);
    item.className = event.severity;
    item.innerHTML = `<time>${time.toLocaleTimeString()}</time><span>${event.event} · ${event.source}</span>`;
    eventsList.prepend(item);
  }
  while (eventsList.children.length > 40) {
    eventsList.lastElementChild.remove();
  }
}

async function refresh() {
  try {
    const [stateResponse, eventsResponse] = await Promise.all([
      fetch("/api/state", { cache: "no-store" }),
      fetch(`/api/events?after=${lastSeq}`, { cache: "no-store" }),
    ]);
    renderState(await stateResponse.json());
    appendEvents((await eventsResponse.json()).events);
  } catch {
    stateValue.textContent = "DISCONNECTED";
    stateValue.style.color = "var(--red)";
  }
}

for (const button of document.querySelectorAll("[data-scenario]")) {
  button.addEventListener("click", async () => {
    await fetch("/api/mock/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: button.dataset.scenario }),
    });
    await refresh();
  });
}

function refreshFrame() {
  liveFrame.src = `/api/frame?t=${Date.now()}`;
}

liveFrame.addEventListener("load", () => {
  liveFrame.classList.add("live");
  const now = performance.now();
  frameTimes.push(now);
  frameTimes = frameTimes.filter((time) => now - time <= 1000);
  visionMessage.textContent = `RDK X5 · LIVE · ${frameTimes.length} FPS`;
});

liveFrame.addEventListener("error", () => {
  liveFrame.classList.remove("live");
  visionMessage.textContent = "等待X5视频适配器";
});

refresh();
setInterval(refresh, 500);
refreshFrame();
setInterval(refreshFrame, 67);
