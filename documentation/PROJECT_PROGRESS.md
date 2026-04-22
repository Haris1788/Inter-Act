# Inter-Act Project - Technical & Progress Documentation

---

## PROJECT OVERVIEW

**Project:** Inter-Act (Robotic Warehouse Automation with Qwen AI)  
**Duration:** March 20 - May 8, 2026  
**Team:** 6 developers in 3 Squads  
**Current Date:** April 16, 2026

---

## SPRINT 1 COMPLETED (March 27 - April 10, 2026)

### User Story #1: Docker Research
- **Owner:** Haris
- **Completed:** March 28, 2026
- **Deliverable:** Docker + docker-compose architecture for 3 isolated containers (UI, Flask API, Qwen AI)
- **Status:** ✅ DONE

### User Story #2: Flask vs Next.js Research
- **Owner:** Haris
- **Completed:** March 28, 2026
- **Decision:** Decoupled architecture
  - Backend: Flask (Python-based API)
  - Frontend: Lightweight UI (HTML/Streamlit)
  - Benefit: Can upgrade frontend to Next.js later without changing Python backend
- **Status:** ✅ DONE

### User Story #3: Task Conceptualization
- **Owner:** Haris
- **Completed:** March 30, 2026
- **Deliverable:** Squad structure and parallel development plan
  - Squad A: DevOps & API
  - Squad B: Digital Twin & UI
  - Squad C: Agentic AI & OOP
- **Status:** ✅ DONE

### User Story #4: GitHub Setup
- **Owner:** Sneh
- **Completed:** April 5, 2026
- **Deliverable:** GitHub repository live with team access
- **Status:** ✅ DONE

### User Story #5: AI Research & Implementation
- **Completed:** April 9, 2026
- **Deliverable:** Initial AI research completed
- **Status:** ✅ DONE

### User Story #6: Qwen Behaviour
- **Owner:** Haris
- **Started:** April 3, 2026
- **Completed:** April 16, 2026
- **Commit:** 6206281
- **Deliverables:**
  - `app/services/qwen_brain.py` (116 lines) - QwenBrain service
  - `qwen_behavior.yaml` (103 lines) - Behavior rules
  - `test_qwen_behavior.py` (129 lines) - Basic tests
  - `test_qwen_comprehensive.py` (317 lines) - Comprehensive tests (8 scenarios)
  - `Qwen_Behaviour.md` - Implementation documentation
- **Test Results:** 88.9% accuracy (8/9 tests passing)
- **Status:** ✅ PRODUCTION READY

**Sprint 1 Summary:** All planned user stories completed. Infrastructure and static twin ready.

---

## SPRINT 2 IN PROGRESS (April 10 - April 24, 2026)

### April 10 - April 16 (Before check-in)
- Qwen Behaviour completed (April 16)
- Waiting for new user stories from Product Owner Alex

### April 17 Check-in Meeting
- Mid-sprint check-in with Product Owner Alex
- New user stories revealed

### User Story #7: Qwen as Brains for Each Arm (Haris)
- **Owner:** Haris
- **Started:** April 17, 2026
- **Status:** 🔄 IN PROGRESS
- **Branch:** dev-haris
- **Description:** 
  - One Qwen instance (brain) per arm (Arm 1, Arm 2, Arm 3)
  - Central unit (demo) sends task: "Arm 1, pick object"
  - Arm 1 asks its Qwen brain: "What should I do?"
  - Qwen processes and outputs decision: `{"action": "pick", "angle": 45}`
  - Arm 1 executes the action
  - Arm 1 sends feedback: "✅ Task complete" or "❌ Failed - motor error"
  - Brain updates state and sends feedback to central unit
  - Frontend shows notification: ✅ SUCCESS or ❌ FAILED

**Implementation Plan:**
- [ ] **app/services/arm_brain.py** (NEW) - One Qwen brain per arm
- [ ] **app/services/arm_execution.py** (NEW) - Execute task + collect feedback
- [ ] **app/services/notifications.py** (NEW) - Success/failure alerts
- [ ] **app/routes/arm_routes.py** (MODIFY) - Add `/execute_task` endpoint
- [ ] **scripts/run_demo.py** (MODIFY) - Use new execution flow with Qwen brains
- [ ] **tests/test_arm_execution.py** (NEW) - Unit tests for execution service
- [ ] **Documentation response** - How to test, run, and what results show

**Testing:**
- [ ] Local test with `docker-compose up --build`
- [ ] Show team + Alex: "Here's Qwen as brains for each arm"
- [ ] Get approval
- [ ] Merge to dev branch for integration testing

---

## TECHNICAL DOCUMENTATION

### Architecture Overview

```
┌─────────────────────────────────────────────────┐
│           Docker Compose (7 Services)           │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌────────────────┐      ┌──────────────────┐  │
│  │      UI        │      │   Flask Backend  │  │
│  │  (Frontend)    │◄────►│    (API Server)  │  │
│  └────────────────┘      └──────────────────┘  │
│                                │                │
│                                │                │
│                          ┌─────▼──────────┐    │
│                          │  Qwen LLM      │    │
│                          │  (0.5B Model)  │    │
│                          └────────────────┘    │
│                                                 │
│  ┌────────────────────────────────────────┐   │
│  │   Robot Arm OOP Classes (Python)       │   │
│  │  - Arm1, Arm2, Arm3                    │   │
│  │  - Position, Rotation, Gripper control │   │
│  └────────────────────────────────────────┘   │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Backend Stack

**Flask API**
- Handles requests from UI
- Manages robot arm state
- Routes:
  - `GET /get-state` - Returns current arm positions
  - `POST /send-action` - Receives and executes actions

**Qwen Brain Service** (`app/services/qwen_brain.py`)
- Analyzes robot state
- Makes decisions via LangChain
- Outputs JSON commands: `{"action": "pick", "arm": 1, "angle": 180}`
- Rule-based behavior defined in `qwen_behavior.yaml`

**Robot Arm Classes** (To be developed in Squad C)
- OOP representation of each robot arm
- Methods: pick(), rotate(), drop()
- State management: position, angle, gripper status

### Frontend Stack

**UI Dashboard** (Lightweight - HTML/Streamlit)
- Displays robot arm positions
- Shows real-time state from Flask API
- Animate arm movements
- User controls: Start, Stop buttons

### AI/LLM Integration

**Model:** Qwen 2.5 (0.5B parameters)
- Lightweight, fast inference
- Runs locally in Docker container
- Connected via LangChain

**Behavior Rules** (`qwen_behavior.yaml`)
- Decision logic for warehouse tasks
- Defines what actions to take based on state
- 8 test scenarios validated

**Test Coverage:**
- 88.9% accuracy (8/9 tests passing)
- Tests warehouse automation scenarios
- Ready for LangChain integration

---

## PROJECT FLOW

### User Interaction Flow

```
1. User clicks "Start" on UI
   ↓
2. UI sends POST /send-action to Flask API
   ↓
3. Flask receives action request
   ↓
4. Flask queries Qwen via LangChain:
   "Current state: Arm1 at 0°, empty. What should I do?"
   ↓
5. Qwen analyzes and outputs:
   {"action": "move_to_station", "arm": 1, "angle": 45}
   ↓
6. Flask executes command:
   Robot_Arm.rotate(45)
   ↓
7. Robot arm state updated in database
   ↓
8. UI fetches GET /get-state
   ↓
9. UI displays new arm position
   ↓
10. Loop continues until task complete
```

### Development Flow

**Squad A (DevOps & API)** → Creates Flask API endpoints
   ↓
**Squad B (UI)** → Consumes API, displays state
   ↓
**Squad C (AI/OOP)** → Integrates Qwen, creates Robot classes
   ↓
**All Squads** → Single agent integration testing
   ↓
**Review Party 1** → Demo to professors (May 8)

---

## CODEBASE STRUCTURE

```
Inter-Act/
├── docker-compose.yml              (7 containerized services)
├── Dockerfile                       (Container image definition)
│
├── app/
│   ├── services/
│   │   └── qwen_brain.py           (LLM decision engine - 116 lines)
│   ├── robot/
│   │   ├── arm.py                  (OOP Robot Arm classes - TBD)
│   │   └── gripper.py              (Gripper control - TBD)
│   └── api/
│       ├── routes.py               (Flask endpoints - TBD)
│       └── models.py               (Data models - TBD)
│
├── frontend/                        (UI Dashboard - TBD)
│   ├── index.html
│   ├── styles.css
│   └── main.js
│
├── config/
│   └── qwen_behavior.yaml          (AI decision rules - 103 lines)
│
├── tests/
│   ├── test_qwen_behavior.py       (Basic tests - 129 lines)
│   └── test_qwen_comprehensive.py  (8 scenarios - 317 lines)
│
└── documentation/
    ├── README.md                   (Points to this file)
    └── PROJECT_PROGRESS.md         (This file)
```

---

## KEY DELIVERABLES (April 16)

**Completed & Delivered:**
- ✅ Docker infrastructure designed
- ✅ Tech stack decided (Flask + Lightweight Frontend)
- ✅ Squad structure defined
- ✅ GitHub repository live
- ✅ Qwen 2.5 service implemented (88.9% test accuracy)
- ✅ Behavior rules defined and tested

**Not Yet Started:**
- ⏳ Flask API endpoints
- ⏳ UI dashboard
- ⏳ Robot Arm OOP classes
- ⏳ LangChain integration
- ⏳ End-to-end integration testing
- ⏳ Final demo preparation

---

## METRICS

**Code (As of April 16):**
- Files created: 5
- Lines of code: 675 (Qwen service + tests)
- Test scenarios: 8
- Test accuracy: 88.9%

**Timeline:**
- Sprint 1 (March 27 - April 10): ✅ COMPLETE
- Sprint 2 (April 10 - April 24): 🔄 IN PROGRESS
- Sprint 3 (April 24 - May 8): ⏳ PENDING

---

**Last Updated:** April 16, 2026
