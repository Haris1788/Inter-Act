# TUC Setup and Run — Operational Runbook

Step-by-step: how to connect to a TU Clausthal GPU node, **stand up our own Ollama** with the two models, open the SSH tunnel, and bring up the Inter.act stack.

> **What changed (6 Jun 2026):** We no longer rely on a pre-existing Ollama on `cloud-247`.
> The shared `cloud-247` H100 is usually saturated (a neighbour job parks ~72 GB of 96 GB).
> We now run **our own** Ollama under `/scratch/hm24` on whichever GPU node is free —
> currently **`cloud-243`** (A100 80 GB, idle). The Docker stack does **not change**: every
> service still points at `http://host.docker.internal:11500`, and the SSH tunnel decides
> which node that 11500 reaches. **Switching GPU nodes = changing one SSH command, no code edits.**

Architecture *why* (history, partially superseded on node choice): [`(20 May 2026) AI Integration.md`](../documentation/(20%20May%202026)%20AI%20Integration.md).

> **✅ Last verified end-to-end: 6 Jun 2026 on cloud-243.** Full chain green with **zero code changes**:
> `ollama ps` → both models `100% GPU`; laptop `/api/tags` via tunnel → both models listed;
> `docker compose up -d --build` → 13 containers up (backend + ollama Healthy);
> `curl.exe -sS http://localhost:8080/ai/health` → `"status":"connected"`, `planner_present:true`, `tactical_present:true`.
> Running stack flags: `COORDINATION_MODE=smart` + `FDAS_PEER_RESOLVE=on` (backend + all AGVs); `ARM_AUTONOMY` off.

---

## Prerequisites (verify once per session)

- **VPN connected** to TU Clausthal — needed to reach `*.rz.tu-clausthal.de`. Network only; it does **not** open the tunnel.
- **SSH** to the node as user `hm24`. Key for the login host is at `C:\Users\as\Desktop\TUC SWZ Key` (in `~/.ssh/config`).
- **Docker Desktop running** on this laptop (only for Part F onward).

Nodes seen so far (`ssh hm24@<node>.rz.tu-clausthal.de`):

| Node | GPU | Notes |
|---|---|---|
| **cloud-243** | A100 80 GB PCIe (GPU 0) + spare Tesla T4 (GPU 1) | **Current target** — idle, 80 GB free |
| cloud-247 | H100 NVL 96 GB | Often busy (neighbour ~72 GB); fallback only |
| cloud-244 / cloud-243 | — | Other shell nodes in `~/.ssh/config` |

The shell on these nodes is **tcsh** (`setenv VAR value`, not `export`).

---

## Part A — Pick a free GPU node (each session)

SSH into a candidate and check free VRAM:

```sh
ssh hm24@cloud-243.rz.tu-clausthal.de
```
```sh
nvidia-smi --query-gpu=memory.free,memory.total,utilization.gpu --format=csv
```

**Go criterion:** you need **≥ 40 GB free** to hold the 32B planner (~19 GB) + 14B tactical (~9 GB) + KV/context together. cloud-243's A100 shows ~80 GB free → green. If a node is tight (e.g. cloud-247 with only ~20 GB free), pick another.

Optional: see who's using the GPU / check the CPU isn't slammed:
```sh
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```
```sh
uptime
```

---

## Part B — One-time setup on the node (install Ollama + pull models)

Done once per node (the binary + models live in `/scratch/hm24` on that node). Skip to Part C if already installed.

> `/scratch` is ~98 % full (only ~100 GB free). The two models need ~30 GB — fits, but check first.

```sh
df -h /scratch
```
```sh
mkdir -p /scratch/hm24/ollama/models
```
```sh
cd /scratch/hm24
```

Download the Linux x64 build. **Note:** the current release ships `.tar.zst`, not `.tgz` — the old `ollama-linux-amd64.tgz` URLs 404. Get the real asset URL from the API if the version changes:
```sh
curl -sS https://api.github.com/repos/ollama/ollama/releases/latest | grep browser_download_url
```
```sh
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.30.6/ollama-linux-amd64.tar.zst -o ollama.tar.zst
```
```sh
ls -lh ollama.tar.zst
```
Extract (Ubuntu 24.04 tar supports zstd directly; creates `bin/` and `lib/`):
```sh
tar --zstd -xf ollama.tar.zst
```
```sh
ls -lh bin/ollama
```

Now start the server (see Part C), then pull both models **once**:
```sh
ollama pull qwq:32b-q4_K_M
```
```sh
ollama pull qwen2.5:14b-instruct-q4_K_M
```

---

## Part C — Start the Ollama server on the node (each session)

The server reads `OLLAMA_MODELS` and `CUDA_VISIBLE_DEVICES` **at launch**, so set them before `serve`:

```sh
setenv PATH /scratch/hm24/bin:$PATH
```
```sh
setenv OLLAMA_MODELS /scratch/hm24/ollama/models
```
```sh
setenv CUDA_VISIBLE_DEVICES 0
```
Pin to GPU 0 = the A100. (GPU 1 is the small T4 — don't let models land there.)

Is a server already running? `ollama ps` connects to it; if it errors "could not connect", start one:
```sh
nohup ollama serve >& /scratch/hm24/ollama_serve.log &
```
```sh
ollama --version
```
`could not connect to a running Ollama instance` before `serve` is normal; after `serve` it prints the version.

**Confirm models are present and on the GPU:**
```sh
ollama list
```
```sh
ollama ps
```
You want **both** models showing `100% GPU` (they co-reside: ~31 GB + ~15 GB ≈ 46 GB of the 80 GB). First load is slow (~60 s, disk → VRAM); warm calls are fast.

> **New SSH session?** You only need `setenv PATH /scratch/hm24/bin:$PATH` to use the CLI —
> client commands (`list`/`ps`/`pull`) just talk to the already-running server on
> `127.0.0.1:11434`. Do **not** run `ollama serve` again. To make PATH automatic:
> `echo 'setenv PATH /scratch/hm24/bin:$PATH' >> ~/.cshrc`.

---

## Part D — Open the SSH tunnel from your laptop

A **local forward**: `localhost:11500` on the laptop → `127.0.0.1:11434` (Ollama) on the node. Open a **new PowerShell window** and run (only one process can bind 11500 — close any old cloud-247 tunnel first):

```powershell
ssh -N -L 11500:127.0.0.1:11434 hm24@cloud-243.rz.tu-clausthal.de
```

`-N` = no shell, just hold the tunnel. Enter the password; the window then **sits there** — correct. Leave it open for the whole session; closing it closes the tunnel.

---

## Part E — Verify the tunnel from the laptop

Run in a **second** PowerShell window. In PowerShell `curl` is an alias for `Invoke-WebRequest` (no `-s`/`-m`) — always use **`curl.exe`**.

```powershell
netstat -ano | findstr "11500"
```
Expect `TCP 127.0.0.1:11500 ... LISTENING`. Empty → tunnel down, redo Part D.

```powershell
curl.exe -s -m 5 http://127.0.0.1:11500/api/tags
```
Expect JSON listing `qwq:32b-q4_K_M` and `qwen2.5:14b-instruct-q4_K_M`. Refused → tunnel listener broken (redo Part D). Timeout → node unreachable (VPN? node down?). Empty models → the `serve` in Part C isn't running.

---

## Part F — Verify Docker is ready

```powershell
docker version
```
```powershell
docker ps -a --format "table {{.Names}}\t{{.Status}}"
```
```powershell
python scripts/generate_compose.py
docker compose config | Out-Null
```
Regenerate compose only if `pipeline.yaml` changed since the last generate.

---

## Part G — Bring up the stack

```powershell
docker compose up -d --build
```
```powershell
docker ps --format "{{.Names}}`t{{.Status}}"
```
All `Up`; backend + ollama `Up (healthy)`.

```powershell
curl.exe -s -m 3 http://localhost:8101/health
curl.exe -s -m 3 http://localhost:8102/health
curl.exe -s -m 3 http://localhost:8103/health
```
Each returns `{"agv_id":"agv-N","status":"ok",...}`.

---

## Part H — Verify the backend actually sees TUC

The key check: the backend container can reach `http://host.docker.internal:11500` and Ollama lists both models.

```powershell
curl.exe -s http://localhost:8080/ai/health
```
Expected:
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
`loaded_model: null` is normal before the first call. If `status` isn't `"connected"` or models are empty, the container can't see the tunnel — usually `extra_hosts: host.docker.internal:host-gateway` missing from the generated compose. Re-run `generate_compose.py` and rebuild.

---

## Part I — Warm the planner (before a live demo)

First `/plan` after the GPU is idle cold-loads `qwq:32b` (disk → VRAM, ~1–5 min); looks like a freeze. Warm in private first.

```powershell
curl.exe -s -m 600 -X POST http://localhost:8080/plan -H "Content-Type: application/json" -d '{"prompt":"Sort 1 package","sync":true}'
```
Then the real prompt (warm calls ~4–10 s each):
```powershell
curl.exe -s -X POST http://localhost:8080/plan -H "Content-Type: application/json" -d '{"prompt":"send 3 packages sorted red green blue, distribute across all AGVs"}'
```
Open **http://localhost:8080** to watch the AGVs.

> Optional: keep models warm across idle gaps by starting `serve` with `setenv OLLAMA_KEEP_ALIVE 2h` (Part C, before `serve`). Default unloads after 5 min idle.

---

## Stopping cleanly

```powershell
docker compose down
```
Close the SSH tunnel window (or Ctrl-C). If detached:
```powershell
netstat -ano | findstr "11500.*LISTENING"
taskkill /PID <PID> /F
```

On the node, the `ollama serve` (nohup) keeps running after you log out — that's intended so the tunnel survives. To stop it when done for good:
```sh
pkill -u hm24 ollama
```

---

## Troubleshooting — which hop is broken

| Symptom | Broken hop | Fix |
|---|---|---|
| `ping <node>.rz.tu-clausthal.de` fails | VPN | Reconnect VPN. |
| `ssh hm24@<node>` hangs / denied | SSH key / login | Check key path in `~/.ssh/config`. |
| `nvidia-smi` free VRAM < 40 GB | Node busy | Pick another node (Part A). |
| `ollama --version` → "could not connect" | `serve` not running | Part C: `nohup ollama serve ...`. |
| `ollama: Command not found` (new session) | PATH not set | `setenv PATH /scratch/hm24/bin:$PATH`. |
| `ollama ps` shows `CPU` not `100% GPU` | Model spilled to CPU / wrong GPU | Check `CUDA_VISIBLE_DEVICES 0` was set before `serve`; restart serve. |
| `netstat findstr 11500` empty on laptop | Tunnel not running | Part D. |
| `curl.exe 127.0.0.1:11500/api/tags` refused | Tunnel window died | Re-open Part D. |
| `curl ... -m 5` → "parameter 'm' is ambiguous" | PowerShell aliased `curl` | Use `curl.exe`. |
| `/plan` → `{"error":"prompt or task_spec required"}` | PowerShell ate the `\"` JSON escaping | Wrap the body in **single quotes**: `-d '{"prompt":"..."}'` (literal in PowerShell). |
| `Unmatched '"'` on a `/plan` curl | Pasted into the **cloud-243 tcsh** window | `/plan` calls run on the **laptop** (`localhost:8080`), not the node. |
| `/api/tags` timeout | Tunnel up, node path broken | VPN? node up? |
| `/api/tags` empty `models` | `serve` running but `OLLAMA_MODELS` wrong / models not pulled | Part B/C. |
| `/ai/health` not `connected` | Backend → host gateway | Re-run `generate_compose.py`, rebuild. |
| First `/plan` hangs minutes | Cold load — expected | Wait, or Part I warm-up. |

---

## One-page summary

```sh
# ON THE NODE (tcsh) — pick a free GPU, start our Ollama
ssh hm24@cloud-243.rz.tu-clausthal.de
nvidia-smi --query-gpu=memory.free --format=csv      # want >= 40 GB
setenv PATH /scratch/hm24/bin:$PATH
setenv OLLAMA_MODELS /scratch/hm24/ollama/models
setenv CUDA_VISIBLE_DEVICES 0
nohup ollama serve >& /scratch/hm24/ollama_serve.log &   # skip if already running
ollama ps                                             # both models, 100% GPU
```
```powershell
# ON THE LAPTOP — tunnel (leave THIS window open), then everything else in a 2nd window
ssh -N -L 11500:127.0.0.1:11434 hm24@cloud-243.rz.tu-clausthal.de

curl.exe -s -m 5 http://127.0.0.1:11500/api/tags      # both models visible
docker compose up -d --build
curl.exe -s http://localhost:8080/ai/health           # status: connected
# warm + run
curl.exe -s -m 600 -X POST http://localhost:8080/plan -H "Content-Type: application/json" -d '{"prompt":"Sort 1 package","sync":true}'
docker compose down                                    # stop; close SSH window to drop tunnel
```
