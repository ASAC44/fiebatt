"""Shared parsing helpers."""


def parse_bbox(s: str) -> dict[str, float]:
    """Parse a comma-separated bbox string 'x,y,w,h' into a dict.

    Example:
        >>> parse_bbox("0.1,0.2,0.3,0.4")
        {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
    """
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"bbox must have 4 comma-separated values, got {len(parts)}: {s!r}")

    x, y, w, h = (float(p) for p in parts)
    return {"x": x, "y": y, "w": w, "h": h}
