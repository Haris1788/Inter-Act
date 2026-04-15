# Inter-Act Project Documentation - Master Timeline

**Project Start:** March 20, 2026 (Kickoff)  
**Current Date:** April 16, 2026  
**Final Review:** May 8, 2026 (Review Party 1)  
**Team:** 6 developers in 3 Squads (A: DevOps/API, B: UI/Twin, C: AI/OOP)

---

## 🎯 PROJECT ROADMAP (March 20 - May 8)

### **Goal 1: Infrastructure & Static Twin** (April 10) ✅
- [x] User Story #1: Docker Research (Haris)
- [x] User Story #2: Flask vs Next.js (Haris)
- [x] User Story #3: Task Conceptualization (Haris)
- [x] User Story #4: GitHub Setup (Sneh)
- [x] User Story #5: AI Research & Implementation
- **Status:** COMPLETE

### **Goal 2: API Bridge & Tool Setup** (April 17) 🔄
- [x] User Story #6: Qwen Behaviour (Haris) - Completed April 16 ✅
- [ ] User Story #7-XX: New stories from Alex (Due: April 17)
- [ ] Squad A: Flask API endpoints ready
- [ ] Squad B: UI fetching from API
- [ ] Squad C: Qwen connected to LangChain
- **Status:** IN PROGRESS (waiting for new stories)

### **Goal 3: Single Agent Integration** (April 24)
- [ ] All Squads: Integration testing
- [ ] Click "Start" → Flask → Qwen → OOP → UI animation
- **Status:** PENDING

### **Goal 4: Logging & Autonomy Polish** (May 1)
- [ ] Squad A & B: Live "Log Panel" in UI
- [ ] Squad C: Refine LangChain prompts for autonomous execution
- **Status:** PENDING

### **Goal 5: Review Party 1 - AI-First Demo** (May 8)
- [ ] `docker compose up` launches perfectly
- [ ] Demonstrate Agentic AI driving robot arm
- [ ] Professors evaluate modular architecture
- **Status:** PENDING

---

## 📊 USER STORIES COMPLETED

| # | Title | Squad | Owner | Status | Commit |
|---|-------|-------|-------|--------|--------|
| 1 | Docker Research | A | Haris | ✅ Done | - |
| 2 | Flask vs Next.js | A | Haris | ✅ Done | - |
| 3 | Task Conceptualization | A | Haris | ✅ Done | - |
| 4 | GitHub Setup | A+B+C | Sneh | ✅ Done | - |
| 5 | AI Research & Implementation | C | ? | ✅ Done | - |
| 6 | Qwen Behaviour | C | Haris | ✅ Done | 6206281 |
| 7-XX | *New stories from Alex* | ? | ? | ⏳ Pending | - |

---

## 📅 SPRINT STRUCTURE

### Sprint 1: March 27 - April 17 (Goal 1 & Goal 2)
- [Sprint 1 Plan](sprints/sprint-1/plan.md)
- [Sprint 1 Progress](sprints/sprint-1/progress.md)
- [Sprint 1 Completed](sprints/sprint-1/completed.md)

### Sprint 2: April 17 - April 24 (Goal 3)
- Sprint 2 Plan (To be created)
- Sprint 2 Progress (To be created)

### Sprint 3: April 24 - May 8 (Goal 4 & 5)
- Sprint 3 Plan (To be created)
- Sprint 3 Progress (To be created)

---

## 🚀 DELIVERABLES BY GOAL

### ✅ Goal 1: Infrastructure & Static Twin (April 10)

**Delivered:**
- GitHub repo live
- Docker research & recommendation
- Flask vs Next.js decision made
- Task conceptualization complete
- Team structure: 3 Squads (A, B, C)
- docker-compose planned

---

### 🔄 Goal 2: API Bridge & Tool Setup (April 17) - DUE TOMORROW

**Delivered So Far:**
- ✅ User Story #6: Qwen Behaviour (Haris)
  - QwenBrain service
  - Behavior rules
  - 8 comprehensive tests (88.9% accuracy)
  - Complete documentation
  - Ready for LangChain integration

**Pending:**
- [ ] User Story #7-XX (New from Product Owner Alex)
- [ ] Squad A: Flask API endpoints (`/get-state`, `/send-action`)
- [ ] Squad B: UI dashboard fetching from API
- [ ] Squad C: Connect Qwen to LangChain, test JSON output

**Deadline:** April 17, 2026 (TOMORROW)

---

### ⏳ Goal 3: Single Agent Integration (April 24)

**Plan:**
- User clicks "Start" on UI
- Flask API receives request
- Qwen analyzes arm state via LangChain
- Qwen outputs JSON: `{"action": "pick", "arm": 1, "angle": 180}`
- OOP Robot Arm class executes
- UI animates arm movement
- Complete loop works end-to-end

---

### ⏳ Goal 4: Logging & Autonomy Polish (May 1)

**Plan:**
- Live "Log Panel" in UI
- Show Qwen's internal reasoning in real-time
- Arm 1 autonomous: Pick → Rotate → Drop (no human intervention)
- Professors can read AI decision-making

---

### ⏳ Goal 5: Review Party 1 (May 8)

**Plan:**
- Run `docker compose up`
- System launches perfectly
- Demonstrate Qwen AI driving robot arm autonomously
- Show 3-Squad modular architecture works
- Professors evaluate

---

## 📝 HOW TO UPDATE DOCUMENTATION

### When You Complete a User Story:

1. **Update Sprint Progress:**
   ```bash
   # Edit: documentation/sprints/sprint-1/progress.md
   # Add: What you completed this week
   ```

2. **Commit to Git:**
   ```bash
   git add .
   git commit -m "User Story #XX [Feature Name]"
   git push origin dev-haris
   ```

3. **Update This Master Index:**
   ```bash
   # Edit: documentation/README.md
   # Update: User Stories table, add commit hash
   ```

4. **If Making a Major Decision:**
   ```bash
   # Create: documentation/decisions/adr-XXX-[decision].md
   # Explain: Why you chose this approach
   ```

---

## 📍 KEY DATES

| Date | Event | What Happens |
|------|-------|--------------|
| March 20, 2026 | Kickoff | Team gathers, project explained |
| March 27, 2026 | Sprint 1 Starts | 3 Squads begin work |
| April 10, 2026 | Goal 1 Deadline | Infrastructure & static twin done |
| **April 16, 2026** | **TODAY** | Qwen complete, waiting for new stories |
| **April 17, 2026** | **TOMORROW** | Goal 2 Deadline + New user stories from Alex |
| April 24, 2026 | Goal 3 Deadline | Single agent integration working |
| May 1, 2026 | Goal 4 Deadline | Logging & autonomy polish |
| May 8, 2026 | Review Party 1 | Demo to professors |

---

## 🎭 THE 3 SQUADS

### Squad A: DevOps & API
**Owner:** Haris  
**Responsibility:** Docker, GitHub, Flask routes  
**Tasks This Sprint:**
- Flask API endpoints ready
- /get-state endpoint working
- /send-action endpoint working

**Completed:**
- ✅ Docker research
- ✅ Flask vs Next.js decision
- ✅ Task conceptualization

### Squad B: Digital Twin & UI
**Owner:** (Who?)  
**Responsibility:** Dashboard, visual robot, animations  
**Tasks This Sprint:**
- UI fetches from Flask API
- Display arm positions real-time
- Animate robot movement

**Completed:**
- ✅ GitHub setup (partial)

### Squad C: Agentic AI & OOP
**Owner:** (Who?) + Haris (Qwen part)  
**Responsibility:** Python OOP, LangChain, AI prompts  
**Tasks This Sprint:**
- Connect Qwen to LangChain
- Test JSON output format
- Robot Arm OOP classes ready

**Completed:**
- ✅ AI Research & Implementation
- ✅ Qwen Behaviour (Haris)

---

## ✅ STATUS SUMMARY

**As of April 16, 2026:**
- Goal 1 (April 10): ✅ COMPLETE
- Goal 2 (April 17): 🔄 IN PROGRESS
  - Qwen done ✅
  - Waiting for new user stories from Alex
  - 3 squads ready to execute

**Next Meeting:** April 17, 2026 (Tomorrow)
- Product Owner Alex presents new user stories
- Sprint 1 review meeting
- Sprint 2 planning

---

**All documentation stays in git. Track every week. Compile for final project docs.**
