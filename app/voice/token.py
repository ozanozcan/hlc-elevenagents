"""HLC voice token: the tenant credential for `/voice-tools/*` (d#1112).

ElevenLabs server-tool calls are unsigned and dynamic variables are set by the
browser, so a plain `team_id` variable would be forgeable. Instead the session
endpoint mints this HS256 token for the caller's verified team; the browser
hands it to the agent as the `secret__hlc_voice_token` dynamic variable, which
ElevenLabs only ever places in tool headers (never in the LLM context). Tool
routes accept the tenant from this token and from nowhere else.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from uuid import UUID

import jwt

from app.config import settings

ISSUER = "hlc"
AUDIENCE = "hlc-voice-tools"
SCOPES = ("plays:read", "plays:draw")
MAX_TTL_SECONDS = 900


class InvalidVoiceToken(Exception):
    """Any reason a voice token cannot be trusted. Callers map it to a bare 401."""


@dataclass(frozen=True)
class VoiceTokenClaims:
    team_id: UUID
    conversation_id: str | None
    scopes: frozenset[str]
    jti: str


def _secret() -> str:
    if not settings.voice_token_secret:
        raise InvalidVoiceToken("voice token secret not configured")
    return settings.voice_token_secret


def mint_voice_token(
    team_id: UUID, *, conversation_id: str | None, now: dt.datetime | None = None
) -> str:
    issued = int((now or dt.datetime.now(dt.UTC)).timestamp())
    ttl = min(settings.voice_token_ttl_seconds, MAX_TTL_SECONDS)
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(team_id),
        "iat": issued,
        "exp": issued + ttl,
        "jti": uuid.uuid4().hex,
        "scp": list(SCOPES),
    }
    if conversation_id:
        payload["cid"] = conversation_id
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_voice_token(token: str, *, now: dt.datetime | None = None) -> VoiceTokenClaims:
    secret = _secret()
    options: dict[str, object] = {"require": ["exp", "iat", "sub", "aud"]}
    try:
        if now is None:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options=options,  # type: ignore[arg-type]
            )
        else:
            # PyJWT checks `exp` against the wall clock; for an injected `now`
            # verify everything else first, then check expiry against it.
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={**options, "verify_exp": False},  # type: ignore[arg-type]
            )
            if int(payload["exp"]) <= int(now.timestamp()):
                raise InvalidVoiceToken("expired")
        team_id = UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise InvalidVoiceToken(str(exc)) from exc

    scopes = payload.get("scp") or []
    if not isinstance(scopes, list):
        raise InvalidVoiceToken("malformed scopes")
    cid = payload.get("cid")
    return VoiceTokenClaims(
        team_id=team_id,
        conversation_id=str(cid) if cid else None,
        scopes=frozenset(str(s) for s in scopes),
        jti=str(payload.get("jti", "")),
    )
