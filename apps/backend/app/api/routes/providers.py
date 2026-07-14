from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.deps import get_session
from app.models.session import Session as SessionModel
from app.services.credentials import (
    PROVIDER_FIELDS,
    delete_provider_credential,
    list_provider_status,
    set_provider_credential,
)

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


def _user_id(session: SessionModel) -> str:
    if not session.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")
    return session.user_id


@router.get("")
async def providers(
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    return await list_provider_status(db, _user_id(session))


@router.put("/{provider}")
async def save_provider(
    provider: str,
    body: ProviderKeyRequest,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    if provider not in PROVIDER_FIELDS:
        raise HTTPException(status_code=404, detail="unsupported provider")
    try:
        await set_provider_credential(db, _user_id(session), provider, body.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"provider": provider, "configured": True, "key_hint": body.api_key[-4:]}


@router.delete("/{provider}")
async def remove_provider(
    provider: str,
    session: SessionModel = Depends(get_session),
    db: AsyncSession = Depends(get_db),
):
    if provider not in PROVIDER_FIELDS:
        raise HTTPException(status_code=404, detail="unsupported provider")
    removed = await delete_provider_credential(db, _user_id(session), provider)
    return {"provider": provider, "configured": False, "removed": removed}
