/**
 * INTER.act Simulation — Canvas rendering engine + SocketIO client.
 *
 * Fetches scene config from /config on load, connects to SocketIO for
 * real-time state updates, and renders 3 robotic arms with smooth animation.
 * Tracks objects (Obj1, Obj2, ...) as they flow through the pipeline.
 */

/* ── Constants ─────────────────────────────────────────── */
const ANIM_DURATION = 600;
const MAX_LOG_LINES = 100;
const LABEL_COLOR = "#7f8c8d";
const OBJ_COLOR = "#e67e22";
const OBJ_TEXT_COLOR = "#fff";

// Scaled drawing sizes — computed from config in init()
let S = 1.0; // scale factor
let ARM_LINE_WIDTH = 4;
let PIVOT_RADIUS = 8;
let TIP_RADIUS = 6;
let OBJ_WIDTH = 32;
let OBJ_HEIGHT = 18;
let LABEL_FONT = "14px sans-serif";
let OBJ_FONT = "bold 10px sans-serif";
let ZONE_LABEL_FONT = "11px sans-serif";
let ARM_LABEL_FONT = "13px sans-serif";

function computeScale() {
  // Keep drawing dimensions stable across different arm counts.
  S = 1.0;

  ARM_LINE_WIDTH = Math.max(2, Math.round(4 * S));
  PIVOT_RADIUS = Math.max(4, Math.round(8 * S));
  TIP_RADIUS = Math.max(3, Math.round(6 * S));
  OBJ_WIDTH = Math.max(18, Math.round(32 * S));
  OBJ_HEIGHT = Math.max(12, Math.round(18 * S));

  const baseFontSize = Math.max(8, Math.round(13 * S));
  LABEL_FONT = `${baseFontSize}px sans-serif`;
  OBJ_FONT = `bold ${Math.max(7, Math.round(10 * S))}px sans-serif`;
  ZONE_LABEL_FONT = `${Math.max(7, Math.round(11 * S))}px sans-serif`;
  ARM_LABEL_FONT = `${Math.max(8, Math.round(13 * S))}px sans-serif`;
}

/* ── State ─────────────────────────────────────────────── */
let config = null; // Scene configuration from /config
let targetAngles = {}; // {1: 90, 2: 90, 3: 90}
let displayedAngles = {}; // Current rendered angles
let armTransitions = {}; // {id: {from, to, startTime}} — active easing per arm
let trackedObjects = {}; // {"Obj1": {object_id, state, carrier_arm}}
let objectPositions = {}; // {"Obj1": {x, y}} — current smooth position per object
let objectTransitions = {}; // {"Obj1": {fromX, fromY, toX, toY, startTime}}
let bgCanvas = null; // Offscreen canvas for static background
let animFrameId = null;
const OBJ_TRANSITION_MS = 400; // ms for object position easing between states
let deviceScale = window.devicePixelRatio || 1;
let viewScale = 1;

/* ── Backend URL (empty = same-origin, set by nginx in container mode) ── */
const BACKEND_URL = window.BACKEND_URL || "";

/* ── DOM References ────────────────────────────────────── */
const canvas = document.getElementById("sim-canvas");
const ctx = canvas.getContext("2d");
const logPanel = document.getElementById("log-panel");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

/* ── SocketIO Setup ────────────────────────────────────── */
const socket = io(BACKEND_URL || undefined);

socket.on("connect", () => {
  statusDot.classList.add("connected");
  statusText.textContent = "Connected";
  // Sync state on reconnect
  fetch(BACKEND_URL + "/state")
    .then((r) => r.json())
    .then((data) => applyStateUpdate(data));
  fetch(BACKEND_URL + "/objects")
    .then((r) => r.json())
    .then((data) => {
      if (data && data.objects) {
        trackedObjects = {};
        data.objects.forEach((obj) => {
          trackedObjects[obj.object_id] = obj;
        });
      }
    });
});

socket.on("disconnect", () => {
  statusDot.classList.remove("connected");
  statusText.textContent = "Disconnected";
});

socket.on("state_update", (data) => {
  applyStateUpdate(data);
});

socket.on("object_update", (data) => {
  if (data && data.object_id) {
    const id = data.object_id;
    const oldObj = trackedObjects[id];
    trackedObjects[id] = data;

    // Carried objects must stay hard-locked to the arm tip.
    if (data.state === "carried") {
      delete objectTransitions[id];
      return;
    }

    // Start smooth position transition if object already had a position
    if (oldObj && objectPositions[id]) {
      objectTransitions[id] = {
        fromX: objectPositions[id].x,
        fromY: objectPositions[id].y,
        startTime: performance.now(),
      };
    }
  }
});

socket.on("objects_reset", () => {
  trackedObjects = {};
  objectPositions = {};
  objectTransitions = {};
});

socket.on("log_message", (data) => {
  appendLogEntry(data);
});

/* ── US #16: Qwen brain task events ────────────────────── */

socket.on("task_status", (data) => {
  // Backend emits this when a task starts (notify_processing).
  // Mirror it into the log panel so the user sees ⏳ immediately.
  appendLogEntry({
    timestamp: data.timestamp,
    source: `arm_${data.arm_id}`,
    message: data.message || `⏳ Arm ${data.arm_id} is ${data.action}...`,
  });
  setTaskStatus("processing", data.message);
});

socket.on("brain_decision", (data) => {
  // Surface Qwen's decision (action, target_angle, confidence) in the log
  // even when the backend has not also sent a log_message for it.
  const conf =
    typeof data.confidence === "number"
      ? ` (conf ${data.confidence.toFixed(2)})`
      : "";
  appendLogEntry({
    timestamp: data.timestamp,
    source: `arm_${data.arm_id}`,
    message:
      `🧠 ${data.action} @ ${data.target_angle}°${conf} — ${data.reasoning || ""}`,
  });
});

socket.on("task_complete", (data) => {
  setTaskStatus(data.status, data.message);
});

socket.on("task_result", (data) => {
  // The /execute_task route also emits the full result object. We do not
  // re-log it (notify_success/notify_failure already produced a log entry),
  // but we update the small indicator next to the Send Task button.
  setTaskStatus(data.status, data.message);
});

socket.on("script_chosen", (data) => {
  // Qwen picked a named script for this arm — make it loud in the UI.
  // If the deterministic safety net overrode Qwen, show it explicitly.
  if (data.override_reason) {
    setRoleStatus(
      "processing",
      `arm ${data.arm_id}: ${data.script} ⚖ overridden (Qwen→${data.qwen_suggested})`,
    );
  } else {
    setRoleStatus("processing", `arm ${data.arm_id}: ${data.script}`);
  }
});

/* ── State Management ──────────────────────────────────── */

function applyStateUpdate(data) {
  /** Update target angles from server state — starts time-based transitions. */
  if (data && data.arms) {
    const now = performance.now();
    data.arms.forEach((arm) => {
      const id = arm.arm_id;
      const newTarget = arm.angle;
      const oldTarget = targetAngles[id];

      if (oldTarget === undefined || newTarget !== oldTarget) {
        armTransitions[id] = {
          from:
            displayedAngles[id] !== undefined
              ? displayedAngles[id]
              : config
                ? config.default_angle
                : 90,
          to: newTarget,
          startTime: now,
        };
      }
      targetAngles[id] = newTarget;
    });
  }
}

/* ── Log Panel ─────────────────────────────────────────── */

function appendLogEntry(data) {
  /** Add a log entry to the browser log panel. */
  const entry = document.createElement("div");
  entry.className = "log-entry";

  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = `[${data.timestamp}] `;

  const source = document.createElement("span");
  source.className = `log-source ${data.source}`;
  source.textContent = `${data.source} `;

  const msg = document.createTextNode(data.message);

  entry.appendChild(time);
  entry.appendChild(source);
  entry.appendChild(msg);
  logPanel.appendChild(entry);

  while (logPanel.children.length > MAX_LOG_LINES) {
    logPanel.removeChild(logPanel.firstChild);
  }
  logPanel.scrollTop = logPanel.scrollHeight;
}

/* ── Drawing: Background (offscreen canvas) ────────────── */

function drawRoundedRect(c, x, y, w, h, r) {
  /** Draw a rounded rectangle path on context c. */
  c.beginPath();
  c.moveTo(x + r, y);
  c.lineTo(x + w - r, y);
  c.quadraticCurveTo(x + w, y, x + w, y + r);
  c.lineTo(x + w, y + h - r);
  c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  c.lineTo(x + r, y + h);
  c.quadraticCurveTo(x, y + h, x, y + h - r);
  c.lineTo(x, y + r);
  c.quadraticCurveTo(x, y, x + r, y);
  c.closePath();
}

function createBackgroundCanvas() {
  /** Draw all static elements into an offscreen canvas (drawn once). */
  const renderScale = deviceScale * viewScale;
  bgCanvas = document.createElement("canvas");
  bgCanvas.width = Math.round(config.canvas_width * renderScale);
  bgCanvas.height = Math.round(config.canvas_height * renderScale);
  const bgCtx = bgCanvas.getContext("2d");
  bgCtx.setTransform(renderScale, 0, 0, renderScale, 0, 0);

  // Background fill
  bgCtx.fillStyle = "#fafafa";
  bgCtx.fillRect(0, 0, config.canvas_width, config.canvas_height);

  // Reach zone circles
  config.reach_zones.forEach((zone) => {
    bgCtx.beginPath();
    bgCtx.arc(zone.cx, zone.cy, zone.radius, 0, Math.PI * 2);
    bgCtx.fillStyle = hexToRgba(zone.color, 0.08);
    bgCtx.fill();
    bgCtx.setLineDash([8, 4]);
    bgCtx.strokeStyle = hexToRgba(zone.color, 0.4);
    bgCtx.lineWidth = 1.5;
    bgCtx.stroke();
    bgCtx.setLineDash([]);
  });

  // Handoff zones
  config.handoff_zones.forEach((zone) => {
    bgCtx.fillStyle = hexToRgba(zone.color, 0.15);
    bgCtx.fillRect(zone.x, zone.y, zone.width, zone.height);
    bgCtx.setLineDash([6, 3]);
    bgCtx.strokeStyle = hexToRgba(zone.color, 0.5);
    bgCtx.lineWidth = 1.5;
    bgCtx.strokeRect(zone.x, zone.y, zone.width, zone.height);
    bgCtx.setLineDash([]);

    bgCtx.fillStyle = LABEL_COLOR;
    bgCtx.font = ZONE_LABEL_FONT;
    bgCtx.textAlign = "center";
    bgCtx.fillText(
      zone.label,
      zone.x + zone.width / 2,
      zone.y - Math.round(4 * S),
    );
  });

  // Intake zone
  const intake = config.intake_zone;
  drawRoundedRect(
    bgCtx,
    intake.x,
    intake.y,
    intake.width,
    intake.height,
    Math.round(4 * S),
  );
  bgCtx.fillStyle = hexToRgba(intake.color, 0.2);
  bgCtx.fill();
  bgCtx.strokeStyle = intake.color;
  bgCtx.lineWidth = Math.max(1, Math.round(1.5 * S));
  bgCtx.stroke();

  bgCtx.fillStyle = LABEL_COLOR;
  bgCtx.font = `bold ${ZONE_LABEL_FONT}`;
  bgCtx.textAlign = "center";
  bgCtx.fillText(intake.label, intake.label_x, intake.label_y);

  // Entry arrow
  const arrowSize = Math.max(4, Math.round(8 * S));
  const ex = intake.entry_x;
  const ey = intake.y + Math.round(3 * S);
  bgCtx.beginPath();
  bgCtx.moveTo(ex - arrowSize, ey);
  bgCtx.lineTo(ex + arrowSize, ey);
  bgCtx.lineTo(ex, ey + arrowSize + 2);
  bgCtx.closePath();
  bgCtx.fillStyle = intake.color;
  bgCtx.fill();

  // Output zone
  const output = config.output_zone;
  drawRoundedRect(
    bgCtx,
    output.x,
    output.y,
    output.width,
    output.height,
    Math.round(4 * S),
  );
  bgCtx.fillStyle = hexToRgba(output.color, 0.15);
  bgCtx.fill();
  bgCtx.strokeStyle = output.color;
  bgCtx.lineWidth = Math.max(1, Math.round(1.5 * S));
  bgCtx.stroke();

  bgCtx.fillStyle = LABEL_COLOR;
  bgCtx.font = `bold ${ZONE_LABEL_FONT}`;
  bgCtx.textAlign = "center";
  bgCtx.fillText(output.label, output.label_x, output.label_y);

  // Arm labels
  config.arms.forEach((arm) => {
    bgCtx.fillStyle = LABEL_COLOR;
    bgCtx.font = ARM_LABEL_FONT;
    bgCtx.textAlign = "center";
    bgCtx.fillText(
      arm.label,
      arm.pivot_x,
      arm.pivot_y + arm.length + Math.round(18 * S),
    );
  });

  config.arms.forEach((arm) => {
    bgCtx.beginPath();
    bgCtx.arc(arm.pivot_x, arm.pivot_y, PIVOT_RADIUS, 0, Math.PI * 2);
    bgCtx.fillStyle = arm.color;
    bgCtx.fill();
    bgCtx.strokeStyle = "#fff";
    bgCtx.lineWidth = Math.max(1, Math.round(1.5 * S));
    bgCtx.stroke();
  });
}

/* ── Drawing: Arms (per frame) ─────────────────────────── */

function drawArm(arm, angleDeg) {
  /** Draw a single arm at the given angle (degrees). Returns tip position. */
  const angleRad = angleDeg * (Math.PI / 180);
  const tipX = arm.pivot_x + arm.length * Math.cos(angleRad);
  const tipY = arm.pivot_y - arm.length * Math.sin(angleRad);

  ctx.beginPath();
  ctx.moveTo(arm.pivot_x, arm.pivot_y);
  ctx.lineTo(tipX, tipY);
  ctx.strokeStyle = arm.color;
  ctx.lineWidth = ARM_LINE_WIDTH;
  ctx.lineCap = "round";
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(tipX, tipY, TIP_RADIUS, 0, Math.PI * 2);
  ctx.fillStyle = arm.color;
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = Math.max(1, Math.round(1.5 * S));
  ctx.stroke();

  return { x: tipX, y: tipY };
}

/* ── Drawing: Objects ──────────────────────────────────── */

function getObjectPosition(obj, armTips) {
  /** Compute pixel position for an object based on its state. */
  if (obj.state === "carried" && obj.carrier_arm && armTips[obj.carrier_arm]) {
    return armTips[obj.carrier_arm];
  }
  if (obj.state === "intake") {
    const iz = config.intake_zone;
    const intakeObjs = Object.values(trackedObjects)
      .filter((o) => o.state === "intake")
      .map((o) => o.object_id)
      .sort();
    const idx = intakeObjs.indexOf(obj.object_id);
    const total = intakeObjs.length;
    // Dynamic spacing: fit all objects within zone, bottom object at y=300
    const padding = 8;
    const availableHeight = iz.height - padding * 2;
    const maxSpacing = OBJ_HEIGHT + 6;
    const spacing = Math.min(maxSpacing, availableHeight / Math.max(total, 1));
    const bottomY = iz.y + iz.height - padding - OBJ_HEIGHT / 2;
    return { x: iz.entry_x, y: bottomY - (total - 1 - idx) * spacing };
  }
  // Dynamic handoff matching: "handoff_1" -> index 0, "handoff_2" -> index 1, etc.
  if (obj.state.startsWith("handoff_")) {
    const idx = parseInt(obj.state.split("_")[1]) - 1;
    if (idx >= 0 && idx < config.handoff_zones.length) {
      const hz = config.handoff_zones[idx];
      const pivotY = config.arms[0] ? config.arms[0].pivot_y : 300;
      return { x: hz.x + hz.width / 2, y: pivotY };
    }
  }
  if (obj.state === "output") {
    const oz = config.output_zone;
    const outputObjs = Object.values(trackedObjects)
      .filter((o) => o.state === "output")
      .map((o) => o.object_id)
      .sort();
    const idx = outputObjs.indexOf(obj.object_id);
    const total = outputObjs.length;
    const padding = 8;
    const availableHeight = oz.height - padding * 2;
    const maxSpacing = OBJ_HEIGHT + 6;
    const spacing = Math.min(maxSpacing, availableHeight / Math.max(total, 1));
    const bottomY = oz.y + oz.height - padding - OBJ_HEIGHT / 2;
    return { x: oz.x + oz.width / 2, y: bottomY - idx * spacing };
  }
  return null;
}

function drawObject(obj, pos) {
  /** Draw a single tracked object at the given position. */
  const x = pos.x - OBJ_WIDTH / 2;
  const y = pos.y - OBJ_HEIGHT / 2;

  // Rounded rect body
  drawRoundedRect(ctx, x, y, OBJ_WIDTH, OBJ_HEIGHT, Math.round(3 * S));
  ctx.fillStyle = OBJ_COLOR;
  ctx.fill();
  ctx.strokeStyle = "#d35400";
  ctx.lineWidth = Math.max(1, Math.round(1.2 * S));
  ctx.stroke();

  // Label text
  ctx.fillStyle = OBJ_TEXT_COLOR;
  ctx.font = OBJ_FONT;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(obj.object_id, pos.x, pos.y);
  ctx.textBaseline = "alphabetic";
}

/* ── Animation Loop ────────────────────────────────────── */

function easeOutCubic(t) {
  /** Deceleration curve — fast start, smooth stop. */
  return 1 - Math.pow(1 - t, 3);
}

function computeDisplayAngle(id, now) {
  /** Compute the visual angle for an arm using time-based easing. */
  const transition = armTransitions[id];
  if (!transition) {
    return targetAngles[id] !== undefined
      ? targetAngles[id]
      : config.default_angle;
  }
  const elapsed = now - transition.startTime;
  if (elapsed >= ANIM_DURATION) {
    return transition.to;
  }
  const t = easeOutCubic(elapsed / ANIM_DURATION);
  return transition.from + (transition.to - transition.from) * t;
}

function renderFrame() {
  /** Render a single frame — update angles, draw arms, draw objects. */
  if (!config) return;

  // Clear and draw background
  ctx.clearRect(0, 0, config.canvas_width, config.canvas_height);
  if (bgCanvas) {
    ctx.drawImage(bgCanvas, 0, 0, config.canvas_width, config.canvas_height);
  }

  // Update and draw each arm, collect tip positions
  const now = performance.now();
  const armTips = {};
  config.arms.forEach((arm) => {
    const id = arm.id;
    displayedAngles[id] = computeDisplayAngle(id, now);
    armTips[id] = drawArm(arm, displayedAngles[id]);
  });

  // Draw tracked objects with smooth position transitions
  Object.values(trackedObjects).forEach((obj) => {
    const targetPos = getObjectPosition(obj, armTips);
    if (!targetPos) return;

    const id = obj.object_id;
    const transition = objectTransitions[id];
    let pos = targetPos;

    if (obj.state === "carried") {
      delete objectTransitions[id];
      pos = targetPos;
    } else if (transition) {
      const elapsed = now - transition.startTime;
      const duration = OBJ_TRANSITION_MS;
      if (elapsed < duration) {
        const t = easeOutCubic(elapsed / duration);
        pos = {
          x: transition.fromX + (targetPos.x - transition.fromX) * t,
          y: transition.fromY + (targetPos.y - transition.fromY) * t,
        };
      } else {
        delete objectTransitions[id];
      }
    }

    objectPositions[id] = pos;
    drawObject(obj, pos);
  });
}

function animationLoop() {
  /** Main render loop — runs at ~60fps via requestAnimationFrame. */
  renderFrame();
  animFrameId = requestAnimationFrame(animationLoop);
}

/* ── Utility ───────────────────────────────────────────── */

function hexToRgba(hex, alpha) {
  /** Convert hex color (#rrggbb) to rgba string. */
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function setupCanvasResolution() {
  /** Keep canvas crisp by rendering at native device pixel ratio. */
  const wrapper = document.getElementById("canvas-wrapper");
  const wrapperWidth = wrapper ? wrapper.clientWidth : config.canvas_width;
  viewScale = Math.min(1, wrapperWidth / config.canvas_width);

  const displayWidth = Math.max(1, Math.round(config.canvas_width * viewScale));
  const displayHeight = Math.max(
    1,
    Math.round(config.canvas_height * viewScale),
  );
  deviceScale = window.devicePixelRatio || 1;
  canvas.width = Math.round(displayWidth * deviceScale);
  canvas.height = Math.round(displayHeight * deviceScale);
  canvas.style.width = `${displayWidth}px`;
  canvas.style.height = `${displayHeight}px`;
  ctx.setTransform(
    deviceScale * viewScale,
    0,
    0,
    deviceScale * viewScale,
    0,
    0,
  );
}

/* ── Initialization ────────────────────────────────────── */

async function init() {
  /** Fetch scene config and start the animation loop. */
  try {
    const resp = await fetch(BACKEND_URL + "/config");
    config = await resp.json();
    setupCanvasResolution();
    computeScale();

    config.arms.forEach((arm) => {
      targetAngles[arm.id] = config.default_angle;
      displayedAngles[arm.id] = config.default_angle;
      armTransitions[arm.id] = null;
    });

    // Sync object state
    try {
      const objResp = await fetch(BACKEND_URL + "/objects");
      const objData = await objResp.json();
      if (objData && objData.objects) {
        objData.objects.forEach((obj) => {
          trackedObjects[obj.object_id] = obj;
        });
      }
    } catch (e) {
      // Objects endpoint may not exist yet — non-critical
    }

    // Build offscreen background
    createBackgroundCanvas();

    animationLoop();
  } catch (err) {
    console.error("Failed to load scene config:", err);
  }
}

window.addEventListener("resize", () => {
  if (!config) return;
  setupCanvasResolution();
  createBackgroundCanvas();
});

/* ── Demo Control ──────────────────────────────────────── */

let demoPollingId = null;

function toggleDemo() {
  const btn = document.getElementById("demo-btn");
  if (btn.classList.contains("running")) return; // Already running

  btn.textContent = "Running...";
  btn.classList.add("running");
  btn.classList.remove("done");

  // Read the "Use Qwen brain" checkbox so the operator can toggle whether
  // the pipeline asks Qwen for every angle (slow, visible AI decisions in
  // the log) or uses the hardcoded angles (fast visualisation).
  const useBrainCb = document.getElementById("use-brain-cb");
  const useBrain = !!(useBrainCb && useBrainCb.checked);

  fetch(BACKEND_URL + "/demo/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products: 3, speed: "normal", use_brain: useBrain }),
  })
    .then((r) => r.json())
    .then(() => {
      // Poll for completion
      demoPollingId = setInterval(checkDemoStatus, 1000);
    })
    .catch(() => {
      btn.textContent = "Run Demo";
      btn.classList.remove("running");
    });
}

function checkDemoStatus() {
  fetch(BACKEND_URL + "/demo/status")
    .then((r) => r.json())
    .then((data) => {
      if (!data.running) {
        clearInterval(demoPollingId);
        const btn = document.getElementById("demo-btn");
        btn.textContent = "Complete";
        btn.classList.remove("running");
        btn.classList.add("done");
        // Reset button after 3 seconds
        setTimeout(() => {
          btn.textContent = "Run Demo";
          btn.classList.remove("done");
        }, 3000);
      }
    });
}

/* ── US #16: Send Task (Qwen brain) control ────────────── */

function setTaskStatus(state, text) {
  const el = document.getElementById("task-status");
  if (!el) return;
  el.className = state ? `state-${state}` : "";
  el.textContent = text || "";
}

function sendTask() {
  const btn = document.getElementById("task-btn");
  const armSel = document.getElementById("task-arm");
  const taskInput = document.getElementById("task-input");
  if (!btn || !armSel || !taskInput) return;

  const armId = parseInt(armSel.value, 10);
  const task = (taskInput.value || "").trim();
  if (!task) {
    setTaskStatus("failed", "task is empty");
    return;
  }

  btn.disabled = true;
  setTaskStatus("processing", "asking Qwen...");

  fetch(BACKEND_URL + "/execute_task", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arm_id: armId, task: task }),
  })
    .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
    .then(({ ok, body }) => {
      // task_result via WebSocket will normally beat us here, but if it
      // hasn't arrived yet we still want to show something useful.
      if (!ok) {
        setTaskStatus("failed", body.message || body.reason || body.error || "failed");
      }
    })
    .catch((err) => {
      setTaskStatus("failed", "network error: " + err.message);
    })
    .finally(() => {
      btn.disabled = false;
    });
}

/* ── US 17: Run role (Qwen picks a script from per-arm menu) ─────── */

function setRoleStatus(state, text) {
  const el = document.getElementById("role-status");
  if (!el) return;
  el.className = state ? `state-${state}` : "";
  el.textContent = text || "";
}

function runRole(armId) {
  const taskInput = document.getElementById("role-task");
  const task = ((taskInput && taskInput.value) || "do your relay job").trim();
  setRoleStatus("processing", `Qwen picking script for arm ${armId}…`);

  return fetch(BACKEND_URL + "/run_script", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arm_id: armId, task: task }),
  })
    .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
    .then(({ ok, body }) => {
      if (ok && body.status === "success") {
        setRoleStatus(
          "success",
          `arm ${armId} done — ${body.chosen_script}`,
        );
        return body;
      }
      setRoleStatus(
        "failed",
        body.reason || body.error || "failed",
      );
      return null;
    })
    .catch((err) => {
      setRoleStatus("failed", "network: " + err.message);
      return null;
    });
}

// Spawn a single object at intake so a relay has something to pick.
function spawnObject() {
  setRoleStatus("processing", "spawning object at intake…");
  return fetch(BACKEND_URL + "/objects/spawn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  })
    .then((r) => r.json())
    .then((d) => {
      if (d && d.status === "ok") {
        setRoleStatus("success", `spawned ${d.object.object_id} at intake`);
        return d.object;
      }
      setRoleStatus("failed", "spawn failed");
      return null;
    })
    .catch((err) => {
      setRoleStatus("failed", "network: " + err.message);
      return null;
    });
}

// Reset the world: clear objects and park all arms at 90° rest.
async function resetWorld() {
  setRoleStatus("processing", "resetting…");
  try {
    await fetch(BACKEND_URL + "/objects/reset", { method: "POST" });
    for (const id of [1, 2, 3]) {
      await fetch(BACKEND_URL + "/set_angle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ arm_id: id, angle: 90 }),
      });
    }
    setRoleStatus("success", "world reset (no objects, arms at 90°)");
  } catch (err) {
    setRoleStatus("failed", "reset failed: " + err.message);
  }
}

// Run arm 1, then 2, then 3 sequentially with a brief pause so each arm sees
// the previous arm's drop. Resets + spawns one object first so it's a complete
// self-contained Qwen demo (no need to click Run Demo first).
async function runAllRoles() {
  setRoleStatus("processing", "preparing world…");
  await fetch(BACKEND_URL + "/objects/reset", { method: "POST" });
  for (const id of [1, 2, 3]) {
    await fetch(BACKEND_URL + "/set_angle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arm_id: id, angle: 90 }),
    });
  }
  await spawnObject();
  // Tiny breath so the spawn event reaches the backend before arm 1's brain reads state.
  await new Promise((r) => setTimeout(r, 200));

  setRoleStatus("processing", "running pipeline arm 1 → 2 → 3…");
  for (const id of [1, 2, 3]) {
    const out = await runRole(id);
    if (!out || out.result?.status === "no_object") {
      setRoleStatus(
        "failed",
        `pipeline stopped at arm ${id} — ${out?.result?.status || "no result"}`,
      );
      return;
    }
  }
  setRoleStatus("success", "pipeline complete (1→2→3) ✓");
}

// Start when DOM is ready
init();
