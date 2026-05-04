# 🎬 LIVE DEMONSTRATION GUIDE
## See How Qwen Brain Works in Real-Time

---

## 🖥️ PART 1: WHAT YOU SEE IN FRONTEND DASHBOARD

**URL:** http://localhost:8080

```
┌─────────────────────────────────────────────────────┐
│  INTER.act                                          │
│  🟢 Connected    [▶️ Run Demo]                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│         ┌──────────┐    ┌──────────┐               │
│   INTAKE│          │ → │ TRANSFER │               │
│        │  ARM 1    │   │   ARM 2  │               │
│        │          │    │          │ → OUTPUT      │
│         └──────────┘    └──────────┘               │
│                        │                           │
│                     ┌──────────┐                  │
│                     │ ARM 3    │                  │
│                     │          │                  │
│                     └──────────┘                  │
│                                                     │
├─────────────────────────────────────────────────────┤
│ 📜 LOG PANEL (Bottom - Real-time messages):         │
│                                                     │
│ [07:17:58] arm_3 Picking Obj3: moving to 180.0°   │
│ [07:17:58] arm_3 Set angle to 180.0°              │
│ [07:18:01] arm_3 Carrying Obj3: moving to 0.0°    │
│ [07:18:01] arm_3 Set angle to 0.0°                │
│ [07:18:03] arm_3 Delivered Obj3 to output!        │
│ ✅ SUCCESS: Arm moved to 90° (Confidence: 1.0)   │
│ 💡 Reasoning: "Intake station at 90°"             │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### **What You See:**
1. **🟢 Connected** - WebSocket connected (shows UI is talking to backend)
2. **▶️ Run Demo** - Button to start demo (uses hardcoded angles)
3. **Visual Canvas** - 3 circles (arms), rectangles (stations), shows object flow
4. **📜 Log Panel** - Real-time messages showing:
   - Arm movements
   - Angles being set
   - **✅ AI Decisions (NEW)** - Shows when Qwen brain makes decision
   - **💡 Reasoning** - Why AI chose that angle
   - **Confidence** - Trust level (0-1 scale)

---

## 🔄 PART 2: WHAT HAPPENS IN BACKEND (Flask)

**Port:** 5000

### **Two Different Paths:**

#### **PATH 1: DEMO (Current - Hardcoded)**
```
Frontend clicks "Run Demo"
↓
Backend runs: python scripts/run_demo.py
↓
NO AI involved, uses hardcoded angles:
  - Pick: 180°
  - Place: 0°
  - Rest: 90°
↓
Arms move (fast, but no intelligence)
```

#### **PATH 2: AI BRAIN (New - Intelligent) ⭐**
```
Send API call:
POST http://localhost:5000/execute_task
Content-Type: application/json
{
  "task": "pick",
  "arm_id": "1"
}

Backend processes:
↓
Step 1: Get current state
  Reads: "Arm 1 is at 90°, position: rest"
↓
Step 2: Call ArmBrain
  File: app/services/arm_brain.py
  Function: decide_action(task="pick", state={...})
↓
Step 3: ArmBrain calls QwenBrain
  File: app/services/qwen_brain.py
  Function: ask_decision(prompt, state)
↓
Step 4: QwenBrain sends to Ollama
  HTTP POST to http://ollama:11434/api/generate
  Sends: "You are Arm 1. Current: 90°. Task: pick. Decide angle."
↓
Step 5: Ollama (Qwen LLM) thinks
  ⏱️ 2-3 seconds of AI processing
  CPU/GPU inference
↓
Step 6: Qwen responds with JSON
  {
    "action": "move",
    "target_angle": 90,
    "confidence": 1.0,
    "reasoning": "Intake station at 90°"
  }
↓
Step 7: ArmBrain parses response
  Returns decision to Backend
↓
Step 8: Backend returns to caller
  Status: 200 OK
  JSON: {
    "status": "success",
    "action": "move",
    "arm_id": 1,
    "target_angle": 90,
    "confidence": 1.0,
    "reasoning": "Intake station at 90°",
    "timestamp": "2026-04-24T07:57:03"
  }
↓
Frontend shows:
  ✅ SUCCESS
  💡 Reasoning: "Intake station at 90°"
  Confidence: 1.0 (100%)
```

### **Backend Response (What You See):**
```json
{
  "status": "success",
  "action": "move",
  "arm_id": 1,
  "confidence": 1.0,
  "message": "✅ Arm 1 completed 'move' successfully",
  "reasoning": "Intake station at 90°",
  "target_angle": 90,
  "timestamp": "2026-04-24T07:57:03.148132"
}
```

---

## 🐳 PART 3: DOCKER CONTAINERS & WHERE BRAIN WORKS

### **Container Architecture:**
```
┌──────────────────────────────────────────────────────┐
│ DOCKER NETWORK (7 services)                          │
├──────────────────────────────────────────────────────┤
│                                                      │
│  🖥️  Frontend (Port 8080)                            │
│      ├─ index.html                                  │
│      ├─ style.css                                   │
│      └─ simulation.js (WebSocket client)            │
│                                                      │
│  📦 Backend (Port 5000) ← AI BRAIN HERE             │
│      ├─ Flask app                                   │
│      ├─ arm_brain.py (AI decision maker)            │
│      ├─ qwen_brain.py (LLM caller)                 │
│      ├─ arm_execution.py (orchestrator)            │
│      ├─ notifications.py (WebSocket sender)        │
│      └─ arm_routes.py (API endpoints)              │
│                                                      │
│  🤖 Ollama (Port 11434) ← QWEN LLM HERE            │
│      ├─ Qwen 2.5 (0.5B model)                      │
│      ├─ HTTP API for inference                     │
│      └─ 2-3 seconds per decision                   │
│                                                      │
│  🤖 Arm 1 (Port 5001)                               │
│      ├─ arm_1_script.py                             │
│      └─ Executes arm movements                      │
│                                                      │
│  🤖 Arm 2 (Port 5002)                               │
│      ├─ arm_2_script.py                             │
│      └─ Executes arm movements                      │
│                                                      │
│  🤖 Arm 3 (Port 5003)                               │
│      ├─ arm_3_script.py                             │
│      └─ Executes arm movements                      │
│                                                      │
│  🎬 Demo Runner                                      │
│      ├─ run_demo.py                                 │
│      └─ Background orchestration                    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### **Where AI Brain Works:**
```
FRONTEND (8080)
    ↓ WebSocket + HTTP
BACKEND (5000) ← 🧠 BRAIN HERE
    ├─ app/services/arm_brain.py ← Individual arm decision
    ├─ app/services/qwen_brain.py ← Calls Qwen
    └─ app/services/arm_execution.py ← 6-step executor
    ↓
OLLAMA (11434) ← 🤖 QWEN MODEL HERE
    (Qwen 2.5 0.5B inference)
    ↓
Arm Simulators (5001/5002/5003)
    (Execute the AI decision)
    ↓
LOGS → FRONTEND (WebSocket)
    (Show results to user)
```

---

## 📊 PART 4: HOW AI DECISION FLOWS END-TO-END

### **Complete Flow Diagram:**

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: USER SENDS REQUEST                                      │
│ Frontend → Backend POST /execute_task                           │
│ {"task": "pick", "arm_id": "1"}                                │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: BACKEND RECEIVES (arm_routes.py)                        │
│ Route: POST /execute_task                                       │
│ Handler: execute_task_endpoint()                                │
│ Action: Creates ArmExecutor for Arm 1                          │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: EXECUTOR ORCHESTRATES (arm_execution.py)               │
│ Function: execute_task(task="pick")                             │
│ Step 3.1: Get current state                                     │
│           State: {"angle": 90, "position": "rest"}            │
│ Step 3.2: Call ArmBrain                                        │
│           decide_action("pick", state)                         │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: ARM BRAIN DECIDES (arm_brain.py) ← 🧠 AI HERE          │
│ Function: decide_action(task, state)                            │
│ Action 4.1: Create smart prompt                                │
│   "You are Arm 1 brain. Current angle: 90°.                   │
│    Task: pick object. Decide best angle (0-180°)."            │
│ Action 4.2: Call QwenBrain                                     │
│   ask_decision(system_prompt, state)                          │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: QWEN BRAIN CALLS LLM (qwen_brain.py)                    │
│ Function: ask_decision(prompt, state)                           │
│ Action: HTTP POST to Ollama                                     │
│   POST http://ollama:11434/api/generate                         │
│   Content: system_prompt + task details                         │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 6: OLLAMA QWEN THINKS (Qwen 0.5B Model)                    │
│ Container: Ollama (Port 11434)                                  │
│ Model: Qwen 2.5 (0.5B - lightweight)                            │
│ Time: ⏱️ 2-3 seconds (CPU inference)                            │
│ Processing: LLM analyzing prompt in detail                      │
│                                                                 │
│ Prompt received:                                                │
│ "You are Arm 1 brain.                                          │
│  Current angle: 90°                                            │
│  Current position: rest                                        │
│  Task: pick object from tray                                  │
│  Constraints: Valid angles 0-180°                             │
│                                                                 │
│  What is the best angle to move to?                           │
│  Return JSON: {action, target_angle, confidence, reasoning}"  │
│                                                                 │
│ LLM Response:                                                   │
│ {                                                               │
│   "action": "move",                                             │
│   "target_angle": 90,                                           │
│   "confidence": 1.0,                                            │
│   "reasoning": "Already at intake station (90°), ready to pick"│
│ }                                                               │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 7: RESPONSE FLOWS BACK                                     │
│ Ollama → QwenBrain → ArmBrain → Executor → Backend API          │
│                                                                 │
│ Backend returns to frontend:                                    │
│ {                                                               │
│   "status": "success",                                          │
│   "action": "move",                                             │
│   "arm_id": 1,                                                  │
│   "target_angle": 90,                                           │
│   "confidence": 1.0,                                            │
│   "reasoning": "Already at intake station",                    │
│   "timestamp": "2026-04-24T07:57:03.148132"                   │
│ }                                                               │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 8: FRONTEND DISPLAYS RESULT                                │
│ WebSocket sends result to browser                               │
│ Frontend shows:                                                 │
│   ✅ SUCCESS                                                    │
│   Action: move                                                  │
│   Target Angle: 90°                                             │
│   Confidence: 1.0 (100%)                                        │
│   💡 Reasoning: "Already at intake station"                    │
│                                                                 │
│ Log Panel Message:                                              │
│ ✅ SUCCESS: Arm 1 moved to 90°                                 │
│ 💡 Reasoning: "Already at intake station (90°), ready to pick" │
│ Confidence: 1.0                                                │
└─────────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────────┐
│ STEP 9: BACKEND EXECUTES                                        │
│ Calls: Arm 1 script (Port 5001)                                │
│ Sends: "Move to 90°"                                           │
│ Result: Arm simulates movement                                  │
│ Time: 1-2 seconds physical movement                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔍 PART 5: WHERE TO FIND THE AI DECISION IN THE CODE

### **File 1: Backend API Endpoint**
**Location:** `app/routes/arm_routes.py`
```python
@arm_bp.route('/execute_task', methods=['POST'])
def execute_task_endpoint():
    data = request.json
    task = data.get('task')
    arm_id = data.get('arm_id')
    
    # This calls the 6-step executor with AI brain
    executor = get_arm_executor(arm_id)
    result = executor.execute_task(task)
    
    # Returns AI decision to frontend
    return jsonify(result)
```

### **File 2: 6-Step Executor**
**Location:** `app/services/arm_execution.py`
```python
def execute_task(self, task):
    # STEP 1: Get state
    state = get_state(self.arm_id)
    
    # STEP 2: Ask brain ← AI WORKS HERE
    decision = self.brain.decide_action(task, state)
    # Returns: {action, target_angle, confidence, reasoning}
    
    # STEPS 3-6: Execute & feedback
    # Move arm, get feedback, update state, return result
    
    return {
        "status": "success",
        "action": decision['action'],
        "target_angle": decision['target_angle'],
        "confidence": decision['confidence'],
        "reasoning": decision['reasoning']
    }
```

### **File 3: ARM Brain (AI Decision Maker)**
**Location:** `app/services/arm_brain.py`
```python
class ArmBrain:
    def decide_action(self, task, current_state):
        # Create smart prompt
        system_prompt = f"""
        You are Arm {self.arm_id} brain.
        Current angle: {current_state['angle']}°
        Task: {task}
        Decide best angle (0-180°)
        """
        
        # Call Qwen LLM ← AI HERE
        decision = self.qwen_brain.ask_decision(
            system_prompt, 
            current_state
        )
        
        return {
            "action": decision['action'],
            "target_angle": decision['target_angle'],
            "confidence": decision['confidence'],
            "reasoning": decision['reasoning']
        }
```

### **File 4: Qwen Brain (LLM Interface)**
**Location:** `app/services/qwen_brain.py`
```python
class QwenBrain:
    def ask_decision(self, system_prompt, state):
        # Call Qwen LLM via Ollama HTTP API
        response = requests.post(
            "http://ollama:11434/api/generate",
            json={
                "model": "qwen2.5:0.5b",
                "prompt": system_prompt,
                "stream": False
            }
        )
        
        # Parse LLM response
        qwen_response = response.json()
        decision = json.loads(qwen_response['response'])
        
        return decision  # Returns angle, confidence, reasoning
```

---

## 🎬 PART 6: HOW TO SEE IT ALL LIVE (Terminal)

### **Terminal 1: Watch Frontend**
```bash
# Browser
open http://localhost:8080
# Watch the log panel update in real-time
```

### **Terminal 2: Watch Backend Processing**
```bash
docker-compose logs -f backend | grep -E "execute_task|ArmBrain|Qwen|confidence"

# You'll see:
# [backend] execute_task_endpoint: Received task=pick, arm_id=1
# [ArmBrain] Calling Qwen LLM...
# [Qwen] Response: confidence=1.0, angle=90
# [Executor] Decision: move to 90°
```

### **Terminal 3: Test API Directly**
```bash
curl -X POST http://localhost:5000/execute_task \
  -H "Content-Type: application/json" \
  -d '{"task": "pick", "arm_id": "1"}'

# Response:
# {
#   "status": "success",
#   "action": "move",
#   "target_angle": 90,
#   "confidence": 1.0,
#   "reasoning": "Intake station at 90°",
#   "timestamp": "2026-04-24T07:57:03.148132"
# }
```

### **Terminal 4: Watch Ollama (Qwen Model)**
```bash
docker-compose logs -f ollama | grep -E "generate|inference"

# You'll see:
# [ollama] Loading model qwen2.5:0.5b
# [ollama] Inference started...
# [ollama] Generation: 2341 tokens/sec
# [ollama] Response ready
```

---

## 📊 PART 7: COMPLETE TIMING BREAKDOWN

```
⏱️  Timeline of Single AI Decision:

TIME    LOCATION              ACTION
────────────────────────────────────────────────────
0ms     Frontend              User clicks/sends request

100ms   Backend (Flask)       Receive request, create executor

200ms   Backend               Get current state from database

300ms   ArmBrain              Create system prompt

400ms   QwenBrain             Format HTTP request

500ms   Network               Send to Ollama

600ms   Ollama                Load model into memory

700ms   Qwen LLM              Process prompt

1200ms  Qwen LLM              Thinking...

2200ms  Qwen LLM              Generate response

2300ms  Qwen LLM              Serialize JSON

2500ms  Network               Response travels back

2600ms  QwenBrain             Parse JSON response

2700ms  ArmBrain              Return decision

2800ms  Backend               Format response

2900ms  Backend               Send to frontend

3000ms  Frontend              Display ✅ SUCCESS

3100ms  Frontend              Show confidence: 1.0

3200ms  Frontend              Show reasoning

3300ms  Backend               Execute arm movement

4500ms  Arm Simulator         Movement complete

────────────────────────────────────────────────────
TOTAL:  ~4.5 seconds from click to completion
        (2-3s = AI thinking, 1-2s = execution)
```

---

## 🎯 PART 8: SUMMARY - WHAT YOU SEE WHERE

| Location | What You See | What's Happening |
|----------|-------------|-----------------|
| **Frontend Dashboard** | ✅ SUCCESS, Confidence: 1.0 | Result display |
| **Log Panel** | "Intake station at 90°" | AI reasoning |
| **Visual Canvas** | Arm moving to 90° | Physical result |
| **Backend Logs** | `[ArmBrain] Decision: 90°` | AI decision making |
| **Terminal/Curl** | JSON response with angle | API return value |
| **Docker Ollama** | Qwen model processing | AI model thinking |
| **Arm Container** | `Set angle to 90°` | Arm executing |

---

## ✅ KEY TAKEAWAYS

1. **Frontend Dashboard** shows:
   - Real-time status (Connected/Disconnected)
   - Visual simulation of arms moving
   - Log panel with AI decisions + reasoning + confidence

2. **Backend (Flask)** does:
   - Receives request
   - Calls AI Brain
   - Returns JSON with decision

3. **AI Brain** works in:
   - `arm_brain.py` - Individual decision maker
   - `qwen_brain.py` - LLM interface

4. **Qwen LLM** (in Ollama container):
   - Receives smart prompt
   - Thinks for 2-3 seconds
   - Returns decision with confidence

5. **Complete Flow**:
   - Frontend (8080) → Backend (5000) → Brain → Ollama (11434) → Back → Frontend

6. **What Makes It AI**:
   - Takes current state into account
   - Considers task requirements
   - Returns confidence score
   - Explains reasoning

---

**Now you can explain the ENTIRE system to your team and client! 🎉**
