# INTER.act API reference

This is the practical API map for the running system.  
Base URL is usually `http://localhost:5000`.

## Page and health

- `GET /`  
  Returns the simulation page.

- `GET /health`  
  Basic backend health check.

- `GET /health/ollama`  
  Health check for the Ollama dependency path used by this project.

## Arm motion and state

- `POST /set_angle`  
  Body:
  ```json
  { "arm_id": 1, "angle": 45 }
  ```
  Validates arm ID and angle range, updates arm state, emits `state_update`.

- `GET /state`  
  Returns all arm states.

- `GET /state/<id>`  
  Returns one arm state.

## Object tracking

- `POST /object_event`  
  Body:
  ```json
  {
    "object_id": "Obj1",
    "state": "carried",
    "carrier_arm": 1,
    "owner_type": "arm",
    "owner_id": "arm-1",
    "event_time": 1710000000000
  }
  ```
  Backend validates owner existence and ownership transition legality against topology.

- `GET /objects`  
  Returns tracked objects snapshot.

- `POST /objects/reset`  
  Clears tracked objects and emits `objects_reset`.

## Logging

- `POST /log`  
  Body:
  ```json
  { "source": "arm_1", "message": "Picking Obj1 from intake" }
  ```
  Broadcasts a `log_message` event.

## Config and pipeline lifecycle

- `GET /config`  
  Returns scene config used by frontend initialization.

- `POST /reload_config`  
  Reloads `pipeline.yaml` into memory only.  
  Does not reset arm/object runtime state.

- `POST /restart_pipeline`  
  Reinitializes runtime state using currently loaded config.  
  Does not reread `pipeline.yaml`.

- `GET /pipeline/status`  
  Returns pipeline stopped flag, arm health map, dependency graph validity, and topological order.

## Arm health and control

- `GET /arms/health`  
  Returns health status for all arms.

- `POST /arms/<id>/health`  
  Used by arm services and demo runtime to report arm health transitions.

- `POST /arms/<id>/stop`  
  Explicitly stops an arm and cascades stop to downstream dependents.

- `POST /arms/<id>/start`  
  Clears explicit stop for that arm.

- `GET /arms/<id>/allowed`  
  Returns whether an arm is allowed to execute under current dependency/health state.

## Asset state and control

- `GET /assets/state`
- `GET /assets/<asset_id>/state`
- `POST /assets/<asset_id>/fail`
- `POST /assets/<asset_id>/recover`

## Demo proxy routes

- `POST /demo/start`  
  Proxies start request to demo-runner.

- `GET /demo/status`  
  Proxies running status from demo-runner.

- `POST /demo/stop`  
  Proxies stop request to demo-runner.

Note: in the current implementation, stop updates status but is not a guaranteed immediate hard interrupt for all in-flight worker actions.

## Socket.IO events emitted by backend

- `state_update`
- `object_update`
- `objects_reset`
- `arm_health`
- `asset_health`
- `log_message`
