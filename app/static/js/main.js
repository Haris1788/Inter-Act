/* Entry point. Fetches /config, wires socket, starts render + tick loop. */

import { setConfig, state, applyObjectUpdate } from './core/state.js';
import { initRender, setupCanvasResolution, createBackgroundCanvas, renderFrame } from './core/render.js';
import { initSocket } from './core/socket.js';
import { initLog } from './core/log.js';
import { initNotif } from './core/notif.js';
import { tick } from './core/tick.js';
import { initControls } from './ui/controls.js';

const BACKEND_URL = window.BACKEND_URL || '';

async function init() {
    const canvas = document.getElementById('sim-canvas');
    const logPanel = document.getElementById('log-panel');
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');

    initLog(logPanel);
    initNotif();
    initRender(canvas, logPanel);

    let cfg;
    try {
        const resp = await fetch(BACKEND_URL + '/config');
        cfg = await resp.json();
    } catch (err) {
        console.error('Failed to load scene config:', err);
        return;
    }

    setConfig(cfg);
    setupCanvasResolution();
    createBackgroundCanvas();

    // Seed target/displayed angles for arms from default.
    for (const a of state.assets.values()) {
        if (a.type === 'arm') {
            const angle = cfg.default_angle ?? 90;
            a.dynamic.angle = angle;
            a.dynamic.targetAngle = angle;
            a.dynamic.displayedAngle = angle;
        }
    }

    // Sync object state (best-effort).
    try {
        const resp = await fetch(BACKEND_URL + '/objects');
        const data = await resp.json();
        if (data && data.objects) {
            data.objects.forEach(applyObjectUpdate);
        }
    } catch (e) { /* non-critical */ }

    initSocket({ backendUrl: BACKEND_URL, statusDot, statusText });
    initControls(BACKEND_URL);

    window.addEventListener('resize', () => {
        if (!state.config) return;
        setupCanvasResolution();
        createBackgroundCanvas();
    });

    function loop(now) {
        tick(now);
        renderFrame();
        requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
}

init();
