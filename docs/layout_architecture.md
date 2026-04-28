# Graph-driven layout architecture

## Overview

INTER.act uses a deterministic graph-driven layout from `pipeline.yaml`. The backend computes geometry and flow order, and the frontend renders from that output.

## Key Principles

### Single flow order
The backend builds `module_flow_order` and uses it to keep geometry and connection ordering stable.

### Flow-based positioning
Assets are positioned sequentially by topology:

1. build module order
2. place arms and non-arm assets from left to right
3. derive handoff boundaries from adjacent modules
4. compute canvas dimensions from final span

### Automatic geometry adjustment
- arms use normalized pivot/reach placement
- vehicles align waypoint track to the computed flow slot
- conveyors align start/end and slot spacing to flow slot

## Backend layout outputs

`app/models/pipeline_config.py` provides:

- normalized module geometry
- `asset_graph`
- `module_flow_order`
- graph validity flags

## Frontend Integration

### State building
The frontend reads `/config` and builds:

- `state.assets`
- `state.connections`
- transfer-point mapping derived from connections

### Handoff zone rendering
`core/render.js` draws zones from `state.connections`. Labels are topology-based (`fromId -> toId`) so ordering is stable and independent of transient index ordering.

### No frontend position recompute
Frontend does not invent topology or ownership positions. It renders backend-provided geometry and ownership state.

## Implementation Files

- `app/models/pipeline_config.py`: Layout engine implementation
  - `_compute_graph_layout()`
  - `_build_flow_order()`
  - `get_module_flow_order()`
  
- `app/static/js/core/state.js`: Frontend asset graph
  - `buildConnections()`: Connection graph builder
  - `buildAssets()`: Asset normalization
  
- `app/static/js/core/render.js`: Visual rendering
  - `drawHandoffZone()`
  - `computeHandoffPosition()`
