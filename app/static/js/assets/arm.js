/* Arm renderer + attach-point (migrated from simulation.js drawArm). */

import { statusColor, isDegraded } from '../core/status.js';

const ARM_LINE_WIDTH = 4;
const PIVOT_RADIUS = 8;
const TIP_RADIUS = 6;

export function render(ctx, asset, now, env) {
    const { pivot, length } = asset.geometry;
    const angleDeg = asset.dynamic.displayedAngle;
    const angleRad = angleDeg * Math.PI / 180;
    const tipX = pivot.x + length * Math.cos(angleRad);
    const tipY = pivot.y - length * Math.sin(angleRad);

    const status = asset.status;
    const isDead = status === 'dead';
    const isStopped = status === 'stopped';
    const isDraining = status === 'draining';
    const isUnhealthy = status === 'unhealthy';
    const degraded = isDegraded(status);

    ctx.save();
    if (degraded) ctx.globalAlpha = isDead ? 0.3 : 0.4;

    ctx.beginPath();
    ctx.moveTo(pivot.x, pivot.y);
    ctx.lineTo(tipX, tipY);
    ctx.strokeStyle = degraded ? '#999' : asset.color;
    ctx.lineWidth = ARM_LINE_WIDTH;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(tipX, tipY, TIP_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = degraded ? '#999' : asset.color;
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.restore();

    drawStatusIndicator(ctx, asset, pivot, { isDead, isStopped, isDraining, isUnhealthy }, now);
    drawArmLabelDot(ctx, asset, pivot, length, status);
}

function drawStatusIndicator(ctx, asset, pivot, flags, now) {
    if (flags.isDead) {
        const sz = 7;
        const markerY = pivot.y - PIVOT_RADIUS - 18;
        ctx.strokeStyle = '#e74c3c';
        ctx.lineWidth = 2.5;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(pivot.x - sz, markerY - sz);
        ctx.lineTo(pivot.x + sz, markerY + sz);
        ctx.moveTo(pivot.x + sz, markerY - sz);
        ctx.lineTo(pivot.x - sz, markerY + sz);
        ctx.stroke();
        ctx.fillStyle = '#e74c3c';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('FAILED', pivot.x, markerY - sz - 5);
    } else if (flags.isStopped) {
        ctx.fillStyle = '#95a5a6';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('STOPPED', pivot.x, pivot.y - PIVOT_RADIUS - 10);
    } else if (flags.isDraining) {
        ctx.fillStyle = '#f39c12';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('DRAINING', pivot.x, pivot.y - PIVOT_RADIUS - 10);
    } else if (flags.isUnhealthy) {
        const pulse = 0.5 + 0.5 * Math.sin(now / 300);
        ctx.beginPath();
        ctx.arc(pivot.x, pivot.y, PIVOT_RADIUS + 5, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(243, 156, 18, ${0.3 + pulse * 0.5})`;
        ctx.lineWidth = 2.5;
        ctx.stroke();
    }
}

function drawArmLabelDot(ctx, asset, pivot, length, status) {
    const labelY = pivot.y + length + 18;
    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center';
    const labelWidth = ctx.measureText(asset.label).width;
    const dotRadius = 4;
    const dotX = pivot.x - labelWidth / 2 - dotRadius - 4;
    const dotCenterY = labelY - 4;
    ctx.beginPath();
    ctx.arc(dotX, dotCenterY, dotRadius, 0, Math.PI * 2);
    ctx.fillStyle = statusColor(status);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();
}

export function canPick(asset) {
    return asset.status !== 'dead' && asset.status !== 'stopped'
        && asset.carriedObject == null;
}

export function canDrop(asset) {
    return asset.status !== 'dead' && asset.status !== 'stopped';
}
