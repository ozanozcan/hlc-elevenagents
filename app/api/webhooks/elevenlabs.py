"""Signed ElevenLabs post-call webhook: meters real call seconds (req #549).

Mirrors `app/api/webhooks/workos.py` with ElevenLabs' scheme
(https://elevenlabs.io/docs/eleven-api/resources/webhooks; elevenlabs-python
v2.71.0 `webhooks_custom.py`): header `ElevenLabs-Signature: t=<unix s>,v0=<hex>`
where `v0 = HMAC-SHA256(secret, f"{t}.{raw_body}")`. Unlike the SDK's
`construct_event`, the compare is constant-time and the timestamp is bounded in
**both** directions (±1800 s).

The team is never read from the payload — its dynamic variables came from the
browser. It comes from `voice:conv:{conversation_id}`, written when the session
was minted. Expected non-events answer 200 `ignored`, because ElevenLabs
disables a webhook after repeated failures; only a Redis outage after
verification answers 503, so the (idempotent) retry can still count the call.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from app import rate_limit
from app.api.v1.voice import conversation_key
from app.config import settings
from app.voice.budget import record_call_seconds

logger = logging.getLogger(__name__)

router = APIRouter()

SIGNATURE_HEADER = "elevenlabs-signature"
TOLERANCE_SECONDS = 1800
_TRANSCRIPTION_EVENT = "post_call_transcription"
_UNIX_SECONDS = re.compile(r"[0-9]{1,12}")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="invalid signature")


def verify_signature(
    raw_body: bytes, header: str, secret: str, *, now: float | None = None
) -> None:
    """Raise 401 unless some `v0` in `header` signs `raw_body` within ±TOLERANCE_SECONDS."""
    timestamp: str | None = None
    candidates: list[str] = []
    for chunk in header.split(","):
        key, _, value = chunk.strip().partition("=")
        if key == "t":
            timestamp = value.strip()
        elif key == "v0":
            candidates.append(value.strip())
    # ASCII digits only (str.isdigit accepts full-width ones) and bounded, so int()
    # can neither parse a look-alike nor raise on a 5000-digit value.
    if timestamp is None or not _UNIX_SECONDS.fullmatch(timestamp) or not candidates:
        raise _unauthorized()
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > TOLERANCE_SECONDS:
        raise _unauthorized()

    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    matched = False
    for candidate in candidates:
        matched |= hmac.compare_digest(expected.encode(), candidate.encode())
    if not matched:
        raise _unauthorized()


def _call_seconds(payload: Any) -> tuple[str, int] | None:
    """`(conversation_id, data.metadata.call_duration_secs)` of a transcription event."""
    if not isinstance(payload, dict) or payload.get("type") != _TRANSCRIPTION_EVENT:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    conversation_id = data.get("conversation_id")
    metadata = data.get("metadata")
    seconds = metadata.get("call_duration_secs") if isinstance(metadata, dict) else None
    if not isinstance(conversation_id, str) or not conversation_id:
        return None
    if type(seconds) is not int or seconds < 0:
        return None
    return conversation_id, seconds


@router.post("/webhooks/elevenlabs")
async def receive_elevenlabs_webhook(request: Request) -> dict[str, str]:
    if not settings.elevenlabs_webhook_secret:
        raise HTTPException(status_code=503, detail="webhooks not configured")

    raw_body = await request.body()
    verify_signature(
        raw_body, request.headers.get(SIGNATURE_HEADER, ""), settings.elevenlabs_webhook_secret
    )
    # After the HMAC: behind the tunnel every post can share one source IP, so an
    # unsigned flood must not spend the window signed deliveries need.
    await rate_limit.enforce_rate_limit(
        key=f"rl:webhook-elevenlabs:ip:{rate_limit.client_ip(request)}",
        limit=settings.rate_limit_webhook_per_minute,
        fail_closed=True,
    )

    try:
        payload = json.loads(raw_body)
    except ValueError:
        return {"status": "ignored"}
    call = _call_seconds(payload)
    if call is None:
        return {"status": "ignored"}
    conversation_id, seconds = call

    try:
        async with rate_limit.redis_client() as client:
            bound = await client.get(conversation_key(conversation_id))
        if bound is None:
            return {"status": "ignored"}
        try:
            team_id = UUID(str(bound))
        except ValueError:
            return {"status": "ignored"}
        counted = await record_call_seconds(team_id, seconds, conversation_id=conversation_id)
    except Exception:
        logger.warning("elevenlabs webhook not recorded: Redis unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="temporarily unavailable") from None
    return {"status": "recorded" if counted else "duplicate"}
