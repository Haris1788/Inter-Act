# INTER.act frontend assets model

This file documents the frontend-only asset layer (`vehicle`, `conveyor`) and how it interacts with backend arm events.

## What is backend-driven vs frontend-driven

Backend remains authoritative for:

- arm angles and arm health
- object lifecycle states (`intake`, `carried`, `handoff_*`, `output`)
- Socket.IO event stream

Frontend extends rendering with additional asset types from config:

- `vehicle`
- `conveyor`

These non-arm assets are visual/runtime behavior in the browser. They are not separate backend services.

## Config source

Non-arm assets come from `assets:` in `pipeline.yaml` and are exposed through `/config`.

`app/models/pipeline_config.py` normalizes geometry for these assets, including conveyor slot positions.

## Main frontend modules

- `app/static/js/main.js` - app bootstrap and frame loop
- `app/static/js/core/state.js` - local state model and transit route handling
- `app/static/js/core/tick.js` - per-frame updates, proximity handoffs
- `app/static/js/core/render.js` - drawing layers and effects
- `app/static/js/core/socket.js` - Socket.IO integration and sync
- `app/static/js/assets/*.js` - asset-specific draw/tick behavior

## Transit behavior

Backend emits handoff states for arm-to-arm flow.  
Frontend can intercept and visually route those transitions through vehicle/conveyor chains defined in assets config.

Important rule: backend state still wins.  
If backend reports a new authoritative object carrier/state, frontend reconciles to that state.

## Kill controls for frontend assets

UI controls (`app/static/js/ui/controls.js`) render local kill buttons for vehicle/conveyor only.

- kill sets asset to draining state locally
- drain completion transitions the local asset to dead

Arm kill behavior still goes through backend endpoints.

## Current practical limits

- This layer is optimized for the demo path, not arbitrary graph complexity.
- Mixed backend events and local transit can produce edge timing races in reconnect/heavy-update scenarios.
- Keep backend object states as source of truth when debugging inconsistencies.
