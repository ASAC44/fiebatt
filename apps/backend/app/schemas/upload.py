from pydantic import BaseModel


class UploadResponse(BaseModel):
    project_id: str
    video_url: str
    duration: float
    fps: float
    width: int
    height: int
