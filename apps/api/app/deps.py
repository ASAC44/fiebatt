import logging
import uuid
from typing import Annotated, Optional

from fastapi import Depends, Header, Request
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import extract_bearer, verify_access_token
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.project import Project
from app.models.session import Session as SessionModel

log = logging.getLogger("fiebatt.deps")


def _session_insert_statement(dialect: str, values: dict):
    """Build the atomic session upsert used by every authentication path."""
    if dialect == "postgresql":
        return postgresql_insert(SessionModel).values(**values).on_conflict_do_nothing(
            index_elements=[SessionModel.id]
        )
    if dialect == "sqlite":
        return sqlite_insert(SessionModel).values(**values).on_conflict_do_nothing(
            index_elements=[SessionModel.id]
        )
    raise RuntimeError(f"unsupported session database dialect: {dialect}")


async def _ensure_session_row(
    db: AsyncSession,
    *,
    sid: str,
    user_id: str | None = None,
    email: str | None = None,
) -> SessionModel:
    """Create a session idempotently, even under concurrent first requests."""
    values = {"id": sid, "user_id": user_id, "email": email}
    dialect = db.get_bind().dialect.name
    await db.execute(_session_insert_statement(dialect, values))
    row = (
        await db.execute(select(SessionModel).where(SessionModel.id == sid))
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError("session row was not visible after idempotent creation")
    return row


async def _migrate_anon_session(
    db: AsyncSession,
    *,
    anon_sid: str,
    user_sid: str,
) -> None:
    """Re-parent an anonymous session's projects onto the signed-in session.

    Runs when a user who previously used fiebatt anonymously signs in for the
    first time — their uploads/edits follow them to their real account
    instead of being orphaned under the random browser uuid.

    Idempotent: if there's nothing to migrate, it's a no-op. Only runs if
    the anon session is real, has no user_id of its own, and isn't the
    same row as the target.
    """
    if not anon_sid or anon_sid == user_sid:
        return
    anon_user_id = (
        await db.execute(
            select(SessionModel.user_id).where(SessionModel.id == anon_sid)
        )
    ).scalar_one_or_none()
    if anon_user_id is not None:
        return

    anon_exists = (
        await db.execute(select(SessionModel.id).where(SessionModel.id == anon_sid))
    ).scalar_one_or_none()
    if anon_exists is None:
        return

    result = await db.execute(
        update(Project)
        .where(Project.session_id == anon_sid)
        .values(session_id=user_sid)
    )
    moved = result.rowcount or 0
    # Delete by predicate instead of deleting a previously loaded ORM object.
    # Two login requests may migrate the same anon id concurrently; the second
    # delete should simply affect zero rows rather than raising stale-row errors.
    await db.execute(
        delete(SessionModel).where(
            SessionModel.id == anon_sid,
            SessionModel.user_id.is_(None),
        )
    )
    if moved:
        log.info("migrated %d project(s) from %s to %s", moved, anon_sid, user_sid)


async def get_session(
    request: Request,
    x_session_id: Annotated[Optional[str], Header(alias="X-Session-Id")] = None,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    db: AsyncSession = Depends(get_db),
) -> SessionModel:
    """Resolve the request's session row.

    Priority:
      1. Verified first-party JWT → session keyed by the user id
         (stable across browsers/devices for a given account).
      2. X-Session-Id header → anonymous compatibility session.
      3. Fresh uuid → first-touch anon.

    On the first authenticated request with a pre-existing anon session,
    any projects under that anon session get re-parented onto the user
    session so the user doesn't lose anonymous work on sign-in.
    """
    bearer = extract_bearer(authorization)
    cookie_token = request.cookies.get(get_settings().auth_cookie_name)
    user = verify_access_token(bearer or cookie_token or "")

    if user is not None:
        sid = f"user:{user.id}"
        row = await _ensure_session_row(
            db,
            sid=sid,
            user_id=user.id,
            email=user.email,
        )
        if row.email != user.email or row.user_id != user.id:
            row.user_id = user.id
            row.email = user.email

        if x_session_id:
            await _migrate_anon_session(db, anon_sid=x_session_id, user_sid=sid)

        await db.commit()
        request.state.session_id = sid
        return row

    sid = x_session_id or str(uuid.uuid4())
    row = await _ensure_session_row(db, sid=sid)
    await db.commit()
    request.state.session_id = sid
    return row


def get_runner(request: Request):
    return request.app.state.runner
