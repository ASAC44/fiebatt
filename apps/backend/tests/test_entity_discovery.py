from app.services.entity_discovery import is_trackable_bbox


def test_entity_discovery_requires_local_target():
    assert is_trackable_bbox({"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7}) is True
    assert is_trackable_bbox({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}) is False
    assert is_trackable_bbox(None) is False
