"""
QwenBrain — AI decision-making service for robotic arms.

Calls Ollama/Qwen to decide arm actions based on package state and behavior rules.
"""

import os
import json
import requests
from typing import Dict, Any, Optional

class QwenBrain:
    """Interface to Qwen 2.5 (0.5b) LLM via Ollama for arm decision-making."""

    def __init__(self):
        self.ollama_url = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.model = os.environ.get('OLLAMA_MODEL', 'qwen2.5:0.5b')
        self.timeout = 30  # Increased for CPU inference (Qwen 0.5b is slow)
        self.temperature = 0.1  # Deterministic decisions

    def ask_decision(
        self,
        system_prompt: str,
        package_state: Dict[str, Any],
        user_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ask Qwen to decide: what should this arm do with this package?

        Args:
            system_prompt: Behavior rules (knowledge, constraints, format instructions)
            package_state: Current package info {weight, destination, fragile, ...}
            user_message: Optional pre-built user message. When provided, it is sent
                verbatim and `package_state` is treated as already embedded in it.
                When omitted, falls back to the legacy "Current package state: ..."
                template so existing US #6 callers keep working.

        Returns:
            Decision dict: {decision, target_angle, next_arm, reasoning, confidence}
        """

        if user_message is None:
            user_message = f"""Current package state:
{json.dumps(package_state, indent=2)}

Analyze and reply with ONLY valid JSON. No explanation, no markdown."""

        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\n{user_message}",
            "stream": False,
            "format": "json",
            "temperature": self.temperature
        }

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code != 200:
                return {
                    "error": f"Ollama error {response.status_code}",
                    "decision": "error",
                    "status": "failed"
                }

            result = response.json()
            response_text = result.get("response", "").strip()

            # Parse JSON
            try:
                decision = json.loads(response_text)
                decision["status"] = "success"
                return decision
            except json.JSONDecodeError:
                return {
                    "error": f"Invalid JSON: {response_text[:100]}",
                    "decision": "error",
                    "status": "failed"
                }

        except requests.Timeout:
            return {
                "error": f"Timeout ({self.timeout}s)",
                "decision": "error",
                "status": "timeout"
            }
        except Exception as e:
            return {
                "error": str(e),
                "decision": "error",
                "status": "exception"
            }

    def test_connection(self) -> Dict[str, Any]:
        """Health check: is Ollama reachable?"""
        try:
            response = requests.get(
                f"{self.ollama_url}/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                return {
                    "status": "connected",
                    "models": models,
                    "url": self.ollama_url
                }
            return {"status": "error", "code": response.status_code}
        except Exception as e:
            return {"status": "disconnected", "error": str(e)}


# Singleton instance
_qwen_brain = None

def get_qwen_brain() -> QwenBrain:
    """Get or create QwenBrain singleton."""
    global _qwen_brain
    if _qwen_brain is None:
        _qwen_brain = QwenBrain()
    return _qwen_brain
