# INTER.act troubleshooting

This is the fast checklist for common problems.

## Browser opens but demo does not start

Checks:

- backend running on `:5000`
- demo-runner reachable (default `:6000`)
- `/demo/status` returns JSON

Try:

```bash
curl http://localhost:5000/demo/status
curl -X POST http://localhost:5000/demo/start -H "Content-Type: application/json" -d "{\"products\":3,\"speed\":\"normal\"}"
```

If start returns demo-runner unavailable, verify container/service wiring and `DEMO_RUNNER_HOST`.

## Arm appears frozen or stale

Checks:

- `/state` changes when calling `/set_angle`
- Socket.IO connection is active
- frontend console has no module/runtime errors

Try:

```bash
curl http://localhost:5000/state
curl -X POST http://localhost:5000/set_angle -H "Content-Type: application/json" -d "{\"arm_id\":1,\"angle\":45}"
```

## Health statuses are wrong or delayed

Checks:

- arm service `/health` endpoint is reachable
- monitor config in `pipeline.yaml` (`health.check_interval`, `timeout`, `max_missed`)
- SIGTERM path from arm service is working when stopping containers

Remember:

- monitor recovery is for liveness only
- `draining` is not auto-recovered by monitor logic

## Config changed but UI still shows old layout

You need both calls:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

`reload_config` alone updates config cache but does not mutate active runtime state.

## Dependency behavior looks wrong in complex graph

Reality check:

- config graph validation may pass
- runtime dependency execution is still optimized for simpler chains in parts of the runner

For production-like branching tests, verify exact path behavior with controlled test cases before relying on it.

## Demo stop does not halt everything instantly

Known behavior:

- `/demo/stop` updates demo-runner status
- not all in-flight worker actions are hard-interrupted immediately

If you need deterministic halt behavior right now, use targeted arm stop endpoints plus service-level controls.

## High event volume or jitter

Symptoms:

- noisy updates under heavy movement
- visual jitter during reconnect windows

Checks:

- browser performance timeline
- socket reconnect frequency
- object/state sync sequence after reconnect

## Last-resort reset path

When in doubt:

1. stop running containers/processes
2. restart backend + demo-runner + arm services
3. call reload/restart pipeline endpoints
4. refresh browser
