# Graph-Driven Layout Architecture

## Overview

The Inter Act pipeline uses a deterministic, graph-driven layout engine that automatically positions assets (arms, vehicles, conveyors) based on their connections in the pipeline graph.

## Key Principles

### Single Algorithm
The layout engine uses one algorithm for all cases, without conditional branching based on asset counts. Whether you have:
- Arms only
- Arms + vehicle
- Arms + vehicle + conveyor
- Any combination

The same layout algorithm handles all cases consistently.

### Flow-Based Positioning
Assets are positioned sequentially based on their flow order in the pipeline graph:

1. **Build flow order** from the asset graph using topological sort
2. **Assign block widths** based on asset type and geometry
3. **Compute cumulative positions** from left to right
4. **Adjust asset geometry** to fit the flow layout
5. **Calculate canvas dimensions** from total width

### Automatic Geometry Adjustment
When assets are positioned in the flow:
- **Arms**: Positioned with consistent spacing
- **Vehicles**: Waypoints are adjusted to fit the current layout position (preserving path length)
- **Conveyors**: From/to endpoints are adjusted to fit the current layout position (preserving belt length)

This ensures all assets connect properly without gaps or overlaps.

## Layout Algorithm

### Flow Order Construction

The `_build_flow_order` function:
1. Separates arms from non-arm assets
2. Sorts arms by numeric ID for stable ordering
3. Identifies connection gaps between arms
4. Inserts vehicles/conveyors into appropriate gaps
5. Returns ordered list: [arm-1, arm-2, vehicle, arm-3, ...]

### Position Calculation

The `_compute_graph_layout` function uses boundary-based positioning:

1. Starts at `LEFT_PADDING` (120px)
2. For each asset in flow order:
   - Calculate gap based on adjacent types:
     - **Arm-to-arm**: 0px (circles touch exactly)
     - **Other pairs**: 40px (room for handoff zones)
   - **Arm**: Position pivot at `x + gap + length`, advance to `pivot + length`
   - **Vehicle**: Adjust waypoints to start at `x + gap`, advance by `path_length`
   - **Conveyor**: Adjust from/to to start at `x + gap`, advance by `belt_length`
3. Final canvas width = `x + RIGHT_PADDING`

This ensures:
- Arm reach circles touch perfectly (no gap, no overlap)
- Non-arm assets have space for handoff zones
- Layout stays compact and connected

## Example Layouts

### Arms Only (3 arms)
```
Arm 1: pivot at 270, reach 120-420
Arm 2: pivot at 570, reach 420-720 (circles touch at 420)
Arm 3: pivot at 870, reach 720-1020 (circles touch at 720)
Canvas: 1140 x 600

Handoff zones: 2 (between each arm pair)
```

### Arms + Vehicle
```
Arm 1: pivot at 270, reach 120-420
Arm 2: pivot at 570, reach 420-720 (circles touch at 420)
[40px gap for handoff zone]
Vehicle: 760 → 930 (adjusted from original 730 → 900)
[40px gap for handoff zone]
Arm 3: pivot at 1120, reach 970-1270
Canvas: 1390 x 600

Handoff zones: 3 (arm-arm, arm-vehicle, vehicle-arm)
```

### Arms + Vehicle + Conveyor
```
Arm 1: pivot at 270, reach 120-420
Arm 2: pivot at 570, reach 420-720 (circles touch at 420)
[40px gap]
Vehicle: 760 → 930
[40px gap]
Conveyor: 970 → 1320 (adjusted from original 900 → 1250)
[40px gap]
Arm 3: pivot at 1510, reach 1360-1660
Canvas: 1780 x 600

Handoff zones: 4 (arm-arm, arm-vehicle, vehicle-conveyor, conveyor-arm)
Note: Zones appear at all asset type boundaries
```

## Frontend Integration

### State Building
The frontend `state.js` builds the asset graph from `/config` endpoint:
- Reads `cfg.arms` and `cfg.assets`
- Normalizes all assets into unified `state.assets` Map
- Builds `state.connections` from upstream/downstream links
- No special handling for repositioned geometry - uses what backend provides

### Handoff Zone Rendering
The frontend `render.js` draws handoff zones dynamically:
- Iterates `state.connections` array
- **Skips only self-connections** (asset connecting to itself - should never occur)
- Draws zones for all valid connections between different assets
- Calls `computeHandoffPosition(from, to)` for each connection
- Uses asset geometry (pivot, waypoints, from/to) to calculate zone position
- Adapts zone placement based on asset types (arm-arm, arm-vehicle, vehicle-conveyor, etc.)

This ensures:
- Handoff zones appear at all transfer boundaries
- Arm-to-arm zones are preserved
- Vehicle-to-conveyor zone is visible
- No duplicate or invalid zones

### No Position Recalculation
The frontend never recalculates asset positions. It:
- Reads geometry from config as-is
- Draws assets at their specified positions
- Computes handoff zones from existing geometry
- Maintains separation of concerns: backend = layout, frontend = rendering

## Configuration

### YAML Structure
```yaml
arms:
  - id: 1
    label: "Arm 1"
    depends_on: []
  - id: 2
    label: "Arm 2"
    depends_on: [1]

assets:
  - id: veh-a
    type: vehicle
    waypoints:
      - { x: 730, y: 300 }  # Original positions
      - { x: 900, y: 300 }  # Will be adjusted by layout engine
    upstream: [arm-2]
    downstream: [arm-3]
```

### Custom Pivot Values Ignored
The layout engine ignores any `pivot_x` values in the YAML. Positioning is purely flow-based. This ensures:
- Consistent spacing regardless of configuration
- No gaps when assets are added/removed
- Deterministic layout from graph structure

## Benefits

### Visual Quality
- Arm reach circles touch perfectly (no gaps between arms)
- Clean handoff zone placement with proper spacing
- One zone per logical transfer (no redundant zones)
- Conveyors and vehicles treated as single visual units

### Stability
- Adding/removing assets automatically adjusts layout
- No manual position tuning required
- Layout stays compact and connected
- Deterministic positioning from graph structure

### Maintainability
- Single algorithm, no special cases
- Clear separation: backend computes, frontend renders
- Easy to extend with new asset types
- Boundary-based positioning ensures correct spacing

### Correctness
- Handoff zones always align with asset connections
- No overlap or collision between assets
- Canvas size automatically fits all content
- Filtered zones prevent visual clutter

## Implementation Files

- `app/models/pipeline_config.py`: Layout engine implementation
  - `_compute_graph_layout()`: Main layout algorithm
  - `_build_flow_order()`: Flow order construction
  
- `app/static/js/core/state.js`: Frontend asset graph
  - `buildConnections()`: Connection graph builder
  - `buildAssets()`: Asset normalization
  
- `app/static/js/core/render.js`: Visual rendering
  - `drawHandoffZone()`: Dynamic handoff zone rendering
  - `computeHandoffPosition()`: Zone position calculation
