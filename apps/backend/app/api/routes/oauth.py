from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import AuthedUser, create_access_token, normalize_email, verify_password
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.integration import OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken
from app.models.session import Session as SessionModel
from app.models.user import User

router = APIRouter(tags=["oauth"])
DEFAULT_SCOPES = "fiebatt:edit projects:read projects:write media:write generation:write"
ALLOWED_SCOPES = frozenset(DEFAULT_SCOPES.split())
PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url_sha256(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("ascii")).digest()).decode("ascii").rstrip("=")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _validate_authorization_request(
    response_type: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
) -> None:
    if response_type != "code" or code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="authorization code with S256 PKCE is required")
    if not PKCE_CHALLENGE_RE.fullmatch(code_challenge):
        raise HTTPException(status_code=400, detail="invalid PKCE code challenge")
    if not set(scope.split()) <= ALLOWED_SCOPES:
        raise HTTPException(status_code=400, detail="unsupported OAuth scope")


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    issuer = get_settings().oauth_issuer
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": DEFAULT_SCOPES.split(),
    }


def _protected_resource_payload() -> dict:
    issuer = get_settings().oauth_issuer
    return {
        "resource": f"{issuer}/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": DEFAULT_SCOPES.split(),
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata():
    return _protected_resource_payload()


class ClientRegistrationRequest(BaseModel):
    client_name: str = Field(default="Codex", max_length=200)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = ["authorization_code", "refresh_token"]
    response_types: list[str] = ["code"]


@router.post("/oauth/register", status_code=201)
async def register_client(body: ClientRegistrationRequest, db: AsyncSession = Depends(get_db)):
    if body.token_endpoint_auth_method != "none":
        raise HTTPException(status_code=400, detail="only public PKCE clients are supported")
    if any(not uri.startswith(("http://127.0.0.1", "http://localhost", "https://")) for uri in body.redirect_uris):
        raise HTTPException(status_code=400, detail="redirect URI must use HTTPS or a loopback address")
    client_id = f"fiebatt_{secrets.token_urlsafe(24)}"
    db.add(OAuthClient(client_id=client_id, client_name=body.client_name, redirect_uris=body.redirect_uris))
    await db.commit()
    return {
        "client_id": client_id,
        "client_name": body.client_name,
        "redirect_uris": body.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


async def _validated_client(db: AsyncSession, client_id: str, redirect_uri: str) -> OAuthClient:
    client = await db.get(OAuthClient, client_id)
    if client is None or redirect_uri not in (client.redirect_uris or []):
        raise HTTPException(status_code=400, detail="invalid OAuth client or redirect URI")
    return client


def _login_page(fields: dict[str, str], error: str = "") -> HTMLResponse:
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in fields.items()
    )
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html><head><meta name="viewport" content="width=device-width">
<title>Connect Fiebatt</title><style>
body{{font:16px system-ui;background:#111;color:#eee;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{width:min(420px,calc(100% - 32px));padding:28px;border:1px solid #333;border-radius:18px;background:#181818}}
input{{box-sizing:border-box;width:100%;padding:12px;margin:7px 0 15px;border-radius:9px;border:1px solid #444;background:#0d0d0d;color:#fff}}
button{{width:100%;padding:12px;border:0;border-radius:9px;background:#fff;color:#111;font-weight:700}} .error{{color:#ff8f8f}}
</style></head><body><main><h1>Connect Fiebatt</h1><p>Sign in or create an account to let Codex use your Fiebatt projects.</p>
{error_html}<form method="post" action="/oauth/authorize">{hidden}
<label>Email<input name="email" type="email" required autocomplete="email"></label>
<label>Password<input name="password" type="password" minlength="8" required autocomplete="current-password"></label>
<label><input style="width:auto" name="create_account" type="checkbox" value="true"> Create an account if this email is new</label>
<button type="submit">Continue to Codex</button></form></main></body></html>"""
    )


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize_page(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = DEFAULT_SCOPES,
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    await _validated_client(db, client_id, redirect_uri)
    _validate_authorization_request(response_type, code_challenge, code_challenge_method, scope)
    return _login_page({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "state": state,
    })


@router.post("/oauth/authorize")
async def authorize_submit(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    response_type: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form(...),
    scope: str = Form(DEFAULT_SCOPES),
    state: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    create_account: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await _validated_client(db, client_id, redirect_uri)
    _validate_authorization_request(response_type, code_challenge, code_challenge_method, scope)
    fields = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": response_type,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
        "scope": scope, "state": state,
    }
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        return _login_page(fields, "Enter a valid email address.")
    if not 8 <= len(password) <= 256:
        return _login_page(fields, "Password must be 8-256 characters.")
    from sqlalchemy import select

    user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if user is None and create_account == "true":
        from app.auth.jwt import hash_password

        user = User(email=normalized, password_hash=hash_password(password))
        db.add(user)
        await db.flush()
        db.add(SessionModel(id=f"user:{user.id}", user_id=user.id, email=user.email))
    elif user is None or not verify_password(password, user.password_hash):
        return _login_page(fields, "Invalid email or password.")

    code = secrets.token_urlsafe(48)
    db.add(OAuthAuthorizationCode(
        code_hash=_hash(code), user_id=user.id, client_id=client_id,
        redirect_uri=redirect_uri, scope=scope, code_challenge=code_challenge,
        expires_at=_now() + timedelta(minutes=5), used=False,
    ))
    await db.commit()
    query = {"code": code}
    if state:
        query["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(query)}", status_code=303)


async def _issue_tokens(db: AsyncSession, *, user: User, client_id: str, scope: str) -> dict:
    settings = get_settings()
    scopes = [item for item in scope.split() if item]
    access_token = create_access_token(
        AuthedUser(id=user.id, email=user.email),
        scopes=scopes,
        audience=f"{settings.oauth_issuer}/mcp",
        expires_minutes=settings.oauth_access_token_minutes,
    )
    refresh_token = secrets.token_urlsafe(64)
    db.add(OAuthRefreshToken(
        token_hash=_hash(refresh_token), user_id=user.id, client_id=client_id,
        scope=" ".join(scopes), expires_at=_now() + timedelta(days=settings.oauth_refresh_token_days),
    ))
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.oauth_access_token_minutes * 60,
        "refresh_token": refresh_token,
        "scope": " ".join(scopes),
    }


@router.post("/oauth/token")
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if grant_type == "authorization_code":
        await _validated_client(db, client_id, redirect_uri)
        row = await db.get(OAuthAuthorizationCode, _hash(code))
        if (
            row is None or row.used or row.client_id != client_id
            or row.redirect_uri != redirect_uri or row.expires_at.replace(tzinfo=timezone.utc) <= _now()
            or not PKCE_VERIFIER_RE.fullmatch(code_verifier)
            or not secrets.compare_digest(_b64url_sha256(code_verifier), row.code_challenge)
        ):
            return _oauth_error("invalid_grant", "invalid, expired, or already used authorization code")
        consumed = await db.execute(
            update(OAuthAuthorizationCode)
            .where(OAuthAuthorizationCode.code_hash == row.code_hash, OAuthAuthorizationCode.used == False)  # noqa: E712
            .values(used=True)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            return _oauth_error("invalid_grant", "invalid, expired, or already used authorization code")
        user = await db.get(User, row.user_id)
        if user is None:
            return _oauth_error("invalid_grant", "account no longer exists")
        await db.commit()
        return await _issue_tokens(db, user=user, client_id=client_id, scope=row.scope)

    if grant_type == "refresh_token":
        row = await db.get(OAuthRefreshToken, _hash(refresh_token))
        if (
            row is None or row.revoked or row.client_id != client_id
            or row.expires_at.replace(tzinfo=timezone.utc) <= _now()
        ):
            return _oauth_error("invalid_grant", "invalid or expired refresh token")
        consumed = await db.execute(
            update(OAuthRefreshToken)
            .where(OAuthRefreshToken.token_hash == row.token_hash, OAuthRefreshToken.revoked == False)  # noqa: E712
            .values(revoked=True)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            return _oauth_error("invalid_grant", "invalid or expired refresh token")
        user = await db.get(User, row.user_id)
        if user is None:
            return _oauth_error("invalid_grant", "account no longer exists")
        await db.commit()
        return await _issue_tokens(db, user=user, client_id=client_id, scope=row.scope)

    return _oauth_error("unsupported_grant_type", "supported grants are authorization_code and refresh_token")
