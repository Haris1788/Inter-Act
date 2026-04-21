/* Minimal UI controls: demo proxy + per-asset kill buttons for vehicle/conveyor. */

import { state } from '../core/state.js';
import { notifyLocalDrain } from '../core/tick.js';
import { logLocal } from '../core/log.js';

let backendUrl = '';
let demoPollingId = null;

export function initControls(url) {
    backendUrl = url || '';
    wireDemoButton();
    renderAssetControls();
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

/* Render a Kill button per non-arm asset (arms killed via backend). */
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
        const btn = document.createElement('button');
        btn.textContent = `Kill ${asset.label}`;
        btn.className = 'asset-kill-btn';
        btn.addEventListener('click', () => killAsset(asset.id, btn));
        panel.appendChild(btn);
    }
}

function killAsset(assetId, btn) {
    const asset = state.assets.get(assetId);
    if (!asset) return;
    if (asset.draining || asset.status === 'dead') return;
    asset.draining = true;
    asset.status = 'draining';
    notifyLocalDrain(asset);
    logLocal(asset.id, `Kill signal received \u2014 draining`);
    if (btn) btn.disabled = true;
}
