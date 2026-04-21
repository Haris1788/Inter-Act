/* Per-frame tick for vehicle + conveyor. Frontend-only.
 * Handles proximity handoff between arms, vehicles, and conveyors. */

import { state, getAssetAttachPoint, advanceTransit, addFlash } from './state.js';
import { RENDERERS, canPick } from '../assets/index.js';
import { distance } from './geometry.js';
import { logLocal } from './log.js';
import { addNotification } from './notif.js';

const PROXIMITY_PX = 14;             // strict handoff distance
const TRANSIT_HOP_PX = 12;           // distance within which transit object moves to next chain asset

let lastNow = null;

export function tick(now) {
    const dt = lastNow == null ? 0 : Math.min(0.1, (now - lastNow) / 1000);
    lastNow = now;
    if (dt <= 0) return;

    const env = {
        tryConveyorOutput,
        onSlotShift: (a, from, to) => {
            const objId = a.dynamic.slots[to];
            if (!objId) return;
            const existing = state.trackedObjects[objId];
            if (existing) existing.state = `on_${a.id}#${to}`;
            // Seed a smooth transition from the previous slot position.
            const prev = a.geometry.slotPositions[from];
            if (prev) {
                state.objectTransitions[objId] = {
                    fromX: prev.x,
                    fromY: prev.y,
                    startTime: performance.now(),
                    duration: 900,   // slower than default 400 — visible slide
                };
            }
        },
    };

    for (const asset of state.assets.values()) {
        const r = RENDERERS[asset.type];
        if (r && r.tick) r.tick(asset, dt, env);
    }

    driveTransits();
    checkProximityHandoffs();
}

/* Advance any in-flight transit: when carrier reaches proximity of next
 * chain asset's attach point, hop object to it. When last chain asset
 * holds the object and next hop is the destination arm, do nothing — we
 * wait for backend to emit `carried arm_N` which triggers endTransit. */
function driveTransits() {
    for (const [objId, t] of state.transit.entries()) {
        const cur = t.route[t.phase];
        const next = t.route[t.phase + 1];
        if (!cur) continue;

        // Vehicle phase: advance when vehicle reaches its downstream end waypoint.
        if (cur.type === 'vehicle') {
            const wp = cur.geometry.waypoints;
            const endPos = wp[wp.length - 1];
            const pos = getAssetAttachPoint(cur.id);
            if (!pos) continue;
            if (distance(pos, endPos) <= TRANSIT_HOP_PX) {
                if (next && (next.type === 'conveyor' || next.type === 'vehicle')) {
                    advanceTransit(objId, cur, next);
                }
            }
        }

        // Conveyor phase: object shifts through slots via conveyor tick.
        // Nothing further to drive here — backend arm pick ends transit.
    }
}

/* Attempt to move last-slot conveyor object to a neighbor (downstream).
 * Skipped for transit-tracked objects — backend decides when arm picks up. */
function tryConveyorOutput(conveyor, slotIdx) {
    const objId = conveyor.dynamic.slots[slotIdx];
    if (!objId) return false;
    if (state.transit.has(objId)) return false;        // wait for backend arm pickup
    const exitPos = conveyor.geometry.slotPositions[slotIdx];

    for (const downId of conveyor.downstream) {
        const dst = state.assets.get(downId);
        if (!dst) continue;
        if (!(dst.type === 'arm' || dst.type === 'vehicle')) continue;
        const dstPos = getAssetAttachPoint(dst.id);
        if (!dstPos) continue;
        if (distance(exitPos, dstPos) > PROXIMITY_PX) continue;

        if (dst.carriedObject == null && canPick(dst)) {
            conveyor.dynamic.slots[slotIdx] = null;
            dst.carriedObject = objId;
            updateCarriedObject(objId, dst);
            addFlash(dstPos);
            logLocal(conveyor.id, `Handoff ${objId} \u2192 ${dst.label}`);
            console.log('TRANSFER:', conveyor.id, '\u2192', dst.id);
            return true;
        }
    }
    return false;
}

/* Strict proximity handoffs with explicit type guards enforcing the pipeline
 * shape: arm→vehicle, vehicle→conveyor, conveyor→arm. Arm→arm and arm→conveyor
 * direct transfers are blocked so the flow always routes through the chain. */
function checkProximityHandoffs() {
    for (const asset of state.assets.values()) {
        if (asset.carriedObject == null) continue;
        if (state.transit.has(asset.carriedObject)) continue;  // transit driver owns hops
        const srcPos = getAssetAttachPoint(asset.id);
        if (!srcPos) continue;

        for (const downId of asset.downstream) {
            const dst = state.assets.get(downId);
            if (!dst) continue;
            if (!isAllowedPair(asset.type, dst.type)) continue;

            const dstPos = getAssetAttachPoint(dst.id);
            if (!dstPos) continue;
            if (distance(srcPos, dstPos) > PROXIMITY_PX) continue;

            if (dst.type === 'conveyor') {
                if (dst.dynamic.slots[0] != null || dst.draining) continue;
                const objId = asset.carriedObject;
                dst.dynamic.slots[0] = objId;
                asset.carriedObject = null;
                updateConveyorSlot(objId, dst, 0);
                addFlash(dstPos);
                logLocal(asset.id, `Loaded ${objId} onto ${dst.label}[1]`);
                console.log('TRANSFER:', asset.id, '\u2192', dst.id);
                break;
            }

            if (dst.type === 'vehicle' || dst.type === 'arm') {
                if (dst.carriedObject != null) continue;
                if (!canPick(dst)) continue;
                const objId = asset.carriedObject;
                asset.carriedObject = null;
                dst.carriedObject = objId;
                updateCarriedObject(objId, dst);
                addFlash(dstPos);
                logLocal(asset.id, `Passed ${objId} to ${dst.label}`);
                console.log('TRANSFER:', asset.id, '\u2192', dst.id);
                break;
            }
        }
    }
}

/* Explicit allow-list: keeps object flow threaded through vehicle+conveyor. */
function isAllowedPair(srcType, dstType) {
    if (srcType === 'arm' && dstType === 'vehicle') return true;
    if (srcType === 'vehicle' && dstType === 'conveyor') return true;
    if (srcType === 'conveyor' && dstType === 'arm') return true;
    if (srcType === 'vehicle' && dstType === 'arm') return true;   // vehicle direct-to-arm OK
    // Blocked: arm\u2192arm, arm\u2192conveyor, conveyor\u2192vehicle, etc.
    return false;
}

function updateCarriedObject(objId, carrier) {
    const existing = state.trackedObjects[objId] || { object_id: objId };
    existing.state = 'carried';
    existing.carrier_asset = carrier.id;
    if (carrier.type === 'arm' && carrier.numericId != null) {
        existing.carrier_arm = carrier.numericId;
    } else {
        existing.carrier_arm = null;
    }
    state.trackedObjects[objId] = existing;
    delete state.objectTransitions[objId];
}

function updateConveyorSlot(objId, conveyor, slotIdx) {
    const existing = state.trackedObjects[objId] || { object_id: objId };
    existing.state = `on_${conveyor.id}#${slotIdx}`;
    existing.carrier_asset = conveyor.id;
    existing.carrier_arm = null;
    state.trackedObjects[objId] = existing;
    delete state.objectTransitions[objId];
}

export function notifyLocalDrain(asset) {
    addNotification(
        asset.status === 'dead' ? 'error' : 'info',
        `${asset.label} \u2014 ${asset.status === 'dead' ? 'Failed' : 'Draining'}`,
        asset.error || 'Local drain',
    );
}
