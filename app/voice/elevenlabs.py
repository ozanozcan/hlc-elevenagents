"""ElevenLabs HTTP calls. The API key is used here and nowhere else.

`create_conversation_token` asks ElevenLabs for a WebRTC conversation token
(`GET /v1/convai/conversation/token?agent_id=…` → `{token, conversation_id}`,
both required per https://elevenlabs.io/docs/api-reference/conversations/get-webrtc-token,
checked 2026-10-08). The browser gets the token; the key never leaves the
backend (req #542).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from app.config import settings

ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
REQUEST_TIMEOUT_SECONDS = 5.0


class VoiceNotConfigured(Exception):
    """API key or agent id unset — no outbound call is attempted."""


class UpstreamError(Exception):
    """ElevenLabs failed or answered with something unusable. Never carries their body."""


@dataclass(frozen=True)
class ConversationToken:
    token: str
    conversation_id: str | None


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)


async def create_conversation_token(
    *, client: httpx.AsyncClient | None = None
) -> ConversationToken:
    api_key, agent_id = settings.elevenlabs_api_key, settings.elevenlabs_agent_id
    if not api_key or not agent_id:
        raise VoiceNotConfigured

    owned = client is None
    http = client or new_http_client()
    try:
        response = await http.get(
            f"{ELEVENLABS_API_BASE}/v1/convai/conversation/token",
            params={"agent_id": agent_id},
            headers={"xi-api-key": api_key},
        )
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__) from None
    finally:
        if owned:
            await http.aclose()

    if not response.is_success:
        raise UpstreamError(f"status {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise UpstreamError("non-JSON body") from None
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise UpstreamError("missing token")
    conversation_id = body.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        conversation_id = None
    return ConversationToken(token=token, conversation_id=conversation_id)


# Scribe batch transcription (req #551). Multipart is built client-side by httpx,
# so the server needs no multipart dependency (plan decision 7). `keyterms` is a
# "list of string" form field in
# https://elevenlabs.io/docs/api-reference/speech-to-text/convert (checked
# 2026-10-13), sent as one repeated `keyterms` field per term.
STT_TIMEOUT_SECONDS = 30.0


def new_stt_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=STT_TIMEOUT_SECONDS)


# Upload name and type per accepted recording format (d#1120: Safari records audio/mp4).
STT_UPLOADS = {"audio/webm": ("note.webm", "audio/webm"), "audio/mp4": ("note.m4a", "audio/mp4")}
# Billed seconds when Scribe returns no word timestamps: the recording cap.
STT_FALLBACK_SECONDS = 60


@dataclass(frozen=True)
class Transcription:
    text: str
    # Audio length from the last word's `end`, rounded up; the cap when absent.
    seconds: int


def _audio_seconds(words: object) -> int:
    ends: list[float] = []
    for word in words if isinstance(words, list) else []:
        end = word.get("end") if isinstance(word, dict) else None
        if isinstance(end, int | float) and not isinstance(end, bool) and 0 < end < math.inf:
            ends.append(end)
    return math.ceil(max(ends)) if ends else STT_FALLBACK_SECONDS


async def transcribe(
    audio: bytes,
    *,
    media_type: str = "audio/webm",
    keyterms: list[str],
    language_code: str | None = "en",
) -> Transcription:
    """The Scribe transcript of a WebM/Opus or MP4 recording, with its billed seconds."""
    api_key = settings.elevenlabs_api_key
    if not api_key:
        raise VoiceNotConfigured

    data: dict[str, str | list[str]] = {
        "model_id": settings.elevenlabs_stt_model,
        "tag_audio_events": "false",
        "diarize": "false",
    }
    if keyterms:
        data["keyterms"] = keyterms
    if language_code:
        data["language_code"] = language_code

    filename, content_type = STT_UPLOADS[media_type]
    http = new_stt_http_client()
    try:
        response = await http.post(
            f"{ELEVENLABS_API_BASE}/v1/speech-to-text",
            data=data,
            files={"file": (filename, audio, content_type)},
            headers={"xi-api-key": api_key},
        )
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__) from None
    finally:
        await http.aclose()

    if not response.is_success:
        raise UpstreamError(f"status {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise UpstreamError("non-JSON body") from None
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise UpstreamError("missing text")
    return Transcription(text=text, seconds=_audio_seconds(body.get("words")))
