from fastapi import APIRouter, Depends, HTTPException, status
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


def _response_for(user: User) -> AuthResponse:
    token_user = AuthedUser(id=user.id, email=user.email)
    return AuthResponse(
        access_token=create_access_token(token_user),
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
async def signup(body: AuthRequest, db: AsyncSession = Depends(get_db)):
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
    return _response_for(user)


@router.post("/login", response_model=AuthResponse)
async def login(body: AuthRequest, db: AsyncSession = Depends(get_db)):
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
    return _response_for(user)
