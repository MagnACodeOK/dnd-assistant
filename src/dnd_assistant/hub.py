"""Localhost HTTP endpoint that receives events from the Foundry module."""

from __future__ import annotations

import asyncio
import hmac
import json
from datetime import datetime
from typing import Callable

from aiohttp import web

from .store import Event

FOUNDRY_KINDS = frozenset({"chat", "roll", "combat", "state"})


def make_app(handle: Callable[[Event], int], token: str, allowed_origins: list[str]) -> web.Application:
    allowed = set(allowed_origins)

    @web.middleware
    async def cors(request: web.Request, handler):
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            resp = exc
        origin = request.headers.get("Origin")
        if origin in allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        return resp

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def preflight(request: web.Request) -> web.Response:
        if request.headers.get("Origin") not in allowed:
            return web.Response(status=403)
        return web.Response(status=204, headers={
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type, X-Token",
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
        })

    async def post_event(request: web.Request) -> web.Response:
        if not hmac.compare_digest(request.headers.get("X-Token", ""), token):
            return web.json_response({"error": "bad token"}, status=401)
        received = datetime.now().astimezone()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be an object"}, status=400)
        kind = body.get("kind")
        if kind not in FOUNDRY_KINDS:
            return web.json_response({"error": f"kind must be one of {sorted(FOUNDRY_KINDS)}"}, status=400)
        data = body.get("data") or {}
        speaker = body.get("speaker")
        text = body.get("text", "")
        if not isinstance(data, dict) or not isinstance(text, str) or not (speaker is None or isinstance(speaker, str)):
            return web.json_response({"error": "bad field types"}, status=400)
        data = dict(data)
        if body.get("ts") is not None:
            data["foundry_ts"] = body["ts"]
        event = Event(ts=received, source="foundry", kind=kind, speaker=speaker, text=text, data=data)
        event_id = await asyncio.to_thread(handle, event)
        return web.json_response({"id": event_id}, status=202)

    app = web.Application(middlewares=[cors])
    app.router.add_get("/health", health)
    app.router.add_post("/event", post_event)
    app.router.add_route("OPTIONS", "/event", preflight)
    return app


async def serve(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
