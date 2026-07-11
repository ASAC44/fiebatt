from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "processing", "done", "error"]
SegmentSource = Literal["original", "generated"]


class BBox(BaseModel):
    """Normalized bounding box, top-left origin. All values in [0, 1]."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)
