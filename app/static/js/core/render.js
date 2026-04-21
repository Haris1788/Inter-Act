/* Render loop + background + object drawing. */

import { state, getAssetAttachPoint } from './state.js';
import { RENDERERS } from '../assets/index.js';
import { drawRoundedRect, hexToRgba, easeOutCubic } from './geometry.js';

const OBJ_COLOR = '#e67e22';
const OBJ_TEXT_COLOR = '#fff';
const OBJ_WIDTH = 32;
const OBJ_HEIGHT = 18;
const ANIM_DURATION = 600;
const OBJ_TRANSITION_MS = 400;
const LABEL_COLOR = '#7f8c8d';
const HZ_WIDTH = 80;
const HZ_HEIGHT = 140;

let canvas = null;
let ctx = null;
let bgCanvas = null;
let deviceScale = 1;
let viewScale = 1;
let logPanel = null;

export function initRender(canvasEl, logPanelEl) {
    canvas = canvasEl;
    ctx = canvas.getContext('2d');
    logPanel = logPanelEl;
}

export function setupCanvasResolution() {
    const cfg = state.config;
    if (!cfg) return;
    const wrapper = document.getElementById('canvas-wrapper');
    const wrapperWidth = wrapper ? wrapper.clientWidth : cfg.canvas_width;
    viewScale = Math.min(1, wrapperWidth / cfg.canvas_width);

    const displayWidth = Math.max(1, Math.round(cfg.canvas_width * viewScale));
    const displayHeight = Math.max(1, Math.round(cfg.canvas_height * viewScale));
    deviceScale = window.devicePixelRatio || 1;
    canvas.width = Math.round(displayWidth * deviceScale);
    canvas.height = Math.round(displayHeight * deviceScale);
    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${displayHeight}px`;
    ctx.setTransform(deviceScale * viewScale, 0, 0, deviceScale * viewScale, 0, 0);

    if (logPanel) logPanel.style.width = `${displayWidth}px`;
}

/* Build the static background layer (reach zones, handoff zones, intake/output, arm pivots). */
export function createBackgroundCanvas() {
    const cfg = state.config;
    if (!cfg) return;
    const renderScale = deviceScale * viewScale;
    bgCanvas = document.createElement('canvas');
    bgCanvas.width = Math.round(cfg.canvas_width * renderScale);
    bgCanvas.height = Math.round(cfg.canvas_height * renderScale);
    const bg = bgCanvas.getContext('2d');
    bg.setTransform(renderScale, 0, 0, renderScale, 0, 0);

    bg.fillStyle = '#fafafa';
    bg.fillRect(0, 0, cfg.canvas_width, cfg.canvas_height);

    // Reach zones (arm-only static geometry)
    (cfg.reach_zones || []).forEach(zone => {
        bg.beginPath();
        bg.arc(zone.cx, zone.cy, zone.radius, 0, Math.PI * 2);
        bg.fillStyle = hexToRgba(zone.color, 0.08);
        bg.fill();
        bg.setLineDash([8, 4]);
        bg.strokeStyle = hexToRgba(zone.color, 0.4);
        bg.lineWidth = 1.5;
        bg.stroke();
        bg.setLineDash([]);
    });

    // Handoff zones - only draw for connections involving at least one arm
    // Skip zones between non-arm assets (e.g., vehicle -> conveyor)
    let handoffIndex = 1;
    state.connections.forEach((conn) => {
        const fromAsset = state.assets.get(conn.from);
        const toAsset = state.assets.get(conn.to);
        
        // Skip if both ends are non-arm assets
        if (fromAsset && toAsset && 
            fromAsset.type !== 'arm' && toAsset.type !== 'arm') {
            return;
        }
        
        drawHandoffZone(bg, conn.from, conn.to, handoffIndex);
        handoffIndex++;
    });

    // Intake
    const intake = cfg.intake_zone;
    if (intake) {
        drawRoundedRect(bg, intake.x, intake.y, intake.width, intake.height, 4);
        bg.fillStyle = hexToRgba(intake.color, 0.2);
        bg.fill();
        bg.strokeStyle = intake.color;
        bg.lineWidth = 1.5;
        bg.stroke();

        bg.fillStyle = LABEL_COLOR;
        bg.font = 'bold 11px sans-serif';
        bg.textAlign = 'center';
        bg.fillText(intake.label, intake.label_x, intake.label_y);

        const arrowSize = 8;
        const ex = intake.entry_x;
        const ey = intake.y + 3;
        bg.beginPath();
        bg.moveTo(ex - arrowSize, ey);
        bg.lineTo(ex + arrowSize, ey);
        bg.lineTo(ex, ey + arrowSize + 2);
        bg.closePath();
        bg.fillStyle = intake.color;
        bg.fill();
    }

    // Output
    const output = cfg.output_zone;
    if (output) {
        drawRoundedRect(bg, output.x, output.y, output.width, output.height, 4);
        bg.fillStyle = hexToRgba(output.color, 0.15);
        bg.fill();
        bg.strokeStyle = output.color;
        bg.lineWidth = 1.5;
        bg.stroke();

        bg.fillStyle = LABEL_COLOR;
        bg.font = 'bold 11px sans-serif';
        bg.textAlign = 'center';
        bg.fillText(output.label, output.label_x, output.label_y);
    }

    // Arm pivots + labels (kept on background for stable positioning)
    for (const a of state.assets.values()) {
        if (a.type !== 'arm') continue;
        const { pivot, length } = a.geometry;

        bg.fillStyle = LABEL_COLOR;
        bg.font = '13px sans-serif';
        bg.textAlign = 'center';
        bg.fillText(a.label, pivot.x, pivot.y + length + 18);

        bg.beginPath();
        bg.arc(pivot.x, pivot.y, 8, 0, Math.PI * 2);
        bg.fillStyle = a.color;
        bg.fill();
        bg.strokeStyle = '#fff';
        bg.lineWidth = 1.5;
        bg.stroke();
    }
}

function drawHandoffZone(bg, fromId, toId, index) {
    const from = state.assets.get(fromId);
    const to = state.assets.get(toId);
    if (!from || !to) return;

    const pos = computeHandoffPosition(from, to);
    if (!pos) return;

    drawRoundedRect(
        bg,
        pos.x - HZ_WIDTH / 2,
        pos.y - HZ_HEIGHT / 2,
        HZ_WIDTH,
        HZ_HEIGHT,
        6
    );
    bg.fillStyle = 'rgba(243, 156, 18, 0.15)';
    bg.fill();
    bg.setLineDash([6, 4]);
    bg.strokeStyle = '#f39c12';
    bg.lineWidth = 1.5;
    bg.stroke();
    bg.setLineDash([]);

    bg.fillStyle = LABEL_COLOR;
    bg.font = '11px sans-serif';
    bg.textAlign = 'center';
    bg.fillText(`Handoff ${index}`, pos.x, pos.y - HZ_HEIGHT / 2 - 4);
}

function computeHandoffPosition(from, to) {
    if (from.type === 'arm' && to.type === 'arm') {
        const fromAnchor = armBoundaryToward(from, to.geometry.pivot);
        const toAnchor = armBoundaryToward(to, from.geometry.pivot);
        return midpoint(fromAnchor, toAnchor);
    }
    if (from.type === 'arm' && to.type === 'vehicle') {
        const vStart = getVehicleStart(to);
        return midpoint(armBoundaryToward(from, vStart), vStart);
    }
    if (from.type === 'vehicle' && to.type === 'conveyor') {
        return midpoint(getVehicleEnd(from), to.geometry.from);
    }
    if (from.type === 'conveyor' && to.type === 'arm') {
        const cOut = from.geometry.to;
        return midpoint(cOut, armBoundaryToward(to, cOut));
    }
    if (from.type === 'vehicle' && to.type === 'arm') {
        const vEnd = getVehicleEnd(from);
        return midpoint(vEnd, armBoundaryToward(to, vEnd));
    }
    if (from.type === 'arm') {
        const target = getAssetCenter(to);
        return midpoint(armBoundaryToward(from, target), target);
    }
    if (to.type === 'arm') {
        const src = getAssetCenter(from);
        return midpoint(src, armBoundaryToward(to, src));
    }
    return midpoint(getAssetCenter(from), getAssetCenter(to));
}

function midpoint(a, b) {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function getArmTip(asset) {
    const { pivot, length } = asset.geometry;
    return { x: pivot.x + length, y: pivot.y };
}

function armBoundaryToward(armAsset, target) {
    const { pivot, length } = armAsset.geometry;
    const dx = target.x - pivot.x;
    const dy = target.y - pivot.y;
    const mag = Math.hypot(dx, dy) || 1;
    return {
        x: pivot.x + (dx / mag) * length,
        y: pivot.y + (dy / mag) * length,
    };
}

function getVehicleStart(asset) {
    const w = asset.geometry.waypoints || [];
    return w[0] || getAssetCenter(asset);
}

function getVehicleEnd(asset) {
    const w = asset.geometry.waypoints || [];
    return w[w.length - 1] || getAssetCenter(asset);
}

function getAssetCenter(asset) {
    if (asset.type === 'arm') return asset.geometry.pivot;
    if (asset.type === 'vehicle') {
        const start = getVehicleStart(asset);
        const end = getVehicleEnd(asset);
        return midpoint(start, end);
    }
    if (asset.type === 'conveyor') {
        return midpoint(asset.geometry.from, asset.geometry.to);
    }
    return { x: 0, y: 0 };
}

function computeDisplayAngle(asset, now) {
    const d = asset.dynamic;
    const tr = d.transition;
    if (!tr) return d.targetAngle;
    const elapsed = now - tr.startTime;
    if (elapsed >= tr.duration) {
        d.transition = null;
        return tr.to;
    }
    const t = easeOutCubic(elapsed / tr.duration);
    return tr.from + (tr.to - tr.from) * t;
}

function drawObject(obj, pos) {
    const x = pos.x - OBJ_WIDTH / 2;
    const y = pos.y - OBJ_HEIGHT / 2;
    drawRoundedRect(ctx, x, y, OBJ_WIDTH, OBJ_HEIGHT, 3);
    ctx.fillStyle = OBJ_COLOR;
    ctx.fill();
    ctx.strokeStyle = '#d35400';
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.fillStyle = OBJ_TEXT_COLOR;
    ctx.font = 'bold 10px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(obj.object_id, pos.x, pos.y);
    ctx.textBaseline = 'alphabetic';
}

function getObjectPosition(obj) {
    const cfg = state.config;
    if (!cfg) return null;

    // Carried by an asset (arm or vehicle) — attach point updates per frame.
    if (obj.state === 'carried') {
        if (obj.carrier_asset) return getAssetAttachPoint(obj.carrier_asset);
        if (obj.carrier_arm != null) return getAssetAttachPoint(`arm-${obj.carrier_arm}`);
        return null;
    }
    // On a conveyor slot — locate asset + slot.
    if (typeof obj.state === 'string' && obj.state.startsWith('on_')) {
        // e.g. "on_conv-1#2"
        const rest = obj.state.slice(3);
        const hashIdx = rest.lastIndexOf('#');
        if (hashIdx >= 0) {
            const assetId = rest.slice(0, hashIdx);
            const slot = parseInt(rest.slice(hashIdx + 1), 10);
            return getAssetAttachPoint(assetId, slot);
        }
    }
    if (obj.state === 'intake') {
        const iz = cfg.intake_zone;
        if (!iz) return null;
        const intakeObjs = Object.values(state.trackedObjects)
            .filter(o => o.state === 'intake')
            .map(o => o.object_id)
            .sort();
        const idx = intakeObjs.indexOf(obj.object_id);
        const total = intakeObjs.length;
        const padding = 8;
        const availableHeight = iz.height - padding * 2;
        const maxSpacing = OBJ_HEIGHT + 6;
        const spacing = Math.min(maxSpacing, availableHeight / Math.max(total, 1));
        const bottomY = iz.y + iz.height - padding - OBJ_HEIGHT / 2;
        return { x: iz.entry_x, y: bottomY - (total - 1 - idx) * spacing };
    }
    if (typeof obj.state === 'string' && obj.state.startsWith('handoff_')) {
        const idx = parseInt(obj.state.split('_')[1], 10) - 1;
        const hz = (cfg.handoff_zones || [])[idx];
        if (hz) {
            const arms = Array.from(state.assets.values()).filter(a => a.type === 'arm');
            const pivotY = arms[0]?.geometry.pivot.y ?? 300;
            return { x: hz.x + hz.width / 2, y: pivotY };
        }
    }
    if (obj.state === 'output') {
        const oz = cfg.output_zone;
        if (!oz) return null;
        const outputObjs = Object.values(state.trackedObjects)
            .filter(o => o.state === 'output')
            .map(o => o.object_id)
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

/* Render tracked objects + objects currently occupying conveyor slots (not in trackedObjects). */
function renderObjects(now) {
    // Backend-tracked objects
    Object.values(state.trackedObjects).forEach(obj => {
        const targetPos = getObjectPosition(obj);
        if (!targetPos) return;

        const id = obj.object_id;
        const transition = state.objectTransitions[id];
        let pos = targetPos;

        // 'carried' hard-locks to carrier tip (arm/vehicle) — already smooth.
        // 'on_X#N' conveyor slots get an easing transition when a slot shift
        // just occurred; otherwise snap to slot center.
        if (obj.state === 'carried') {
            delete state.objectTransitions[id];
            pos = targetPos;
        } else if (transition) {
            const duration = transition.duration || OBJ_TRANSITION_MS;
            const elapsed = now - transition.startTime;
            if (elapsed < duration) {
                const t = easeOutCubic(elapsed / duration);
                pos = {
                    x: transition.fromX + (targetPos.x - transition.fromX) * t,
                    y: transition.fromY + (targetPos.y - transition.fromY) * t,
                };
            } else {
                delete state.objectTransitions[id];
            }
        }
        state.objectPositions[id] = pos;
        drawObject(obj, pos);
    });
}

export function renderFrame() {
    const cfg = state.config;
    if (!cfg) return;

    ctx.clearRect(0, 0, cfg.canvas_width, cfg.canvas_height);
    if (bgCanvas) ctx.drawImage(bgCanvas, 0, 0, cfg.canvas_width, cfg.canvas_height);

    const now = performance.now();

    // Update arm displayed angles, then render every asset via its type renderer.
    for (const asset of state.assets.values()) {
        if (asset.type === 'arm') {
            asset.dynamic.displayedAngle = computeDisplayAngle(asset, now);
        }
    }

    drawConnectionAnchors();

    for (const asset of state.assets.values()) {
        const r = RENDERERS[asset.type];
        if (r && r.render) r.render(ctx, asset, now, { config: cfg });
    }

    renderObjects(now);
    drawFlashes(now);
}

/* Small neutral dots at junction points replacing the hidden handoff boxes. */
function drawConnectionAnchors() {
    const points = [];
    for (const a of state.assets.values()) {
        if (a.type === 'vehicle') {
            const wp = a.geometry.waypoints;
            if (wp.length) {
                points.push(wp[0]);
                points.push(wp[wp.length - 1]);
            }
        } else if (a.type === 'conveyor') {
            points.push(a.geometry.from);
            points.push(a.geometry.to);
        }
    }
    ctx.save();
    for (const p of points) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#bdc3c7';
        ctx.fill();
        ctx.strokeStyle = '#7f8c8d';
        ctx.lineWidth = 1;
        ctx.stroke();
    }
    ctx.restore();
}

function drawFlashes(now) {
    if (!state.flashes.length) return;
    const kept = [];
    ctx.save();
    for (const f of state.flashes) {
        const elapsed = now - f.startTime;
        if (elapsed >= f.duration) continue;
        const p = elapsed / f.duration;
        const r = 6 + p * 18;
        ctx.beginPath();
        ctx.arc(f.x, f.y, r, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(241, 196, 15, ${1 - p})`;
        ctx.lineWidth = 2;
        ctx.stroke();
        kept.push(f);
    }
    ctx.restore();
    state.flashes = kept;
}
