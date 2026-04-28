/* Minimal UI controls: demo button + per-asset kill/recover + debug overlay. */

import { state, setDebugOverlay } from '../core/state.js';
import { logLocal } from '../core/log.js';
import { addNotification } from '../core/notif.js';

let backendUrl = '';
let demoPollingId = null;

export function initControls(url) {
    backendUrl = url || '';
    wireDemoButton();
    renderAssetControls();
    renderDebugToggle();
}

function wireDemoButton() {
    const btn = document.getElementById('demo-btn');
    if (!btn) return;
    btn.addEventListener('click', toggleDemo);
}

function toggleDemo() {
    const btn = document.getElementById('demo-btn');
    if (!btn || btn.classList.contains('running')) return;

    btn.textContent = 'Running...';
    btn.classList.add('running');
    btn.classList.remove('done');

    fetch(backendUrl + '/demo/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ products: 3, speed: 'normal' }),
    })
        .then(r => r.json())
        .then(() => {
            demoPollingId = setInterval(checkDemoStatus, 1000);
        })
        .catch(() => {
            btn.textContent = 'Run Demo';
            btn.classList.remove('running');
        });
}

function checkDemoStatus() {
    fetch(backendUrl + '/demo/status')
        .then(r => r.json())
        .then(data => {
            if (!data.running) {
                clearInterval(demoPollingId);
                const btn = document.getElementById('demo-btn');
                btn.textContent = 'Complete';
                btn.classList.remove('running');
                btn.classList.add('done');
                setTimeout(() => {
                    btn.textContent = 'Run Demo';
                    btn.classList.remove('done');
                }, 3000);
            }
        })
        .catch(() => {});
}

/* Per-asset Kill / Recover buttons (vehicles + conveyors only). */
function renderAssetControls() {
    let panel = document.getElementById('asset-controls');
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'asset-controls';
        const statusBar = document.getElementById('status-bar');
        if (statusBar && statusBar.parentNode) {
            statusBar.parentNode.insertBefore(panel, statusBar.nextSibling);
        } else {
            document.body.appendChild(panel);
        }
    }
    panel.innerHTML = '';

    for (const asset of state.assets.values()) {
        if (asset.type !== 'vehicle' && asset.type !== 'conveyor') continue;

        const killBtn = document.createElement('button');
        killBtn.textContent = `Kill ${asset.label}`;
        killBtn.className = 'control-btn control-btn-danger asset-kill-btn';
        killBtn.addEventListener('click', () => failAsset(asset.id));
        panel.appendChild(killBtn);

        const recoverBtn = document.createElement('button');
        recoverBtn.textContent = `Recover ${asset.label}`;
        recoverBtn.className = 'control-btn control-btn-ghost asset-recover-btn';
        recoverBtn.addEventListener('click', () => recoverAsset(asset.id));
        panel.appendChild(recoverBtn);
    }
}

function failAsset(assetId) {
    fetch(backendUrl + `/assets/${assetId}/fail`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'manually killed' }),
    }).then(r => r.json()).then(() => {
        logLocal(assetId, 'Kill signal sent to backend');
    }).catch(() => {
        addNotification('error', assetId, 'Kill request failed');
    });
}

function recoverAsset(assetId) {
    fetch(backendUrl + `/assets/${assetId}/recover`, { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            logLocal(assetId, 'Recover signal sent to backend');
        })
        .catch(() => {
            addNotification('error', assetId, 'Recover request failed');
        });
}

/* Debug overlay toggle: shows asset ids, ownership, connection boundaries. */
function renderDebugToggle() {
    let panel = document.getElementById('asset-controls');
    if (!panel) return;
    const btn = document.createElement('button');
    btn.textContent = 'Debug: off';
    btn.className = 'control-btn control-btn-debug asset-debug-btn';
    btn.addEventListener('click', () => {
        const next = !state.debugOverlay;
        setDebugOverlay(next);
        btn.textContent = next ? 'Debug: on' : 'Debug: off';
        btn.classList.toggle('is-active', next);
    });
    panel.appendChild(btn);
}
