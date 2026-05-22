/* Shared asset status vocabulary + colors. */

export const STATUS_COLORS = {
    alive:      '#27ae60',
    idle:       '#27ae60',
    busy:       '#3498db',
    unhealthy:  '#f39c12',
    draining:   '#f39c12',
    recovering: '#f1c40f',
    stopped:    '#e74c3c',
    dead:       '#e74c3c',
    failed:     '#e74c3c',
};

export function statusColor(status) {
    return STATUS_COLORS[status] || '#27ae60';
}

export function isDegraded(status) {
    return status === 'dead' || status === 'stopped' || status === 'failed';
}

export function isAlive(status) {
    return status === 'alive' || status === 'idle';
}
