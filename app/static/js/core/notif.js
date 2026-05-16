/* Toast notification system. */

const NOTIF_DURATION = 8000;
const MAX_NOTIFICATIONS = 5;
const ICONS = { error: '\u2715', warning: '\u26a0', success: '\u2713', info: '\u2139' };

let container = null;
let notifications = [];
let nextId = 0;

export function initNotif() {
    container = document.createElement('div');
    container.id = 'notif-container';
    document.body.appendChild(container);
    window.dismissNotif = dismissNotif;
}

export function addNotification(type, title, message) {
    if (!container) return;
    const id = nextId++;
    const timeStr = new Date().toTimeString().slice(0, 8);

    const el = document.createElement('div');
    el.className = `notif notif-${type}`;
    el.dataset.notifId = id;
    el.innerHTML = `
        <span class="notif-icon">${ICONS[type] || ICONS.info}</span>
        <div class="notif-body">
            <div class="notif-title">${title}</div>
            <div class="notif-msg">${message}</div>
        </div>
        <span class="notif-time">${timeStr}</span>
        <button class="notif-close" onclick="dismissNotif(${id})">\u00d7</button>
    `;

    container.prepend(el);
    requestAnimationFrame(() => el.classList.add('notif-show'));

    notifications.push({
        id,
        el,
        timer: setTimeout(() => dismissNotif(id), NOTIF_DURATION),
    });

    while (notifications.length > MAX_NOTIFICATIONS) {
        dismissNotif(notifications[0].id);
    }
}

export function dismissNotif(id) {
    const idx = notifications.findIndex(n => n.id === id);
    if (idx === -1) return;
    const { el, timer } = notifications[idx];
    clearTimeout(timer);
    notifications.splice(idx, 1);
    el.classList.add('notif-exit');
    el.addEventListener('animationend', () => el.remove());
}
