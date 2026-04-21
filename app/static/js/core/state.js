/* Central state: assets Map + tracked objects + attach-point lookup. */

import { polylineInterp } from './geometry.js';

export const state = {
    config: null,
    assets: new Map(),          // id -> Asset
    connections: [],            // [{from,to}] derived from asset graph
    trackedObjects: {},         // object_id -> {object_id, state, carrier_arm, carrier_asset?}
    objectPositions: {},        // object_id -> {x, y}
    objectTransitions: {},      // object_id -> {fromX, fromY, startTime}
    draining: new Set(),        // asset ids flagged drain locally (for vehicle/conveyor)
    // Visual transit: maps backend "handoff_N" for an object onto a chain of
    // non-arm assets so it visibly traverses vehicle/conveyor. Backend still
    // thinks object sits in handoff zone — purely a rendering reroute.
    transit: new Map(),         // object_id -> {route:Asset[], phase:number}
    flashes: [],                // [{x,y,startTime,duration}] transfer flash overlay
};

export function setConfig(cfg) {
    state.config = cfg;
    state.assets.clear();
    for (const a of buildAssets(cfg)) {
        state.assets.set(a.id, a);
    }
    state.connections = buildConnections(state.assets);
}

/* Build graph edges from actual asset links.
 * Arm->arm baseline edges stay dynamic by arm order, and are suppressed only
 * when a complete non-arm path connects the same arm pair. */
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

    // 1) Explicit graph edges from config-provided downstream arrays.
    for (const asset of assets.values()) {
        for (const downstreamId of asset.downstream || []) {
            if (assets.has(downstreamId)) add(asset.id, downstreamId);
        }
    }

    // 2) Add baseline arm->arm chain for dynamic N-arm layout.
    // Skip only when an explicit non-arm route already bridges the same pair.
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

        const xA = getAssetFlowX(fromA);
        const xB = getAssetFlowX(fromB);
        return xA - xB;
    });
}

function getAssetFlowX(asset) {
    if (asset.type === 'arm') return asset.geometry.pivot.x;
    if (asset.type === 'vehicle') {
        const w = asset.geometry.waypoints || [];
        return w[0] ? w[0].x : 0;
    }
    if (asset.type === 'conveyor') {
        return asset.geometry.from.x;
    }
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

/* Build unified asset list from /config response.
 * Accepts either legacy arm list (cfg.arms) or new cfg.assets (or both). */
function buildAssets(cfg) {
    const out = [];
    const seen = new Set();

    if (Array.isArray(cfg.assets)) {
        for (const raw of cfg.assets) {
            const a = normalizeAsset(raw);
            if (a) { out.push(a); seen.add(a.id); }
        }
    }

    // Legacy arms fallback — inject as type:'arm' only if not already provided.
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
                draining: false,
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
        draining: false,
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
                dynamic: {
                    angle: 90, displayedAngle: 90, targetAngle: 90, transition: null,
                },
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
                dynamic: { segIndex: 0, t: 0, direction: 1 },
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
                    shiftInterval: g.shift_interval ?? 1.2,
                },
                dynamic: {
                    slots: new Array(slotCount).fill(null),
                    sinceShift: 0,
                },
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

/* Ensure graph edges are consistent:
 * - keep declared downstream links
 * - add reverse links from upstream declarations
 * This makes zone rendering truly graph-driven even when only upstream
 * is present in config (common for arm dependencies). */
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
        const ups = Array.isArray(asset.upstream) ? asset.upstream : [];
        for (const upId of ups) {
            const up = byId.get(upId);
            if (!up) continue;
            if (!Array.isArray(up.downstream)) up.downstream = [];
            if (!up.downstream.includes(asset.id)) up.downstream.push(asset.id);
        }
    }

    // If a non-arm asset has arm upstreams but no valid downstream, connect it
    // to the next arm in order so a partial asset chain still renders and hands off.
    for (const asset of assets) {
        if (asset.type === 'arm') continue;
        if ((asset.downstream || []).length > 0) continue;

        const upstreamArmNums = (asset.upstream || [])
            .map(id => byId.get(id))
            .filter(a => a && a.type === 'arm' && Number.isFinite(a.numericId))
            .map(a => a.numericId);
        if (upstreamArmNums.length === 0) continue;

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

/* Apply backend state_update for arms. */
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
            d.transition = {
                from: d.displayedAngle,
                to: newTarget,
                startTime: now,
                duration: ANIM,
            };
        }
        d.targetAngle = newTarget;
    }
}

/* Apply backend arm_health for arms. */
export function applyArmHealth(data) {
    if (!data || data.arm_id == null) return;
    const asset = state.assets.get(`arm-${data.arm_id}`);
    if (!asset) return;
    asset.status = data.health;
    asset.error = data.error || null;
    asset.draining = data.health === 'draining';
}

/* Apply backend object_update. Mirrors `carried` onto the arm asset's
 * `carriedObject` field so proximity-handoff logic can see what arms hold.
 * Intercepts backend `handoff_N` states when a non-arm chain exists between
 * the two arms: the object is routed through vehicle + conveyor visually. */
export function applyObjectUpdate(data) {
    if (!data || !data.object_id) return;
    const id = data.object_id;
    const old = state.trackedObjects[id];

    // Clear any previous carrier reference.
    if (old && (old.state === 'carried')) {
        const prev = state.assets.get(old.carrier_asset || `arm-${old.carrier_arm}`);
        if (prev && prev.carriedObject === id) prev.carriedObject = null;
    }

    // Backend took the object back (carried by arm, output, etc.) — end any transit.
    if (state.transit.has(id) && data.state !== 'handoff_1' && !data.state.startsWith('handoff_')) {
        endTransit(id);
    }

    // Redirect handoff_N → visual transit chain (vehicle, conveyor, ...).
    if (typeof data.state === 'string' && data.state.startsWith('handoff_')) {
        const n = parseInt(data.state.split('_')[1], 10);
        const chain = buildTransitRoute(n, n + 1);
        if (chain && chain.length > 0 && !state.transit.has(id)) {
            startTransit(id, chain, data);
            return;   // don't store raw handoff_N state — transit owns rendering
        }
    }

    state.trackedObjects[id] = data;

    if (data.state === 'carried') {
        const carrierId = data.carrier_asset || (data.carrier_arm != null ? `arm-${data.carrier_arm}` : null);
        const carrier = carrierId ? state.assets.get(carrierId) : null;
        if (carrier) carrier.carriedObject = id;
        delete state.objectTransitions[id];
        return;
    }
    if (old && state.objectPositions[id]) {
        state.objectTransitions[id] = {
            fromX: state.objectPositions[id].x,
            fromY: state.objectPositions[id].y,
            startTime: performance.now(),
        };
    }
}

/* Walk non-arm asset graph from arm-src to arm-dst via upstream/downstream. */
export function buildTransitRoute(srcArmId, dstArmId) {
    const srcId = `arm-${srcArmId}`;
    const dstId = `arm-${dstArmId}`;
    // Find first non-arm asset whose upstream includes srcId.
    let first = null;
    for (const a of state.assets.values()) {
        if (a.type === 'arm') continue;
        if (a.upstream && a.upstream.includes(srcId)) { first = a; break; }
    }
    if (!first) return null;

    const chain = [first];
    let cur = first;
    while (cur && cur.downstream && cur.downstream.length) {
        const nxt = state.assets.get(cur.downstream[0]);
        if (!nxt) break;
        if (nxt.id === dstId) return chain;
        if (nxt.type === 'arm') return null;       // wrong arm — abort
        chain.push(nxt);
        cur = nxt;
    }
    return null;
}

function startTransit(objId, chain, backendData) {
    const first = chain[0];
    let synthetic;
    if (first.type === 'vehicle') {
        first.carriedObject = objId;
        synthetic = { object_id: objId, state: 'carried', carrier_asset: first.id, carrier_arm: null };
    } else if (first.type === 'conveyor') {
        const freeIdx = first.dynamic.slots.findIndex(s => s == null);
        const slot = freeIdx >= 0 ? freeIdx : 0;
        first.dynamic.slots[slot] = objId;
        synthetic = { object_id: objId, state: `on_${first.id}#${slot}`, carrier_asset: first.id, carrier_arm: null };
    } else {
        return;
    }
    state.trackedObjects[objId] = synthetic;
    state.transit.set(objId, { route: chain, phase: 0, backend: backendData });
    delete state.objectTransitions[objId];
    addFlash(getAssetAttachPoint(first.id));
    console.log('[TRANSIT start]', objId, '\u2192', first.id);
}

export function endTransit(objId) {
    const t = state.transit.get(objId);
    if (!t) return;
    for (const a of t.route) {
        if (a.carriedObject === objId) a.carriedObject = null;
        if (a.type === 'conveyor') {
            const idx = a.dynamic.slots.indexOf(objId);
            if (idx >= 0) a.dynamic.slots[idx] = null;
        }
    }
    state.transit.delete(objId);
    console.log('[TRANSIT end]', objId);
}

export function advanceTransit(objId, currentAsset, nextAsset) {
    const t = state.transit.get(objId);
    if (!t) return;
    // Capture current screen position BEFORE detaching so transition can ease.
    const prevPos = state.objectPositions[objId] || getAssetAttachPoint(currentAsset.id);
    // Detach from current
    if (currentAsset.type === 'vehicle') currentAsset.carriedObject = null;
    if (currentAsset.type === 'conveyor') {
        const idx = currentAsset.dynamic.slots.indexOf(objId);
        if (idx >= 0) currentAsset.dynamic.slots[idx] = null;
    }
    // Attach to next
    let newState;
    if (nextAsset.type === 'vehicle') {
        nextAsset.carriedObject = objId;
        newState = 'carried';
    } else if (nextAsset.type === 'conveyor') {
        if (nextAsset.dynamic.slots[0] == null) {
            nextAsset.dynamic.slots[0] = objId;
            nextAsset.dynamic.sinceShift = 0;
        }
        newState = `on_${nextAsset.id}#0`;
    } else {
        newState = 'carried';
    }
    state.trackedObjects[objId] = {
        object_id: objId, state: newState, carrier_asset: nextAsset.id, carrier_arm: null,
    };
    // Seed smooth transition from previous visual position to new slot (conveyor only).
    if (prevPos && nextAsset.type === 'conveyor') {
        state.objectTransitions[objId] = {
            fromX: prevPos.x, fromY: prevPos.y,
            startTime: performance.now(),
            duration: 700,
        };
    } else {
        delete state.objectTransitions[objId];
    }
    t.phase++;
    addFlash(getAssetAttachPoint(nextAsset.id));
    console.log('[TRANSIT hop]', objId, currentAsset.id, '\u2192', nextAsset.id);
}

export function addFlash(pos) {
    if (!pos) return;
    state.flashes.push({ x: pos.x, y: pos.y, startTime: performance.now(), duration: 450 });
}

export function resetObjects() {
    state.trackedObjects = {};
    state.objectPositions = {};
    state.objectTransitions = {};
}

/* Compute current attach point for an asset (arm tip, vehicle pos, conveyor slot). */
export function getAssetAttachPoint(assetId, slotHint) {
    const a = state.assets.get(assetId);
    if (!a) return null;
    switch (a.type) {
        case 'arm': {
            const { pivot, length } = a.geometry;
            const rad = a.dynamic.displayedAngle * Math.PI / 180;
            return { x: pivot.x + length * Math.cos(rad), y: pivot.y - length * Math.sin(rad) };
        }
        case 'vehicle':
            return polylineInterp(a.geometry.waypoints, a.dynamic.segIndex, a.dynamic.t);
        case 'conveyor': {
            const slots = a.geometry.slotPositions;
            if (slotHint != null && slots[slotHint]) return { ...slots[slotHint] };
            const firstOcc = a.dynamic.slots.findIndex(s => s != null);
            return { ...(slots[firstOcc >= 0 ? firstOcc : 0] || { x: 0, y: 0 }) };
        }
    }
    return null;
}

/* Find the first occupied conveyor slot index. */
export function firstOccupiedSlot(asset) {
    return asset.dynamic.slots.findIndex(s => s != null);
}

export function getAssetsByType(type) {
    const out = [];
    for (const a of state.assets.values()) if (a.type === type) out.push(a);
    return out;
}
