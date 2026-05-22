"""Page routes - serves simulation page and health endpoints."""

import os

import requests
from flask import Blueprint, render_template, jsonify, make_response

page_bp = Blueprint("pages", __name__)


@page_bp.route("/")
def index():
    """Serve the main simulation page with no-cache headers."""
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@page_bp.route("/health")
def health():
    """Lightweight health check endpoint for container orchestration."""
    return jsonify({"status": "ok"}), 200


@page_bp.route("/health/ollama")
def ollama_health():
    """Check backend connectivity to the configured Ollama instance."""
    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL")

    try:
        resp = requests.get(f"{ollama_base_url}/api/tags", timeout=3)
        resp.raise_for_status()
        payload = resp.json()
        models = payload.get("models", [])
        model_names = [
            m.get("name") for m in models if isinstance(m, dict) and m.get("name")
        ]
        configured_model_available = (
            ollama_model in model_names if ollama_model else None
        )
        return (
            jsonify(
                {
                    "status": "ok",
                    "ollama_reachable": True,
                    "ollama_base_url": ollama_base_url,
                    "configured_model": ollama_model,
                    "configured_model_available": configured_model_available,
                    "model_count": len(models),
                }
            ),
            200,
        )
    except (requests.RequestException, ValueError) as exc:
        return (
            jsonify(
                {
                    "status": "error",
                    "ollama_reachable": False,
                    "ollama_base_url": ollama_base_url,
                    "configured_model": ollama_model,
                    "error": str(exc),
                }
            ),
            503,
        )
