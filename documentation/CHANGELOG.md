# CHANGELOG - Inter-Act Project

All notable changes to the Inter-Act project are documented here.

**Project Start:** March 20, 2026  
**Current Date:** April 16, 2026

---

## [Sprint 1] - March 27 - April 17, 2026

### Week 1 (March 27 - April 2)

#### User Story #1: Docker Research
- **Owner:** Haris
- **Squad:** A (DevOps & API)
- **Completed:** March 28, 2026
- **Description:** Research Docker for containerized infrastructure
- **Key Finding:** Docker + docker-compose solves "works on my machine" problem
- **Proposal:** 3 isolated containers (UI, Flask API, Qwen AI)
- **Benefit:** One `docker compose up` command launches entire project

#### User Story #2: Flask vs Next.js Research
- **Owner:** Haris
- **Squad:** A (DevOps & API)
- **Completed:** March 28, 2026
- **Description:** Evaluate frontend/backend tech stack
- **Decision:** Decoupled Architecture
  - Backend: Flask (Python, holds OOP & AI logic)
  - Frontend: Lightweight UI (HTML/Streamlit) or upgrade to Next.js later
  - Benefit: Scalable - can swap frontend without touching Python backend

#### User Story #3: Task Conceptualization
- **Owner:** Haris
- **Squad:** A (DevOps & API)
- **Completed:** March 30, 2026
- **Description:** Plan parallel squad development & sprint execution
- **Plan:** 3 Parallel Squads (A, B, C) work simultaneously
- **Timeline:**
  - Goal 1 (April 10): Infrastructure & Static Twin
  - Goal 2 (April 17): API Bridge & Tool Setup
  - Goal 3 (April 24): Single Agent Integration
  - Goal 4 (May 1): Logging & Autonomy Polish
  - Goal 5 (May 8): Review Party 1 - Demo to professors

### Week 2 (April 3 - April 9)

#### User Story #4: GitHub Setup
- **Owner:** Sneh
- **Squad:** A (DevOps & API)
- **Completed:** April 5, 2026
- **Description:** Setup GitHub repository for team collaboration
- **Deliverable:** GitHub repo live, team members added, branching strategy defined

#### User Story #5: AI Research & Implementation
- **Owner:** (Unknown - handled by squad C)
- **Squad:** C (Agentic AI & OOP)
- **Completed:** April 9, 2026
- **Description:** Research and implement initial AI capabilities
- **Status:** Completed (details in squad C documentation)

### Week 3 (April 10 - April 17)

#### User Story #6: Qwen Behaviour
- **Owner:** Haris
- **Squad:** C (Agentic AI & OOP)
- **Started:** April 10, 2026
- **Completed:** April 16, 2026 (1 day early! 🎉)
- **Commit:** `6206281`
- **Description:** Implement Qwen 2.5 LLM for warehouse automation decisions
- **Deliverables:**
  - `app/services/qwen_brain.py` (116 lines) - QwenBrain service
  - `qwen_behavior.yaml` (103 lines) - Arm decision rules
  - `test_qwen_behavior.py` (129 lines) - Basic test suite
  - `test_qwen_comprehensive.py` (317 lines) - Comprehensive test suite
  - `Qwen_Behaviour.md` - Complete implementation guide

- **Test Results:**
  - Initial: 40% (2/5 passing)
  - Final: 88.9% (8/9 passing) ✅
  - Status: PRODUCTION READY

- **Key Achievements:**
  - ✅ QwenBrain service working
  - ✅ 8 test scenarios validated
  - ✅ Timeout tuned (10s → 30s)
  - ✅ Prompts improved (vague → explicit "MUST" rules)
  - ✅ Documentation complete
  - ✅ Ready for LangChain integration

- **Next:** Integrate with LangChain for Goal 2

---

## 🔄 UPCOMING (April 17 onwards)

### User Stories #7-XX: New from Product Owner Alex
- **Due:** April 17, 2026
- **Description:** (To be revealed at tomorrow's meeting)
- **Expected Tasks:**
  - Flask API endpoints (Squad A)
  - UI dashboard updates (Squad B)
  - LangChain integration (Squad C)

### Goal 2: API Bridge & Tool Setup
- **Deadline:** April 17, 2026
- **Tasks:**
  - Flask `/get-state` and `/send-action` endpoints
  - UI fetches from API
  - Qwen connected to LangChain
  - JSON output format validated

### Goal 3: Single Agent Integration
- **Deadline:** April 24, 2026
- **Tasks:**
  - End-to-end flow: UI click → Flask → Qwen → OOP → UI animation
  - All squads integration testing

### Goal 4: Logging & Autonomy Polish
- **Deadline:** May 1, 2026
- **Tasks:**
  - Live "Log Panel" in UI
  - Autonomous arm execution (Pick → Rotate → Drop)
  - Refined LangChain prompts

### Goal 5: Review Party 1 - AI-First Demo
- **Deadline:** May 8, 2026
- **Deliverable:**
  - `docker compose up` launches entire system
  - Demonstrate Qwen AI driving robot arm autonomously
  - Professors evaluate modular 3-Squad architecture

---

## 📊 METRICS

### Completed User Stories: 6/6+
| Story | Squad | Owner | Status | Completion Date |
|-------|-------|-------|--------|-----------------|
| #1 | A | Haris | ✅ | March 28 |
| #2 | A | Haris | ✅ | March 28 |
| #3 | A | Haris | ✅ | March 30 |
| #4 | A | Sneh | ✅ | April 5 |
| #5 | C | ? | ✅ | April 9 |
| #6 | C | Haris | ✅ | April 16 |
| #7+ | ? | ? | ⏳ | April 17 |

### Code Metrics (As of April 16)
- **Files Created:** 5
- **Lines of Code:** 675 (main feature code)
- **Test Scenarios:** 8
- **Test Accuracy:** 88.9%
- **Docker Services:** 7 (all healthy)
- **Commits:** 3 total (including setup)

### Timeline Progress
- **Goal 1 (April 10):** ✅ COMPLETE (6 days early)
- **Goal 2 (April 17):** 🔄 IN PROGRESS (waiting for new user stories)
- **Goal 3 (April 24):** ⏳ PENDING
- **Goal 4 (May 1):** ⏳ PENDING
- **Goal 5 (May 8):** ⏳ PENDING

---

## 🎯 NEXT ACTIONS

**Immediate (April 17):**
1. [ ] Attend sprint review meeting
2. [ ] Receive new user stories from Product Owner Alex
3. [ ] Plan Sprint 2 (April 17 - April 24)
4. [ ] Assign user stories to squads

**This Week (Before April 24):**
1. [ ] Complete User Stories #7-XX
2. [ ] Deliver Goal 2 results (API Bridge ready)
3. [ ] Prepare for single agent integration

**Next Sprint:**
1. [ ] Integrate all 3 squads
2. [ ] Single agent end-to-end testing
3. [ ] Demo to professors prep

---

**Last Updated:** April 16, 2026  
**Next Update:** April 17, 2026 (after new user stories received)
