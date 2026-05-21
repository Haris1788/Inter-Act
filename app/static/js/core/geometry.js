/* Geometry helpers shared across asset renderers and ticks. */

export function lerp(a, b, t) {
    return a + (b - a) * t;
}

export function lerpPoint(p1, p2, t) {
    return { x: lerp(p1.x, p2.x, t), y: lerp(p1.y, p2.y, t) };
}

export function segmentLen(p1, p2) {
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    return Math.hypot(dx, dy);
}

export function polylineInterp(waypoints, segIndex, t) {
    if (!waypoints || waypoints.length === 0) return { x: 0, y: 0 };
    if (waypoints.length === 1) return { ...waypoints[0] };
    const i = Math.max(0, Math.min(segIndex, waypoints.length - 2));
    return lerpPoint(waypoints[i], waypoints[i + 1], Math.max(0, Math.min(1, t)));
}

export function distance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

export function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function drawRoundedRect(c, x, y, w, h, r) {
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

export function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}
