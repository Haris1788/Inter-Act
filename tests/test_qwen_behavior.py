#!/usr/bin/env python3
"""
Test Qwen behavior with real packages and scenarios.
Run this to see how Qwen makes decisions.

Usage:
    python test_qwen_behavior.py

Prerequisites:
    docker compose up --build  (Qwen running)
"""

import sys
import os
import json

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.services.qwen_brain import QwenBrain
import yaml

# Load behavior rules
with open('qwen_behavior.yaml') as f:
    behaviors = yaml.safe_load(f)

# Test scenarios
TEST_SCENARIOS = [
    {
        "name": "Light package - Arm 1 handles",
        "arm": 1,
        "package": {"weight": 15, "destination": "packaging", "fragile": False}
    },
    {
        "name": "Medium package - Arm 1 rejects, Arm 2 picks",
        "arm": 1,
        "package": {"weight": 28, "destination": "assembly", "fragile": False}
    },
    {
        "name": "Fragile light - Arm 1 gentle angle",
        "arm": 1,
        "package": {"weight": 12, "destination": "qc", "fragile": True}
    },
    {
        "name": "Heavy from Arm 1 - Arm 2 rejects, Arm 3 picks",
        "arm": 2,
        "package": {"weight": 42, "destination": "assembly", "fragile": False}
    },
    {
        "name": "System overweight - Arm 3 rejects",
        "arm": 3,
        "package": {"weight": 65, "destination": "packaging", "fragile": False}
    },
]

def run_tests():
    """Test Qwen with all scenarios."""

    qwen = QwenBrain()

    print("\n" + "="*70)
    print("QWEN BEHAVIOR TEST SUITE")
    print("="*70)

    # Check connection first
    print("\n[*] Checking Qwen connection...")
    conn = qwen.test_connection()

    if conn.get("status") != "connected":
        print(f"❌ FAILED: Qwen not reachable")
        print(f"   Error: {conn.get('error')}")
        print(f"   Make sure: docker compose up --build")
        return False

    print(f"✅ Connected to Qwen")
    print(f"   URL: {conn.get('url')}")
    print(f"   Models: {conn.get('models')}")

    # Run scenarios
    print("\n" + "-"*70)
    print("RUNNING TEST SCENARIOS")
    print("-"*70)

    passed = 0
    failed = 0

    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n[{i}/{len(TEST_SCENARIOS)}] {scenario['name']}")
        print(f"     Arm: {scenario['arm']}")
        print(f"     Package: {json.dumps(scenario['package'])}")

        arm_id = scenario['arm']
        arm_key = f"arm_{arm_id}"

        if arm_key not in behaviors:
            print(f"❌ FAILED: No behavior defined for {arm_key}")
            failed += 1
            continue

        system_prompt = behaviors[arm_key]['system_prompt']

        # Ask Qwen
        decision = qwen.ask_decision(system_prompt, scenario['package'])

        # Show result
        if decision.get('error'):
            print(f"❌ ERROR: {decision.get('error')}")
            failed += 1
        elif decision.get('status') != 'success':
            print(f"❌ FAILED: {decision.get('status')}")
            failed += 1
        else:
            print(f"✅ Decision: {json.dumps(decision, indent=6)}")
            passed += 1

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"✅ Passed: {passed}/{len(TEST_SCENARIOS)}")
    print(f"❌ Failed: {failed}/{len(TEST_SCENARIOS)}")
    accuracy = (passed / len(TEST_SCENARIOS)) * 100
    print(f"📊 Accuracy: {accuracy:.1f}%")

    return failed == 0

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
