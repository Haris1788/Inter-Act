// LabelsOverlay - Domain 4 DOM labels.
//
// The legacy cap-tags, dock-state pill and "Decentralised negotiation"
// annotation were hardcoded to old prototype SVG coordinates (285,90 /
// 1035,90 / 310,355 / 495,138) that have no meaning in the warehouse-grid
// model. They floated mid-canvas and confused the scene, so they are removed.
//
// The component is kept (CanvasViewport mounts it) as an intentional no-op
// placeholder: warehouse labels are now drawn data-driven inside the Konva
// layers (WarehouseGridLayer / ArmLayer) from /config, not as fixed DOM tags.

export function LabelsOverlay() {
  return null;
}
