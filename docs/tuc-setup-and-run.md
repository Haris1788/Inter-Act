# TUC Setup and Run — Operational Runbook

Step-by-step: how to connect to TU Clausthal, verify the GPU models are reachable, check Docker, and bring up the Inter.act stack. Use this when starting a session from a cold machine, after a reboot, or after the SSH tunnel has been killed.

The architecture of *why* this works the way it does is in [`(20 May 2026) AI Integration.md`](../documentation/(20%20May%202026)%20AI%20Integration.md) §"Setup: the TUC server connection". This file is just the commands, in order, with the failure modes that map to each one.

---

## Prerequisites (verify once per session)

- **VPN connected** to TU Clausthal — needed to reach `*.rz.tu-clausthal.de`. Network reachability only; the VPN does **not** bring up the SSH tunnel.
- **SSH key on disk** at `C:\Users\as\Desktop\TUC SWZ Key` (referenced from `~/.ssh/config` for `as.rz.tu-clausthal.de`).
- **Docker Desktop running** on this laptop.

Sanity check the VPN before anything else:

```bash
ping -n 2 cloud-247.rz.tu-clausthal.de
```

If this fails, fix the VPN before continuing — every step below depends on it.

---

## Step 1 — Open the SSH tunnel from your laptop

The earlier `~/revtun.sh` reverse-tunnel script does not exist on the `hm24` account on cloud-247 (and `@reboot` cron there does not establish anything). Confirmed during the 22 May session: connecting and running `~/revtun.sh` returned `Command not found` and the log file did not exist either.

**Use a local SSH forward instead — one command, one window, no server-side script needed.**

Open a **new PowerShell window** on this laptop and run:

```powershell
ssh -N -L 11500:127.0.0.1:11434 cloud-247.rz.tu-clausthal.de
```

What it does:

- `-L 11500:127.0.0.1:11434` — anything connecting to `localhost:11500` on this laptop is forwarded through SSH to `127.0.0.1:11434` on cloud-247, where Ollama is listening.
- `-N` — no remote shell, just hold the tunnel open.

Enter the cloud-247 password when prompted. The window will then **just sit there** — that is correct. Leave it open. The tunnel is alive as long as the SSH session is alive; closing the window closes the tunnel.

To verify Ollama itself is alive on cloud-247 (only if the tunnel isn't returning data — usually skip this), SSH in directly and curl Ollama on its loopback:

```bash
ssh cloud-247.rz.tu-clausthal.de
curl -s http://127.0.0.1:11434/api/tags | python -c "import sys,json;print([m['name'] for m in json.load(sys.stdin)['models']])"
#   Should list at least qwq:32b-q4_K_M and qwen2.5:14b-instruct-q4_K_M.
exit
```

---

## Step 2 — Verify the tunnel from this laptop

Run these in a **second** PowerShell window (not the one holding the SSH tunnel — that one is busy).

**Important PowerShell note:** in PowerShell, `curl` is an alias for `Invoke-WebRequest`, which does NOT understand `-s` / `-m`. Always use `curl.exe` (the real binary) for these checks.

```powershell
# 2a. Is something listening on 11500?
netstat -ano | findstr "11500"
#   Expect: TCP 127.0.0.1:11500  LISTENING  <PID>
#   Empty → tunnel is down. Go back to Step 1.

# 2b. Does Ollama respond through the tunnel?
curl.exe -s -m 5 http://127.0.0.1:11500/api/tags
#   Expect JSON listing qwq:32b-q4_K_M and qwen2.5:14b-instruct-q4_K_M.
#   Exit 7 / Connection refused → tunnel listener exists but is broken;
#                                  close the SSH window and re-do Step 1.
#   Timeout                     → tunnel up, cloud-247 unreachable. VPN? cloud-247 down?
#   503 / empty models          → Ollama process on cloud-247 not running.
```

If both pass, the AI path is good end-to-end. Proceed to Docker.

---

## Step 3 — Verify Docker is ready

```bash
# 3a. Docker engine reachable?
docker version
#   If "error during connect" → Docker Desktop is not running. Start it.

# 3b. Stale containers from a previous run?
docker ps -a --format "table {{.Names}}\t{{.Status}}"
#   "Exited" entries are fine — `docker compose up` will recreate them.
#   "Up" entries from a previous session: stop them first with `docker compose down`.

# 3c. Compose file regenerated from pipeline.yaml?
#     Only needed if you edited pipeline.yaml since the last compose generate.
python scripts/generate_compose.py
docker compose config >/dev/null && echo "compose file valid"
```

---

## Step 4 — Bring up the stack

```bash
# 4a. Build and start all 7 containers in the background.
docker compose up -d --build

# 4b. Wait for the backend to report healthy (Docker's own healthcheck).
docker ps --format "{{.Names}}\t{{.Status}}" | grep -E "backend|agv|frontend|ollama|demo"
#   All should be Up; backend and ollama should be "Up (healthy)".

# 4c. Frontend reachable?
curl -s -m 3 http://localhost:8080 | findstr -i "interact" >NUL && echo "frontend OK"

# 4d. All three AGV containers healthy?
curl -s -m 3 http://localhost:8101/health
curl -s -m 3 http://localhost:8102/health
curl -s -m 3 http://localhost:8103/health
#   Each should return {"agv_id":"agv-N","status":"ok","task_status":"idle",...}.

# 4e. Coordination mode visible in backend startup log.
docker logs interact-backend 2>&1 | findstr "COORDINATION_MODE"
#   "[startup] COORDINATION_MODE=decentralized" if pipeline.yaml has that;
#   "centralized" otherwise.
```

---

## Step 5 — Verify the backend actually sees TUC

This is the most important check before driving the system from the UI. It confirms that the backend, running inside its Docker container, can reach `http://host.docker.internal:11500` and that Ollama lists both models.

```bash
curl -s http://localhost:8080/ai/health | python -m json.tool
```

Expected response:

```json
{
  "url": "http://host.docker.internal:11500",
  "status": "connected",
  "planner_present": true,
  "tactical_present": true,
  "models": ["qwq:32b-q4_K_M", "qwen2.5:14b-instruct-q4_K_M"],
  "loaded_model": null
}
```

`loaded_model: null` is normal before the first call — it becomes one of the two model names once a request warms the GPU. If `status` is anything other than `"connected"` or the models list is empty, the backend container can't see the tunnel — usually because `extra_hosts: host.docker.internal:host-gateway` isn't in the generated `docker-compose.yml`. Re-run `python scripts/generate_compose.py` and `docker compose up -d --build`.

---

## Step 6 — Warm the planner (only before a live demo)

The first `/plan` call after the GPU is idle cold-loads `qwq:32b` from disk to VRAM — that takes **2-5 minutes** and looks like a freeze if you run it in front of an audience. Always warm in private first.

```bash
curl -s -m 600 -X POST http://localhost:8080/plan \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Sort 1 package","sync":true}' \
  | python -c "import sys,json;d=json.load(sys.stdin);print('warmed:', d.get('status'), d.get('_model'))"
```

When that returns, run a normal dispatch and the AI calls are warm-fast (4-10 s each instead of minutes):

```bash
curl -s -X POST http://localhost:8080/plan \
  -H "Content-Type: application/json" \
  -d '{"prompt":"send 3 packages sorted red green blue, distribute across all AGVs"}'
```

Then open **http://localhost:8080** in the browser to watch the AGVs.

---

## Stopping cleanly

```powershell
# A. Stop the Inter.act stack (containers + network).
docker compose down

# B. Close the SSH tunnel — simplest is to just close the PowerShell window
#    that holds the `ssh -N -L ...` session, or press Ctrl-C in it.

# C. If the SSH window was somehow detached, find and kill the ssh.exe PID:
netstat -ano | findstr "11500.*LISTENING"
taskkill /PID <PID> /F
```

---

## Troubleshooting — which hop is broken

Match the symptom you observe to the row; that tells you which command from Step 1 or Step 2 to re-run.

| Symptom | Hop that is broken | What to do |
|---|---|---|
| `ping cloud-247.rz.tu-clausthal.de` fails | VPN to TU Clausthal | Reconnect VPN. |
| `ssh as.rz.tu-clausthal.de` hangs / "Permission denied" | Login host / SSH key | Check key path in `~/.ssh/config`; key passphrase if any. |
| `netstat findstr 11500` empty on laptop | SSH local-forward not running | Step 1: open a new window and run `ssh -N -L 11500:127.0.0.1:11434 cloud-247.rz.tu-clausthal.de`. |
| `curl.exe 127.0.0.1:11500/api/tags` exit 7 / refused | Tunnel window died | Step 1: re-open the SSH window. |
| `curl ... -m 5` → `Parameter cannot be processed because the parameter name 'm' is ambiguous` | PowerShell aliased `curl` to `Invoke-WebRequest` | Use `curl.exe` (the real binary) for all curl commands in this doc. |
| `curl 127.0.0.1:11500/api/tags` timeout | Tunnel up, cloud-247 path broken | Check VPN; check cloud-247 is up. |
| `/api/tags` returns 503 or empty `models` list | Ollama process on cloud-247 | SSH in and restart Ollama on cloud-247. |
| `docker ps` empty / errors | Docker Desktop | Start Docker Desktop. |
| `/ai/health` `status:"failed"` from inside backend | Backend container → host gateway | Re-run `generate_compose.py`, rebuild backend. |
| First `/plan` hangs for minutes | Cold load — expected | Wait, or run Step 6 warm-up. |
| `/plan` returns but AGVs don't move | Plan dispatched but agents busy / faulted | `curl localhost:8080/coord/snapshot` to see who is idle/faulted. |

---

## One-page summary

```powershell
# 1. Tunnel — leave THIS window open for the whole session
ssh -N -L 11500:127.0.0.1:11434 cloud-247.rz.tu-clausthal.de

# In a SECOND window, do everything else:

# 2. Verify from laptop
curl.exe -s -m 5 http://127.0.0.1:11500/api/tags

# 3. Docker
docker ps -a
python scripts/generate_compose.py

# 4. Run
docker compose up -d --build

# 5. Backend sees TUC
curl.exe -s http://localhost:8080/ai/health

# 6. (Demo only) Warm
curl.exe -s -m 600 -X POST http://localhost:8080/plan -H "Content-Type: application/json" -d "{\"prompt\":\"Sort 1 package\",\"sync\":true}"

# Run real prompt
curl.exe -s -X POST http://localhost:8080/plan -H "Content-Type: application/json" -d "{\"prompt\":\"send 3 packages sorted red green blue, distribute across all AGVs\"}"

# Stop
docker compose down
# Close the SSH window (or Ctrl-C inside it) to close the tunnel.
```
