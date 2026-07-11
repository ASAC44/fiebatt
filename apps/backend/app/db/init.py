from sqlalchemy import text

from app.db.base import Base
from app.db.session import engine

# import all models so Base.metadata is populated
from app import models  # noqa: F401


# lightweight idempotent ddl that runs every boot. use this for additive
# schema changes so existing deployments auto-upgrade without a real
# migration tool. postgres-only syntax (IF NOT EXISTS) but sqlite also
# accepts ALTER TABLE ADD COLUMN when the column is new.
_SCHEMA_UPGRADES_POSTGRES = [
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id VARCHAR",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS email VARCHAR",
    "CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions (user_id)",
    # hot-path indexes for the queries we actually run today.
    # `projects(session_id, created_at DESC)` backs the library list.
    # `segments(project_id, active)` backs /api/timeline + accept's
    # overlap check. `entity_appearances(entity_id, start_ts)` keeps the
    # inspector snappy. `jobs(status)` lets the job runner scan pending
    # work without a table scan.
    "CREATE INDEX IF NOT EXISTS ix_projects_session_created "
    "ON projects (session_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_segments_project_active "
    "ON segments (project_id, active)",
    "CREATE INDEX IF NOT EXISTS ix_entity_appearances_entity_start "
    "ON entity_appearances (entity_id, start_ts)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_status "
    "ON jobs (status)",
    # persisted edit decision list — saves split/trim/delete/reorder/volume
    # state across reel reopens. nullable on legacy projects so they still
    # fall through to the old build_timeline() reconstruction path.
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS timeline_edl JSONB",
]


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        dialect = conn.dialect.name
        if dialect == "postgresql":
            for stmt in _SCHEMA_UPGRADES_POSTGRES:
                await conn.execute(text(stmt))
