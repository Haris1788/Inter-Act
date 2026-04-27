"""Page routes — serves the main simulation HTML page and health endpoint."""

from flask import Blueprint, render_template, jsonify, make_response

page_bp = Blueprint("pages", __name__)


@page_bp.route("/")
def index():
    """Serve the main simulation page with no-cache headers."""
    response = make_response(render_template("index.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@page_bp.route("/health")
def health():
    """Lightweight health check endpoint for container orchestration."""
    return jsonify({"status": "ok"}), 200
