from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    AuthedUser,
    create_access_token,
    hash_password,
    normalize_email,
    verify_password,
)
from app.db.session import get_db
from app.config.settings import get_settings
from app.models.session import Session as SessionModel
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class AuthUserOut(BaseModel):
    id: str
    email: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


def _response_for(user: User, response: Response) -> AuthResponse:
    token_user = AuthedUser(id=user.id, email=user.email)
    token = create_access_token(token_user)
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_jwt_expires_minutes * 60,
        path="/",
    )
    return AuthResponse(
        access_token=token,
        user=AuthUserOut(id=user.id, email=user.email),
    )


def _validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="valid email is required",
        )
    return normalized


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: AuthRequest, response: Response, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()

    db.add(SessionModel(id=f"user:{user.id}", user_id=user.id, email=user.email))
    await db.commit()
    await db.refresh(user)
    return _response_for(user, response)


@router.post("/login", response_model=AuthResponse)
async def login(body: AuthRequest, response: Response, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")

    session = await db.get(SessionModel, f"user:{user.id}")
    if session is None:
        db.add(SessionModel(id=f"user:{user.id}", user_id=user.id, email=user.email))
        await db.commit()
    return _response_for(user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
