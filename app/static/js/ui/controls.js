/* Minimal UI controls: demo button + per-asset kill/recover + debug overlay. */

import { state, setDebugOverlay } from "../core/state.js";
import { createBackgroundCanvas } from "../core/render.js";
import { logLocal } from "../core/log.js";
import { addNotification } from "../core/notif.js";

let backendUrl = "";
let demoPollingId = null;

export function initControls(url) {
  backendUrl = url || "";
  wireDemoButton();
  renderAssetControls();
  renderDebugToggle();
  wireAIBar();
}

function wireDemoButton() {
  const btn = document.getElementById("demo-btn");
  if (!btn) return;
  btn.addEventListener("click", toggleDemo);
}

function toggleDemo() {
  const btn = document.getElementById("demo-btn");
  if (!btn || btn.classList.contains("running")) return;

  btn.textContent = "Running...";
  btn.classList.add("running");
  btn.classList.remove("done");

  // Read products and speed from config; fall back to defaults if not available
  const numProducts = state.config?.products || 3;
  const speedName = state.config?.speed || "normal";

  fetch(backendUrl + "/demo/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products: numProducts, speed: speedName }),
  })
    .then((r) => r.json())
    .then(() => {
      demoPollingId = setInterval(checkDemoStatus, 1000);
    })
    .catch(() => {
      btn.textContent = "Run Demo";
      btn.classList.remove("running");
    });
}

function checkDemoStatus() {
  fetch(backendUrl + "/demo/status")
    .then((r) => r.json())
    .then((data) => {
      if (!data.running) {
        clearInterval(demoPollingId);
        const btn = document.getElementById("demo-btn");
        btn.textContent = "Complete";
        btn.classList.remove("running");
        btn.classList.add("done");
        setTimeout(() => {
          btn.textContent = "Run Demo";
          btn.classList.remove("done");
        }, 3000);
      }
    })
    .catch(() => {});
}

/* Per-asset Kill / Recover buttons (vehicles + conveyors only). */
function renderAssetControls() {
  let panel = document.getElementById("asset-controls");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "asset-controls";
    const statusBar = document.getElementById("status-bar");
    if (statusBar && statusBar.parentNode) {
      statusBar.parentNode.insertBefore(panel, statusBar.nextSibling);
    } else {
      document.body.appendChild(panel);
    }
  }
  panel.innerHTML = "";

  for (const asset of state.assets.values()) {
    if (asset.type !== "vehicle" && asset.type !== "conveyor") continue;

    const killBtn = document.createElement("button");
    killBtn.textContent = `Kill ${asset.label}`;
    killBtn.className = "control-btn control-btn-danger asset-kill-btn";
    killBtn.addEventListener("click", () => failAsset(asset.id));
    panel.appendChild(killBtn);

    const recoverBtn = document.createElement("button");
    recoverBtn.textContent = `Recover ${asset.label}`;
    recoverBtn.className = "control-btn control-btn-ghost asset-recover-btn";
    recoverBtn.addEventListener("click", () => recoverAsset(asset.id));
    panel.appendChild(recoverBtn);
  }
}

function failAsset(assetId) {
  fetch(backendUrl + `/assets/${assetId}/fail`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: "manually killed" }),
  })
    .then((r) => r.json())
    .then(() => {
      logLocal(assetId, "Kill signal sent to backend");
    })
    .catch(() => {
      addNotification("error", assetId, "Kill request failed");
    });
}

function recoverAsset(assetId) {
  fetch(backendUrl + `/assets/${assetId}/recover`, { method: "POST" })
    .then((r) => r.json())
    .then(() => {
      logLocal(assetId, "Recover signal sent to backend");
    })
    .catch(() => {
      addNotification("error", assetId, "Recover request failed");
    });
}

/* ─────────────────────────────────────────────────────────────────────────
 * AI bar — reset / spawn / run-all + per-arm Qwen script dispatch.
 *
 * Calls the backend /run_script endpoint, which asks Qwen to pick one of
 * three named scripts for that arm (relay / hold / return_to_rest), runs
 * the chosen script, and emits log lines. Run All walks all arms in
 * pipeline order.
 * ──────────────────────────────────────────────────────────────────────── */

let aiBusy = false;

function setAIStatus(msg, kind) {
  const el = document.getElementById("ai-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "ai-status" + (kind ? ` ai-status-${kind}` : "");
}

const actorBusy = new Set();

function setActorButtonBusy(actorId, busy) {
  // Disable only the matching per-actor button while it runs. Other actors
  // remain clickable so the operator can drive several in parallel if they
  // want (Qwen is shared but the runs serialise on the network anyway).
  const btn = document.querySelector(
    `#ai-actor-buttons button[data-actor-id="${actorId}"]`
  );
  if (btn) btn.disabled = busy;
  if (busy) actorBusy.add(actorId);
  else actorBusy.delete(actorId);
}

function wireAIBar() {
  const reset = document.getElementById("ai-reset-btn");
  const spawn = document.getElementById("ai-spawn-btn");
  const runAll = document.getElementById("ai-run-all-btn");
  if (!reset || !spawn || !runAll) return;

  reset.addEventListener("click", () => aiReset());
  spawn.addEventListener("click", () => aiSpawn());
  runAll.addEventListener("click", () => aiRunAll());

  renderAIActorButtons();
  // Re-render actor buttons whenever asset state lands (handles late hydration).
  setTimeout(renderAIActorButtons, 800);
  setTimeout(renderAIActorButtons, 2000);
}

function renderAIActorButtons() {
  // Render one button per actor (arms + assets) in pipeline flow order.
  // The host element id was renamed from #ai-arm-buttons to #ai-actor-buttons;
  // keep tolerating either id so an old cached page does not blow up.
  const host =
    document.getElementById("ai-actor-buttons") ||
    document.getElementById("ai-arm-buttons");
  if (!host) return;

  const arms = Array.from(state.assets.values())
    .filter((a) => a.type === "arm")
    .sort((a, b) => {
      const na = Number(a.id.replace(/^arm-/, ""));
      const nb = Number(b.id.replace(/^arm-/, ""));
      return na - nb;
    });
  const assets = Array.from(state.assets.values())
    .filter((a) => a.type === "vehicle" || a.type === "conveyor");

  const flow = [...arms, ...assets];
  if (flow.length === 0) return;

  const sig = flow.map((a) => a.id).join(",");
  if (host.dataset.rendered === sig) return;
  host.innerHTML = "";
  for (const a of flow) {
    const btn = document.createElement("button");
    if (a.type === "arm") {
      const n = Number(a.id.replace(/^arm-/, ""));
      btn.textContent = `Run Arm ${n}`;
      btn.dataset.actorId = String(n);
      btn.addEventListener("click", () => aiRunActor(n));
    } else {
      btn.textContent = `Run ${a.label || a.id}`;
      btn.dataset.actorId = a.id;
      btn.addEventListener("click", () => aiRunActor(a.id));
    }
    btn.className = "control-btn control-btn-ghost";
    host.appendChild(btn);
  }
  host.dataset.rendered = sig;
}

function aiReset() {
  // Reset is always allowed — used to recover from a stuck or
  // half-finished run.
  setAIStatus("resetting…");
  fetch(backendUrl + "/objects/reset", { method: "POST" })
    .then(() => setAIStatus("world cleared", "ok"))
    .catch(() => setAIStatus("reset failed", "err"));
}

function aiSpawn() {
  // Spawn is always allowed — useful to queue more work mid-run.
  setAIStatus("spawning…");
  fetch(backendUrl + "/objects/spawn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  })
    .then((r) => r.json())
    .then((d) => setAIStatus(`spawned ${d?.object?.object_id || "object"}`, "ok"))
    .catch(() => setAIStatus("spawn failed", "err"));
}

function actorLabel(actorId) {
  return typeof actorId === "number" ? `Arm ${actorId}` : actorId;
}

async function _dispatch(actorId) {
  // Body shape matches the backend: numeric → arm_id, string → actor_id.
  const body = typeof actorId === "number"
    ? { arm_id: actorId, task: "do your relay" }
    : { actor_id: actorId, task: "do your relay" };
  const r = await fetch(backendUrl + "/run_script", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function aiRunActor(actorId) {
  if (actorBusy.has(actorId)) return;
  setActorButtonBusy(actorId, true);
  setAIStatus(`${actorLabel(actorId)}: thinking…`);
  _dispatch(actorId)
    .then((d) => {
      if (d?.status === "success") {
        setAIStatus(`${actorLabel(actorId)} → ${d.chosen_script}`, "ok");
      } else {
        setAIStatus(`${actorLabel(actorId)} failed: ${d?.reason || "?"}`, "err");
      }
    })
    .catch((e) => {
      setAIStatus(`${actorLabel(actorId)} error: ${e.message || e}`, "err");
    })
    .finally(() => setActorButtonBusy(actorId, false));
}

/* ─────────────────────────────────────────────────────────────────────────
 * Run All — N parallel actor loops (one per actor, all running concurrently).
 *
 * Each actor independently polls its brain. When its upstream has an
 * object and its downstream is alive, it relays. Otherwise the brain
 * fast-paths to "hold" without consulting Qwen, the actor sleeps briefly,
 * then checks again.
 *
 * Result: the operator spawns objects whenever they like. As soon as an
 * actor sees work, it moves the object. arm-1 and arm-2 work in parallel —
 * arm-1 is already picking Obj2 from intake while arm-2 is carrying Obj1
 * to the shuttle.
 *
 * The Run All button doubles as Stop while the loops are active.
 * Clicking it again sets `runAllStop`; each loop exits at its next
 * iteration boundary.
 * ──────────────────────────────────────────────────────────────────────── */

let runAllStop = false;
let runAllLoopsActive = 0;

function setRunAllRunning(running) {
  const btn = document.getElementById("ai-run-all-btn");
  if (!btn) return;
  if (running) {
    btn.textContent = "Stop";
    btn.classList.add("control-btn-danger");
  } else {
    btn.textContent = "Run All (Qwen)";
    btn.classList.remove("control-btn-danger");
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function actorLoop(fid) {
  const actorId = fid.startsWith("arm-")
    ? Number(fid.replace(/^arm-/, ""))
    : fid;
  let consecutiveHolds = 0;
  let movedCount = 0;

  while (!runAllStop) {
    setActorButtonBusy(actorId, true);
    let didWork = false;
    try {
      const d = await _dispatch(actorId);
      if (d?.status === "success" && d?.result?.status === "done") {
        didWork = true;
        movedCount++;
        consecutiveHolds = 0;
      } else {
        consecutiveHolds++;
      }
    } catch (e) {
      // Network blip — keep looping, the next poll will retry.
      consecutiveHolds++;
    }
    setActorButtonBusy(actorId, false);
    if (runAllStop) break;

    // Backoff: when the actor has no work, sleep longer between polls so
    // we don't hammer the backend with hold cycles. Active actors poll
    // quickly so they pick up new work fast.
    const sleepMs = didWork ? 200 : Math.min(1000 + consecutiveHolds * 500, 5000);
    await sleep(sleepMs);
  }
  return { actorId, movedCount };
}

async function aiRunAll() {
  if (aiBusy) {
    // Second click while running = stop request.
    runAllStop = true;
    setAIStatus("Run All: stop requested — actors finishing their current step…");
    return;
  }
  aiBusy = true;
  runAllStop = false;
  setRunAllRunning(true);
  setAIStatus("Run All: parallel loops active. Spawn or kill anything; the actors react on their own.");

  // Authoritative actor list from the backend.
  let flow = await fetch(backendUrl + "/topology")
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (d && Array.isArray(d.flow) ? d.flow : null))
    .catch(() => null);
  if (!flow) {
    const arms = Array.from(state.assets.values())
      .filter((a) => a.type === "arm")
      .map((a) => a.id)
      .sort();
    const assets = Array.from(state.assets.values())
      .filter((a) => a.type !== "arm")
      .map((a) => a.id);
    flow = [...arms, ...assets];
  }

  // Disable per-actor buttons while loops own them. (They are still
  // visually busy-toggled by setActorButtonBusy inside the loop.)
  document
    .querySelectorAll("#ai-actor-buttons button")
    .forEach((b) => (b.disabled = true));

  // Kick off one loop per actor. They run concurrently — arm-1 polls
  // intake while arm-5 polls handover_4, etc.
  runAllLoopsActive = flow.length;
  const loops = flow.map((fid) => actorLoop(fid));
  const results = await Promise.all(loops);
  runAllLoopsActive = 0;

  const total = results.reduce((s, r) => s + (r.movedCount || 0), 0);
  setAIStatus(`Run All: stopped (${total} relay(s) total across all actors)`, "ok");

  // Re-enable manual buttons.
  document
    .querySelectorAll("#ai-actor-buttons button")
    .forEach((b) => (b.disabled = false));

  setRunAllRunning(false);
  aiBusy = false;
  runAllStop = false;
}

/* Debug overlay toggle: shows asset ids, ownership, connection boundaries. */
function renderDebugToggle() {
  let panel = document.getElementById("asset-controls");
  if (!panel) return;
  const btn = document.createElement("button");
  btn.textContent = "Debug: off";
  btn.className = "control-btn control-btn-debug asset-debug-btn";
  btn.addEventListener("click", () => {
    const next = !state.debugOverlay;
    setDebugOverlay(next);
    btn.textContent = next ? "Debug: on" : "Debug: off";
    btn.classList.toggle("is-active", next);
    // Redraw background so handover zone labels update
    createBackgroundCanvas();
  });
  panel.appendChild(btn);
}
