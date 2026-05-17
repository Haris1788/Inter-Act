"""Build system prompts for the AI interpreter from live pipeline state.

Never hardcodes counts or IDs — reads pipeline_config + agv_state so the
model always sees an accurate picture of what is available right now.
"""

from __future__ import annotations

from app.models import pipeline_config, agv_state


def build_interpreter_prompt() -> str:
    """Return the system prompt for the Interpreter AI (qwq:32b).

    Sections:
        STATIC CONFIGURATION — arms, AGVs, warehouse layout (from pipeline.yaml)
        CURRENT LIVE STATE   — runtime AGV positions and statuses
        TASK                 — JSON schema + rules the model must follow
    """
    # ── arms ────────────────────────────────────────────────────────────────
    arms = pipeline_config.get_arms()
    stations = pipeline_config.get_stations()

    # Build a role annotation for each arm that serves a station.
    served_by = {}
    for st_name, st in stations.items():
        sb = st.get("served_by")
        if sb:
            served_by[sb] = st_name.replace("_", " ").upper()

    arm_lines = []
    for arm in arms:
        sid = f"arm-{arm['id']}"
        role = f" [{served_by[sid]}]" if sid in served_by else ""
        arm_lines.append(f"  {sid}: {arm.get('label', sid)}{role}")

    # ── AGV static config ────────────────────────────────────────────────────
    agvs_cfg = pipeline_config.get_agvs()
    agv_cfg_lines = [
        f"  {a['id']}: {a.get('label', a['id'])}  bay={a.get('bay')}  color={a.get('color')}"
        for a in agvs_cfg
    ]

    # ── live AGV state ────────────────────────────────────────────────────────
    live = agv_state.get_all()
    agv_state_lines = []
    for agv_id, rec in live.items():
        carried = f"  carrying={rec['carried_object']}" if rec.get("carried_object") else ""
        agv_state_lines.append(
            f"  {agv_id}: status={rec['status']}  cell={rec.get('cell')}{carried}"
        )

    idle_ids = [aid for aid, rec in live.items() if rec.get("status") == "idle"]

    max_pkg = min(10, max(1, len(idle_ids) * 3)) if idle_ids else 10

    return f"""You are the Interpreter AI for INTER.act, an adaptive warehouse automation system.

## STATIC CONFIGURATION

Robotic arms in the pipeline:
{chr(10).join(arm_lines) or "  (none configured)"}

AGVs (Automated Guided Vehicles):
{chr(10).join(agv_cfg_lines) or "  (none configured)"}

Shared travel lane: only ONE AGV may traverse the lane at a time.

## CURRENT LIVE STATE

AGV runtime status:
{chr(10).join(agv_state_lines) or "  (no AGVs registered)"}

Idle AGVs available for assignment: {idle_ids}

## YOUR TASK

Interpret the user's natural-language request. Return a SINGLE JSON object — no
markdown, no explanation, no code fences, JSON only:

{{
  "intent":        "<sort | move | demo | custom>",
  "package_count": <integer {1}–{max_pkg}>,
  "ordering":      ["<label1>", "<label2>", ...],
  "constraints":   ["<constraint1>", ...],
  "agv_ids":       ["<agv-id>", ...],
  "description":   "<one-sentence plain-English summary>"
}}

Rules:
- intent       : exactly one of: sort, move, demo, custom
- package_count: integer between 1 and {max_pkg}
- ordering     : sort keys in order (empty list if not a sort task)
- constraints  : any special requirements the user mentioned (empty list if none)
- agv_ids      : choose from idle AGVs {idle_ids} — use the minimum needed
- description  : one sentence, plain English

Return ONLY the JSON object."""
