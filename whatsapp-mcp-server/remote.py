"""Remote (Streamable HTTP) entry point for the WhatsApp MCP server.

Serves, under BASE_PATH (default /whatsapp):

  {BASE}/mcp        MCP over Streamable HTTP           Bearer token
  {BASE}/setup/qr   phone-linking QR (HTML, ?format=png) Bearer token (or Basic, see below)
  {BASE}/healthz    process liveness only              no auth

Every route except /healthz needs MCP_AUTH_TOKEN, compared in constant time.
/setup/qr also accepts HTTP Basic auth with the same token as the password
(any user name), so a browser can show the page: the browser prompts once and
resends the header on every auto-refresh. The token is never accepted in a
URL.

Binds 127.0.0.1 only; it is meant to be reached through Cloudflare Tunnel.

Environment:
  MCP_AUTH_TOKEN      required, >= 32 characters
  PUBLIC_HOST         public hostname (e.g. local.sheiksadi.dev), allowed in Host
  BASE_PATH           path prefix, default /whatsapp
  WHATSAPP_MCP_HOST   default 127.0.0.1
  WHATSAPP_MCP_PORT   default 8000
  WHATSAPP_API_URL    bridge REST base, e.g. http://127.0.0.1:8790/api
  DATA_DIR            shared with the bridge (see whatsapp.py)
"""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import io
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import qrcode
import qrcode.image.pure
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from main import mcp
from mcp_config import resolve_host, resolve_port
from whatsapp import WHATSAPP_API_BASE_URL, _bridge_headers

MIN_TOKEN_LEN = 32
QR_REFRESH_SECONDS = 4

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def normalize_base_path(value: str | None) -> str:
    base = (value or "/whatsapp").strip().rstrip("/")
    if base and not base.startswith("/"):
        base = "/" + base
    return base


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _token_matches(candidate: str, token: str) -> bool:
    return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))


def check_authorization(header: str | None, token: str, allow_basic: bool) -> bool:
    """Constant-time check of an Authorization header against token."""
    if not header:
        return False
    scheme, _, value = header.strip().partition(" ")
    value = value.strip()
    scheme = scheme.lower()
    if scheme == "bearer":
        return _token_matches(value, token)
    if scheme == "basic" and allow_basic:
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        _, sep, password = decoded.partition(":")
        return bool(sep) and _token_matches(password, token)
    return False


class BearerAuthMiddleware:
    """ASGI middleware: every HTTP request needs the token, except public_paths."""

    def __init__(self, app: ASGIApp, token: str, public_paths: set[str], basic_paths: set[str]):
        if len(token) < MIN_TOKEN_LEN:
            raise ValueError(f"MCP_AUTH_TOKEN must be at least {MIN_TOKEN_LEN} characters")
        self.app = app
        self.token = token
        self.public_paths = public_paths
        self.basic_paths = basic_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan
            return
        path = scope.get("path", "")
        if path in self.public_paths:
            await self.app(scope, receive, send)
            return
        header = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header = value.decode("latin-1")
                break
        allow_basic = path in self.basic_paths
        if check_authorization(header, self.token, allow_basic):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        challenge = 'Basic realm="whatsapp-mcp", charset="UTF-8"' if allow_basic else 'Bearer realm="whatsapp-mcp"'
        response = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# Pairing QR
# ---------------------------------------------------------------------------

_NO_STORE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_HTML_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'none'",
}


def qr_png(code: str) -> bytes:
    img = qrcode.make(code, image_factory=qrcode.image.pure.PyPNGImage, box_size=8, border=4)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def _page(title: str, body: str, refresh: int | None) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"{meta}<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;"
        "text-align:center;color:#111;background:#fff}img{width:100%;max-width:320px;image-rendering:pixelated}"
        "p{line-height:1.5}.muted{color:#666;font-size:.9rem}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )


async def _bridge_get_connection(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.get(f"{WHATSAPP_API_BASE_URL}/connection", headers=_bridge_headers())
    resp.raise_for_status()
    return resp.json()


async def _bridge_reconnect(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.post(f"{WHATSAPP_API_BASE_URL}/connection/reconnect", headers=_bridge_headers())
    resp.raise_for_status()
    return resp.json()


async def setup_qr(request: Request) -> Response:
    want_png = request.query_params.get("format") == "png"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            status = await _bridge_get_connection(client)
            # Not paired and no live code (first start finished, code timed out,
            # or the phone logged this device out): start a new linking round.
            if (
                not status.get("paired")
                and not status.get("qrCode")
                and status.get("state")
                not in (
                    "pairing",
                    "connecting",
                    "outdated",
                )
            ):
                status = await _bridge_reconnect(client)
        except (httpx.HTTPError, ValueError):
            return PlainTextResponse("WhatsApp bridge is not reachable.", status_code=503, headers=_NO_STORE)

    if status.get("paired"):
        msg = "Already paired. To link again, remove this device in WhatsApp → Linked Devices first."
        if want_png:
            return JSONResponse({"paired": True, "state": status.get("state"), "message": msg}, 409, headers=_NO_STORE)
        return HTMLResponse409(_page("Already paired", f"<p>{html.escape(msg)}</p>", None))

    if status.get("state") == "outdated":
        return PlainTextResponse(
            "The WhatsApp bridge is outdated; update whatsmeow before linking.", 503, headers=_NO_STORE
        )

    code = status.get("qrCode")
    if not code:
        if want_png:
            return JSONResponse({"paired": False, "state": status.get("state")}, 503, headers=_NO_STORE)
        body = "<p>Requesting a code from WhatsApp…</p><p class='muted'>This page refreshes by itself.</p>"
        return Response(_page("Link WhatsApp", body, 2), 503, media_type="text/html", headers=_HTML_HEADERS)

    png = qr_png(code)
    if want_png:
        return Response(png, media_type="image/png", headers=_NO_STORE)
    data = base64.b64encode(png).decode("ascii")
    body = (
        f"<img alt='WhatsApp linking QR code' src='data:image/png;base64,{data}'>"
        "<p>WhatsApp → Settings → Linked Devices → Link a Device, then scan.</p>"
        f"<p class='muted'>Codes rotate about every 20 seconds; this page refreshes every {QR_REFRESH_SECONDS} s.</p>"
    )
    return Response(_page("Link WhatsApp", body, QR_REFRESH_SECONDS), media_type="text/html", headers=_HTML_HEADERS)


def HTMLResponse409(content: str) -> Response:  # noqa: N802 - reads like a constructor
    return Response(content, 409, media_type="text/html", headers=_HTML_HEADERS)


async def healthz(request: Request) -> Response:
    # Process liveness only: never pairing state or WhatsApp data.
    return Response(json.dumps({"status": "ok"}), media_type="application/json", headers=_NO_STORE)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def build_app(token: str, public_host: str, base_path: str, port: int) -> ASGIApp:
    base = normalize_base_path(base_path)
    mcp.settings.streamable_http_path = f"{base}/mcp"
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if public_host:
        hosts.append(public_host)
        origins.append(f"https://{public_host}")
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )
    mcp.custom_route(f"{base}/setup/qr", methods=["GET"])(setup_qr)
    mcp.custom_route(f"{base}/healthz", methods=["GET"])(healthz)
    app = mcp.streamable_http_app()
    return BearerAuthMiddleware(
        app,
        token=token,
        public_paths={f"{base}/healthz"},
        basic_paths={f"{base}/setup/qr"},
    )


def main() -> None:
    import uvicorn

    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if len(token) < MIN_TOKEN_LEN:
        raise SystemExit(f"MCP_AUTH_TOKEN is missing or shorter than {MIN_TOKEN_LEN} characters")
    try:
        host = resolve_host(os.getenv("WHATSAPP_MCP_HOST"))
        port = resolve_port(os.getenv("WHATSAPP_MCP_PORT"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    public_host = os.getenv("PUBLIC_HOST", "").strip()
    base = normalize_base_path(os.getenv("BASE_PATH"))
    app = build_app(token, public_host, base, port)
    # Keep the token out of the environment of anything this process starts.
    os.environ.pop("MCP_AUTH_TOKEN", None)
    print(f"WhatsApp MCP (remote) on http://{host}:{port}{base}/mcp", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
