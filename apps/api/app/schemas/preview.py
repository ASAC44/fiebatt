"""Pydantic schemas for the /api/preview/* routes.

These back the three preview endpoints consumed by the studio for timeline
scrubbing / filmstrip thumbnails / range playback:

- ``PreviewFrameResponse``   -> GET /api/preview/{id}/frame?ts=
- ``PreviewStripResponse``   -> GET /api/preview/{id}/strip?start=&end=&fps=
- ``PreviewRangeResponse``   -> GET /api/preview/{id}/range?start=&end=
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PreviewFrame(BaseModel):
    """A single preview JPEG pinned to a project timestamp."""

    ts: float = Field(ge=0.0, description="timeline-space timestamp (seconds)")
    url: str


class PreviewFrameResponse(BaseModel):
    ts: float = Field(ge=0.0)
    url: str


class PreviewStripResponse(BaseModel):
    """An ordered filmstrip of frames covering the requested range."""

    frames: list[PreviewFrame] = Field(default_factory=list)


class PreviewRangeResponse(BaseModel):
    """A rendered low-res MP4 covering a requested slice of the timeline."""

    preview_url: str
    duration: float = Field(ge=0.0)
