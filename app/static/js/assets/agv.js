/* AGV renderer. Position is backend-driven (agv_update -> state.js sets a
 * px transition); this module just eases between fromX/Y and targetX/Y and
 * draws the AGV body, label and status. Mirrors the arm/vehicle renderer
 * module shape: render(ctx, asset, now, env) + canPick/canDrop stubs. */

import { drawRoundedRect, easeOutCubic } from '../core/geometry.js';
import { statusColor } from '../core/status.js';

const BODY_W = 40;
const BODY_H = 28;

/* Current eased pixel position from the active transition (set in state.js). */
export function agvPosition(asset, now) {
    const d = asset.dynamic;
    const tr = d.transition;
    if (!tr) return { x: d.x, y: d.y };
    const elapsed = now - tr.startTime;
    if (elapsed >= tr.duration) {
        d.transition = null;
        d.x = tr.toX;
        d.y = tr.toY;
        return { x: d.x, y: d.y };
    }
    const t = easeOutCubic(elapsed / tr.duration);
    d.x = tr.fromX + (tr.toX - tr.fromX) * t;
    d.y = tr.fromY + (tr.toY - tr.fromY) * t;
    return { x: d.x, y: d.y };
}

export function render(ctx, asset, now) {
    const pos = agvPosition(asset, now);
    if (pos.x == null || pos.y == null) return;

    const faulted = asset.status === 'faulted' || asset.status === 'stopped';
    const x = pos.x - BODY_W / 2;
    const y = pos.y - BODY_H / 2;

    ctx.save();
    if (faulted) ctx.globalAlpha = 0.55;

    // Body
    drawRoundedRect(ctx, x, y, BODY_W, BODY_H, 5);
    ctx.fillStyle = faulted ? '#7f8c8d' : asset.color;
    ctx.fill();
    ctx.strokeStyle = statusColor(asset.status);
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Carried-package pip
    if (asset.carriedObject) {
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(pos.x, pos.y - 1, 5, 0, Math.PI * 2);
        ctx.fill();
    }

    // Id label centred on the body
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 10px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const short = String(asset.label || asset.id).replace(/^AGV\s*/i, 'A');
    ctx.fillText(short, pos.x, pos.y + (asset.carriedObject ? 7 : 0));
    ctx.textBaseline = 'alphabetic';
    ctx.restore();

    // Status badge above the body when not nominal
    const s = asset.status;
    if (s && s !== 'idle' && s !== 'alive') {
        ctx.save();
        ctx.fillStyle = statusColor(s);
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(s.toUpperCase(), pos.x, y - 6);
        ctx.restore();
    }
}

/* AGVs are not part of the arm/vehicle pick/drop graph (Phase 5 handles
 * package transfer); keep stubs so the RENDERERS contract stays uniform. */
export function canPick() { return false; }
export function canDrop() { return false; }
