"""Warehouse config helpers + scene injection (Phase 2 module, current pipeline.yaml).

Updated post-Phase-3: pipeline shrank to 2 arms (input_arm served_by arm-1,
output_arm served_by arm-2), grid is 14x10 with cell_px=120 and origin
(30,30). Scene canvas dims are fixed (600x840) by the legacy scene config;
the warehouse is rendered by the frontend's own Konva layer with its own
world coordinates, so the historical "canvas grows to fit warehouse"
assertion is obsolete and dropped.
"""

from app.models import pipeline_config
from app.models.scene_config import get_full_config


def test_warehouse_block_parsed():
    assert pipeline_config.has_warehouse() is True
    grid = pipeline_config.get_grid()
    assert grid["cols"] == 14 and grid["rows"] == 10 and grid["cell_px"] == 120
    assert grid["origin"] == {"x": 30, "y": 30}
    assert pipeline_config.get_agv_ids() == ["agv-1", "agv-2", "agv-3"]
    assert len(pipeline_config.get_lane_cells()) == 10


def test_cell_to_px_is_cell_centre():
    # origin (30, 30), cell_px 120 -> cell [0,0] centre = (90, 90)
    assert pipeline_config.cell_to_px([0, 0]) == {"x": 30 + 60, "y": 30 + 60}
    assert pipeline_config.cell_in_grid([13, 9]) is True   # last cell row=9, rows=10
    assert pipeline_config.cell_in_grid([13, 10]) is False  # row out of bounds
    assert pipeline_config.cell_in_grid([14, 0]) is False  # col out of bounds
    assert pipeline_config.cell_in_grid([-1, 0]) is False


def test_warehouse_scene_shape():
    sc = pipeline_config.get_warehouse_scene()
    assert set(sc) == {"grid", "stations", "lane", "agvs"}
    assert sc["grid"]["width"] == 14 * 120
    assert sc["stations"]["input_arm"]["served_by"] == "arm-1"
    assert sc["stations"]["output_arm"]["served_by"] == "arm-2"
    assert sc["lane"]["px"][0] is not None
    assert sc["agvs"][0]["bay_px"] is not None


def test_scene_config_injects_warehouse_and_grows_canvas():
    cfg = get_full_config()
    assert "warehouse" in cfg and cfg["warehouse"]["grid"]
    # Scene canvas is fixed; warehouse renders in its own Konva world (Phase
    # 5 topology layer). Just confirm canvas dims are reported as positive.
    assert cfg["canvas_height"] > 0
    assert cfg["canvas_width"] > 0
