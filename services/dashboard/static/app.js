// Configuration
const SMART_CONTROLLER_URL = "http://localhost:8082";
const BUILDSIM_URL = "http://localhost:9090";

// Chart instances
let tempChart = null;
let mpcChart = null;

const timeLabels = [];
const smartTempData = [];
const baseTempData = [];

let lastLoggedReason = "";

function initCharts() {
  const ctxTemp = document.getElementById('tempChart').getContext('2d');
  tempChart = new Chart(ctxTemp, {
    type: 'line',
    data: {
      labels: timeLabels,
      datasets: [
        {
          label: 'Smart Temp (°C)',
          data: smartTempData,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.1)',
          borderWidth: 2,
          tension: 0.3,
          fill: true
        },
        {
          label: 'Baseline Temp (°C)',
          data: baseTempData,
          borderColor: '#f59e0b',
          borderDash: [4, 4],
          borderWidth: 2,
          tension: 0.3,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          min: 16,
          max: 26,
          grid: { color: 'rgba(255,255,255,0.06)' },
          ticks: { color: '#94a3b8' }
        },
        x: {
          grid: { display: false },
          ticks: { color: '#94a3b8', maxTicksLimit: 8 }
        }
      },
      plugins: {
        legend: { labels: { color: '#cbd5e1' } }
      }
    }
  });

  const ctxMpc = document.getElementById('mpcChart').getContext('2d');
  mpcChart = new Chart(ctxMpc, {
    type: 'bar',
    data: {
      labels: ['+5m', '+10m', '+15m', '+20m', '+25m', '+30m'],
      datasets: [
        {
          label: 'Predicted Temp (°C)',
          data: [21, 21, 21, 21, 21, 21],
          backgroundColor: 'rgba(56, 189, 248, 0.6)',
          borderColor: '#38bdf8',
          borderWidth: 1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          min: 18,
          max: 26,
          grid: { color: 'rgba(255,255,255,0.06)' },
          ticks: { color: '#94a3b8' }
        },
        x: {
          grid: { display: false },
          ticks: { color: '#94a3b8' }
        }
      },
      plugins: {
        legend: { labels: { color: '#cbd5e1' } }
      }
    }
  });
}

async function fetchTelemetry() {
  try {
    const res = await fetch(`${SMART_CONTROLLER_URL}/api/comparison`);
    if (!res.ok) return;
    const data = await res.json();

    const smart = data.smart_controller || {};
    const base = data.baseline_controller || {};
    const bench = data.benchmarks || {};

    // Update KPIs
    document.getElementById('savings-pct').innerText = `${bench.energy_savings_percentage?.toFixed(1) || '--'}%`;
    document.getElementById('savings-kwh').innerText = `${bench.energy_saved_kwh?.toFixed(4) || '0'} kWh saved`;
    document.getElementById('current-temp').innerText = `${smart.current_temp?.toFixed(1) || '--'}°C`;
    document.getElementById('current-setpoint').innerText = `${smart.current_action?.setpoint?.toFixed(1) || '--'}°C`;
    document.getElementById('current-co2').innerText = `${smart.current_co2?.toFixed(0) || '--'} ppm`;
    document.getElementById('current-damper').innerText = `Level ${smart.current_action?.damper ?? '--'}`;
    document.getElementById('current-occ').innerText = `${smart.current_occupancy ?? 0} persons`;
    document.getElementById('current-power').innerText = `${smart.current_power_watts?.toFixed(0) || '--'} W`;

    // Update Temp Chart
    const now = new Date().toLocaleTimeString();
    timeLabels.push(now);
    smartTempData.push(smart.current_temp);
    baseTempData.push(base.simulated_temp);

    if (timeLabels.length > 20) {
      timeLabels.shift();
      smartTempData.shift();
      baseTempData.shift();
    }
    tempChart.update('none');

    // Update Decision Log
    const reason = smart.current_action?.reason || "";
    if (reason && reason !== lastLoggedReason) {
      lastLoggedReason = reason;
      const logContainer = document.getElementById('decision-log');
      const entry = document.createElement('div');
      entry.className = 'log-entry';
      entry.innerHTML = `<span class="log-time">[${now}]</span> ${reason}`;
      logContainer.insertBefore(entry, logContainer.firstChild);
    }
  } catch (err) {
    console.warn("Telemetry fetch error:", err);
  }
}

async function fetchMpcTrajectory() {
  try {
    const res = await fetch(`${SMART_CONTROLLER_URL}/api/mpc/trajectory`);
    if (!res.ok) return;
    const data = await res.json();
    const traj = data.latest_plan?.trajectory || [];
    if (traj.length > 0) {
      mpcChart.data.datasets[0].data = traj.map(t => t.predicted_temp);
      mpcChart.update('none');
    }
  } catch (err) {
    console.warn("Trajectory fetch error:", err);
  }
}

async function setOccupancy(num) {
  const persons = [];
  for (let i = 1; i <= num; i++) {
    persons.push({ id: `p${i}`, name: `Student ${i}` });
  }

  const payload = {
    "level0/A109": {
      "persons": persons,
      "aliens": []
    }
  };

  try {
    const res = await fetch(`${BUILDSIM_URL}/api/occupancy`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      alert(`Successfully injected ${num} occupants into Room A109! Watch the MPC controller react.`);
    }
  } catch (e) {
    alert(`Failed to set occupancy: ${e}`);
  }
}

function testChaos() {
  alert("Running chaos test: Simulating an out-of-bounds 45°C thermal injection to verify Simplex safety clamping!");
  fetch("http://localhost:8080/commands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room: "A109", setpoint: 45.0, reason: "Manual Chaos Injection" })
  }).then(r => r.json()).then(d => {
    alert(`Guardrail response: ${d.message} (Successfully clamped to safe 28°C max!)`);
  });
}

async function resetBenchmarks() {
  try {
    const res = await fetch(`${SMART_CONTROLLER_URL}/api/benchmarks/reset`, { method: "POST" });
    if (res.ok) {
      alert("Benchmarks successfully reset! Cumulative kWh counters are now at 0.00.");
      fetchTelemetry();
    }
  } catch (e) {
    alert(`Failed to reset: ${e}`);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  initCharts();
  fetchTelemetry();
  fetchMpcTrajectory();
  setInterval(fetchTelemetry, 1500);
  setInterval(fetchMpcTrajectory, 5000);
});

