/* Conveyor renderer + tick (linear belt, N slots). */

import { statusColor, isDegraded } from '../core/status.js';

const BELT_WIDTH = 14;
const SLOT_RADIUS = 10;

export function render(ctx, asset, now, env) {
    const { from, to, slotPositions } = asset.geometry;
    const degraded = isDegraded(asset.status);

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineWidth = BELT_WIDTH;
    if (asset.status === 'draining') ctx.strokeStyle = '#f39c12';
    else if (degraded) ctx.strokeStyle = '#999';
    else ctx.strokeStyle = '#34495e';
    if (degraded && asset.status !== 'draining') ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
    ctx.restore();

    // Slot markers
    for (let i = 0; i < slotPositions.length; i++) {
        const p = slotPositions[i];
        ctx.beginPath();
        ctx.arc(p.x, p.y, SLOT_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = '#ecf0f1';
        ctx.fill();
        ctx.strokeStyle = '#7f8c8d';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Slot index label (small)
        ctx.fillStyle = '#95a5a6';
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(String(i + 1), p.x, p.y + SLOT_RADIUS + 11);
    }

    // Label + status dot at midpoint
    drawLabel(ctx, asset);

    // Status badge if not healthy
    drawStatusBadge(ctx, asset);
}

function drawLabel(ctx, asset) {
    const { from, to } = asset.geometry;
    const midX = (from.x + to.x) / 2;
    // Label below the belt, matching arm label placement.
    const labelY = Math.max(from.y, to.y) + SLOT_RADIUS + 34;

    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center';
    const labelWidth = ctx.measureText(asset.label).width;
    const dotRadius = 4;
    const dotX = midX - labelWidth / 2 - dotRadius - 4;
    ctx.beginPath();
    ctx.arc(dotX, labelY - 4, dotRadius, 0, Math.PI * 2);
    ctx.fillStyle = statusColor(asset.status);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.fillStyle = '#7f8c8d';
    ctx.fillText(asset.label, midX, labelY);
}

function drawStatusBadge(ctx, asset) {
    const s = asset.status;
    if (s === 'alive' || s === 'idle') return;
    const { from, to } = asset.geometry;
    const midX = (from.x + to.x) / 2;
    const badgeY = Math.min(from.y, to.y) - 14;
    const labels = { dead: 'FAILED', stopped: 'STOPPED', draining: 'DRAINING', unhealthy: 'UNHEALTHY' };
    const colors = { dead: '#e74c3c', stopped: '#95a5a6', draining: '#f39c12', unhealthy: '#f39c12' };
    ctx.fillStyle = colors[s] || '#f39c12';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(labels[s] || s.toUpperCase(), midX, badgeY);
}

/* Phase 3: tick removed. Slots populated by backend `object_update` events. */

export function getAttachPoint(asset, slotHint) {
    const slots = asset.geometry.slotPositions;
    if (slotHint != null && slots[slotHint]) return { ...slots[slotHint] };
    const firstOcc = asset.dynamic.slots.findIndex(s => s != null);
    return { ...(slots[firstOcc >= 0 ? firstOcc : 0] || { x: 0, y: 0 }) };
}

export function canPick(asset, slotHint) {
    if (asset.status === 'dead' || asset.status === 'stopped') return false;
    if (slotHint != null) return asset.dynamic.slots[slotHint] != null;
    return asset.dynamic.slots.some(s => s != null);
}

export function canDrop(asset, slotHint) {
    if (asset.draining) return false;
    if (asset.status === 'dead' || asset.status === 'stopped') return false;
    // Default drop slot is index 0 (input end).
    const idx = slotHint ?? 0;
    return asset.dynamic.slots[idx] == null;
}

export function freeInputSlot(asset) {
    return asset.dynamic.slots[0] == null ? 0 : -1;
}
