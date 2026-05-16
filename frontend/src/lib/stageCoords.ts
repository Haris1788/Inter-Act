// The prototype authors everything in a fixed 1320x720 coordinate space and
// scales it into the canvas pane. We reuse those coordinates verbatim as React
// Flow *world* coordinates so the initial layout matches pixel-for-pixel while
// gaining infinite pan/zoom for free.
export const STAGE_W = 1320;
export const STAGE_H = 720;

// Chrome insets that overlay the canvas pane in the React layout:
//   T — goal bar (top-center, ~y64 + height)   B — message-bus strip (h46)
//   L/R — floating toolbar / zoom / fleet card breathing room
// (The prototype's original PAD_R:220 assumed an inspector overlapping the
//  canvas; here the inspector is its own grid column, so we center instead.)
export const PAD = { L: 24, R: 24, T: 120, B: 64 } as const;

// Fit the fixed 1320x720 stage into the pane and CENTER it (matches the
// prototype's visual framing regardless of pane aspect ratio).
export function computeFit(paneW: number, paneH: number) {
  const cw = paneW - PAD.L - PAD.R;
  const ch = paneH - PAD.T - PAD.B;
  const zoom = Math.max(0.4, Math.min(cw / STAGE_W, ch / STAGE_H));
  const x = PAD.L + (cw - STAGE_W * zoom) / 2;
  const y = PAD.T + (ch - STAGE_H * zoom) / 2;
  return { x, y, zoom };
}

// Bounding rect of the authored stage — used for React Flow fitView.
export const STAGE_RECT = { x: 0, y: 0, width: STAGE_W, height: STAGE_H };
