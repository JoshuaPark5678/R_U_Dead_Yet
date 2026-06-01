const SPIKE_THRESHOLD = 120;
const MAX_POINTS = 60;

let readings = [];

// Chart setup
const ctx = document.getElementById("bpm-chart").getContext("2d");
const chart = new Chart(ctx, {
    type: "line",
    data: {
        labels: [],
        datasets: [{
            label: "BPM",
            data: [],
            borderColor: "red",
            fill: false,
        }]
    },
    options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { min: 40, max: 160 } }
    }
});

function updateStats() {
    if (!readings.length) return;
    const bpms = readings.map(r => r.bpm);
    document.getElementById("bpm-current").textContent = bpms.at(-1);
    document.getElementById("bpm-avg").textContent = Math.round(bpms.reduce((a, b) => a + b, 0) / bpms.length);
    document.getElementById("bpm-peak").textContent = Math.max(...bpms);
    const activity = readings.at(-1)?.activity;
    if (activity != null && activity !== "") {
        document.getElementById("activity-status").textContent = activity;
    }
}

function pushToChart(reading) {
    chart.data.labels.push(reading.timestamp);
    chart.data.datasets[0].data.push(reading.bpm);
    if (chart.data.labels.length > MAX_POINTS) {
        chart.data.labels.shift();
        chart.data.datasets[0].data.shift();
    }
    chart.update("none");
}

function addAlert(msg, ts) {
    const li = document.createElement("li");
    li.textContent = `[${ts}] ${msg}`;
    document.getElementById("alert-list").prepend(li);
}

function addChatMessage(user, ai) {
    const log = document.getElementById("chat-log");

    const userDiv = document.createElement("div");
    userDiv.style.cssText = "text-align: right;";
    userDiv.innerHTML = `<span style="background:#e0e0e0; padding: 6px 10px; border-radius: 12px; display: inline-block; max-width: 85%;">${user}</span>`;

    const aiDiv = document.createElement("div");
    aiDiv.style.cssText = "text-align: left;";
    aiDiv.innerHTML = `<span style="background:#ff746c; color: white; padding: 6px 10px; border-radius: 12px; display: inline-block; max-width: 85%;">${ai}</span>`;

    log.appendChild(userDiv);
    log.appendChild(aiDiv);

    // Auto-scroll to bottom
    log.scrollTop = log.scrollHeight;
}

// SocketIO
const socket = io();

socket.on("connect", () => socket.emit("request_history"));

socket.on("history_data", ({ readings: history }) => {
    readings = history;
    history.forEach(r => pushToChart(r));
    updateStats();
});

socket.on("new_reading", (reading) => {
    readings.push(reading);
    pushToChart(reading);
    updateStats();
});

socket.on("spike_alert", ({ bpm, timestamp, message }) => {
    addAlert(message, timestamp);
});

socket.on("ai_analysis", ({ analysis, trigger }) => {
    document.getElementById("ai-output").textContent = analysis;
});

socket.on("chat_message", ({ user, ai }) => {
    addChatMessage(user, ai);
});