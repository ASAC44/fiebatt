from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ai.services.health import router as ai_observability_router
from app.config.settings import get_settings
from app.db.init import create_all
from app.workers.runner import JobRunner
from app.api.routes import (
    health,
    upload,
    projects,
    generate,
    scoring,
    remix,
    identify,
    jobs,
    accept,
    entities,
    propagate,
    timeline,
    timeline_ops,
    narrate,
    export,
    mask,
    color,
    agent,
    conversations,
    preview,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    app.state.runner = JobRunner()
    try:
        yield
    finally:
        await app.state.runner.shutdown()


settings = get_settings()

app = FastAPI(title="iris backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)


@app.middleware("http")
async def echo_session_id(request: Request, call_next):
    response = await call_next(request)
    sid = getattr(request.state, "session_id", None)
    if sid:
        response.headers["X-Session-Id"] = sid
    return response


# legacy /media mount. kept for offline dev where vultr creds aren't set
# — when S3 is configured all media URLs come from vultr and the frontend
# never hits this endpoint.
if not settings.s3_enabled:
    app.mount(
        "/media",
        StaticFiles(directory=str(settings.storage_path)),
        name="media",
    )

app.include_router(health.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(scoring.router, prefix="/api")
app.include_router(remix.router, prefix="/api")
app.include_router(identify.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(accept.router, prefix="/api")
app.include_router(entities.router, prefix="/api")
app.include_router(propagate.router, prefix="/api")
app.include_router(timeline.router, prefix="/api")
app.include_router(timeline_ops.router, prefix="/api")
app.include_router(narrate.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(mask.router, prefix="/api")
app.include_router(color.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(preview.router, prefix="/api")
app.include_router(ai_observability_router, prefix="/api/ai")
