/* Asset renderer registry + unified predicates. */

import * as arm from './arm.js';
import * as vehicle from './vehicle.js';

export const RENDERERS = { arm, vehicle };

export function canPick(asset, slotHint) {
    const r = RENDERERS[asset.type];
    return !!(r && r.canPick && r.canPick(asset, slotHint));
}

export function canPlace(src, dst, slotHint) {
    const r = RENDERERS[dst.type];
    if (!r || !r.canDrop) return false;
    if (!src.carriedObject) return false;
    if (dst.draining) return false;
    if (dst.status === 'dead' || dst.status === 'stopped') return false;
    return r.canDrop(dst, slotHint);
}
