"""Scene configuration — delegates to pipeline_config for dynamic arm layout.

Angle constants stay here (they're physics, not layout).
Everything else comes from pipeline.yaml via pipeline_config.
"""

from app.models.pipeline_config import generate_scene_config, MIN_ANGLE, MAX_ANGLE, DEFAULT_ANGLE


def get_full_config():
    """Return the scene config dict for the browser. Arm count is dynamic."""
    return generate_scene_config()
