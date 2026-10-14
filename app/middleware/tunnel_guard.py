"""Tunnel guard: the public tunnel host only reaches the voice tool surface (d#1113).

The live demo exposes the dev backend through an ngrok static domain so
ElevenLabs can call `/voice-tools/*` and `/webhooks/elevenlabs`. The dev
backend runs stub auth (`app/auth/dependencies.py`), so anything else reached
through that host would be a team admin session open to the internet.

Fails closed: when `settings.voice_tunnel_host` is set, any request whose
`Host` or `X-Forwarded-Host` (port stripped, lower-cased, trailing '.' dropped)
is not local (loopback, a private or link-local IP, or a `*.local` name — a
phone on the LAN reaches FastAPI through Next's rewrite with its LAN host in
`X-Forwarded-Host`) may reach only the allow-listed paths; everything else is
404. Matching the configured host is not required, so a tunnel restarted on a
different or rotated domain is still guarded. Unset, this is a no-op (dev
without a tunnel).

Relies on the tunnel delivering the public Host (or keeping it in
X-Forwarded-Host): start ngrok without `--host-header`.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})

logger = logging.getLogger(__name__)


def _normalize(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # [v6]:port
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:  # name:port (a bare v6 literal has several colons)
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def _is_local(host: str) -> bool:
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _path_allowed(path: str) -> bool:
    if path.startswith("/voice-tools/"):
        return True
    return path == "/webhooks/elevenlabs" or path.startswith("/webhooks/elevenlabs/")


class TunnelGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.voice_tunnel_host.strip() or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # X-Forwarded-Host may be a comma-separated chain; any hop counts.
        hosts = {
            _normalize(part)
            for name, value in scope.get("headers", [])
            if name in (b"host", b"x-forwarded-host")
            for part in value.decode("latin-1").split(",")
        }
        # No Host at all is not local either: guarded.
        is_local = bool(hosts) and all(_is_local(h) for h in hosts)
        if not is_local and not _path_allowed(scope.get("path", "")):
            logger.warning(
                "tunnel guard denied %s for hosts %s", scope.get("path", ""), sorted(hosts)
            )
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return

        await self.app(scope, receive, send)
