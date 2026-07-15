"""Public, OAuth-protected MCP endpoint for the Codex plugin."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.services.config import set_settings_overrides
from app.api.routes.agent import OPENAI_TOOLS
from app.auth.jwt import decode_access_token, extract_bearer
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.project import Project
from app.models.segment import Segment
from app.models.entity import Entity
from app.services import agent_tools, storage
from app.services.credentials import list_provider_status, provider_overrides
from app.services.hosted_uploads import complete_upload, prepare_upload, receive_local_upload

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-03-26"
REQUIRED_SCOPE = "fiebatt:edit"

SPECIAL_TOOLS = [
    {
        "name": "account_status",
        "description": "Check the connected Fiebatt account and configured AI provider keys.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "prepare_upload",
        "description": "Create a short-lived signed URL for uploading a source video directly to Fiebatt storage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content_type": {"type": "string", "enum": ["video/mp4", "video/quicktime", "video/webm", "video/x-m4v"]},
                "size_bytes": {"type": "integer", "minimum": 1},
            },
            "required": ["filename", "content_type", "size_bytes"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "complete_upload",
        "description": "Validate a prepared upload and create an editable Fiebatt project from it.",
        "inputSchema": {
            "type": "object",
            "properties": {"upload_id": {"type": "string"}},
            "required": ["upload_id"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "list_projects",
        "description": "List video projects owned by the connected Fiebatt account.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_project",
        "description": "Get source details, active segments, and known entities for one owned project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def _tools() -> list[dict[str, Any]]:
    tools = list(SPECIAL_TOOLS)
    for item in OPENAI_TOOLS:
        function = item["function"]
        name = function["name"]
        tools.append({
            "name": name,
            "description": function.get("description", ""),
            "inputSchema": function.get("parameters", {"type": "object"}),
            "annotations": {
                "readOnlyHint": name.startswith(("get_", "list_", "preview_", "score_", "analyze_", "identify_")),
                "destructiveHint": name in {"delete_segment", "accept_variant", "revert_timeline"},
            },
        })
    return tools


def _jsonrpc(request_id: Any, result: Any = None, error: dict | None = None, *, status: int = 200):
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return JSONResponse(payload, status_code=status)


def _unauthorized() -> JSONResponse:
    metadata = f'{get_settings().oauth_issuer}/.well-known/oauth-protected-resource/mcp'
    return JSONResponse(
        {"error": "invalid_token", "error_description": "A valid Fiebatt OAuth token is required."},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
    )


async def _execute_special(
    name: str,
    args: dict[str, Any],
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any] | None:
    if name == "account_status":
        return {
            "connected": True,
            "providers": await list_provider_status(db, user_id),
            "settings_url": f"{get_settings().app_url.rstrip('/')}/settings",
        }
    if name == "prepare_upload":
        return await prepare_upload(db, user_id=user_id, **args)
    if name == "complete_upload":
        return await complete_upload(db, user_id=user_id, **args)
    if name == "list_projects":
        rows = (
            await db.execute(
                select(Project)
                .where(Project.session_id == f"user:{user_id}")
                .order_by(Project.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        return {
            "projects": [
                {
                    "project_id": row.id,
                    "video_url": storage.normalize_url_like(row.video_url, fallback=row.video_url),
                    "duration": row.duration,
                    "fps": row.fps,
                    "width": row.width,
                    "height": row.height,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        }
    if name == "get_project":
        project = await db.get(Project, str(args.get("project_id", "")))
        if project is None or project.session_id != f"user:{user_id}":
            raise ValueError("project not found")
        segments = (
            await db.execute(
                select(Segment)
                .where(Segment.project_id == project.id, Segment.active == True)  # noqa: E712
                .order_by(Segment.order_index, Segment.start_ts)
            )
        ).scalars().all()
        entities = (
            await db.execute(select(Entity).where(Entity.project_id == project.id))
        ).scalars().all()
        return {
            "project_id": project.id,
            "video_url": storage.normalize_url_like(project.video_url, fallback=project.video_url),
            "duration": project.duration,
            "fps": project.fps,
            "width": project.width,
            "height": project.height,
            "segments": [
                {
                    "id": segment.id,
                    "start_ts": segment.start_ts,
                    "end_ts": segment.end_ts,
                    "source": segment.source,
                    "url": storage.normalize_url_like(segment.url, fallback=segment.url),
                    "variant_id": segment.variant_id,
                    "order_index": segment.order_index,
                }
                for segment in segments
            ],
            "entities": [
                {"id": entity.id, "description": entity.description, "category": entity.category}
                for entity in entities
            ],
        }
    return None


@router.post("/mcp")
async def mcp(request: Request, db: AsyncSession = Depends(get_db)):
    claims = decode_access_token(extract_bearer(request.headers.get("authorization")) or "")
    if claims is None:
        return _unauthorized()
    scopes = set(str(claims.get("scope", "")).split())
    if REQUIRED_SCOPE not in scopes:
        return JSONResponse({"error": "insufficient_scope"}, status_code=403)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _jsonrpc(None, error={"code": -32700, "message": "Parse error"}, status=400)
    request_id = body.get("id")
    method = body.get("method")

    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "initialize":
        requested = body.get("params", {}).get("protocolVersion")
        return _jsonrpc(request_id, {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fiebatt", "version": "0.1.0"},
            "instructions": "Upload or select a project, inspect it, then preview edits before accepting or exporting.",
        })
    if method == "ping":
        return _jsonrpc(request_id, {})
    if method == "tools/list":
        return _jsonrpc(request_id, {"tools": _tools()})
    if method != "tools/call":
        return _jsonrpc(request_id, error={"code": -32601, "message": "Method not found"})

    params = body.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(args, dict):
        return _jsonrpc(request_id, error={"code": -32602, "message": "Invalid tool arguments"})

    user_id = str(claims["sub"])
    try:
        set_settings_overrides(await provider_overrides(db, user_id))
        result = await _execute_special(name, args, db, user_id)
        if result is None:
            result = await agent_tools.execute_tool(
                name,
                args,
                db,
                f"user:{user_id}",
                getattr(request.app.state, "runner", None),
            )
        return _jsonrpc(request_id, {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "structuredContent": result,
        })
    except (TypeError, ValueError) as exc:
        return _jsonrpc(request_id, {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        })
    except Exception:
        return _jsonrpc(request_id, {
            "content": [{"type": "text", "text": "Fiebatt could not complete this tool call."}],
            "isError": True,
        })


@router.put("/uploads/{upload_id}")
async def upload_media(
    upload_id: str,
    request: Request,
    token: str = "",
    db: AsyncSession = Depends(get_db),
):
    try:
        return await receive_local_upload(
            db,
            upload_id=upload_id,
            token=token,
            request=request,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
