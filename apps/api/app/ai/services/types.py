"""Shared types between backend (Person 2) and ai/services (Person 3).

Do not change these without pinging the other side — they are the contract.
"""
from typing import TypedDict


class BBoxDict(TypedDict):
    x: float
    y: float
    w: float
    h: float


class EditPlan(TypedDict):
    description: str
    tone: str
    color_grading: str
    region_emphasis: str
    prompt_for_runway: str


class VariantResult(TypedDict):
    url: str
    description: str


class QualityScore(TypedDict):
    visual_coherence: int
    prompt_adherence: int


class EntityIdentity(TypedDict):
    description: str
    category: str
    attributes: dict


class EntityHit(TypedDict):
    start_ts: float
    end_ts: float
    keyframe_url: str
    confidence: float
