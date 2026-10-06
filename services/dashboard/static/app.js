// Hosts are derived from the page address so the dashboard works from any machine.
const HOST = window.location.hostname || "localhost";
const WS_PROTO = window.location.protocol === "https:" ? "wss" : "ws";
const SMART_CONTROLLER_URL = `${window.location.protocol}//${HOST}:8082`;
const SMART_CONTROLLER_WS = `${WS_PROTO}://${HOST}:8082/ws/telemetry`;
const BUILDSIM_URL = `${window.location.protocol}//${HOST}:9090`;
const ACTUATOR_URL = `${window.location.protocol}//${HOST}:8080`;
const INGESTOR_URL = `${window.location.protocol}//${HOST}:8081`;

const REST_FALLBACK_MS = 2000;
const STALE_AFTER_MS = 5000;

let tempChart = null;
let mpcChart = null;
const timeLabels = [];
const smartTempData = [];
const baseTempData = [];
let lastLoggedReason = "";
let lastOccupancy = 0;

let socket = null;
let reconnectDelayMs = 1000;
let restTimer = null;
let lastUpdateMs = 0;
let transport = "connecting";

function initCharts() {
  tempChart = new Chart(document.getElementById('tempChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: timeLabels,
      datasets: [
        { label: 'Smart Temp (°C)', data: smartTempData, borderColor: '#38bdf8', backgroundColor: 'rgba(56, 189, 248, 0.1)', borderWidth: 2, tension: 0.3, fill: true },
        { label: 'Baseline Twin Temp (°C)', data: baseTempData, borderColor: '#f59e0b', borderDash: [4, 4], borderWidth: 2, tension: 0.3, fill: false }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: {
        y: { min: 14, max: 28, grid: { color: 'rgba(255,255,255,0.06)' }, ticks: { color: '#94a3b8' } },
        x: { grid: { display: false }, ticks: { color: '#94a3b8', maxTicksLimit: 8 } }
      },
      plugins: { legend: { labels: { color: '#cbd5e1' } } }
    }
  });

  mpcChart = new Chart(document.getElementById('mpcChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: ['+5m', '+10m', '+15m', '+20m', '+25m', '+30m'],
      datasets: [
        { label: 'Model 02 predicted temp (°C)', data: [], backgroundColor: 'rgba(56, 189, 248, 0.6)', borderColor: '#38bdf8', borderWidth: 1, yAxisID: 'y' },
        { type: 'line', label: 'Model 01 forecast occupancy', data: [], borderColor: '#22c55e', backgroundColor: '#22c55e', borderWidth: 2, yAxisID: 'occ' },
        { type: 'line', label: 'Actual occupancy now', data: [], borderColor: '#f59e0b', borderDash: [4, 4], pointRadius: 0, borderWidth: 2, yAxisID: 'occ' }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: {
        y: { min: 14, max: 26, grid: { color: 'rgba(255,255,255,0.06)' }, ticks: { color: '#94a3b8' } },
        occ: { position: 'right', min: 0, suggestedMax: 25, grid: { display: false }, ticks: { color: '#22c55e' } },
        x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
      },
      plugins: { legend: { labels: { color: '#cbd5e1', boxWidth: 12 } } }
    }
  });
}

function fmt(value, digits, suffix) {
  return (value === null || value === undefined || Number.isNaN(value)) ? `--${suffix}` : `${Number(value).toFixed(digits)}${suffix}`;
}

function renderComparison(data) {
  const smart = data.smart_controller || {};
  const base = data.baseline_controller || {};
  const bench = data.benchmarks || {};
  const action = smart.current_action || {};

  document.getElementById('savings-pct').innerText = fmt(bench.energy_savings_percentage, 1, '%');
  document.getElementById('savings-kwh').innerText = `${fmt(bench.energy_saved_kwh, 4, '')} kWh saved · smart ${fmt(smart.cumulative_energy_kwh, 4, '')} vs baseline ${fmt(base.cumulative_energy_kwh, 4, '')} kWh`;
  document.getElementById('current-temp').innerText = fmt(smart.current_temp, 1, '°C');
  document.getElementById('current-setpoint').innerText = fmt(smart.applied_setpoint ?? action.setpoint, 1, '°C');
  document.getElementById('current-co2').innerText = fmt(smart.current_co2, 0, ' ppm');
  document.getElementById('current-damper').innerText = `Level ${smart.applied_damper ?? action.damper ?? '--'}`;
  lastOccupancy = smart.current_occupancy ?? 0;
  document.getElementById('current-occ').innerText = `${lastOccupancy} persons`;
  document.getElementById('current-power').innerText = fmt(smart.current_power_watts, 0, ' W');

  const modeBadge = document.getElementById('mode-badge');
  const age = data.telemetry_age_s;
  if (data.mode === 'SAFE_FALLBACK') {
    modeBadge.className = 'badge badge-red';
    modeBadge.innerText = `SAFE FALLBACK · telemetry ${age === null ? 'missing' : age + 's old'}`;
  } else {
    modeBadge.className = 'badge badge-green';
    modeBadge.innerText = `MPC active · telemetry ${age === null ? '--' : age + 's old'}`;
  }

  const now = new Date().toLocaleTimeString();
  timeLabels.push(now);
  smartTempData.push(smart.current_temp);
  baseTempData.push(base.simulated_temp);
  if (timeLabels.length > 60) {
    timeLabels.shift();
    smartTempData.shift();
    baseTempData.shift();
  }
  tempChart.update('none');

  const reason = action.reason || "";
  if (reason && reason !== lastLoggedReason) {
    lastLoggedReason = reason;
    const logContainer = document.getElementById('decision-log');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const time = document.createElement('span');
    time.className = 'log-time';
    time.innerText = `[${now}] `;
    entry.appendChild(time);
    entry.appendChild(document.createTextNode(reason));
    logContainer.insertBefore(entry, logContainer.firstChild);
    while (logContainer.children.length > 100) logContainer.removeChild(logContainer.lastChild);
  }
}

function renderTrajectory(data) {
  const plan = data.latest_plan || {};
  const traj = plan.trajectory || [];
  mpcChart.data.datasets[0].data = traj.map(t => t.predicted_temp);
  mpcChart.data.datasets[1].data = traj.map(t => t.occupancy);
  mpcChart.data.datasets[2].data = traj.map(() => lastOccupancy);
  mpcChart.update('none');
}

function setTransport(state) {
  transport = state;
  const badge = document.getElementById('transport-badge');
  const labels = {
    websocket: ['badge badge-green', 'Live: WebSocket push'],
    rest: ['badge badge-amber', 'Fallback: REST polling'],
    connecting: ['badge badge-blue', 'Connecting…'],
  };
  const [cls, text] = labels[state];
  badge.className = cls;
  badge.innerText = text;
}

function handleSnapshot(comparison, trajectory) {
  lastUpdateMs = Date.now();
  if (comparison) renderComparison(comparison);
  if (trajectory) renderTrajectory(trajectory);
}

// --- WebSocket transport with automatic reconnect -------------------------------
function connectWebSocket() {
  setTransport(transport === 'rest' ? 'rest' : 'connecting');
  try {
    socket = new WebSocket(SMART_CONTROLLER_WS);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  socket.onopen = () => {
    reconnectDelayMs = 1000;
    stopRestFallback();
    setTransport('websocket');
  };
  socket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'snapshot') handleSnapshot(msg.comparison, msg.trajectory);
    } catch (e) {
      console.warn('Bad WebSocket frame', e);
    }
  };
  socket.onclose = () => {
    socket = null;
    startRestFallback();
    scheduleReconnect();
  };
  socket.onerror = () => {
    if (socket) socket.close();
  };
}

function scheduleReconnect() {
  setTimeout(connectWebSocket, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, 15000);
}

// --- REST fallback while the WebSocket is down ----------------------------------
async function pollRest() {
  try {
    const [c, t] = await Promise.all([
      fetch(`${SMART_CONTROLLER_URL}/api/comparison`).then(r => r.ok ? r.json() : null),
      fetch(`${SMART_CONTROLLER_URL}/api/mpc/trajectory`).then(r => r.ok ? r.json() : null),
    ]);
    if (c || t) handleSnapshot(c, t);
  } catch (err) {
    console.warn("REST fallback fetch error:", err);
  }
}

function startRestFallback() {
  if (restTimer) return;
  setTransport('rest');
  pollRest();
  restTimer = setInterval(pollRest, REST_FALLBACK_MS);
}

function stopRestFallback() {
  if (restTimer) clearInterval(restTimer);
  restTimer = null;
}

// Mark the view as stale if nothing arrived recently over either transport.
setInterval(() => {
  const stale = lastUpdateMs && Date.now() - lastUpdateMs > STALE_AFTER_MS;
  document.getElementById('stale-banner').style.display = stale ? 'block' : 'none';
}, 1000);

// --- Interactive controls -------------------------------------------------------
async function setOccupancy(num) {
  const persons = [];
  for (let i = 1; i <= num; i++) persons.push({ id: `p${i}`, name: `Student ${i}` });
  try {
    const res = await fetch(`${BUILDSIM_URL}/api/occupancy`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ "level0/A109": { persons, aliens: [] } })
    });
    alert(res.ok ? `Set Room A109 occupancy to ${num}. Watch the MPC react within one control cycle.` : `BuildSim returned HTTP ${res.status}`);
  } catch (e) {
    alert(`Failed to set occupancy: ${e}`);
  }
}

async function testGuardrail() {
  try {
    const res = await fetch(`${ACTUATOR_URL}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "A109", setpoint: 45.0, reason: "Manual guardrail test (45°C request)" })
    });
    const body = res.ok ? await res.json() : await res.text();
    alert(res.ok ? `Safety guardrail response: ${body.message}` : `Actuator returned HTTP ${res.status}: ${body}`);
  } catch (e) {
    alert(`Guardrail test failed: ${e}`);
  }
}

async function resetBenchmarks() {
  try {
    const res = await fetch(`${SMART_CONTROLLER_URL}/api/benchmarks/reset`, { method: "POST" });
    if (res.ok) alert("Benchmarks reset; the baseline twin was re-synchronised with the room.");
  } catch (e) {
    alert(`Failed to reset: ${e}`);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('link-buildsim').href = BUILDSIM_URL;
  document.getElementById('link-ingestor').href = `${INGESTOR_URL}/api/stats?room=A109`;
  initCharts();
  connectWebSocket();
});
