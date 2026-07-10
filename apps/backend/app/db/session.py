from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings

_settings = get_settings()


def _engine_kwargs(url: str) -> dict:
    """Tune the engine per dialect.

    • postgres (vultr managed): pool, pre_ping, recycle idle conns so the
      ssl connection doesn't rot after a few mins. ssl is negotiated via
      the ?ssl=require / ?sslmode=require flag on the DSN.
    • sqlite: default pool is fine, no extras.
    """
    kwargs: dict = {"echo": False, "future": True}
    if url.startswith("postgresql") or url.startswith("postgres"):
        # The agent SSE stream pins a checked-out connection for the full
        # duration of the conversation — and ``wait_for_job`` can legitimately
        # hold it for 180s while Veo renders. At pool_size=5/overflow=10 we
        # ran out of connections after ~15 chats and started throwing
        # QueuePool timeout 30s errors mid-render. Give ourselves more
        # headroom and a shorter checkout timeout so a leak surfaces
        # as a fast error instead of a 30s hang.
        kwargs.update(
            pool_size=20,
            max_overflow=40,
            pool_timeout=10.0,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return kwargs


engine = create_async_engine(_settings.database_url, **_engine_kwargs(_settings.database_url))

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
