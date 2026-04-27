/* Central frontend state.
 *
 * Phase 3 contract: this module is a pure reflection of backend state.
 * The frontend NEVER simulates pipeline progress. Every position update
 * comes from a SocketIO event (`state_update`, `arm_health`, `asset_health`,
 * `object_update`). When backend emits `carried` for a vehicle, it includes
 * `start_time` + `duration_ms`; the frontend interpolates between the
 * vehicle's first and last waypoint based on those timestamps. When backend
 * emits `on_<conv>#<slot>`, the frontend snaps the object to that slot.
 */

import { polylineInterp } from './geometry.js';

export const state = {
    config: null,
    assets: new Map(),                  // id -> Asset
    connections: [],                    // [{from,to}] derived from asset graph
    trackedObjects: {},                 // object_id -> backend record (see normalizeObj)
    objectPositions: {},                // object_id -> last rendered {x,y}
    objectTransitions: {},              // object_id -> {fromX,fromY,startTime,duration} (arm easing only)
    flashes: [],                        // [{x,y,startTime,duration}] visual transfer pulses
    debugOverlay: false,                // toggled by ui/controls.js
};

export function setConfig(cfg) {
    state.config = cfg;
    state.assets.clear();
    for (const a of buildAssets(cfg)) {
        state.assets.set(a.id, a);
    }
    state.connections = buildConnections(state.assets);
}

/* Build graph edges. Arm->arm chain is suppressed when a non-arm path bridges the pair. */
export function buildConnections(assets) {
    const connections = [];
    const seen = new Set();

    function add(from, to) {
        if (!from || !to || from === to) return;
        const key = `${from}->${to}`;
        if (seen.has(key)) return;
        seen.add(key);
        connections.push({ from, to });
    }

    for (const asset of assets.values()) {
        for (const downstreamId of asset.downstream || []) {
            if (assets.has(downstreamId)) add(asset.id, downstreamId);
        }
    }

    const arms = Array.from(assets.values())
        .filter(a => a.type === 'arm')
        .sort((a, b) => (a.numericId ?? 0) - (b.numericId ?? 0));
    for (let i = 0; i < arms.length - 1; i++) {
        const src = arms[i].id;
        const dst = arms[i + 1].id;
        if (hasNonArmPathBetween(assets, src, dst)) continue;
        add(src, dst);
    }

    return sortConnectionsByFlowOrder(connections, assets);
}

function sortConnectionsByFlowOrder(connections, assets) {
    return connections.sort((a, b) => {
        const fromA = assets.get(a.from);
        const fromB = assets.get(b.from);
        if (!fromA || !fromB) return 0;
        return getAssetFlowX(fromA) - getAssetFlowX(fromB);
    });
}

function getAssetFlowX(asset) {
    if (asset.type === 'arm') return asset.geometry.pivot.x;
    if (asset.type === 'vehicle') {
        const w = asset.geometry.waypoints || [];
        return w[0] ? w[0].x : 0;
    }
    if (asset.type === 'conveyor') return asset.geometry.from.x;
    return 0;
}

function hasNonArmPathBetween(assets, srcId, dstId) {
    const queue = [{ id: srcId, usedNonArm: false }];
    const seen = new Set([`${srcId}|0`]);
    while (queue.length) {
        const { id, usedNonArm } = queue.shift();
        const cur = assets.get(id);
        if (!cur) continue;
        for (const nextId of cur.downstream || []) {
            const next = assets.get(nextId);
            if (!next) continue;
            const nextUsedNonArm = usedNonArm || next.type !== 'arm';
            if (nextId === dstId && nextUsedNonArm) return true;
            const key = `${nextId}|${nextUsedNonArm ? 1 : 0}`;
            if (seen.has(key)) continue;
            seen.add(key);
            queue.push({ id: nextId, usedNonArm: nextUsedNonArm });
        }
    }
    return false;
}

function buildAssets(cfg) {
    const out = [];
    const seen = new Set();
    if (Array.isArray(cfg.assets)) {
        for (const raw of cfg.assets) {
            const a = normalizeAsset(raw);
            if (a) { out.push(a); seen.add(a.id); }
        }
    }
    if (Array.isArray(cfg.arms)) {
        for (const arm of cfg.arms) {
            const id = `arm-${arm.id}`;
            if (seen.has(id)) continue;
            out.push({
                id,
                numericId: arm.id,
                type: 'arm',
                label: arm.label,
                color: arm.color,
                status: 'idle',
                error: null,
                currentTask: null,
                carriedObject: null,
                upstream: (arm.depends_on || []).map(depId => `arm-${depId}`),
                downstream: [],
                geometry: {
                    pivot: { x: arm.pivot_x, y: arm.pivot_y },
                    length: arm.length,
                    minAngle: cfg.min_angle ?? 0,
                    maxAngle: cfg.max_angle ?? 180,
                },
                dynamic: {
                    angle: cfg.default_angle ?? 90,
                    displayedAngle: cfg.default_angle ?? 90,
                    targetAngle: cfg.default_angle ?? 90,
                    transition: null,
                },
            });
        }
    }
    wireAssetGraph(out);
    return out;
}

function normalizeAsset(raw) {
    if (!raw || !raw.type) return null;
    const base = {
        id: raw.id,
        type: raw.type,
        label: raw.label || raw.id,
        color: raw.color || '#888',
        status: 'idle',
        error: null,
        currentTask: null,
        carriedObject: null,
        upstream: raw.upstream || [],
        downstream: raw.downstream || [],
    };
    switch (raw.type) {
        case 'arm': {
            const g = raw.geometry || {};
            const numericId = typeof raw.id === 'string' && raw.id.startsWith('arm-')
                ? parseInt(raw.id.slice(4), 10)
                : raw.numericId;
            return {
                ...base,
                numericId,
                geometry: {
                    pivot: g.pivot || { x: 0, y: 0 },
                    length: g.length ?? 150,
                    minAngle: g.min_angle ?? 0,
                    maxAngle: g.max_angle ?? 180,
                },
                dynamic: { angle: 90, displayedAngle: 90, targetAngle: 90, transition: null },
            };
        }
        case 'vehicle': {
            const g = raw.geometry || {};
            return {
                ...base,
                geometry: {
                    waypoints: g.waypoints || [],
                    loop: !!g.loop,
                    speed: g.speed ?? 100,
                },
                // dynamic.t reflects backend-driven progress in [0,1]; updated
                // on every render frame from the carried object's timestamps.
                dynamic: { t: 0, frozenAt: null },
            };
        }
        case 'conveyor': {
            const g = raw.geometry || {};
            const slotCount = g.slot_count ?? 3;
            const slotPositions = g.slot_positions || computeSlotPositions(g.from, g.to, slotCount);
            return {
                ...base,
                geometry: {
                    from: g.from || { x: 0, y: 0 },
                    to: g.to || { x: 0, y: 0 },
                    slotCount,
                    slotPositions,
                    shiftInterval: g.shift_interval ?? 1.0,
                },
                // slots are filled by backend object_update events with `slot` field.
                dynamic: { slots: new Array(slotCount).fill(null) },
            };
        }
    }
    return null;
}

function computeSlotPositions(from, to, n) {
    if (!from || !to || n <= 0) return [];
    if (n === 1) return [{ x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 }];
    const out = [];
    for (let i = 0; i < n; i++) {
        const t = i / (n - 1);
        out.push({ x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t });
    }
    return out;
}

function wireAssetGraph(assets) {
    const byId = new Map(assets.map(a => [a.id, a]));
    const armIds = assets
        .filter(a => a.type === 'arm')
        .map(a => a.numericId)
        .filter(v => Number.isFinite(v))
        .sort((a, b) => a - b);

    for (const asset of assets) {
        asset.downstream = Array.isArray(asset.downstream)
            ? asset.downstream.filter(id => byId.has(id))
            : [];
    }

    for (const asset of assets) {
        for (const upId of (asset.upstream || [])) {
            const up = byId.get(upId);
            if (!up) continue;
            if (!Array.isArray(up.downstream)) up.downstream = [];
            if (!up.downstream.includes(asset.id)) up.downstream.push(asset.id);
        }
    }

    for (const asset of assets) {
        if (asset.type === 'arm') continue;
        if ((asset.downstream || []).length > 0) continue;
        const upstreamArmNums = (asset.upstream || [])
            .map(id => byId.get(id))
            .filter(a => a && a.type === 'arm' && Number.isFinite(a.numericId))
            .map(a => a.numericId);
        if (!upstreamArmNums.length) continue;
        const maxUp = Math.max(...upstreamArmNums);
        const nextArmNum = armIds.find(n => n > maxUp);
        if (!nextArmNum) continue;
        const nextArmId = `arm-${nextArmNum}`;
        if (!byId.has(nextArmId)) continue;
        asset.downstream.push(nextArmId);
        const nextArm = byId.get(nextArmId);
        if (!Array.isArray(nextArm.upstream)) nextArm.upstream = [];
        if (!nextArm.upstream.includes(asset.id)) nextArm.upstream.push(asset.id);
    }
}

/* ── Backend event appliers ──────────────────────────────────── */

/* state_update — arm angles. */
export function applyStateUpdate(data) {
    if (!data || !data.arms) return;
    const now = performance.now();
    const ANIM = 600;
    for (const arm of data.arms) {
        const asset = state.assets.get(`arm-${arm.arm_id}`);
        if (!asset || asset.type !== 'arm') continue;
        const d = asset.dynamic;
        const newTarget = arm.angle;
        if (d.targetAngle === undefined || newTarget !== d.targetAngle) {
            d.transition = { from: d.displayedAngle, to: newTarget, startTime: now, duration: ANIM };
        }
        d.targetAngle = newTarget;
    }
}

/* arm_health — arm status. */
export function applyArmHealth(data) {
    if (!data || data.arm_id == null) return;
    const asset = state.assets.get(`arm-${data.arm_id}`);
    if (!asset) return;
    asset.status = data.health;
    asset.error = data.error || null;
}

/* asset_health — non-arm asset status (Phase 3 new event). */
export function applyAssetHealth(data) {
    if (!data || !data.asset_id) return;
    const asset = state.assets.get(data.asset_id);
    if (!asset) return;
    const next = data.status;
    asset.status = next;
    asset.error = data.error || null;
    // Freeze any object riding this asset at its current rendered position.
    if (next === 'failed') {
        for (const obj of Object.values(state.trackedObjects)) {
            if (obj.carrier_asset === asset.id) {
                const pos = state.objectPositions[obj.object_id];
                if (pos) obj._frozen = { ...pos };
            }
        }
    } else if (next === 'idle' || next === 'recovering' || next === 'busy') {
        for (const obj of Object.values(state.trackedObjects)) {
            if (obj.carrier_asset === asset.id) delete obj._frozen;
        }
    }
}

/* object_update — single source of truth for object positions. */
export function applyObjectUpdate(data) {
    if (!data || !data.object_id) return;
    const id = data.object_id;
    const old = state.trackedObjects[id];

    // Detach from previous carrier asset.
    if (old) {
        const prevCarrierId = old.carrier_asset || (old.carrier_arm != null ? `arm-${old.carrier_arm}` : null);
        const prev = prevCarrierId ? state.assets.get(prevCarrierId) : null;
        if (prev) {
            if (prev.carriedObject === id) prev.carriedObject = null;
            if (prev.type === 'conveyor') {
                const idx = prev.dynamic.slots.indexOf(id);
                if (idx >= 0) prev.dynamic.slots[idx] = null;
            }
            // Vehicle returns to its start waypoint after handing the object
            // off, so it is positioned for the next pickup. Backend does not
            // emit a return-trip — this is a deterministic visual reset.
            if (prev.type === 'vehicle') {
                prev.dynamic.t = 0;
                prev.dynamic.frozenAt = null;
            }
        }
    }

    state.trackedObjects[id] = data;

    // Attach to new carrier asset.
    const carrierId = data.carrier_asset || (data.carrier_arm != null ? `arm-${data.carrier_arm}` : null);
    const carrier = carrierId ? state.assets.get(carrierId) : null;
    if (carrier) {
        if (carrier.type === 'arm' || carrier.type === 'vehicle') {
            carrier.carriedObject = id;
        } else if (carrier.type === 'conveyor' && typeof data.slot === 'number') {
            const slots = carrier.dynamic.slots;
            // Clear any other slot still holding this object (shift case).
            for (let i = 0; i < slots.length; i++) {
                if (slots[i] === id && i !== data.slot) slots[i] = null;
            }
            slots[data.slot] = id;
        }
    }

    // Arm-zone easing for legacy intake/handoff/output snap states.
    const isCarried = data.state === 'carried';
    const isOnAsset = typeof data.state === 'string' &&
        (data.state.startsWith('on_') || data.state.startsWith('at_'));
    if (isCarried || isOnAsset) {
        delete state.objectTransitions[id];
        return;
    }
    if (old && state.objectPositions[id]) {
        state.objectTransitions[id] = {
            fromX: state.objectPositions[id].x,
            fromY: state.objectPositions[id].y,
            startTime: performance.now(),
            duration: 400,
        };
    }
}

export function resetObjects() {
    state.trackedObjects = {};
    state.objectPositions = {};
    state.objectTransitions = {};
    for (const a of state.assets.values()) {
        a.carriedObject = null;
        if (a.type === 'conveyor') a.dynamic.slots.fill(null);
        if (a.type === 'vehicle') { a.dynamic.t = 0; a.dynamic.frozenAt = null; }
    }
}

/* ── Position resolution ─────────────────────────────────────── */

export function addFlash(pos) {
    if (!pos) return;
    state.flashes.push({ x: pos.x, y: pos.y, startTime: performance.now(), duration: 450 });
}

/* Compute current attach point for an asset.
 * For vehicles, t comes from the carried object's start_time + duration_ms. */
export function getAssetAttachPoint(assetId, slotHint) {
    const a = state.assets.get(assetId);
    if (!a) return null;
    switch (a.type) {
        case 'arm': {
            const { pivot, length } = a.geometry;
            const rad = a.dynamic.displayedAngle * Math.PI / 180;
            return { x: pivot.x + length * Math.cos(rad), y: pivot.y - length * Math.sin(rad) };
        }
        case 'vehicle': {
            const wp = a.geometry.waypoints;
            if (!wp.length) return { x: 0, y: 0 };
            const t = vehicleProgress(a);
            // Single-segment lerp from first to last waypoint.
            const start = wp[0], end = wp[wp.length - 1];
            return { x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t };
        }
        case 'conveyor': {
            const slots = a.geometry.slotPositions;
            if (slotHint != null && slots[slotHint]) return { ...slots[slotHint] };
            const occ = a.dynamic.slots.findIndex(s => s != null);
            return { ...(slots[occ >= 0 ? occ : 0] || { x: 0, y: 0 }) };
        }
    }
    return null;
}

/* Vehicle progress in [0,1] derived from its currently carried object's
 * backend timestamps. Frozen if asset.status === 'failed'. */
function vehicleProgress(asset) {
    if (asset.status === 'failed' && asset.dynamic.frozenAt != null) {
        return asset.dynamic.frozenAt;
    }
    const objId = asset.carriedObject;
    if (!objId) {
        // No carried object — sit at start (idle) or end (after delivery).
        return asset.dynamic.t ?? 0;
    }
    const obj = state.trackedObjects[objId];
    if (!obj) return 0;
    const start = obj.start_time;
    const dur = obj.duration_ms;
    if (!start || !dur || dur <= 0) {
        return asset.dynamic.t ?? 0;
    }
    const elapsed = Date.now() - start;
    let t = elapsed / dur;
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    if (asset.status === 'failed') {
        asset.dynamic.frozenAt = t;
    } else {
        asset.dynamic.frozenAt = null;
    }
    asset.dynamic.t = t;
    return t;
}

export function firstOccupiedSlot(asset) {
    return asset.dynamic.slots.findIndex(s => s != null);
}

export function getAssetsByType(type) {
    const out = [];
    for (const a of state.assets.values()) if (a.type === type) out.push(a);
    return out;
}

/* Debug overlay toggle. */
export function setDebugOverlay(on) {
    state.debugOverlay = !!on;
}
