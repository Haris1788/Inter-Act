"""
Smoke-test / usage reference for the Clausthal GPU Ollama models, FROM THIS
COMPANY SERVER.

How the models are reached here (read this before using):

    This server has NO GPU and its outbound firewall blocks the TU Clausthal
    VPN. The two GPU models run on the Clausthal box (cloud-247) and are
    exposed here through a persistent REVERSE SSH tunnel:

        cloud-247 Ollama 127.0.0.1:11434  --ssh -R-->  this server 127.0.0.1:11500

    So on THIS server:
      * http://127.0.0.1:11500  -> real Clausthal GPU models  (use this)
      * http://127.0.0.1:11434  -> local docker CPU qwen2.5:0.5b dev container

    The tunnel auto-starts/reconnects from cloud-247 (~/revtun.sh + @reboot
    cron). If this test cannot reach the endpoint, the tunnel is down:
      - check here:        ss -tlnp | grep 11500
      - check on cloud-247: tail ~/revtun.log   (and that revtun.sh is running)

Using these models from application code (no hardcoding needed):

    The app already reads OLLAMA_BASE_URL. To use the Clausthal GPU models,
    set it to the tunnel endpoint, e.g.:

        export OLLAMA_BASE_URL=http://127.0.0.1:11500
        export OLLAMA_MODEL=qwen2.5:14b-instruct-q4_K_M   # or qwq:32b-q4_K_M

Usage:
    python test_ollama_two_model.py
"""

import os
import sys
import time

import requests

# Defaults to the reverse-tunnel endpoint on this server; override with
# OLLAMA_BASE_URL if the tunnel port ever changes.
BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11500")
MODELS = ["qwen2.5:14b-instruct-q4_K_M", "qwq:32b-q4_K_M"]
PROMPT = "Hello which model are you?"

# The Clausthal GPU is shared and holds only one of these models at a time, so
# the first call after a swap pays a cold load that can take minutes under
# contention. Warm the model first (untimed), then measure with a generous cap.
WARMUP_TIMEOUT = 600
GEN_TIMEOUT = 300


def _generate(model: str, prompt: str, num_predict: int, timeout: int) -> dict:
    r = requests.post(
        f"{BASE}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": num_predict},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def test_model(model: str) -> bool:
    print(f"\n--- {model} ---")
    try:
        # Warm-up: force the model into VRAM. A cold load / VRAM swap on the
        # shared Clausthal GPU can take a long time; not counted against the test.
        print("  warming up (loading model into GPU, may be slow)...")
        _generate(model, "ok", 1, WARMUP_TIMEOUT)

        t0 = time.time()
        data = _generate(model, PROMPT, 128, GEN_TIMEOUT)
        elapsed = time.time() - t0
        load_s = data.get("load_duration", 0) / 1e9
        eval_s = data.get("eval_duration", 0) / 1e9
        tokens = data.get("eval_count", 0)
        tps = tokens / eval_s if eval_s > 0 else 0

        print(f"Response : {data['response'][:150].strip()}")
        print(f"Tokens   : {tokens}")
        print(f"Load     : {load_s:.2f}s   Eval: {eval_s:.2f}s  ({tps:.1f} tok/s)")
        print(f"Total    : {elapsed:.2f}s")
        print("PASS")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    print(f"Endpoint: {BASE}")
    try:
        requests.get(f"{BASE}/api/tags", timeout=5).raise_for_status()
        print("Ollama server: OK")
    except Exception as e:
        sys.exit(
            f"Ollama server not reachable: {e}\n"
            f"The reverse SSH tunnel is likely down. Check 'ss -tlnp | grep 11500' "
            f"here and ~/revtun.log on cloud-247."
        )

    results = [test_model(m) for m in MODELS]
    print(f"\n{'=' * 40}")
    print(f"Results: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
