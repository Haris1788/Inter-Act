"""Scene configuration - delegates to pipeline_config for dynamic arm layout.

Angle constants stay here (they're physics, not layout).
Everything else comes from pipeline.yaml via pipeline_config.
"""

from app.models.pipeline_config import (
    generate_scene_config,
    MIN_ANGLE,
    MAX_ANGLE,
    DEFAULT_ANGLE,
    get_config,
)


def get_full_config():
    """Return the scene config dict for the browser.

    Includes:
    - Scene geometry (arms, assets, zones, canvas size)
    - Pipeline settings (products, speed, mode)
    - Angle constraints
    """
    scene = generate_scene_config()
    cfg = get_config()
    pipeline_cfg = cfg.get("pipeline", {})

    return {
        **scene,
        # Add pipeline configuration for frontend to use
        "products": pipeline_cfg.get("products", 5),
        "speed": pipeline_cfg.get("speed", "normal"),
        "mode": pipeline_cfg.get("mode", "sequential"),
    }
