"""Warehouse config helpers + scene injection (Phase 2)."""

from app.models import pipeline_config
from app.models.scene_config import get_full_config


def test_warehouse_block_parsed():
    assert pipeline_config.has_warehouse() is True
    grid = pipeline_config.get_grid()
    assert grid["cols"] == 14 and grid["rows"] == 6 and grid["cell_px"] == 54
    assert grid["origin"] == {"x": 120, "y": 620}
    assert pipeline_config.get_agv_ids() == ["agv-1", "agv-2", "agv-3"]
    assert len(pipeline_config.get_lane_cells()) == 10


def test_cell_to_px_is_cell_centre():
    # origin (120,620), cell_px 54 -> cell [0,0] centre = (147, 647)
    assert pipeline_config.cell_to_px([0, 0]) == {"x": 120 + 27, "y": 620 + 27}
    assert pipeline_config.cell_in_grid([13, 5]) is True
    assert pipeline_config.cell_in_grid([14, 0]) is False
    assert pipeline_config.cell_in_grid([-1, 0]) is False


def test_warehouse_scene_shape():
    sc = pipeline_config.get_warehouse_scene()
    assert set(sc) == {"grid", "stations", "lane", "agvs"}
    assert sc["grid"]["width"] == 14 * 54
    assert sc["stations"]["input_arm"]["served_by"] == "arm-1"
    assert sc["stations"]["output_arm"]["served_by"] == "arm-4"
    assert sc["lane"]["px"][0] is not None
    assert sc["agvs"][0]["bay_px"] is not None


def test_scene_config_injects_warehouse_and_grows_canvas():
    cfg = get_full_config()
    assert "warehouse" in cfg and cfg["warehouse"]["grid"]
    # canvas must extend to cover the grid band (origin.y + rows*cell_px + 40).
    assert cfg["canvas_height"] >= 620 + 6 * 54 + 40
    assert cfg["canvas_width"] >= 120 + 14 * 54 + 40
