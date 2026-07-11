import logging
import uuid
from typing import Annotated, Optional

from fastapi import Depends, Header, Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.supabase import extract_bearer, verify_supabase_token
from app.db.session import get_db
from app.models.project import Project
from app.models.session import Session as SessionModel

log = logging.getLogger("iris.deps")


async def _migrate_anon_session(
    db: AsyncSession,
    *,
    anon_sid: str,
    user_sid: str,
) -> None:
    """Re-parent an anonymous session's projects onto the signed-in session.

    Runs when a user who previously used iris anonymously signs in for the
    first time — their uploads/edits follow them to their real account
    instead of being orphaned under the random browser uuid.

    Idempotent: if there's nothing to migrate, it's a no-op. Only runs if
    the anon session is real, has no user_id of its own, and isn't the
    same row as the target.
    """
    if not anon_sid or anon_sid == user_sid:
        return
    anon = await db.get(SessionModel, anon_sid)
    if anon is None or anon.user_id is not None:
        return

    result = await db.execute(
        update(Project)
        .where(Project.session_id == anon_sid)
        .values(session_id=user_sid)
    )
    moved = result.rowcount or 0
    # drop the now-empty anon session row so it doesn't accumulate
    await db.delete(anon)
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
      1. Verified Supabase JWT → session keyed by the google user id
         (stable across browsers/devices for a given google account).
      2. X-Session-Id header → anonymous browser session (legacy flow).
      3. Fresh uuid → first-touch anon.

    On the first authenticated request with a pre-existing anon session,
    any projects under that anon session get re-parented onto the user
    session so the user doesn't lose anonymous work on sign-in.
    """
    user = verify_supabase_token(extract_bearer(authorization) or "")

    if user is not None:
        sid = f"user:{user.id}"
        row = await db.get(SessionModel, sid)
        if row is None:
            row = SessionModel(id=sid, user_id=user.id, email=user.email)
            db.add(row)
            await db.flush()
        elif row.email != user.email or row.user_id != user.id:
            row.user_id = user.id
            row.email = user.email

        if x_session_id:
            await _migrate_anon_session(db, anon_sid=x_session_id, user_sid=sid)

        await db.commit()
        request.state.session_id = sid
        return row

    sid = x_session_id or str(uuid.uuid4())
    row = await db.get(SessionModel, sid)
    if row is None:
        row = SessionModel(id=sid)
        db.add(row)
        await db.commit()
    request.state.session_id = sid
    return row


def get_runner(request: Request):
    return request.app.state.runner
