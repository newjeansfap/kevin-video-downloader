/* =========================================================================
   Kevin Lim Fung Yen Video Downloader — frontend logic
   ========================================================================= */
"use strict";

const $ = (id) => document.getElementById(id);

let currentJob = null;      // active job id
let pollTimer = null;       // status poller
let cookiePath = null;      // uploaded cookies.txt path on the server
let lastFiles = "";

const STATUS = {
  idle:        { text: "Ready.",          busy: false },
  downloading: { text: "Downloading…",    busy: true  },
  paused:      { text: "Paused — press Resume to continue", busy: false },
  done:        { text: "Complete!",       busy: false },
  cancelled:   { text: "Cancelled.",      busy: false },
  error:       { text: "Error.",          busy: false },
};

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

/* ---------- Health ---------- */
async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    document.title = d.name || "Video Downloader";
  } catch (e) { /* server unreachable */ }
}

/* ---------- Login option UI ---------- */
function onLoginChange() {
  const login = document.querySelector('input[name="login"]:checked').value;
  const hint = $("login-hint");
  const cookieRow = $("cookie-row");
  const hints = {
    none:    "None — only downloads public videos.",
    chrome:  "Reads your logged-in cookies from the server's Chrome (must be fully closed there first).",
    edge:    "Reads your logged-in cookies from the server's Edge.",
    firefox: "Reads your logged-in cookies from the server's Firefox.",
    file:    "Most reliable. Upload your cookies.txt file (e.g. exported by a browser extension).",
  };
  hint.textContent = hints[login];
  cookieRow.hidden = login !== "file";
}

/* ---------- Cookie upload ---------- */
async function uploadCookie(file) {
  $("cookie-name").textContent = file.name;
  try {
    const r = await fetch("/api/upload-cookie", { method: "POST", body: file });
    const d = await r.json();
    if (d.cookie_path) {
      cookiePath = d.cookie_path;
      $("cookie-name").textContent = "✓ " + file.name;
    } else {
      $("cookie-name").textContent = "✗ " + (d.error || "invalid file");
      cookiePath = null;
    }
  } catch (e) {
    $("cookie-name").textContent = "✗ upload failed";
    cookiePath = null;
  }
}

/* ---------- Status updates ---------- */
function setStatus(state, message, detail) {
  const s = STATUS[state] || STATUS.idle;
  const statusEl = $("status");
  statusEl.textContent = message || s.text;
  statusEl.className = "status" + (state === "error" ? " is-error" : "") +
    (state === "done" ? " is-done" : "");
  $("status-detail").textContent = detail || "";

  const dl = $("btn-download"), pause = $("btn-pause"),
        resume = $("btn-resume"), cancel = $("btn-cancel");
  dl.disabled = !!currentJob && s.busy;
  pause.disabled = state !== "downloading";
  resume.disabled = state !== "paused";
  cancel.disabled = !currentJob || !(state === "downloading" || state === "paused");
  if (state === "idle" || state === "done" || state === "cancelled" || state === "error") {
    pause.disabled = true;
    resume.disabled = true;
    cancel.disabled = true;
  }
}

function setProgress(percent) {
  $("progress").style.width = (percent || 0) + "%";
}

/* ---------- Poll status ---------- */
async function pollStatus() {
  if (!currentJob) return;
  try {
    const r = await fetch("/api/status?id=" + currentJob);
    const d = await r.json();
    if (d.error) { setStatus("error", d.error); stopPolling(); return; }

    setProgress(d.percent);
    if (d.state === "downloading") {
      setStatus("downloading", null, d.size_mb ? d.size_mb + " MB downloaded" : "");
    } else if (d.state === "paused") {
      setStatus("paused");
    } else if (d.state === "done") {
      setStatus("done", "Complete! Your download is ready.");
      stopPolling();
      refreshFiles();
    } else if (d.state === "cancelled") {
      setStatus("cancelled");
      stopPolling();
    } else if (d.state === "error") {
      setStatus("error", "Download failed", d.message);
      stopPolling();
    }
  } catch (e) {
    setStatus("error", "Connection lost", String(e));
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollStatus, 1000);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

/* ---------- Start download ---------- */
async function startDownload() {
  const url = $("url").value.trim();
  if (!url) { setStatus("idle", "Please paste a video URL first."); return; }
  const format = document.querySelector('input[name="format"]:checked').value;
  const login = document.querySelector('input[name="login"]:checked').value;
  if (login === "file" && !cookiePath) {
    setStatus("error", "Please upload a cookies.txt file first.");
    return;
  }
  currentJob = null;
  try {
    const r = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format, login, cookie_path: cookiePath }),
    });
    const d = await r.json();
    if (d.error) { setStatus("error", d.error); return; }
    currentJob = d.id;
    setProgress(0);
    setStatus("downloading", "Starting…");
    startPolling();
  } catch (e) {
    setStatus("error", "Could not reach the server", String(e));
  }
}

/* ---------- Control (pause / resume / cancel) ---------- */
async function control(action) {
  if (!currentJob) return;
  try {
    await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: currentJob, action }),
    });
    if (action === "cancel") { setStatus("cancelled"); stopPolling(); }
    if (action === "resume") { startPolling(); }
    if (action === "pause") { setStatus("paused"); }
  } catch (e) {
    setStatus("error", "Control failed", String(e));
  }
}

/* ---------- Files list ---------- */
async function refreshFiles() {
  try {
    const r = await fetch("/api/files");
    const d = await r.json();
    const list = $("file-list");
    if (!d.files || d.files.length === 0) {
      list.innerHTML = '<li class="file-empty">Nothing downloaded yet.</li>';
      return;
    }
    const key = d.files.map((f) => f.id + ":" + f.done_time).join(",");
    if (key === lastFiles) return;
    lastFiles = key;
    list.innerHTML = "";
    d.files.forEach((f) => {
      list.appendChild(el(
        `<li>
           <span class="file-name-ellipsis" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</span>
           <a class="file-dl" href="${f.url}">Save to this device ↓</a>
         </li>`
      ));
    });
  } catch (e) { /* ignore */ }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------- Wire up ---------- */
document.addEventListener("DOMContentLoaded", () => {
  checkHealth();
  setStatus("idle");
  refreshFiles();
  setInterval(refreshFiles, 5000);

  document.querySelectorAll('input[name="login"]').forEach((r) =>
    r.addEventListener("change", onLoginChange));
  onLoginChange();

  $("cookie-input").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) uploadCookie(e.target.files[0]);
  });

  $("btn-download").addEventListener("click", startDownload);
  $("btn-pause").addEventListener("click", () => control("pause"));
  $("btn-resume").addEventListener("click", () => control("resume"));
  $("btn-cancel").addEventListener("click", () => control("cancel"));
});
