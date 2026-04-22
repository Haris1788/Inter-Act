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
import json
from typing import Dict, Any

# ═══════════════════════════════════════════════════════════════════════════
# IMPORTS: What we're using from other files
# ═══════════════════════════════════════════════════════════════════════════

# Import existing Qwen service (created in User Story #6)
# This gives us access to the LLM (Large Language Model)
from app.services.qwen_brain import get_qwen_brain


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
2. Consider the current arm position and state
3. Decide the BEST action to take
4. Calculate the TARGET ANGLE (0-180 degrees)
5. Rate your confidence (0.0 = not sure, 1.0 = very sure)
6. Explain your reasoning briefly

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
- Task: "pick object at intake" | Current: position=0°, empty
  Output: {{"action": "pick", "target_angle": 45, "confidence": 0.95, "reasoning": "Intake station at 45°"}}

- Task: "move to handoff zone" | Current: position=45°, holding object
  Output: {{"action": "move", "target_angle": 90, "confidence": 0.88, "reasoning": "Handoff zone at 90°"}}

- Task: "drop at output" | Current: position=90°, holding object
  Output: {{"action": "drop", "target_angle": 0, "confidence": 0.92, "reasoning": "Output station at 0°"}}
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
        # STEP 3: ASK QWEN
        # PURPOSE: Send prompt + message to Qwen LLM, get decision back
        # ═════════════════════════════════════════════════════════════════

        decision = self.qwen.ask_decision(system_prompt, current_state)

        # WHAT HAPPENS HERE:
        # - self.qwen.ask_decision() calls Ollama/Qwen
        # - Qwen analyzes: system_prompt + user_message + current_state
        # - Qwen returns JSON response with decision
        # - We get back: {action, target_angle, confidence, reasoning, status}

        # ═════════════════════════════════════════════════════════════════
        # STEP 4: ADD ARM ID TO RESPONSE
        # PURPOSE: Mark which arm made this decision
        # ═════════════════════════════════════════════════════════════════

        decision["arm_id"] = self.arm_id

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
