# User Story #7: Qwen as Brains for Each Arm

**Owner:** Haris  
**Branch:** dev-haris  
**Started:** April 17, 2026  
**Expected Completion:** April 24, 2026

---

## 📋 USER STORY REQUIREMENT

### Narrative
"Use Qwen as Brains for Each Arm. Use one Qwen instance for each arm and use it as a brain. Central Unit (demo) saying: 'Arm 1, you need to do stuff'. Request send to 'Brain' (Qwen) of arm 1. Processes request and runs 'arm 1 script' inside arm 1 container. Feedback to Qwen 'I'm done' once done (or 'doesn't work because'). Send feedback back to central unit (demo). Then proceed with next arm."

### Acceptance Criteria
- ✅ Each arm has independent Qwen brain
- ✅ Central unit sends tasks
- ✅ Qwen makes decisions for arm
- ✅ Arm executes decision
- ✅ Feedback loop: Arm → Brain → Central Unit
- ✅ Notifications: ✅ SUCCESS or ❌ FAILED
- ✅ Works with all 3 arms (Arm 1, 2, 3)
- ✅ Frontend shows live notifications

---

## 🛠️ IMPLEMENTATION GUIDE

### STEP 1: Create ArmBrain Service

**File:** `app/services/arm_brain.py`

```python
"""
ArmBrain — Qwen decision engine for individual arm.

One instance per arm. Each arm asks its brain:
"What should I do with this task?"
"""

import os
import json
import requests
from typing import Dict, Any
from app.services.qwen_brain import get_qwen_brain

class ArmBrain:
    """One Qwen brain per arm (arm 1, 2, or 3)"""
    
    def __init__(self, arm_id: int):
        self.arm_id = arm_id
        self.qwen = get_qwen_brain()  # Use existing Qwen via Ollama
    
    def decide_action(self, task: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ask Qwen: What should Arm N do with this task?
        
        Args:
            task: Task description (e.g., "pick object from intake")
            current_state: Current arm state {position, holding, etc.}
        
        Returns:
            Decision: {action, target_angle, confidence, status}
        """
        system_prompt = f"""You are the brain (AI decision engine) for Arm {self.arm_id}.
Your job is to decide what this arm should do based on the task and current state.
Respond with ONLY valid JSON. No explanation, no markdown.

Output format:
{{
  "action": "pick|drop|move|rotate",
  "target_angle": <0-180>,
  "confidence": <0-1>,
  "reasoning": "why this action"
}}
"""
        
        user_message = f"""Task: {task}
Current arm state: {json.dumps(current_state)}

What should Arm {self.arm_id} do?"""
        
        decision = self.qwen.ask_decision(system_prompt, {"task": task, "state": current_state})
        
        # Add arm_id to response
        decision["arm_id"] = self.arm_id
        return decision


# Keep one brain per arm
_arm_brains = {}

def get_arm_brain(arm_id: int) -> ArmBrain:
    """Get or create ArmBrain singleton for this arm."""
    if arm_id not in _arm_brains:
        _arm_brains[arm_id] = ArmBrain(arm_id)
    return _arm_brains[arm_id]
```

### STEP 2: Create ArmExecutor Service

**File:** `app/services/arm_execution.py`

```python
"""
ArmExecutor — Execute arm actions and collect feedback.

1. Ask brain what to do
2. Send command to arm server
3. Collect feedback
4. Update state
5. Return result
"""

import os
import requests
from typing import Dict, Any
from app.services.arm_brain import get_arm_brain
from app.models import arm_state

class ArmExecutor:
    """Execute tasks on an arm via its Qwen brain"""
    
    def __init__(self, arm_id: int):
        self.arm_id = arm_id
        self.brain = get_arm_brain(arm_id)
        self.arm_server = os.environ.get(
            'ARM_SERVER',
            f'http://arm-{arm_id}:8000'
        )
    
    def execute_task(self, task: str) -> Dict[str, Any]:
        """
        Execute task on arm:
        1. Ask brain what to do
        2. Send to arm server
        3. Get feedback
        4. Return result
        """
        # Step 1: Ask brain
        current_state = arm_state.get_state(self.arm_id)
        decision = self.brain.decide_action(task, current_state)
        
        if decision.get("status") == "failed":
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Brain error: {decision.get('error')}",
                "action": "error"
            }
        
        # Step 2: Execute on arm server
        try:
            response = requests.post(
                f"{self.arm_server}/move",
                json={"angle": decision.get("target_angle", 0)},
                timeout=15
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Arm server timeout",
                "action": decision.get("action")
            }
        except Exception as e:
            return {
                "status": "failed",
                "arm_id": self.arm_id,
                "reason": f"Execution error: {str(e)}",
                "action": decision.get("action")
            }
        
        # Step 3: Task complete!
        arm_state.set_angle(self.arm_id, decision.get("target_angle", 0))
        
        return {
            "status": "success",
            "arm_id": self.arm_id,
            "action": decision.get("action"),
            "target_angle": decision.get("target_angle"),
            "confidence": decision.get("confidence"),
            "reasoning": decision.get("reasoning")
        }


# Keep one executor per arm
_arm_executors = {}

def get_arm_executor(arm_id: int) -> ArmExecutor:
    """Get or create ArmExecutor singleton for this arm."""
    if arm_id not in _arm_executors:
        _arm_executors[arm_id] = ArmExecutor(arm_id)
    return _arm_executors[arm_id]
```

### STEP 3: Create Notifications Service

**File:** `app/services/notifications.py`

```python
"""
Notifications — Send task result alerts to frontend.

Sends to browser via socketio:
- Task completed successfully
- Task failed with reason
- Real-time status updates
"""

from datetime import datetime
from app import socketio

class TaskNotification:
    """Send task status notifications to frontend"""
    
    @staticmethod
    def notify_success(arm_id: int, action: str, confidence: float = 1.0):
        """Send: ✅ Arm N completed action successfully"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        socketio.emit('task_complete', {
            "status": "success",
            "arm_id": arm_id,
            "action": action,
            "confidence": confidence,
            "timestamp": timestamp,
            "message": f"✅ Arm {arm_id} completed '{action}' successfully",
            "emoji": "✅"
        })
        
        # Also log it
        socketio.emit('log_message', {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}',
            'message': f'✅ Task completed: {action}',
        })
    
    @staticmethod
    def notify_failure(arm_id: int, reason: str, action: str = "unknown"):
        """Send: ❌ Arm N FAILED with reason"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        socketio.emit('task_complete', {
            "status": "failed",
            "arm_id": arm_id,
            "action": action,
            "reason": reason,
            "timestamp": timestamp,
            "message": f"❌ Arm {arm_id} FAILED: {reason}",
            "emoji": "❌"
        })
        
        # Also log it
        socketio.emit('log_message', {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}',
            'message': f'❌ Task failed: {reason}',
        })
    
    @staticmethod
    def notify_processing(arm_id: int, action: str):
        """Send: ⏳ Arm N is processing"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        socketio.emit('task_status', {
            "status": "processing",
            "arm_id": arm_id,
            "action": action,
            "timestamp": timestamp,
            "message": f"⏳ Arm {arm_id} is {action}...",
            "emoji": "⏳"
        })

# Singleton instance
_notifications = TaskNotification()

def get_notification_service() -> TaskNotification:
    """Get notification service"""
    return _notifications
```

### STEP 4: Add API Endpoint

**File:** `app/routes/arm_routes.py` (ADD NEW ENDPOINT)

```python
from app.services.arm_execution import get_arm_executor
from app.services.notifications import get_notification_service

@arm_bp.route('/execute_task', methods=['POST'])
def execute_task():
    """
    Execute task on arm using Qwen brain.
    
    Central unit (demo) sends:
    {
        "arm_id": 1,
        "task": "pick object from intake"
    }
    
    Arm brain processes, executes, and returns result:
    {
        "status": "success|failed",
        "arm_id": 1,
        "action": "pick",
        "message": "✅ Arm 1 picked object successfully"
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON required"}), 400
    
    arm_id = data.get('arm_id')
    task = data.get('task')
    
    if not arm_id or not task:
        return jsonify({"error": "Missing arm_id or task"}), 400
    
    # Notify: Processing
    notif = get_notification_service()
    notif.notify_processing(arm_id, task)
    
    # Execute task via Qwen brain
    executor = get_arm_executor(arm_id)
    result = executor.execute_task(task)
    
    # Send notification based on result
    if result["status"] == "success":
        notif.notify_success(
            arm_id,
            result.get("action", "execute"),
            result.get("confidence", 1.0)
        )
    else:
        notif.notify_failure(
            arm_id,
            result.get("reason", "Unknown error"),
            result.get("action", "unknown")
        )
    
    # Emit to frontend
    socketio.emit('task_result', result)
    
    return jsonify(result)
```

### STEP 5: Update Demo Runner

**File:** `scripts/run_demo.py` (MODIFY)

```python
# In demo loop, replace direct angle setting with:

for arm_id in [1, 2, 3]:
    task = f"Pick object and move to next station"
    
    # Execute via Qwen brain (NEW!)
    response = requests.post(
        "http://backend:5000/execute_task",
        json={"arm_id": arm_id, "task": task},
        timeout=60
    )
    
    result = response.json()
    
    if result["status"] == "success":
        print(f"✅ Arm {arm_id}: {result['action']} completed")
        print(f"   Confidence: {result.get('confidence', 'N/A')}")
        print(f"   Reasoning: {result.get('reasoning', 'N/A')}")
    else:
        print(f"❌ Arm {arm_id}: FAILED - {result['reason']}")
        # Handle failure as needed
        continue_demo = False
        break
    
    time.sleep(1)  # Wait between arms
```

---

## 🧪 HOW TO TEST

### Test 1: Local Testing with Docker

```bash
# 1. Make sure you're on dev-haris branch
git checkout dev-haris

# 2. Create all 5 files above (arm_brain.py, arm_execution.py, etc.)

# 3. Build and run
docker-compose up --build

# 4. In another terminal, run demo
curl -X POST http://localhost:5000/demo/start \
  -H "Content-Type: application/json" \
  -d '{"duration": 30}'

# 5. Watch the magic! 🎉
```

### Test 2: Individual Arm Test

```bash
# Test one arm manually
curl -X POST http://localhost:5000/execute_task \
  -H "Content-Type: application/json" \
  -d '{
    "arm_id": 1,
    "task": "pick object from intake"
  }'

# Expected response:
{
  "status": "success",
  "arm_id": 1,
  "action": "pick",
  "target_angle": 45,
  "confidence": 0.95,
  "reasoning": "Object at intake requires pickup action"
}
```

### Test 3: Check Notifications in Frontend

Open browser: `http://localhost:8080`

You will see in real-time:
- **Task Processing:** ⏳ Arm 1 is picking...
- **Task Success:** ✅ Arm 1 completed 'pick' successfully
- **Task Failure:** ❌ Arm 1 FAILED: motor timeout
- **Log Panel:** Shows all actions and reasons

---

## 📊 EXPECTED RESULTS

### Frontend Notifications (Real-time)

```
┌──────────────────────────────────────┐
│  TASK NOTIFICATIONS                  │
├──────────────────────────────────────┤
│                                      │
│ ⏳ Arm 1 is picking...                │
│                                      │
│ ✅ Arm 1 completed 'pick' successfully │
│    Confidence: 0.95                  │
│    Time: 14:32:15                    │
│                                      │
│ ⏳ Arm 2 is moving...                 │
│                                      │
│ ✅ Arm 2 completed 'move' successfully │
│    Confidence: 0.88                  │
│    Time: 14:32:18                    │
│                                      │
│ ⏳ Arm 3 is dropping...               │
│                                      │
│ ✅ Arm 3 completed 'drop' successfully │
│    Confidence: 0.92                  │
│    Time: 14:32:21                    │
│                                      │
└──────────────────────────────────────┘
```

### Log Panel (Shows Qwen Brain Decisions)

```
14:32:15 [arm_1] ⏳ Task: pick object from intake
14:32:15 [arm_1] 🧠 Qwen Brain: decision={action: pick, angle: 45, confidence: 0.95}
14:32:16 [arm_1] 🤖 Arm 1: Moving to angle 45°
14:32:17 [arm_1] ✅ Task completed: pick

14:32:18 [arm_2] ⏳ Task: move object to handoff
14:32:18 [arm_2] 🧠 Qwen Brain: decision={action: move, angle: 90, confidence: 0.88}
14:32:18 [arm_2] 🤖 Arm 2: Moving to angle 90°
14:32:19 [arm_2] ✅ Task completed: move

14:32:21 [arm_3] ⏳ Task: drop object at output
14:32:21 [arm_3] 🧠 Qwen Brain: decision={action: drop, angle: 0, confidence: 0.92}
14:32:21 [arm_3] 🤖 Arm 3: Moving to angle 0°
14:32:22 [arm_3] ✅ Task completed: drop
```

### Terminal Output (Backend)

```
POST /execute_task {arm_id: 1, task: "pick object"}
  → Asking Arm 1 Brain...
  → Qwen decision: pick (45°, confidence: 0.95)
  → Sending to arm-1:8000/move
  → Arm response: OK
  → Task complete! ✅
  → Notification sent to frontend

POST /execute_task {arm_id: 2, task: "move object"}
  → Asking Arm 2 Brain...
  → Qwen decision: move (90°, confidence: 0.88)
  → Sending to arm-2:8000/move
  → Arm response: OK
  → Task complete! ✅
  → Notification sent to frontend
```

---

## ✅ COMPLETION CHECKLIST

### Implementation
- [ ] Create `app/services/arm_brain.py` (Qwen per arm)
- [ ] Create `app/services/arm_execution.py` (Execute + feedback)
- [ ] Create `app/services/notifications.py` (Alerts)
- [ ] Add `/execute_task` endpoint in `arm_routes.py`
- [ ] Update `scripts/run_demo.py` to use new flow
- [ ] Create `tests/test_arm_execution.py` (unit tests)

### Testing
- [ ] Docker build succeeds
- [ ] Local test with curl works
- [ ] Frontend shows notifications
- [ ] Log panel shows decisions
- [ ] All 3 arms work correctly
- [ ] Success notifications appear
- [ ] Failure notifications work
- [ ] No errors in console

### Documentation & Review
- [ ] Update `documentation/PROJECT_PROGRESS.md`
- [ ] Show to team (Sneh, Amir, etc.)
- [ ] Show to Alex (Product Owner)
- [ ] Get approval
- [ ] Push to dev-haris branch
- [ ] Create PR: dev-haris → dev

### Integration (After Approval)
- [ ] Merge to dev branch
- [ ] Run integration tests in dev
- [ ] Confirm works with Sneh's UI
- [ ] Final test before Sprint 3

---

## 📝 FILES CREATED/MODIFIED

| File | Type | Status |
|------|------|--------|
| `app/services/arm_brain.py` | NEW | To create |
| `app/services/arm_execution.py` | NEW | To create |
| `app/services/notifications.py` | NEW | To create |
| `app/routes/arm_routes.py` | MODIFY | Add endpoint |
| `scripts/run_demo.py` | MODIFY | Use new flow |
| `tests/test_arm_execution.py` | NEW | To create |
| `app/services/__init__.py` | MODIFY | Export services |

---

## 🎉 DELIVERABLES

When complete, you will have:

✅ **3 independent Qwen brains** (one per arm)  
✅ **Real-time task execution** (arm brain → decide → execute)  
✅ **Complete feedback loop** (arm → brain → central unit)  
✅ **Live frontend notifications** (✅ SUCCESS / ❌ FAILED)  
✅ **Detailed logging** (shows Qwen decisions)  
✅ **Production-ready code** (tested, documented)

---

**Start Implementation:** April 17, 2026  
**Target Completion:** April 24, 2026  
**Ready for Integration:** Dev branch testing  
**Ready for Final Review:** May 8, 2026 (Review Party 1)

---

**Good luck, Haris! 🚀**
