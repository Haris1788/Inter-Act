"""
═══════════════════════════════════════════════════════════════════════════
 USER STORY #7: QWEN AS BRAINS FOR EACH ARM

 FILE: app/services/arm_execution.py (STEP 2)
═══════════════════════════════════════════════════════════════════════════

WHAT THIS FILE DOES:
====================
Takes a decision from the brain and EXECUTES it on the arm.

Bridges the gap between DECISION and EXECUTION:
  Decision alone does nothing → Executor makes it real!

COMPLETE FLOW:
==============
1. Get current arm state (where is arm now?)
2. Ask brain: "What should I do?" (get decision)
3. Execute on arm server: "Move to X°"
4. Collect feedback: "Done!" or "Error: timeout"
5. Update arm state (store new position)
6. Return result to backend (status + details)

WHY WE NEED THIS:
=================
Without executor (BAD):
  arm_brain.py says: "Move to 45°"
  Nothing happens! (brain says something but nobody does it)
  Result: Brain is useless

With executor (GOOD):
  arm_brain.py says: "Move to 45°"
  arm_execution.py does it: Sends to arm server
  Arm executes: Actually moves to 45°
  Feedback returns: Task complete
  Result: Intelligent EXECUTION with feedback loop

ARCHITECTURE:
==============
Backend (/execute_task endpoint)
    ↓
Executor (THIS FILE: arm_execution.py) ← YOU ARE HERE
    ↓
    ├─→ ArmBrain (gets decision from brain)
    ├─→ Arm Server (sends command to move)
    └─→ ArmState (tracks arm positions)

USAGE EXAMPLE:
==============
>>> from app.services.arm_execution import get_arm_executor
>>>
>>> executor_1 = get_arm_executor(1)  # Get executor for Arm 1
>>>
>>> result = executor_1.execute_task("pick object from intake")
>>>
>>> print(result)
{
    "status": "success",
    "arm_id": 1,
    "action": "pick",
    "target_angle": 45,
    "confidence": 0.95,
    "reasoning": "Object at intake requires rotation to 45°",
    "message": "✅ Arm 1 completed 'pick' successfully"
}

═══════════════════════════════════════════════════════════════════════════
"""

import os
import requests
from typing import Dict, Any
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════
# IMPORTS: What we're using from other files
# ═══════════════════════════════════════════════════════════════════════════

# Import the brain service (created in STEP 1)
# This gives us access to Qwen decision-making
from app.services.arm_brain import get_arm_brain

# Import the arm state manager (tracks arm positions)
# This remembers where each arm is located
from app.models import arm_state

# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ArmExecutor
# PURPOSE: One execution engine per arm - executes brain's decisions
# ═══════════════════════════════════════════════════════════════════════════


class ArmExecutor:
    """
    ┌─────────────────────────────────────────────────────────────────────┐
    │  ARMEXECUTOR: The "Body" that executes the Brain's decisions        │
    └─────────────────────────────────────────────────────────────────────┘

    This class represents the "body" (execution engine) for ONE specific arm.

    WHAT IT DOES:
    =============
    - Gets current arm state (position, holding status, etc.)
    - Asks brain: "What should I do with this task?"
    - Executes brain's decision on the actual arm server
    - Collects feedback from arm server
    - Updates our records with new arm position
    - Returns complete result (success/failed + details)

    RELATIONSHIP TO BRAIN:
    ======================
    Brain thinks. Executor acts.

    Brain says:    "I decided: pick at 45°"
    Executor does: "Sending command to arm-1:8000/move (angle=45)"
    Arm responds:  "✅ OK, moved to 45°"
    Executor logs: "Task complete, Arm 1 now at 45°"

    WHY SEPARATE EXECUTOR:
    ======================
    - Brain focuses on DECISIONS (what to do)
    - Executor focuses on EXECUTION (how to do it)
    - Arm server handles MOVEMENT (physical action)
    - Clear separation of concerns = easier to debug + test
    """

    def __init__(self, arm_id: int):
        """
        ┌─────────────────────────────────────────────────────────────────┐
        │  INITIALIZATION: Prepare executor for a specific arm             │
        └─────────────────────────────────────────────────────────────────┘

        WHAT HAPPENS:
        =============
        1. Store which arm this executor controls
        2. Get access to this arm's brain (from STEP 1)
        3. Figure out where this arm's server is located

        WHY THIS INITIALIZATION:
        ========================
        Every executor needs to know:
        - Which arm am I controlling? (arm_id)
        - How do I ask for decisions? (brain reference)
        - Where is the arm server? (arm_server URL)

        PARAMETERS:
        ===========
        arm_id (int): Which arm? (1, 2, or 3)
                     This identifies which robot arm this executor controls

        EXAMPLE:
        ========
        executor_1 = ArmExecutor(1)  # Create executor for Arm 1
        executor_2 = ArmExecutor(2)  # Create executor for Arm 2
        executor_3 = ArmExecutor(3)  # Create executor for Arm 3
        """

        # ═════════════════════════════════════════════════════════════════
        # STORE: Which arm does this executor control?
        # ═════════════════════════════════════════════════════════════════
        self.arm_id = arm_id
        """
        arm_id (int): Identifier for the arm this executor controls

        Used for:
        - Decision-making: "Ask brain for arm {arm_id}"
        - Command sending: "Send command to arm-{arm_id}"
        - Response tracking: "Feedback from arm-{arm_id}"
        - Result logging: "Arm {arm_id} moved to 45°"

        Example: self.arm_id = 1  (this executor controls Arm 1)
        """

        # ═════════════════════════════════════════════════════════════════
        # GET: Access to this arm's brain
        # ═════════════════════════════════════════════════════════════════
        self.brain = get_arm_brain(arm_id)
        """
        brain (ArmBrain): Access to Qwen decision engine for this arm

        What is it?
        -----------
        Each arm has its own brain (created in STEP 1: arm_brain.py)
        The brain makes decisions for THIS SPECIFIC ARM based on:
        - The task description
        - This arm's current state

        How we use it?
        --------------
        When executor needs to execute a task:
        1. Get current arm state
        2. Call: self.brain.decide_action(task, current_state)
        3. Brain returns decision: {action, target_angle, confidence, reasoning}
        4. Executor then executes this decision

        Why this matters?
        -----------------
        - Brain makes INTELLIGENT decisions (not just follow commands)
        - Each arm has its OWN brain (Arm 1 brain ≠ Arm 2 brain)
        - Same task → Different decisions (context-aware)
        """

        # ═════════════════════════════════════════════════════════════════
        # FIGURE OUT: Where is this arm's server?
        # ═════════════════════════════════════════════════════════════════
        self.arm_server = os.environ.get("ARM_SERVER", f"http://arm-{arm_id}:8000")
        """
        arm_server (str): URL where this arm's server is running

        In Docker (PRODUCTION):
        ----------------------
        - Arm 1 server: http://arm-1:8000
        - Arm 2 server: http://arm-2:8000
        - Arm 3 server: http://arm-3:8000

        Why arm-1, arm-2, arm-3?
        These are Docker service names (from docker-compose.yml)
        Docker DNS resolves them automatically

        On Localhost (DEVELOPMENT):
        ---------------------------
        - Arm 1 server: http://localhost:8001
        - Arm 2 server: http://localhost:8002
        - Arm 3 server: http://localhost:8003

        How we get it?
        --------------
        1. Check environment variable 'ARM_SERVER'
        2. If not set, use default: f'http://arm-{arm_id}:8000'
        3. This allows flexibility: can override via .env file

        Example:
        --------
        self.arm_server = "http://arm-1:8000"  (executor for Arm 1)
        """

    def execute_task(self, task: str) -> Dict[str, Any]:
        """
        ┌─────────────────────────────────────────────────────────────────┐
        │  EXECUTE_TASK: The main execution flow for a task               │
        └─────────────────────────────────────────────────────────────────┘

        THIS IS THE MAIN FUNCTION - It's where execution magic happens!

        COMPLETE 6-STEP FLOW:
        =====================
        Step 1: Get current arm state
                (Where is the arm now? What is it holding?)

        Step 2: Ask brain what to do
                (Brain analyzes task + state, makes decision)

        Step 3: Send decision to arm server
                (Actually execute the decision on the physical arm)

        Step 4: Collect feedback
                (Arm responds: "Done!" or "Error: timeout")

        Step 5: Update arm state
                (Remember new arm position for next task)

        Step 6: Return result
                (Send status + details back to caller)

        WHY THIS ORDER MATTERS:
        =======================
        ❌ WRONG: Send command directly without asking brain
           Result: Arm moves to wrong position, ignores context

        ✅ CORRECT: Get state → Ask brain → Execute → Collect feedback → Update state
           Result: Arm makes intelligent decisions, learns from feedback

        PARAMETERS:
        ===========
        task (str): What needs to be done?
                   Examples:
                   - "pick object from intake"
                   - "move object to handoff zone"
                   - "drop object at output"
                   - "rotate to 90 degrees"

        RETURNS:
        ========
        Success result dictionary:
        {
            "status": "success",              # Task succeeded
            "arm_id": 1,                      # Which arm?
            "action": "pick",                 # What did it do?
            "target_angle": 45,               # Where did it move to?
            "confidence": 0.95,               # How sure was Qwen? (0-1)
            "reasoning": "Object at...",      # Why did it decide this?
            "timestamp": "2026-04-22T...",   # When did it happen?
            "message": "✅ Arm 1 completed..." # Display message
        }

        OR failure result dictionary:
        {
            "status": "failed",                           # Task failed
            "arm_id": 1,                                  # Which arm?
            "action": "pick",                             # What was it trying?
            "reason": "Timeout waiting for arm server",  # Why did it fail?
            "message": "❌ Arm 1 FAILED: Timeout"        # Display message
        }

        EXAMPLE USAGE:
        ==============
        >>> executor = ArmExecutor(1)
        >>> task = "pick object from intake"
        >>> result = executor.execute_task(task)
        >>> print(result)
        {
            "status": "success",
            "arm_id": 1,
            "action": "pick",
            "target_angle": 45,
            "confidence": 0.95,
            "reasoning": "Object located at 45°, arm needs to reach it"
        }
        """

        print(f"\n{'='*60}")
        print(f"EXECUTING TASK ON ARM {self.arm_id}")
        print(f"Task: {task}")
        print(f"{'='*60}")

        # ========================================================================
        # STEP 1: GET CURRENT ARM STATE
        # PURPOSE: Know where the arm is now (position, what it's holding)
        # ========================================================================
        # Why this step?
        # The brain needs context: "Where are you now?" before deciding what to do
        # Example:
        #   Task: "Pick object"
        #   Without state: Brain doesn't know where to pick from
        #   With state: Brain knows "I'm at 0°, object is at 45°" → "Move to 45°"

        try:
            current_state = arm_state.get_state(self.arm_id)
            print(f"📍 Current arm state: {current_state}")
        except Exception as e:
            print(f"❌ Error getting arm state: {e}")
            # If we can't get arm state, task fails immediately
            # Can't ask brain what to do without knowing current state
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Cannot get arm state: {str(e)}",
                "action": "error",
                "message": f"❌ Arm {self.arm_id} FAILED: Cannot read state",
            }

        # ========================================================================
        # STEP 2: ASK BRAIN WHAT TO DO
        # PURPOSE: Brain analyzes task + current state, makes decision
        # ========================================================================
        # This is where AI happens! Brain considers:
        # - The task (what needs to be done)
        # - Current state (where we are, what we're holding)
        # - Makes intelligent decision (best action + angle to move to)
        #
        # Flow:
        # 1. Brain gets system prompt (role: "You are Arm X's brain")
        # 2. Brain gets user message (task + current state)
        # 3. Brain asks Qwen LLM: "What should I (Arm X) do?"
        # 4. Qwen responds with JSON: {action, target_angle, confidence, reasoning}
        # 5. Brain returns decision to executor

        print(f"\n🧠 Asking brain for decision...")
        decision = self.brain.decide_action(task, current_state)

        print(f"🧠 Brain decision:")
        print(f"   Action: {decision.get('action')}")
        print(f"   Target angle: {decision.get('target_angle')}")
        print(f"   Confidence: {decision.get('confidence')}")
        print(f"   Reasoning: {decision.get('reasoning')}")

        # CHECK: Did brain succeed in making a decision?
        # Brain can fail if:
        # - Qwen LLM didn't respond
        # - Qwen response was invalid JSON
        # - Qwen timed out
        if decision.get("status") == "failed":
            print(f"❌ Brain failed to make decision: {decision.get('error')}")
            # Brain failed, so executor can't execute
            # Return failure to caller
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Brain error: {decision.get('error')}",
                "action": "error",
                "message": f"❌ Arm {self.arm_id} FAILED: Brain error",
            }

        # ────────────────────────────────────────────────────────────────
        # NOOP: Brain explicitly refused to move the arm.
        # ────────────────────────────────────────────────────────────────
        # The pre-filter returns this for tasks like "stop", "don't move",
        # "halt". We honour it: no /move call, no state change, no error.
        # The frontend gets a success-with-noop so the operator sees that
        # the system understood the request.
        if decision.get("action") == "noop":
            print(f"🚫 Brain returned noop - not moving arm {self.arm_id}")
            return {
                "status": "success",
                "arm_id": self.arm_id,
                "action": "noop",
                "target_angle": current_state.get("angle"),
                "confidence": decision.get("confidence", 1.0),
                "reasoning": decision.get("reasoning", "no movement requested"),
                "timestamp": datetime.now().isoformat(),
                "message": f"🚫 Arm {self.arm_id} stayed still (noop)",
            }

        # ========================================================================
        # STEP 3: SEND DECISION TO ARM SERVER
        # PURPOSE: Actually execute the decision on the physical arm
        # ========================================================================
        # Now we have the brain's decision: {action, target_angle, confidence, reasoning}
        # Time to tell the actual arm to execute this decision.
        #
        # How it works:
        # 1. Extract target_angle from brain's decision
        # 2. Make HTTP POST request to arm server (e.g., http://arm-1:8000/move)
        # 3. Send JSON: {"angle": 45}
        # 4. Arm server executes: moves servo to 45°
        # 5. Arm server responds: {"status": "ok"} or {"error": "timeout"}
        # 6. We handle response (success or timeout/connection error)

        target_angle = decision.get("target_angle", 0)

        print(f"\n🤖 Sending command to arm server...")
        print(f"   URL: {self.arm_server}/move")
        print(f"   Command: Move to {target_angle}°")

        try:
            # Make HTTP request to arm server
            # Timeout: 15 seconds (if arm doesn't respond in 15 sec, consider it failed)
            response = requests.post(
                f"{self.arm_server}/move",
                json={"angle": target_angle},
                timeout=15,  # Wait max 15 seconds for arm to respond
            )

            # Check if request was successful (HTTP 200-299)
            response.raise_for_status()
            print(f"✅ Arm server responded: {response.status_code}")

        except requests.exceptions.Timeout:
            # TIMEOUT ERROR: Arm server didn't respond in time (15 seconds)
            # This happens when:
            # - Arm is too slow to execute movement
            # - Arm crashed but container still running
            # - Arm server is overloaded
            print(f"❌ Arm server timeout (waited 15 seconds)")
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": "Arm server timeout - no response after 15 seconds",
                "action": decision.get("action"),
                "message": f"❌ Arm {self.arm_id} FAILED: Server timeout",
            }

        except requests.exceptions.ConnectionError:
            # CONNECTION ERROR: Cannot connect to arm server at all
            # This happens when:
            # - Arm container crashed
            # - Arm server is not running
            # - Wrong hostname (arm-1 vs arm-2)
            # - Docker network issues
            print(f"❌ Cannot connect to arm server: {self.arm_server}")
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Cannot connect to arm server: {self.arm_server}",
                "action": decision.get("action"),
                "message": f"❌ Arm {self.arm_id} FAILED: Connection error",
            }

        except Exception as e:
            # OTHER ERROR: Something unexpected happened
            # Could be network error, SSL error, bad JSON, etc.
            print(f"❌ Execution error: {str(e)}")
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Execution error: {str(e)}",
                "action": decision.get("action"),
                "message": f"❌ Arm {self.arm_id} FAILED: {str(e)}",
            }

        # ========================================================================
        # STEP 4: COLLECT FEEDBACK
        # PURPOSE: Parse arm server's response and extract feedback
        # ========================================================================
        # Arm server responded! Now we need to understand what it says.
        #
        # How it works:
        # 1. Arm server sent back HTTP response
        # 2. Extract JSON from response body
        # 3. Parse: {"status": "ok", "angle": 45, "duration": 2.5}
        # 4. Store feedback for next step
        #
        # Why this matters:
        # - Arm might say "OK" (successfully moved)
        # - Arm might say "Error" (motor stuck, sensor failed, etc.)
        # - We need to know the outcome before updating our state

        print(f"\n📦 Collecting feedback from arm...")
        try:
            # Arm server sent JSON response, parse it
            response_data = response.json()
            print(f"✅ Feedback: {response_data}")
        except Exception as e:
            # If we can't parse the response, that's a warning but not a failure
            # The arm probably moved anyway (response was OK)
            print(f"⚠️  Warning: Could not parse arm response: {e}")
            response_data = {}

        # ========================================================================
        # STEP 5: ARM STATE IS ALREADY UP-TO-DATE
        # PURPOSE: No-op - kept for documentation
        # ========================================================================
        # The arm container's /move handler proxies to backend's /set_angle,
        # which has already written `arm_state` AND emitted `state_update`
        # over WebSocket by the time `requests.post(arm_server/move)` returned
        # successfully above. Writing again here would (a) duplicate the
        # `state_update` event, making the canvas re-trigger its easing
        # animation, and (b) waste a lock acquisition. So we skip it.
        print(
            f"\n📊 Arm state already updated by proxy chain (single source of truth)."
        )

        # ========================================================================
        # STEP 6: RETURN SUCCESS RESULT
        # PURPOSE: Send complete result back to caller (Backend API)
        # ========================================================================
        # Task completed successfully!
        # Return all information so Backend/Frontend knows what happened.
        #
        # What goes in result:
        # - status: "success" (task completed)
        # - arm_id: which arm? (1, 2, or 3)
        # - action: what did it do? (from brain's decision)
        # - target_angle: where did it move to?
        # - confidence: how sure was Qwen? (0.95 = 95% sure)
        # - reasoning: why did brain decide this?
        # - timestamp: when did this happen?
        # - message: display text for frontend
        #
        # Backend will use this to:
        # - Send notifications to frontend
        # - Update logs
        # - Proceed with next task

        success_result = {
            "status": "success",
            "arm_id": self.arm_id,
            "action": decision.get("action"),
            "target_angle": target_angle,
            "confidence": decision.get("confidence"),
            "reasoning": decision.get("reasoning"),
            "timestamp": datetime.now().isoformat(),
            "message": f"✅ Arm {self.arm_id} completed '{decision.get('action')}' successfully",
        }

        print(f"\n{'='*60}")
        print(f"✅ TASK COMPLETED SUCCESSFULLY")
        print(f"{'='*60}")
        print(f"Result: {success_result}")

        return success_result


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON PATTERN: Keep one executor per arm in memory
# ═══════════════════════════════════════════════════════════════════════════

_arm_executors = {}  # Dictionary: {arm_id: ArmExecutor instance}

# WHY SINGLETON PATTERN:
# ======================
# Without singleton (BAD - inefficient):
#   get_arm_executor(1)  → Creates new executor, starts fresh
#   get_arm_executor(1)  → Creates ANOTHER executor (what?! again?)
#   get_arm_executor(1)  → Creates ANOTHER executor (3 created for same arm!)
#   Problem: 3 executors doing same job, wasting memory and connections
#
# With singleton (GOOD - efficient):
#   get_arm_executor(1)  → Creates executor, stores in _arm_executors[1]
#   get_arm_executor(1)  → Reuses executor from _arm_executors[1] (fast!)
#   get_arm_executor(1)  → Reuses executor from _arm_executors[1] (fast!)
#   Result: 1 executor created, reused 3 times = memory efficient!
#
# Real-world comparison:
# BAD: Hire new employee every request (training overhead, no continuity)
# GOOD: Keep same employee, reuse their knowledge (efficient, experienced)


def get_arm_executor(arm_id: int) -> ArmExecutor:
    """
    ┌─────────────────────────────────────────────────────────────────┐
    │  GET_ARM_EXECUTOR: Get or create executor for an arm (singleton) │
    └─────────────────────────────────────────────────────────────────┘

    WHAT THIS DOES:
    ===============
    - If executor for this arm already exists → return it
    - If not → create it, store it, return it

    WHY:
    ====
    Efficiency: Don't create new executors repeatedly, reuse them

    PARAMETERS:
    ===========
    arm_id (int): Which arm? (1, 2, or 3)

    RETURNS:
    ========
    ArmExecutor instance for that arm

    EXAMPLE:
    ========
    >>> executor_1 = get_arm_executor(1)  # First call: creates executor
    >>> executor_1 = get_arm_executor(1)  # Second call: reuses executor (fast!)
    >>> executor_2 = get_arm_executor(2)  # Creates new executor for Arm 2
    """

    # ═════════════════════════════════════════════════════════════════
    # CHECK: Does executor for this arm already exist?
    # ═════════════════════════════════════════════════════════════════

    if arm_id not in _arm_executors:
        # Executor doesn't exist yet, create it
        # This happens on first call for this arm
        _arm_executors[arm_id] = ArmExecutor(arm_id)
        # Note: __init__ runs here, setting up:
        # - self.arm_id = arm_id
        # - self.brain = get_arm_brain(arm_id)
        # - self.arm_server = URL to arm server

    # ═════════════════════════════════════════════════════════════════
    # RETURN: The executor (either newly created or existing)
    # ═════════════════════════════════════════════════════════════════

    return _arm_executors[arm_id]

    # WHY RETURN HERE:
    # - Either: newly created executor (just made, initialized)
    # - Or: existing executor (been used before, has history)
    # - Both are ready to execute tasks!
