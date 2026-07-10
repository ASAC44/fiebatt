from typing import Literal

from pydantic import BaseModel

from app.schemas.common import JobStatus


class ExportRequest(BaseModel):
    project_id: str
    format: Literal["mp4"] = "mp4"


class ExportResponse(BaseModel):
    export_job_id: str


class ExportStatus(BaseModel):
    export_job_id: str
    status: JobStatus
    # URL suitable for in-browser playback (Content-Type: video/mp4,
    # inline disposition). Use this for a <video src>.
    export_url: str | None = None
    # URL that forces a save-to-disk when navigated to. Signed with
    # ResponseContentDisposition=attachment so <a download> works even
    # cross-origin, without needing bucket CORS.
    download_url: str | None = None
    error: str | None = None
