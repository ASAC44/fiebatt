from pydantic import BaseModel


class NarrateRequest(BaseModel):
    variant_id: str
    description: str | None = None


class NarrateResponse(BaseModel):
    audio_url: str
