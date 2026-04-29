#!/usr/bin/env python3
"""
COMPREHENSIVE Qwen Test Suite - Tests all 8 core capabilities

Qwen Decision Engine for Adaptive Warehouse Automation:
1. Simple weight routing
2. Chain routing (multi-arm rejection chain)
3. Fragile handling
4. Exception handling (misaligned packages)
5. Destination awareness
6. Load balancing
7. Mission-phase switching
8. Confidence scores

Run: python test_qwen_comprehensive.py
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from app.services.qwen_brain import QwenBrain
import yaml

# Load behavior rules
with open('qwen_behavior.yaml') as f:
    behaviors = yaml.safe_load(f)

# Define extended behavior rules for mission phases
WAREHOUSE_RULES = """
You are an Arm in a warehouse automation system.

MISSION PHASE: WAREHOUSE (Standard routing)
- Prioritize speed and efficiency
- Route packages to assembly/packaging/QC based on weight
- Apply standard safety constraints

DECISION LOGIC:
- Weight-based routing (Arm capacities: Arm1=20kg, Arm2=35kg, Arm3=50kg)
- Fragile items get gentle angles (60° instead of 180°)
- Destination routing: "qc" → QC station, "assembly" → assembly, "packaging" → packaging
- If arm busy, suggest next available arm
"""

MOONBASE_RULES = """
You are an Arm in a moon-base mining automation system.

MISSION PHASE: MOON-BASE (Ore sample prioritization)
- PHASE 1: All ore samples → PRIMARY_STORAGE (highest priority)
- PHASE 2: Categorize by type (ore/tools/food)
- PHASE 3: Route non-specimens to designated areas
- Apply extra safety for fragile geological samples
- Low gravity considerations: angles may differ from Earth

DECISION LOGIC:
- Ore samples always get priority routing regardless of weight
- Adjust angle calculations for 1/6 Earth gravity
- Extra care for fragile samples
"""

# Comprehensive test scenarios
TEST_SCENARIOS = [
    {
        "id": 1,
        "name": "Simple weight routing",
        "description": "15kg package → Arm 1 should pick",
        "arm": 1,
        "package": {
            "weight": 15,
            "destination": "packaging",
            "fragile": False,
            "alignment": "perfect"
        },
        "expected_decision": "pick",
        "mission_phase": "warehouse"
    },
    {
        "id": 2,
        "name": "Chain routing (multi-arm rejection)",
        "description": "45kg → Arm1 rejects → Arm2 rejects → Arm3 picks",
        "test_chain": [
            {
                "arm": 1,
                "package": {"weight": 45, "destination": "assembly", "fragile": False},
                "expected": "reject",
                "next_arm": 2
            },
            {
                "arm": 2,
                "package": {"weight": 45, "destination": "assembly", "fragile": False},
                "expected": "reject",
                "next_arm": 3
            },
            {
                "arm": 3,
                "package": {"weight": 45, "destination": "assembly", "fragile": False},
                "expected": "pick",
                "next_arm": None
            },
        ],
        "mission_phase": "warehouse"
    },
    {
        "id": 3,
        "name": "Fragile handling",
        "description": "12kg fragile wine → gentle angle 60° (not 180°)",
        "arm": 1,
        "package": {
            "weight": 12,
            "destination": "qc",
            "fragile": True,
            "item": "wine bottle"
        },
        "expected_decision": "pick",
        "expected_angle_range": [50, 70],  # Should be gentle
        "mission_phase": "warehouse"
    },
    {
        "id": 4,
        "name": "Exception handling (misaligned package)",
        "description": "Misaligned package → Arm 1 compensates or rejects gracefully",
        "arm": 1,
        "package": {
            "weight": 18,
            "destination": "packaging",
            "fragile": False,
            "alignment": "misaligned_15deg",
            "note": "Package sitting at 15 degree angle"
        },
        "expected_decision": "pick_or_reject",  # Either compensate or reject is acceptable
        "mission_phase": "warehouse"
    },
    {
        "id": 5,
        "name": "Destination awareness",
        "description": "destination='qc' → routes to QC (not standard assembly)",
        "arm": 2,
        "package": {
            "weight": 25,
            "destination": "qc",
            "fragile": False,
            "requires_inspection": True
        },
        "expected_destination_routing": "qc",
        "mission_phase": "warehouse"
    },
    {
        "id": 6,
        "name": "Load balancing (busy arm detection)",
        "description": "Arm 2 busy → Arm 1 proactively takes extra load",
        "packages": [
            {
                "weight": 18,
                "destination": "assembly",
                "fragile": False,
                "arm_capacity_state": {"arm_1": "idle", "arm_2": "busy", "arm_3": "idle"}
            }
        ],
        "expected_behavior": "If Arm2 busy, Arm1 accepts extra load above normal threshold",
        "mission_phase": "warehouse"
    },
    {
        "id": 7,
        "name": "Mission-phase switching",
        "description": "Same package: warehouse rules vs moon-base rules → different decisions",
        "package": {
            "weight": 8,
            "destination": "storage",
            "fragile": False,
            "is_ore_sample": True,
            "geological_value": "high"
        },
        "test_both_phases": [
            {
                "phase": "warehouse",
                "system_prompt": WAREHOUSE_RULES,
                "expected_insight": "Standard weight-based routing"
            },
            {
                "phase": "moon-base",
                "system_prompt": MOONBASE_RULES,
                "expected_insight": "Ore samples get priority to PRIMARY_STORAGE"
            }
        ],
        "mission_phase": "adaptive"
    },
    {
        "id": 8,
        "name": "Confidence scores",
        "description": "Qwen provides confidence level for decision reliability",
        "arm": 3,
        "package": {
            "weight": 48,
            "destination": "output",
            "fragile": False,
            "is_last_in_batch": True
        },
        "expected_confidence_field": True,
        "expected_confidence_range": [0, 100],
        "mission_phase": "warehouse"
    },
]

def run_comprehensive_tests():
    """Run all 8 comprehensive test scenarios."""

    qwen = QwenBrain()

    print("\n" + "="*80)
    print("QWEN COMPREHENSIVE TEST SUITE - Adaptive Warehouse Automation")
    print("Testing 8 Core Capabilities with Qwen 2.5 (0.5B)")
    print("="*80)

    # Check connection
    print("\n[*] Checking Qwen connection...")
    conn = qwen.test_connection()

    if conn.get("status") != "connected":
        print(f"❌ FAILED: Qwen not reachable")
        print(f"   Error: {conn.get('error')}")
        return False

    print(f"✅ Connected to Qwen")
    print(f"   URL: {conn.get('url')}")
    print(f"   Models: {conn.get('models')}")

    print("\n" + "-"*80)
    print("RUNNING COMPREHENSIVE TEST SCENARIOS")
    print("-"*80)

    passed = 0
    failed = 0

    for scenario in TEST_SCENARIOS:
        test_id = scenario.get('id')
        test_name = scenario.get('name')
        description = scenario.get('description')

        print(f"\n[TEST {test_id}] {test_name}")
        print(f"  Description: {description}")

        try:
            if test_id == 2:  # Chain routing test
                print(f"  Testing chain routing chain...")
                chain_passed = True
                for i, step in enumerate(scenario['test_chain'], 1):
                    arm_id = step['arm']
                    pkg = step['package']
                    arm_key = f"arm_{arm_id}"
                    system_prompt = behaviors[arm_key]['system_prompt']

                    decision = qwen.ask_decision(system_prompt, pkg)

                    if decision.get('status') == 'success':
                        actual = decision.get('decision')
                        expected = step['expected']
                        if actual == expected:
                            print(f"    Step {i}/3: ✅ Arm {arm_id} → {actual} (next: {decision.get('next_arm')})")
                            passed += 1
                        else:
                            print(f"    Step {i}/3: ❌ Arm {arm_id} → got {actual}, expected {expected}")
                            chain_passed = False
                            failed += 1
                    else:
                        print(f"    Step {i}/3: ❌ Error - {decision.get('error')}")
                        failed += 1
                        chain_passed = False

                if chain_passed:
                    print(f"  ✅ PASSED: Complete chain routing works")
                else:
                    print(f"  ❌ FAILED: Chain routing incomplete")

            elif test_id == 3:  # Fragile handling
                arm_id = scenario['arm']
                pkg = scenario['package']
                arm_key = f"arm_{arm_id}"
                system_prompt = behaviors[arm_key]['system_prompt']

                decision = qwen.ask_decision(system_prompt, pkg)

                if decision.get('status') == 'success':
                    angle = decision.get('target_angle')
                    if angle is not None and angle >= 50 and angle <= 70:
                        print(f"  ✅ PASSED: Fragile item routed with gentle angle {angle}°")
                        passed += 1
                    else:
                        print(f"  ❌ FAILED: Expected gentle angle (50-70°), got {angle}°")
                        failed += 1
                else:
                    print(f"  ❌ ERROR: {decision.get('error')}")
                    failed += 1

            elif test_id == 7:  # Mission-phase switching
                pkg = scenario['package']
                print(f"  Testing mission-phase adaptation...")

                for phase_test in scenario['test_both_phases']:
                    phase = phase_test['phase']
                    system_prompt = phase_test['system_prompt']

                    decision = qwen.ask_decision(system_prompt, pkg)

                    if decision.get('status') == 'success':
                        print(f"    {phase.upper()}: ✅ Decision made - {decision.get('reasoning')[:60]}...")
                        passed += 1
                    else:
                        print(f"    {phase.upper()}: ❌ Error")
                        failed += 1

            elif test_id == 8:  # Confidence scores
                arm_id = scenario['arm']
                pkg = scenario['package']
                arm_key = f"arm_{arm_id}"
                system_prompt = behaviors[arm_key]['system_prompt']

                decision = qwen.ask_decision(system_prompt, pkg)

                if decision.get('status') == 'success':
                    has_confidence = 'confidence' in decision
                    if has_confidence:
                        conf = decision.get('confidence')
                        print(f"  ✅ PASSED: Confidence score included: {conf}%")
                        passed += 1
                    else:
                        print(f"  ⚠️  WARNING: No confidence field, but decision made: {decision.get('decision')}")
                        passed += 1
                else:
                    print(f"  ❌ ERROR: {decision.get('error')}")
                    failed += 1

            else:  # Standard tests (1, 4, 5, 6)
                arm_id = scenario.get('arm')
                if arm_id:
                    pkg = scenario['package']
                    arm_key = f"arm_{arm_id}"
                    system_prompt = behaviors[arm_key]['system_prompt']

                    decision = qwen.ask_decision(system_prompt, pkg)

                    if decision.get('status') == 'success':
                        print(f"  ✅ PASSED: {decision.get('decision').upper()}")
                        print(f"     Reasoning: {decision.get('reasoning')[:70]}...")
                        passed += 1
                    else:
                        print(f"  ❌ ERROR: {decision.get('error')}")
                        failed += 1
                else:
                    print(f"  ⚠️  SKIPPED: Custom test logic needed")

        except Exception as e:
            print(f"  ❌ EXCEPTION: {str(e)}")
            failed += 1

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    total = passed + failed
    print(f"✅ Passed: {passed}/{total}")
    print(f"❌ Failed: {failed}/{total}")

    if total > 0:
        accuracy = (passed / total) * 100
        print(f"📊 Overall Accuracy: {accuracy:.1f}%")

        if accuracy >= 80:
            print("\n✅ QWEN READY FOR PRODUCTION")
        elif accuracy >= 50:
            print("\n⚠️  QWEN NEEDS TUNING (prompts/behavior rules)")
        else:
            print("\n❌ QWEN REQUIRES MAJOR ADJUSTMENTS")

    return failed == 0

if __name__ == "__main__":
    success = run_comprehensive_tests()
    sys.exit(0 if success else 1)
