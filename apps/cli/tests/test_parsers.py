"""Tests for iris_cli.parsers — bbox parsing."""

import pytest

from iris_cli.parsers import parse_bbox


class TestParseBbox:
    """parse_bbox converts 'x,y,w,h' strings to dicts."""

    def test_valid_input(self) -> None:
        result = parse_bbox("0.1,0.2,0.3,0.4")
        assert result == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}

    def test_handles_whitespace(self) -> None:
        result = parse_bbox(" 0.1 , 0.2 , 0.3 , 0.4 ")
        assert result == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}

    def test_too_few_values_raises(self) -> None:
        with pytest.raises(ValueError, match="4 comma-separated"):
            parse_bbox("0.1,0.2,0.3")

    def test_too_many_values_raises(self) -> None:
        with pytest.raises(ValueError, match="4 comma-separated"):
            parse_bbox("0.1,0.2,0.3,0.4,0.5")

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bbox("a,b,c,d")

    def test_full_frame(self) -> None:
        result = parse_bbox("0,0,1,1")
        assert result == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

    def test_zero_size_box(self) -> None:
        result = parse_bbox("0.0,0.0,0.0,0.0")
        assert result == {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bbox("")

    def test_single_value_raises(self) -> None:
        with pytest.raises(ValueError, match="4 comma-separated"):
            parse_bbox("0.5")
