"""
═══════════════════════════════════════════════════════════════════════════
 USER STORY #7: QWEN AS BRAINS FOR EACH ARM

 FILE: app/services/arm_brain.py (STEP 1)
═══════════════════════════════════════════════════════════════════════════

WHAT THIS FILE DOES:
====================
Creates ONE independent Qwen decision-making service for EACH arm.

Each arm has its own "brain" (ArmBrain instance) that:
1. Receives task descriptions
2. Analyzes the task + current arm state
3. Asks Qwen LLM for decision
4. Returns decision (action + target angle)

WHY WE NEED THIS:
=================
Without this file (OLD WAY):
  Demo: "Arm 1, move to 45°"  (manual angle calculation)
  Demo: "Arm 2, move to 90°"  (demo has to know all angles)
  Demo: "Arm 3, move to 0°"   (not flexible, not intelligent)

With this file (NEW WAY):
  Demo: "Arm 1, pick object!"  (describe task)
  Brain 1: Analyzes + Asks Qwen → "Move to 45°"  (intelligent)

  Demo: "Arm 2, move to handoff!"  (describe task)
  Brain 2: Analyzes + Asks Qwen → "Move to 90°"  (intelligent)

RESULT: Intelligent, flexible, adaptive decision-making!

ARCHITECTURE:
==============
Frontend/Demo
    ↓
Backend (/execute_task endpoint - STEP 4)
    ↓
Executor (STEP 2: arm_execution.py)
    ↓
    └─→ ArmBrain (THIS FILE: arm_brain.py) ← YOU ARE HERE
         ↓
         └─→ Qwen LLM (via QwenBrain from STEP 6)

USAGE EXAMPLE:
==============
>>> from app.services.arm_brain import get_arm_brain
>>>
>>> brain_1 = get_arm_brain(1)  # Get brain for Arm 1
>>>
>>> decision = brain_1.decide_action(
...     task="pick object from intake",
...     current_state={"position": 0, "holding": False}
... )
>>>
>>> print(decision)
{
    "action": "pick",
    "target_angle": 45,
    "confidence": 0.95,
    "reasoning": "Object at intake requires rotation to 45°",
    "status": "success",
    "arm_id": 1
}

═══════════════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import difflib
from typing import Dict, Any, Optional

# ═══════════════════════════════════════════════════════════════════════════
# IMPORTS: What we're using from other files
# ═══════════════════════════════════════════════════════════════════════════

# Import existing Qwen service (created in User Story #6)
# This gives us access to the LLM (Large Language Model)
from app.services.qwen_brain import get_qwen_brain


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS: Output validation
# ═══════════════════════════════════════════════════════════════════════════
# Qwen 2.5 0.5b is small enough that it occasionally hallucinates angles
# outside [0, 180] or invents actions that aren't in our 4-verb vocabulary.
# We clamp/whitelist the response before letting it reach the executor so
# bad LLM output cannot drive the arm to an illegal pose.
#
# `noop` is the brain's way of refusing: "do not move the arm." The executor
# treats it specially and never sends a /move command.

VALID_ACTIONS = {"pick", "drop", "move", "rotate", "noop"}
MIN_ANGLE = 0.0
MAX_ANGLE = 180.0


# ═══════════════════════════════════════════════════════════════════════════
# DETERMINISTIC PRE-FILTERS
# ═══════════════════════════════════════════════════════════════════════════
# Two task families that must NOT be left to a 0.5 B model:
#
# 1. "stop / don't move / halt / stay" — a small LLM gladly converts these
#    into a "drop" or "move" because those are the only verbs it knows.
#    Disastrous: the arm executes the opposite of what was asked.
#
# 2. "rotate to N degrees" / "move to N" — these are unambiguous and
#    deterministic; sending them to Qwen wastes 2-8 s of CPU inference.
#
# We handle both before the LLM is consulted.

_STOP_PATTERNS = re.compile(
    r"\b(stop|halt|hold|stay|wait|freeze|don't\s*move|do\s*not\s*move|no[\s-]*op|abort|cancel)\b",
    re.IGNORECASE,
)
_NUMERIC_PATTERNS = re.compile(
    r"\b(?:rotate|move|go|turn|set)\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*(?:deg(?:rees?)?|°)?",
    re.IGNORECASE,
)


def _try_pre_filter(task: str) -> Optional[Dict[str, Any]]:
    """Try to answer the task without calling Qwen.

    Returns a fully-formed decision dict on a hit, or None to fall through
    to the LLM. The dict is shaped exactly like a normal Qwen decision
    (action + target_angle + confidence + reasoning + status), so the rest
    of the pipeline doesn't care which path produced it.
    """
    if not task:
        return None

    if _STOP_PATTERNS.search(task):
        # Hard refusal: do not move the arm regardless of what an LLM might say.
        return {
            "action": "noop",
            "target_angle": None,
            "confidence": 1.0,
            "reasoning": "Task contains a stop/halt intent; arm will not move.",
            "status": "success",
            "source": "rule:stop",
        }

    m = _NUMERIC_PATTERNS.search(task)
    if m:
        try:
            angle = float(m.group(1))
        except (TypeError, ValueError):
            return None
        # Clamp here too; the validator would do it again but the reasoning
        # is clearer when we say so explicitly.
        clamped = max(MIN_ANGLE, min(MAX_ANGLE, angle))
        out_of_range = clamped != angle
        return {
            "action": "rotate",
            "target_angle": clamped,
            "confidence": 1.0,
            "reasoning": (
                f"Literal angle {angle}° parsed from task"
                + (f" (clamped to {clamped}° because {MIN_ANGLE}–{MAX_ANGLE}° is the safe range)" if out_of_range else "")
            ),
            "status": "success",
            "source": "rule:numeric",
        }

    return None


def _validate_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Clamp/whitelist Qwen's raw output. Always returns a usable dict.

    - target_angle is coerced to float and clamped to [0, 180]
    - action is forced to one of {pick, drop, move, rotate}; unknown maps to "move"
    - confidence is coerced to float in [0, 1]; missing/garbage becomes 0.5
    - reasoning is coerced to a string
    Sets `validated=True` so callers can tell a sanitised dict from a raw one.
    Failure dicts (status="failed") pass through unchanged.
    """
    # qwen_brain.ask_decision uses three failure tags depending on the cause:
    #   "failed"    — Ollama returned non-200 OR JSON could not be parsed
    #   "timeout"   — request exceeded our 30 s budget
    #   "exception" — connection error / DNS failure / misc.
    # All three mean "no usable LLM output." We must preserve that failure
    # signal — the validator's job is to clamp valid output, not to invent
    # a default `move @ 90, conf 0.5` that would silently ship a fabricated
    # decision to the arm. Normalise to "failed" for the executor.
    if decision.get("status") in ("failed", "timeout", "exception"):
        decision["status"] = "failed"
        return decision

    action = str(decision.get("action", "move")).strip().lower()
    if action not in VALID_ACTIONS:
        action = "move"

    if action == "noop":
        # noop has no target angle — the executor must not move the arm.
        angle = None
    else:
        raw_angle = decision.get("target_angle")
        try:
            angle = float(raw_angle)
        except (TypeError, ValueError):
            angle = 90.0  # safe rest position
        angle = max(MIN_ANGLE, min(MAX_ANGLE, angle))

    raw_conf = decision.get("confidence")
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reasoning = str(decision.get("reasoning", ""))[:300]

    decision["target_angle"] = angle
    decision["action"] = action
    decision["confidence"] = conf
    decision["reasoning"] = reasoning
    decision["validated"] = True
    return decision


# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ArmBrain
# PURPOSE: One AI decision-maker per arm
# ═══════════════════════════════════════════════════════════════════════════

class ArmBrain:
    """
    ┌─────────────────────────────────────────────────────────────────────┐
    │  ARMBRAIN: Individual Qwen Decision Engine for One Arm              │
    └─────────────────────────────────────────────────────────────────────┘

    This class represents the "brain" (AI intelligence) for ONE specific arm.

    WHAT IT DOES:
    =============
    - Receives task descriptions ("pick object", "move to handoff", etc.)
    - Analyzes current arm state (position, what it's holding)
    - Asks Qwen LLM: "What should I (Arm N) do?"
    - Returns intelligent decision with target angle

    WHY SEPARATE BRAINS:
    ====================
    Each arm can:
    - Make DIFFERENT decisions for the SAME task
    - Work INDEPENDENTLY without waiting for others
    - Have DIFFERENT states (Arm 1 at 0°, Arm 2 at 90°, etc.)

    Example:
      Task: "Pick object"

      Brain 1: "I'm at 0°, object is at 45° → Move to 45°"
      Brain 2: "I'm at 90°, object is at 30° → Move to 30°"
      Brain 3: "I'm at 180°, object is at 60° → Move to 60°"

      Each brain makes the RIGHT decision for its ARM'S situation!

    SINGLETON PATTERN:
    ==================
    Each arm has ONE brain instance (not created multiple times)
    This is managed by get_arm_brain() function below
    """

    def __init__(self, arm_id: int):
        """
        INITIALIZATION: Create brain for specific arm

        WHAT HAPPENS:
        =============
        1. Store which arm this brain controls
        2. Get access to Qwen LLM service

        WHY:
        ====
        Every brain needs to know:
        - Which arm am I controlling? (for identification)
        - How do I access Qwen? (for decision-making)

        PARAMETERS:
        ===========
        arm_id (int): Which arm? (1, 2, or 3)
                     This identifies which robot arm this brain controls

        EXAMPLE:
        ========
        brain_1 = ArmBrain(1)  # Create brain for Arm 1
        brain_2 = ArmBrain(2)  # Create brain for Arm 2
        brain_3 = ArmBrain(3)  # Create brain for Arm 3
        """

        # ═════════════════════════════════════════════════════════════════
        # STORE: Which arm does this brain control?
        # ═════════════════════════════════════════════════════════════════
        self.arm_id = arm_id

        # ═════════════════════════════════════════════════════════════════
        # CACHE: Memoise decisions per (task, angle-bucket)
        # ═════════════════════════════════════════════════════════════════
        # Qwen 0.5b on CPU takes 2–3 s per call. Repeats are common ("pick"
        # at angle 0 → 180 happens every cycle). We bucket the current
        # angle to 15° intervals so 47° and 53° share a cache slot.
        # Capped at 64 entries — small enough that we never need eviction.
        self._cache: Dict[Any, Dict[str, Any]] = {}
        self._cache_max = 64
        """
        arm_id (int): Identifier for the arm this brain controls

        Used for:
        - Logging: "[Arm 1 Brain] Made decision..."
        - Identification: "Which brain made this decision?"
        - Response: Return decision with arm_id so system knows which arm

        Example: self.arm_id = 1  (this brain controls Arm 1)
        """

        # ═════════════════════════════════════════════════════════════════
        # GET: Access to Qwen LLM service
        # ═════════════════════════════════════════════════════════════════
        self.qwen = get_qwen_brain()
        """
        qwen (QwenBrain): Access to the Qwen language model

        What is it?
        -----------
        QwenBrain is the service that calls Ollama/Qwen LLM
        It handles:
        - Sending prompts to Qwen
        - Parsing Qwen's responses
        - Error handling

        All arms share the SAME Qwen model (but make different decisions)

        Why share?
        - One LLM instance is enough
        - Each arm asks it different questions
        - Qwen responds based on context each brain provides
        """

    def decide_action(self, task: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        ┌─────────────────────────────────────────────────────────────────┐
        │  DECIDE_ACTION: Ask Qwen "What should I (Arm N) do?"            │
        └─────────────────────────────────────────────────────────────────┘

        THIS IS THE MAIN FUNCTION - It's where the AI magic happens!

        WHAT IT DOES:
        =============
        Step 1: Create system prompt (tells Qwen how to behave)
        Step 2: Create user message (describes the task + current state)
        Step 3: Ask Qwen (send prompt + state to LLM)
        Step 4: Parse response (extract decision from Qwen's answer)
        Step 5: Return decision (with arm_id added)

        WHY THIS IS IMPORTANT:
        ======================
        This function BRIDGES:
        - Human task description (e.g., "pick object")
        - AI decision-making (Qwen analyzes)
        - Robot execution (returns angle to move to)

        WITHOUT THIS: No intelligence (just manual commands)
        WITH THIS: Intelligent adaptation to different situations

        PARAMETERS:
        ===========
        task (str): What needs to be done?
                   Examples:
                   - "pick object from intake"
                   - "move object to handoff zone"
                   - "drop object at output"

        current_state (Dict[str, Any]): What's the arm's current situation?
                                       Example:
                                       {
                                           "position": 45.0,  # Current angle
                                           "holding": False,  # Holding something?
                                           "objects_nearby": []  # What's around?
                                       }

        RETURNS:
        ========
        Dictionary with Qwen's decision:
        {
            "action": "pick",           # What to do
            "target_angle": 45,         # Where to move (degrees 0-180)
            "confidence": 0.95,         # How sure is Qwen? (0.0-1.0)
            "reasoning": "Object is...", # Why this decision?
            "status": "success",        # Did decision succeed?
            "arm_id": 1                 # Which arm? (added by us)
        }

        EXAMPLE USAGE:
        ==============
        >>> brain = ArmBrain(1)
        >>> task = "pick object from intake"
        >>> state = {"position": 0, "holding": False}
        >>> decision = brain.decide_action(task, state)
        >>> print(decision)
        {
            "action": "pick",
            "target_angle": 45,
            "confidence": 0.95,
            "reasoning": "Object located at 45°, need to reach it"
        }
        """

        # ═════════════════════════════════════════════════════════════════
        # STEP 1: CREATE SYSTEM PROMPT
        # PURPOSE: Tell Qwen how to behave and what format to use
        # ═════════════════════════════════════════════════════════════════

        system_prompt = f"""You are the brain (AI decision engine) for Arm {self.arm_id}.

Your job: Decide what action this arm should take based on the task and current state.

CONSTRAINTS YOU MUST FOLLOW:
1. Analyze the task carefully
2. Consider the current arm angle and status
3. Decide the BEST action to take
4. Calculate the TARGET ANGLE (0-180 degrees)
5. Rate your confidence (0.0 = not sure, 1.0 = very sure)
6. Explain your reasoning briefly

STATION ANGLE REFERENCE (use these unless the task names a specific angle):
- intake / pick station: 180°
- handoff / carry zone:  90°
- output / drop station:  0°
- rest position:         90°

CRITICAL: Output ONLY valid JSON. No explanation, no markdown, no comments.
Invalid JSON will break the system!

OUTPUT FORMAT (MUST be exactly this):
{{
  "action": "pick" OR "drop" OR "move" OR "rotate",
  "target_angle": <number between 0 and 180>,
  "confidence": <number between 0.0 and 1.0>,
  "reasoning": "<explain why this action and angle>"
}}

EXAMPLES OF GOOD DECISIONS:
- Task: "pick object from intake" | Current: angle=0, status=idle
  Output: {{"action": "pick", "target_angle": 180, "confidence": 0.95, "reasoning": "Intake is at 180 degrees"}}

- Task: "move to handoff zone" | Current: angle=180, status=idle
  Output: {{"action": "move", "target_angle": 90, "confidence": 0.9, "reasoning": "Handoff zone at 90 degrees"}}

- Task: "drop at output" | Current: angle=90, status=idle
  Output: {{"action": "drop", "target_angle": 0, "confidence": 0.92, "reasoning": "Output station at 0 degrees"}}

- Task: "rotate to 45" | Current: angle=90, status=idle
  Output: {{"action": "rotate", "target_angle": 45, "confidence": 1.0, "reasoning": "User requested 45 degrees"}}
"""
        # WHY THIS PROMPT:
        # - Tells Qwen it's an ARM'S BRAIN (gives role)
        # - Defines the DECISION PROCESS (step by step)
        # - Specifies JSON FORMAT (so we can parse response)
        # - Gives EXAMPLES (helps Qwen understand task types)
        # - Sets CONSTRAINTS (must be valid JSON, specific fields)

        # ═════════════════════════════════════════════════════════════════
        # STEP 2: CREATE USER MESSAGE
        # PURPOSE: Tell Qwen the specific task + current state for THIS arm
        # ═════════════════════════════════════════════════════════════════

        user_message = f"""TASK: {task}

CURRENT ARM STATE:
{json.dumps(current_state, indent=2)}

What should Arm {self.arm_id} do?
Respond with ONLY the JSON decision. No explanation, no markdown."""

        # WHY THIS MESSAGE:
        # - Specific to THIS task (what needs to be done)
        # - Shows THIS arm's state (position, holding status)
        # - Asks Qwen specifically about THIS arm (arm_id)
        # - Reminds Qwen to output JSON only (prevents chat responses)

        # ═════════════════════════════════════════════════════════════════
        # STEP 2.5: DETERMINISTIC PRE-FILTERS
        # PURPOSE: Catch "stop/don't move" + literal angles BEFORE the LLM
        # ═════════════════════════════════════════════════════════════════
        # A 0.5 B model cannot reliably handle these — it converts "stop" into
        # the closest verb in its vocabulary, which can move the arm in the
        # opposite direction of what the user asked. Rules win here.
        rule_decision = _try_pre_filter(task)
        if rule_decision is not None:
            print(f"[BRAIN] pre-filter hit ({rule_decision.get('source')}): "
                  f"{rule_decision['action']} @ {rule_decision.get('target_angle')}")
            decision = _validate_decision(dict(rule_decision))
            decision["arm_id"] = self.arm_id
            decision["cache_hit"] = False
            return decision

        # ═════════════════════════════════════════════════════════════════
        # STEP 3: CACHE LOOKUP
        # PURPOSE: Return previously-seen decision for this task+angle
        # ═════════════════════════════════════════════════════════════════
        # Bucket current angle to 15° so near-identical states share a slot.
        try:
            angle_now = float(current_state.get("angle", 90.0))
        except (TypeError, ValueError):
            angle_now = 90.0
        angle_bucket = int(round(angle_now / 15.0) * 15)
        cache_key = (self.arm_id, task.strip().lower(), angle_bucket)

        cached = self._cache.get(cache_key)
        if cached is not None:
            print(f"[BRAIN] cache hit for {cache_key}")
            # Return a fresh dict so callers can mutate it without poisoning the cache
            decision = dict(cached)
            decision["cache_hit"] = True
            decision["arm_id"] = self.arm_id
            return decision

        # ═════════════════════════════════════════════════════════════════
        # STEP 4: ASK QWEN
        # PURPOSE: Send prompt + message to Qwen LLM, get decision back
        # ═════════════════════════════════════════════════════════════════

        decision = self.qwen.ask_decision(
            system_prompt,
            current_state,
            user_message=user_message,
        )

        # WHAT HAPPENS HERE:
        # - self.qwen.ask_decision() calls Ollama/Qwen
        # - Qwen analyzes: system_prompt + user_message (which includes the task)
        # - Qwen returns JSON response with decision
        # - We get back: {action, target_angle, confidence, reasoning, status}

        # ═════════════════════════════════════════════════════════════════
        # STEP 5: VALIDATE OUTPUT
        # PURPOSE: Clamp angle, whitelist action, default missing fields
        # ═════════════════════════════════════════════════════════════════
        decision = _validate_decision(decision)

        # ═════════════════════════════════════════════════════════════════
        # STEP 6: STORE IN CACHE (only successful, validated decisions)
        # ═════════════════════════════════════════════════════════════════
        if decision.get("status") != "failed":
            if len(self._cache) >= self._cache_max:
                # Trivial eviction: drop the first inserted key.
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = dict(decision)

        # ═════════════════════════════════════════════════════════════════
        # STEP 7: ADD ARM ID TO RESPONSE
        # PURPOSE: Mark which arm made this decision
        # ═════════════════════════════════════════════════════════════════

        decision["arm_id"] = self.arm_id
        decision["cache_hit"] = False

        # WHY:
        # - Next service (Executor) needs to know which arm to command
        # - Frontend needs to know which arm made which decision
        # - Logging needs to track decisions per arm

        # ═════════════════════════════════════════════════════════════════
        # STEP 5: RETURN DECISION
        # PURPOSE: Send decision back to caller (Executor)
        # ═════════════════════════════════════════════════════════════════

        return decision

        # WHAT CALLER GETS:
        # {
        #     "action": "pick",
        #     "target_angle": 45,
        #     "confidence": 0.95,
        #     "reasoning": "...",
        #     "status": "success",
        #     "arm_id": 1          ← WE ADDED THIS
        # }


    def _dead_intermediates(self):
        """Return non-arm assets surrounding this arm that are not alive.

        Two slices matter:

        - Upstream chain: assets between the previous arm and this arm. If
          any is dead, the object can't have legitimately reached this arm's
          input zone (e.g. arm-3 must not pick from handover_2 if veh-a or
          conv-1 are failed — the object got there only because they used to
          be alive, and re-relaying would break the demo's physical story).

        - Immediate downstream actor: if this arm is about to drop onto an
          asset (e.g. arm-2 → veh-a), and that asset is dead, the object
          would pile up there with no way forward.

        Both lists are merged so the safety-net override has a single
        "intermediate not alive" condition to react to.
        """
        try:
            from app.models import pipeline_config
            from app.models import asset_state
        except Exception:
            return []
        try:
            flow = pipeline_config.get_module_flow_order()
        except Exception:
            return []
        my_id = f"arm-{self.arm_id}"
        if my_id not in flow:
            return []
        i_self = flow.index(my_id)
        dead = []

        # Upstream intermediates (between previous arm and me).
        if self.arm_id > 1:
            prev_id = f"arm-{self.arm_id - 1}"
            if prev_id in flow:
                i_prev = flow.index(prev_id)
                if i_self > i_prev + 1:
                    upstream_between = flow[i_prev + 1 : i_self]
                    dead.extend(
                        aid for aid in upstream_between
                        if not aid.startswith("arm-") and not asset_state.is_alive(aid)
                    )

        # Immediate downstream actor — if it's an asset and dead, hold.
        if i_self + 1 < len(flow):
            nxt = flow[i_self + 1]
            if not nxt.startswith("arm-") and not asset_state.is_alive(nxt):
                dead.append(nxt)

        # Dedup while preserving order.
        seen = set()
        return [a for a in dead if not (a in seen or seen.add(a))]

    # ════════════════════════════════════════════════════════════════════
    # SCRIPT DISPATCHER — Qwen picks one named behaviour from a per-arm menu
    # ════════════════════════════════════════════════════════════════════
    # Different from `decide_action`. Instead of inventing an angle, Qwen
    # is asked to classify the task into one of N (~3) script names. This
    # is a much smaller cognitive load — a 0.5 B model can do it reliably.

    def pick_script(self, task: str, scripts: list,
                    object_summary: Dict[str, int] = None) -> Dict[str, Any]:
        """
        Ask Qwen to choose one script name from this arm's menu.

        Args:
            task: free-form English task ("pick from intake", "stop", …).
            scripts: list of {name, desc} dicts (the per-arm menu).
            object_summary: optional {state: count} dict so Qwen sees where
                real objects currently sit. Used to pick `hold_*` instead
                of `relay_*` when the source location is empty.

        Returns:
            {"script": "<chosen name>", "reasoning": "<why>", "confidence": <0-1>,
             "status": "success" | "failed", "arm_id": <id>}
        """
        # Build a menu the model can rank against.
        menu = "\n".join(f'- "{s["name"]}": {s["desc"]}' for s in scripts)
        valid_names = {s["name"] for s in scripts}
        valid_list = list(valid_names)
        relay_options = [n for n in valid_list if n.startswith("relay_")]
        hold_options = [n for n in valid_list if n.startswith("hold_")]
        rest_option = "return_to_rest" if "return_to_rest" in valid_names else None

        # Per-arm geometry derived from pipeline.yaml — works for any arm count.
        # Each arm always picks at 180° (its upstream reach) and drops at 0°
        # (its downstream reach). The zone names ("intake", "handover_<i>",
        # "on_<asset>", "output") come from pipeline_config so the same code
        # handles any pipeline shape without changes.
        from app.services.arm_scripts import _actor_zones  # local import avoids cycle
        zones = _actor_zones(self.arm_id)
        my_source = zones["upstream"]
        my_dest = zones["downstream"]
        per_arm_facts = (
            f"You are arm {self.arm_id}. You can reach {my_source} (180°) "
            f"and {my_dest} (0°). Your 'source' is {my_source}; your "
            f"'destination' is {my_dest}."
        )

        source_count_pre = (object_summary or {}).get(my_source, 0) if my_source else 0
        dead_intermediates_pre = self._dead_intermediates()

        # ── Fast-path: skip Qwen when the answer is forced ─────────────
        # In a continuous-loop demo, every actor polls a few times per
        # second. Calling Qwen for an obvious "nothing to do / can't do
        # it" answer wastes 5–30 s of CPU inference per cycle. When the
        # safety-net rule would override anyway, return the override
        # answer directly without consulting the LLM. The script_chosen
        # event still fires, so the operator sees the (synthetic)
        # decision in the log panel.
        if dead_intermediates_pre:
            forced = (hold_options[0] if hold_options
                      else (rest_option or (valid_list[0] if valid_list else "")))
            return {
                "status": "success",
                "script": forced,
                "qwen_suggested": "(skipped — LLM not consulted)",
                "qwen_raw": "",
                "reasoning": (
                    f"intermediate asset(s) not alive: "
                    f"{', '.join(dead_intermediates_pre)}"
                ),
                "override_reason": (
                    f"intermediate asset(s) not alive: "
                    f"{', '.join(dead_intermediates_pre)}; hold until recovered"
                ),
                "typo_corrected": False,
                "fallback": False,
                "confidence": 1.0,
                "arm_id": self.arm_id,
            }
        if my_source and source_count_pre == 0:
            forced = (hold_options[0] if hold_options
                      else (rest_option or (valid_list[0] if valid_list else "")))
            return {
                "status": "success",
                "script": forced,
                "qwen_suggested": "(skipped — LLM not consulted)",
                "qwen_raw": "",
                "reasoning": f"{my_source} is empty; nothing to relay",
                "override_reason": None,
                "typo_corrected": False,
                "fallback": False,
                "confidence": 1.0,
                "arm_id": self.arm_id,
            }

        system_prompt = (
            f"You are the dispatcher for Arm {self.arm_id}.\n"
            f"{per_arm_facts}\n"
            "You must choose exactly ONE script name from the menu below.\n"
            "Rule: pick a relay_* script ONLY if there is a real object at YOUR source. "
            "Otherwise prefer the hold_* script (wait) or return_to_rest.\n\n"
            f"MENU:\n{menu}\n\n"
            "Output ONLY valid JSON of the form:\n"
            '{"script": "<name from the menu>", "reasoning": "<one short sentence>"}\n'
            "Do not invent script names. Pick exactly one from the menu."
        )

        # Tell the model ONLY about its own source location (in plain English,
        # not a structured count) — a 0.5 B model handles "yes/no" prose much
        # better than "intake=1, handover_1=0, …".
        source_count = (object_summary or {}).get(my_source, 0) if my_source else 0
        if source_count >= 1:
            availability = (
                f"There IS an object available at YOUR source ({my_source}). "
                "You must pick a relay_* script."
            )
        else:
            availability = (
                f"There is NO object at YOUR source ({my_source}). "
                "You must NOT pick a relay_* script. Pick hold_* or return_to_rest."
            )

        user_message = (
            f"TASK: {task}\n"
            f"AVAILABILITY: {availability}\n"
            "Output ONLY the JSON described above."
        )

        # Reuse the existing Qwen client.
        raw = self.qwen.ask_decision(system_prompt, {}, user_message=user_message)

        # Surface Qwen failure as failure (not as a fake success).
        if raw.get("status") in ("failed", "timeout", "exception"):
            return {"status": "failed",
                    "error": raw.get("error", "LLM unavailable"),
                    "arm_id": self.arm_id}

        qwen_raw = str(raw.get("script", "")).strip()
        qwen_reasoning = str(raw.get("reasoning", ""))

        # ── Step A: fuzzy-match Qwen's name against the menu ─────────────
        # Handles common typos like `hold_at_handoff_2` → `hold_at_handoff2`
        # and `relay_intake_to_handoff1` → `relay_intake_to_handoff_1`.
        if qwen_raw in valid_names:
            qwen_suggested = qwen_raw
            typo_corrected = False
        else:
            matches = difflib.get_close_matches(qwen_raw, valid_list, n=1, cutoff=0.6)
            if matches:
                qwen_suggested = matches[0]
                typo_corrected = True
            else:
                # No close match. Use a safe default and mark it.
                qwen_suggested = "return_to_rest" if "return_to_rest" in valid_names else valid_list[0]
                typo_corrected = True

        # ── Step B: deterministic override based on source availability ──
        # The 0.5 B model sometimes contradicts its own reasoning (saw an
        # object available, then picked `return_to_rest`). The rule
        # "relay iff source has an object" is unambiguous, so we enforce it.
        # `relay_options` / `hold_options` / `rest_option` were computed at
        # the top of this method (before the fast-paths) so they are
        # already available here.

        final_choice = qwen_suggested
        override_reason = None

        # We only get here when there IS work to do (the fast-paths above
        # return early when source is empty or intermediates are dead).
        # Enforce: the final choice MUST be a relay_*. If Qwen tried to
        # hold or rest while the source has an object, override.
        if not final_choice.startswith("relay_") and relay_options:
            override_reason = (
                f"object present at {my_source}; relay_* is required"
            )
            final_choice = relay_options[0]

        return {
            "status": "success",
            "script": final_choice,
            "qwen_suggested": qwen_suggested,
            "qwen_raw": qwen_raw,
            "reasoning": qwen_reasoning,
            "override_reason": override_reason,
            "typo_corrected": typo_corrected,
            "fallback": typo_corrected and qwen_raw not in valid_names and not difflib.get_close_matches(qwen_raw, valid_list, n=1, cutoff=0.6),
            "confidence": 1.0,
            "arm_id": self.arm_id,
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON PATTERN: Keep one brain per arm in memory
# ═══════════════════════════════════════════════════════════════════════════

_arm_brains = {}  # Dictionary: {arm_id: ArmBrain instance}

# WHY SINGLETON PATTERN:
# ======================
# Without singleton (BAD):
#   get_arm_brain(1)  → Creates new brain
#   get_arm_brain(1)  → Creates another new brain
#   get_arm_brain(1)  → Creates another new brain
#   Result: 3 brains created for same arm (wasteful!)
#
# With singleton (GOOD):
#   get_arm_brain(1)  → Creates brain, stores in _arm_brains
#   get_arm_brain(1)  → Reuses brain from _arm_brains (fast!)
#   get_arm_brain(1)  → Reuses brain from _arm_brains (fast!)
#   Result: 1 brain created, reused 3 times (efficient!)


def get_arm_brain(arm_id: int) -> ArmBrain:
    """
    ┌─────────────────────────────────────────────────────────────────┐
    │  GET_ARM_BRAIN: Get or create brain for an arm (singleton)      │
    └─────────────────────────────────────────────────────────────────┘

    WHAT THIS DOES:
    ===============
    - If brain for this arm already exists → return it
    - If not → create it, store it, return it

    WHY:
    ====
    Efficiency: Don't create new brains repeatedly, reuse them

    PARAMETERS:
    ===========
    arm_id (int): Which arm? (1, 2, or 3)

    RETURNS:
    ========
    ArmBrain instance for that arm

    EXAMPLE:
    ========
    >>> brain_1 = get_arm_brain(1)  # First call: creates brain
    >>> brain_1 = get_arm_brain(1)  # Second call: reuses brain (fast!)
    >>> brain_2 = get_arm_brain(2)  # Creates new brain for Arm 2
    """

    # ═════════════════════════════════════════════════════════════════
    # CHECK: Does brain for this arm already exist?
    # ═════════════════════════════════════════════════════════════════

    if arm_id not in _arm_brains:
        # Brain doesn't exist yet, create it
        _arm_brains[arm_id] = ArmBrain(arm_id)
        print(f"[BRAIN] Created new brain for Arm {arm_id}")

    # ═════════════════════════════════════════════════════════════════
    # RETURN: The brain (either newly created or existing)
    # ═════════════════════════════════════════════════════════════════

    return _arm_brains[arm_id]

    # WHY RETURN HERE:
    # - Either: newly created brain (just made)
    # - Or: existing brain (reused from storage)
    # - Both are ready to make decisions!
