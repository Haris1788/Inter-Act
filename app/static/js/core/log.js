/* Browser log panel. */

const MAX_LOG_LINES = 100;
let logPanel = null;

export function initLog(panelEl) {
    logPanel = panelEl;
}

export function appendLogEntry(data) {
    if (!logPanel) return;
    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = `[${data.timestamp}] `;

    const source = document.createElement('span');
    source.className = `log-source ${data.source}`;
    source.textContent = `${data.source} `;

    const msg = document.createTextNode(data.message);

    entry.appendChild(time);
    entry.appendChild(source);
    entry.appendChild(msg);
    logPanel.appendChild(entry);

    while (logPanel.children.length > MAX_LOG_LINES) {
        logPanel.removeChild(logPanel.firstChild);
    }
    logPanel.scrollTop = logPanel.scrollHeight;
}

export function logLocal(source, message) {
    const timestamp = new Date().toTimeString().slice(0, 8);
    appendLogEntry({ timestamp, source, message });
}
