/* Vehicle renderer + tick (polyline path + moving dot). */

import { polylineInterp, segmentLen, hexToRgba } from '../core/geometry.js';
import { statusColor, isDegraded } from '../core/status.js';

const DOT_RADIUS = 11;

export function render(ctx, asset, now, env) {
    const wp = asset.geometry.waypoints;
    if (wp.length < 2) return;

    const degraded = isDegraded(asset.status);

    // Path (dashed)
    ctx.save();
    ctx.setLineDash([6, 6]);
    ctx.lineWidth = 2;
    const pathColor = asset.status === 'draining' ? '#f39c12'
                    : (degraded ? '#bbb' : hexToRgba(asset.color, 0.45));
    ctx.strokeStyle = pathColor;
    ctx.beginPath();
    ctx.moveTo(wp[0].x, wp[0].y);
    for (let i = 1; i < wp.length; i++) ctx.lineTo(wp[i].x, wp[i].y);
    ctx.stroke();
    ctx.setLineDash([]);

    // Waypoint markers
    ctx.fillStyle = hexToRgba(asset.color, 0.6);
    for (const p of wp) ctx.fillRect(p.x - 3, p.y - 3, 6, 6);
    ctx.restore();

    // Vehicle dot
    const pos = polylineInterp(wp, asset.dynamic.segIndex, asset.dynamic.t);
    ctx.save();
    if (degraded) ctx.globalAlpha = 0.45;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, DOT_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = degraded ? '#999' : asset.color;
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();

    // Heading arrow toward next waypoint
    drawHeadingArrow(ctx, asset, pos);

    // Label + status dot
    drawLabel(ctx, asset, pos);

    // Status badge
    drawStatusBadge(ctx, asset, pos);
}

function drawHeadingArrow(ctx, asset, pos) {
    const wp = asset.geometry.waypoints;
    const d = asset.dynamic;
    const dir = d.direction ?? 1;
    const targetIdx = dir > 0
        ? Math.min(d.segIndex + 1, wp.length - 1)
        : Math.max(d.segIndex, 0);
    const next = wp[targetIdx];
    if (!next || (next.x === pos.x && next.y === pos.y)) return;
    const dx = next.x - pos.x;
    const dy = next.y - pos.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const tipX = pos.x + ux * (DOT_RADIUS + 6);
    const tipY = pos.y + uy * (DOT_RADIUS + 6);
    const baseX = pos.x + ux * (DOT_RADIUS + 1);
    const baseY = pos.y + uy * (DOT_RADIUS + 1);
    const perpX = -uy * 4, perpY = ux * 4;
    ctx.beginPath();
    ctx.moveTo(tipX, tipY);
    ctx.lineTo(baseX + perpX, baseY + perpY);
    ctx.lineTo(baseX - perpX, baseY - perpY);
    ctx.closePath();
    ctx.fillStyle = statusColor(asset.status);
    ctx.fill();
}

// Label anchored to first waypoint so it does not drift with the dot.
function drawLabel(ctx, asset, pos) {
    const wp = asset.geometry.waypoints;
    const anchor = wp[0] || pos;
    const labelX = (wp[0] && wp[wp.length - 1])
        ? (wp[0].x + wp[wp.length - 1].x) / 2
        : anchor.x;
    const labelY = anchor.y + DOT_RADIUS + 20;

    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center';
    const labelWidth = ctx.measureText(asset.label).width;

    const dotRadius = 4;
    const dotX = labelX - labelWidth / 2 - dotRadius - 4;
    ctx.beginPath();
    ctx.arc(dotX, labelY - 4, dotRadius, 0, Math.PI * 2);
    ctx.fillStyle = statusColor(asset.status);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.fillStyle = '#7f8c8d';
    ctx.fillText(asset.label, labelX, labelY);
}

function drawStatusBadge(ctx, asset, pos) {
    const s = asset.status;
    if (s === 'alive' || s === 'idle') return;
    const labels = { dead: 'FAILED', stopped: 'STOPPED', draining: 'DRAINING', unhealthy: 'UNHEALTHY' };
    const colors = { dead: '#e74c3c', stopped: '#95a5a6', draining: '#f39c12', unhealthy: '#f39c12' };
    ctx.fillStyle = colors[s] || '#f39c12';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(labels[s] || s.toUpperCase(), pos.x, pos.y - DOT_RADIUS - 8);
}

export function tick(asset, dt) {
    const d = asset.dynamic;
    const g = asset.geometry;
    if (g.waypoints.length < 2) return;
    if (asset.status === 'dead' || asset.status === 'stopped') return;

    if (d.direction == null) d.direction = 1;

    // Active segment is always between waypoints[segIndex] and waypoints[segIndex+1].
    // Forward: t 0→1. Reverse: t 1→0.
    const segStart = g.waypoints[d.segIndex];
    const segEnd = g.waypoints[d.segIndex + 1];
    if (!segEnd) return;
    const len = segmentLen(segStart, segEnd);
    if (len <= 0) { advanceSegment(asset); return; }

    const step = (g.speed * dt) / len;
    d.t += step * d.direction;

    if (d.t >= 1) {
        d.t = 1;
        advanceSegment(asset);
    } else if (d.t <= 0) {
        d.t = 0;
        advanceSegment(asset);
    }
}

function advanceSegment(asset) {
    const d = asset.dynamic;
    const g = asset.geometry;
    const lastSeg = g.waypoints.length - 2;

    if (d.direction > 0) {
        // Reached end of current forward segment.
        if (d.segIndex < lastSeg) {
            d.segIndex++;
            d.t = 0;
        } else if (g.loop) {
            d.segIndex = 0;
            d.t = 0;
        } else {
            // Bounce: reverse direction, keep segIndex at last, t stays at 1.
            d.direction = -1;
            if (asset.draining) asset.status = 'dead';
        }
    } else {
        // Reversing: reached start of current segment.
        if (d.segIndex > 0) {
            d.segIndex--;
            d.t = 1;
        } else {
            // Reached beginning — flip back forward.
            d.direction = 1;
            if (asset.draining) asset.status = 'dead';
        }
    }
}

export function getAttachPoint(asset) {
    return polylineInterp(asset.geometry.waypoints, asset.dynamic.segIndex, asset.dynamic.t);
}

export function canPick(asset) {
    return !asset.draining && asset.status !== 'dead' && asset.status !== 'stopped'
        && asset.carriedObject == null;
}

export function canDrop(asset) {
    return !asset.draining && asset.status !== 'dead' && asset.status !== 'stopped';
}
