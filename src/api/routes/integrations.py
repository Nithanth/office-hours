"""User-owned third-party integrations."""

from __future__ import annotations

import base64
import secrets
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config as _cfg
from services.auth.session import (
    OAUTH_STATE_COOKIE_NAME,
    clear_oauth_state_cookie,
    get_auth_user_id,
    require_auth_user_id,
    set_oauth_state_cookie,
    sign_oauth_state,
    verify_oauth_state,
)
from services.persistence.database import database_available, session_scope

router = APIRouter(tags=["integrations"])

_NOTION_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
_NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_NOTION_SEARCH_URL = "https://api.notion.com/v1/search"
_NOTION_VERSION = "2026-03-11"


def _notion_redirect_uri() -> str:
    return f"{_cfg.config.AUTH_REDIRECT_BASE_URL}/integrations/notion/callback"


def _notion_oauth_configured() -> bool:
    return bool(
        database_available()
        and _cfg.config.NOTION_OAUTH_CLIENT_ID
        and _cfg.config.NOTION_OAUTH_CLIENT_SECRET
        and _cfg.config.SECRET_ENCRYPTION_KEY
    )


def _basic_auth_header() -> str:
    raw = (
        f"{_cfg.config.NOTION_OAUTH_CLIENT_ID}:{_cfg.config.NOTION_OAUTH_CLIENT_SECRET}"
    )
    return "Basic " + base64.b64encode(raw.encode()).decode()


@router.get("/integrations/notion/connect")
async def notion_connect(request: Request, next: str = "/") -> Response:  # noqa: A002
    if not get_auth_user_id(request):
        # Safety net for direct hits (library link, bookmarks): bounce through
        # Google sign-in and come back here instead of a bare 401 page.
        login_next = (
            f"/integrations/notion/connect?next={urllib.parse.quote(next, safe='')}"
        )
        return RedirectResponse(
            "/auth/google/login?next=" + urllib.parse.quote(login_next, safe="/?&=")
        )
    if not _notion_oauth_configured():
        # Local paths only — `//host` would navigate off-site from our page.
        safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
        return HTMLResponse(
            "<div style='font-family:system-ui,sans-serif;max-width:420px;margin:120px auto;text-align:center;'>"
            "<h2 style='font-size:1.1rem;'>Notion sync isn't set up yet</h2>"
            "<p style='color:#666;font-size:0.9rem;'>The Notion integration isn't configured on this deployment.</p>"
            f"<a href='{urllib.parse.quote(safe_next, safe='/?&=')}' style='color:#2563eb;'>← Back</a>"
            "</div>",
            status_code=503,
        )

    state = sign_oauth_state(
        {"nonce": secrets.token_urlsafe(16), "next": next, "provider": "notion"}
    )
    params = {
        "owner": "user",
        "client_id": _cfg.config.NOTION_OAUTH_CLIENT_ID,
        "redirect_uri": _notion_redirect_uri(),
        "response_type": "code",
        "state": state,
    }
    response = RedirectResponse(f"{_NOTION_AUTH_URL}?{urllib.parse.urlencode(params)}")
    set_oauth_state_cookie(response, state, request)
    return response


@router.get("/integrations/notion/callback")
async def notion_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    user_id = require_auth_user_id(request)
    if error:
        raise HTTPException(
            status_code=400, detail=f"Notion authorization failed: {error}"
        )
    if not _notion_oauth_configured():
        raise HTTPException(status_code=503, detail="Notion OAuth is not configured.")
    if not code or not state:
        raise HTTPException(
            status_code=400, detail="Missing Notion OAuth code or state."
        )
    if request.cookies.get(OAUTH_STATE_COOKIE_NAME) != state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch.")
    payload = verify_oauth_state(state)
    if payload.get("provider") != "notion":
        raise HTTPException(status_code=400, detail="Invalid Notion OAuth state.")

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            _NOTION_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Notion-Version": _NOTION_VERSION,
            },
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _notion_redirect_uri(),
            },
        )
        token_resp.raise_for_status()
        token_data: dict[str, Any] = token_resp.json()

    from services.persistence.repositories import upsert_notion_integration

    async for session in session_scope():
        await upsert_notion_integration(
            session,
            user_id=user_id,
            access_token=str(token_data["access_token"]),
            refresh_token=token_data.get("refresh_token"),
            bot_id=token_data.get("bot_id"),
            workspace_id=token_data.get("workspace_id"),
            workspace_name=token_data.get("workspace_name"),
            workspace_icon=token_data.get("workspace_icon"),
        )

    response = RedirectResponse(str(payload.get("next") or "/"))
    clear_oauth_state_cookie(response)
    return response


@router.get("/api/notion/destinations")
async def notion_destinations(request: Request) -> JSONResponse:
    user_id = require_auth_user_id(request)
    if not database_available():
        return JSONResponse(
            {"ok": False, "destinations": [], "detail": "Database not configured"}
        )

    from services.persistence.repositories import (
        get_notion_integration,
        notion_access_token,
    )

    async for session in session_scope():
        integration = await get_notion_integration(session, user_id=user_id)
    if integration is None:
        return JSONResponse(
            {"ok": False, "destinations": [], "detail": "Connect Notion first"}
        )

    token = notion_access_token(integration)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                _NOTION_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": _NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                json={"page_size": 50},
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            {
                "ok": False,
                "destinations": [],
                "detail": f"Notion unreachable: {type(exc).__name__}",
            }
        )
    if resp.status_code != 200:
        detail = resp.json().get("message", f"HTTP {resp.status_code}")
        return JSONResponse(
            {"ok": False, "destinations": [], "detail": f"Notion: {detail}"}
        )
    payload = resp.json()

    destinations = []
    for item in payload.get("results", []):
        # Skip archived/trashed entries — Notion search can still return them
        # and a pick would land on an unwritable object.
        if item.get("archived") or item.get("in_trash"):
            continue
        object_type = item.get("object")
        title = _notion_title(item)
        destinations.append(
            {
                "id": item.get("id"),
                "type": object_type,
                "title": title or ("Database" if object_type == "database" else "Page"),
            }
        )
    return JSONResponse({"ok": True, "destinations": destinations})


@router.post("/api/notion/destination")
async def notion_destination(
    request: Request,
    destination_id: str = Form(...),
    destination_type: str = Form(...),
) -> JSONResponse:
    user_id = require_auth_user_id(request)
    if destination_type not in {"page", "database"}:
        raise HTTPException(
            status_code=400, detail="destination_type must be page or database"
        )

    parent_page_id = destination_id if destination_type == "page" else None
    if destination_type == "database":
        # A database pick means "put my databases next to this one", not
        # "write rows into it" — the user's own schema can't hold our
        # Status/Topic columns, so we anchor our databases on its parent
        # page instead.
        from services.notion.notion_exporter import NotionExporter
        from services.persistence.repositories import (
            get_notion_integration,
            notion_access_token,
        )

        async for session in session_scope():
            integration = await get_notion_integration(session, user_id=user_id)
        token = notion_access_token(integration) if integration else None
        if not token:
            raise HTTPException(status_code=404, detail="Connect Notion first")
        exporter = NotionExporter(token)
        try:
            parent_page_id = await exporter.get_database_parent_page(destination_id)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "detail": f"Notion: {exc}"},
                status_code=400,
            )
        finally:
            await exporter.close()
        if not parent_page_id:
            return JSONResponse(
                {
                    "ok": False,
                    "detail": (
                        "That database sits at your workspace root — pick a "
                        "page instead so the databases can be created next to it."
                    ),
                },
                status_code=400,
            )

    from services.persistence.repositories import update_notion_destination

    async for session in session_scope():
        integration = await update_notion_destination(
            session,
            user_id=user_id,
            parent_page_id=parent_page_id,
            parent_database_id=None,
        )
    if integration is None:
        raise HTTPException(status_code=404, detail="Connect Notion first")
    return JSONResponse({"ok": True})


@router.post("/integrations/notion/disconnect")
async def notion_disconnect(request: Request) -> JSONResponse:
    user_id = require_auth_user_id(request)
    from services.persistence.repositories import delete_notion_integration

    async for session in session_scope():
        await delete_notion_integration(session, user_id=user_id)
    return JSONResponse({"ok": True})


def _notion_title(item: dict[str, Any]) -> str:
    if item.get("object") == "database":
        title_parts = item.get("title") or []
    else:
        props = item.get("properties") or {}
        title_parts = []
        for prop in props.values():
            if prop.get("type") == "title":
                title_parts = prop.get("title") or []
                break
    chunks = []
    for part in title_parts:
        text = part.get("plain_text") or part.get("text", {}).get("content")
        if text:
            chunks.append(str(text))
    return "".join(chunks).strip()
