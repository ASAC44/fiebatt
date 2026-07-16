import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tracking_contract.py"
SPEC = importlib.util.spec_from_file_location("vision_worker_main", MODULE_PATH)
assert SPEC and SPEC.loader
vision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vision)


def test_bounded_window_keeps_seed_and_frame_budget():
    assert vision.bounded_window(20, 10, 7) == (7, 14, 3)
    assert vision.bounded_window(5, 0, 4) == (0, 4, 0)
    assert vision.bounded_window(5, 4, 4) == (1, 5, 3)


def test_stub_frame_preserves_source_index_and_normalized_box():
    bbox = {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.75}
    result = vision.stub_frame(7, bbox)
    assert result["frame_index"] == 7
    assert result["bbox"] == bbox
    assert result["state"] == "tracked"
