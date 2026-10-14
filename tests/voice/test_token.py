"""HLC voice token: mint and verify (d#1112, req #543).

The token is the only place a `/voice-tools` route learns its tenant, so every
way of forging or replaying one must fail closed with `InvalidVoiceToken`.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid

import jwt
import pytest

from app.config import Settings, settings
from app.voice.token import InvalidVoiceToken, mint_voice_token, verify_voice_token

_SECRET = "test-voice-token-secret-0123456789abcdef"
_TEAM = uuid.UUID("70ce7001-0000-4000-8000-000000000001")


@pytest.fixture(autouse=True)
def _voice_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_token_secret", _SECRET)


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_round_trip_carries_team_conversation_and_scopes() -> None:
    token = mint_voice_token(_TEAM, conversation_id="conv_123")

    claims = verify_voice_token(token)

    assert claims.team_id == _TEAM
    assert claims.conversation_id == "conv_123"
    assert claims.scopes == frozenset({"plays:read", "plays:draw"})
    assert len(claims.jti) == 32


def test_minted_claims_match_the_d1112_shape() -> None:
    now = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5)
    token = mint_voice_token(_TEAM, conversation_id=None, now=now)

    payload = jwt.decode(token, _SECRET, algorithms=["HS256"], audience="hlc-voice-tools")

    assert payload["iss"] == "hlc"
    assert payload["sub"] == str(_TEAM)
    assert payload["exp"] - payload["iat"] == 900
    assert "cid" not in payload
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_token_minted_16_minutes_ago_is_rejected() -> None:
    minted = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=16)
    token = mint_voice_token(_TEAM, conversation_id=None, now=minted)

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)


def test_ttl_is_capped_at_900_seconds_even_if_configured_higher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "voice_token_ttl_seconds", 3600)
    token = mint_voice_token(_TEAM, conversation_id=None)

    payload = jwt.decode(token, _SECRET, algorithms=["HS256"], audience="hlc-voice-tools")

    assert payload["exp"] - payload["iat"] == 900


def test_sub_edited_after_signing_is_rejected() -> None:
    header, _payload, signature = mint_voice_token(_TEAM, conversation_id=None).split(".")
    payload = jwt.decode(
        mint_voice_token(_TEAM, conversation_id=None),
        _SECRET,
        algorithms=["HS256"],
        audience="hlc-voice-tools",
    )
    payload["sub"] = str(uuid.uuid4())
    forged = f"{header}.{_b64(payload)}.{signature}"

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(forged)


def test_wrong_audience_is_rejected() -> None:
    now = dt.datetime.now(dt.UTC)
    token = jwt.encode(
        {
            "iss": "hlc",
            "aud": "other",
            "sub": str(_TEAM),
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 600,
            "jti": uuid.uuid4().hex,
            "scp": ["plays:read"],
        },
        _SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)


def test_alg_none_token_is_rejected() -> None:
    now = int(dt.datetime.now(dt.UTC).timestamp())
    payload = {
        "iss": "hlc",
        "aud": "hlc-voice-tools",
        "sub": str(_TEAM),
        "iat": now,
        "exp": now + 600,
        "jti": uuid.uuid4().hex,
        "scp": ["plays:read"],
    }
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(payload)}."

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)


def test_token_signed_with_another_secret_is_rejected() -> None:
    now = int(dt.datetime.now(dt.UTC).timestamp())
    token = jwt.encode(
        {"iss": "hlc", "aud": "hlc-voice-tools", "sub": str(_TEAM), "iat": now, "exp": now + 600},
        "some-other-secret-0123456789abcdef0123",
        algorithm="HS256",
    )

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)


def test_non_uuid_subject_is_rejected() -> None:
    now = int(dt.datetime.now(dt.UTC).timestamp())
    token = jwt.encode(
        {"iss": "hlc", "aud": "hlc-voice-tools", "sub": "team-a", "iat": now, "exp": now + 600},
        _SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)


def test_garbage_is_rejected() -> None:
    with pytest.raises(InvalidVoiceToken):
        verify_voice_token("not-a-jwt")


def test_unset_secret_refuses_to_mint_or_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    token = mint_voice_token(_TEAM, conversation_id=None)
    monkeypatch.setattr(settings, "voice_token_secret", "")

    with pytest.raises(InvalidVoiceToken):
        verify_voice_token(token)
    with pytest.raises(InvalidVoiceToken):
        mint_voice_token(_TEAM, conversation_id=None)


def test_bare_settings_defaults_hold_d1118(monkeypatch: pytest.MonkeyPatch) -> None:
    # `load_dotenv()` in app.config may have exported real values; clear them.
    for name in Settings.model_fields:
        if name.startswith(("voice_", "elevenlabs_")):
            monkeypatch.delenv(name.upper(), raising=False)

    bare = Settings(_env_file=None)  # type: ignore[call-arg]

    assert bare.voice_session_starts_per_hour == 30
    assert bare.voice_minutes_per_month == 120
    assert bare.voice_token_ttl_seconds == 900
    assert bare.elevenlabs_stt_model == "scribe_v2"
    assert bare.voice_stt_per_minute == 10
    assert bare.elevenlabs_api_key == ""
    assert bare.voice_tunnel_host == ""
