/* Socket.IO wiring — uses existing backend events unchanged. */

import {
    applyStateUpdate,
    applyArmHealth,
    applyObjectUpdate,
    resetObjects,
    state,
} from './state.js';
import { appendLogEntry } from './log.js';
import { addNotification } from './notif.js';

let socket = null;
let statusDot = null;
let statusText = null;
let backendUrl = '';

export function initSocket({ backendUrl: url, statusDot: dotEl, statusText: textEl }) {
    backendUrl = url || '';
    statusDot = dotEl;
    statusText = textEl;

    socket = window.io(backendUrl || undefined);

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('state_update', applyStateUpdate);
    socket.on('object_update', applyObjectUpdate);
    socket.on('objects_reset', resetObjects);
    socket.on('arm_health', (data) => {
        const prev = state.assets.get(`arm-${data.arm_id}`)?.status || 'idle';
        applyArmHealth(data);
        notifyHealthChange(data, prev);
    });
    socket.on('log_message', appendLogEntry);
}

function onConnect() {
    if (statusDot) statusDot.classList.add('connected');
    if (statusText) statusText.textContent = 'Connected';

    fetch(backendUrl + '/state').then(r => r.json()).then(applyStateUpdate).catch(() => {});
    fetch(backendUrl + '/objects').then(r => r.json()).then(data => {
        if (data && data.objects) {
            resetObjects();
            data.objects.forEach(obj => { state.trackedObjects[obj.object_id] = obj; });
        }
    }).catch(() => {});
    fetch(backendUrl + '/arms/health').then(r => r.json()).then(data => {
        if (data && data.arms) {
            for (const [id, h] of Object.entries(data.arms)) {
                applyArmHealth({ arm_id: parseInt(id, 10), health: h.health, error: h.error });
            }
        }
    }).catch(() => {});
}

function onDisconnect() {
    if (statusDot) statusDot.classList.remove('connected');
    if (statusText) statusText.textContent = 'Disconnected';
}

function notifyHealthChange(data, prev) {
    const next = data.health;
    if (prev === next) return;
    const asset = state.assets.get(`arm-${data.arm_id}`);
    const label = asset?.label || `Arm ${data.arm_id}`;
    switch (next) {
        case 'dead':
            addNotification('error', `${label} \u2014 Failed`, data.error || 'Container stopped responding');
            break;
        case 'draining':
            addNotification('info', `${label} \u2014 Draining`, data.error || 'Processing remaining products');
            break;
        case 'stopped':
            addNotification('warning', `${label} \u2014 Stopped`, data.error || 'Drain complete');
            break;
        case 'unhealthy':
            addNotification('warning', `${label} \u2014 Unhealthy`, data.error || 'Health check failed');
            break;
        case 'alive':
            if (prev !== 'idle') addNotification('success', `${label} \u2014 Recovered`, 'Back online');
            break;
    }
}

export function getSocket() {
    return socket;
}

export function emitObjectEvent(body) {
    // Best-effort POST to keep backend log/object tracking in sync for arm-initiated handoffs.
    fetch(backendUrl + '/object_event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).catch(() => {});
}
