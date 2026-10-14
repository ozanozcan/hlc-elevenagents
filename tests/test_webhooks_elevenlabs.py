"""POST /webhooks/elevenlabs — HMAC-verified post-call metering (req #549).

Redis is the in-memory `FakeVoiceRedis`; nothing here touches the shared :6379.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from app.config import settings
from app.main import app
from tests.support.fake_redis import FakeVoiceRedis

_SECRET = "wsec_test_elevenlabs_0123456789"
_TEAM = uuid.UUID("70ce7005" + uuid.uuid4().hex[8:])
_OTHER_TEAM = uuid.UUID("70ce7005" + uuid.uuid4().hex[8:])
_PATH = "/webhooks/elevenlabs"


@pytest.fixture()
def client(fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", _SECRET)
    monkeypatch.setattr(settings, "rate_limit_webhook_per_minute", 1000)
    with TestClient(app) as test_client:
        yield test_client


def _payload(
    conversation_id: str = "conv_1", seconds: Any = 22, *, event: str = "post_call_transcription"
) -> bytes:
    body = {
        "type": event,
        "event_timestamp": int(time.time()),
        "data": {
            "agent_id": "agent_x",
            "conversation_id": conversation_id,
            "status": "done",
            "metadata": {"start_time_unix_secs": int(time.time()), "call_duration_secs": seconds},
            "conversation_initiation_client_data": {
                "dynamic_variables": {"team_id": str(_OTHER_TEAM)}
            },
        },
    }
    return json.dumps(body).encode()


def _sign(body: bytes, *, t: int | None = None, secret: str = _SECRET) -> str:
    t = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v0={digest}"


def _month() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m")


def _bind(fake_redis: FakeVoiceRedis, conversation_id: str, team_id: uuid.UUID) -> None:
    fake_redis.values[f"voice:conv:{conversation_id}"] = str(team_id)


def _post(client: TestClient, body: bytes, signature: str | None) -> Any:
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["ElevenLabs-Signature"] = signature
    return client.post(_PATH, content=body, headers=headers)


def _seconds(fake_redis: FakeVoiceRedis, team_id: uuid.UUID) -> str | None:
    return fake_redis.values.get(f"voice:secs:{team_id}:{_month()}")


def test_valid_delivery_posted_twice_counts_once(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()

    first = _post(client, body, _sign(body))
    second = _post(client, body, _sign(body))

    assert first.status_code == 200
    assert first.json() == {"status": "recorded"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert _seconds(fake_redis, _TEAM) == "22"


def test_payload_naming_another_team_credits_the_mint_time_team(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()

    assert _post(client, body, _sign(body)).status_code == 200

    assert _seconds(fake_redis, _TEAM) == "22"
    assert _seconds(fake_redis, _OTHER_TEAM) is None


def _flip_last_hex(signature: str) -> str:
    last = signature[-1]
    return signature[:-1] + ("0" if last != "0" else "1")


def _no_seconds_recorded(fake_redis: FakeVoiceRedis) -> bool:
    return not any(k.startswith(("voice:secs:", "voice:webhook:")) for k in fake_redis.values)


@pytest.mark.parametrize(
    "case",
    ["flipped_hex", "future_7200", "past_1801", "missing", "malformed", "wrong_secret", "no_v0"],
)
def test_bad_signatures_are_401_and_record_nothing(
    client: TestClient, fake_redis: FakeVoiceRedis, case: str
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()
    now = int(time.time())
    signature: str | None = {
        "flipped_hex": _flip_last_hex(_sign(body)),
        "future_7200": _sign(body, t=now + 7200),
        "past_1801": _sign(body, t=now - 1801),
        "missing": None,
        "malformed": "garbage",
        "wrong_secret": _sign(body, secret="other"),
        "no_v0": f"t={now}",
    }[case]

    response = _post(client, body, signature)

    assert response.status_code == 401
    assert _no_seconds_recorded(fake_redis)


def test_any_matching_v0_entry_is_accepted(client: TestClient, fake_redis: FakeVoiceRedis) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()
    good = _sign(body)
    t_part, v0_part = good.split(",")

    response = _post(client, body, f"{t_part},v0={'0' * 64},{v0_part}")

    assert response.status_code == 200
    assert _seconds(fake_redis, _TEAM) == "22"


def test_secret_unset_is_503(
    client: TestClient, fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", "")
    body = _payload()

    response = _post(client, body, _sign(body))

    assert response.status_code == 503
    assert _no_seconds_recorded(fake_redis)


def test_unknown_conversation_is_ignored_with_200(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    body = _payload("conv_never_minted")

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert _no_seconds_recorded(fake_redis)


@pytest.mark.parametrize("event", ["post_call_audio", "call_initiation_failure"])
def test_other_event_types_are_ignored_with_200(
    client: TestClient, fake_redis: FakeVoiceRedis, event: str
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload(event=event)

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert _no_seconds_recorded(fake_redis)


@pytest.mark.parametrize("seconds", [None, "22", -5, True, 1.5])
def test_unusable_duration_is_ignored(
    client: TestClient, fake_redis: FakeVoiceRedis, seconds: Any
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload(seconds=seconds)

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert _no_seconds_recorded(fake_redis)


@pytest.mark.parametrize(
    "data",
    [None, [], {"conversation_id": "", "metadata": {"call_duration_secs": 5}}, {"metadata": {}}],
)
def test_malformed_data_shapes_are_ignored(
    client: TestClient, fake_redis: FakeVoiceRedis, data: Any
) -> None:
    body = json.dumps({"type": "post_call_transcription", "data": data}).encode()

    response = _post(client, body, _sign(body))

    assert response.json() == {"status": "ignored"}
    assert _no_seconds_recorded(fake_redis)


def test_corrupt_conversation_binding_is_ignored(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    fake_redis.values["voice:conv:conv_1"] = "not-a-uuid"
    body = _payload()

    response = _post(client, body, _sign(body))

    assert response.json() == {"status": "ignored"}
    assert _no_seconds_recorded(fake_redis)


def test_signed_non_json_body_is_ignored(client: TestClient, fake_redis: FakeVoiceRedis) -> None:
    body = b"not json"

    response = _post(client, body, _sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_tolerance_is_inclusive_at_exactly_1800_seconds(
    client: TestClient, fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.webhooks import elevenlabs as webhook

    frozen = 1_800_000_000
    monkeypatch.setattr(webhook.time, "time", lambda: float(frozen))
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()

    assert _post(client, body, _sign(body, t=frozen - 1800)).status_code == 200
    assert _post(client, body, _sign(body, t=frozen + 1800)).status_code == 200
    assert _post(client, body, _sign(body, t=frozen + 1801)).status_code == 401


def test_per_ip_limit_is_enforced_and_fails_closed(
    client: TestClient, fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_webhook_per_minute", 1)
    body = _payload("conv_unknown")

    assert _post(client, body, _sign(body)).status_code == 200
    assert _post(client, body, _sign(body)).status_code == 429
    assert any(k.startswith("rl:webhook-elevenlabs:ip:") for k in fake_redis.values)

    fake_redis.fail = True
    assert _post(client, body, _sign(body)).status_code == 503


def test_unsigned_and_forged_posts_never_spend_the_per_ip_window(
    client: TestClient, fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind a tunnel every post may share one source IP: a flood of bad
    signatures must not 429 the signed delivery that follows."""
    monkeypatch.setattr(settings, "rate_limit_webhook_per_minute", 1)
    _bind(fake_redis, "conv_1", _TEAM)
    body = _payload()

    refused = [
        _post(client, body, None),
        _post(client, body, _sign(body, secret="wsec_forged")),
        _post(client, body, "t=1,v0=00"),
    ]

    assert [r.status_code for r in refused] == [401, 401, 401]
    assert not any(k.startswith("rl:webhook-elevenlabs:") for k in fake_redis.values)
    signed = _post(client, body, _sign(body))
    assert signed.status_code == 200
    assert signed.json() == {"status": "recorded"}


def test_unsigned_post_is_401_even_while_redis_is_down(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    fake_redis.fail = True
    body = _payload()

    assert _post(client, body, None).status_code == 401


def test_redis_down_after_verification_is_503_so_elevenlabs_retries(
    client: TestClient, fake_redis: FakeVoiceRedis
) -> None:
    _bind(fake_redis, "conv_1", _TEAM)
    fake_redis.fail_on = {"get"}
    body = _payload()

    response = _post(client, body, _sign(body))

    assert response.status_code == 503


def test_tunnel_guard_lets_the_webhook_through_on_the_public_host(
    client: TestClient, fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_tunnel_host", "demo.ngrok.app")
    body = _payload("conv_unknown")
    headers = {"host": "demo.ngrok.app", "ElevenLabs-Signature": _sign(body)}

    allowed = client.post(_PATH, content=body, headers=headers)
    blocked = client.post("/webhooks/workos", content=body, headers=headers)

    assert allowed.status_code == 200
    assert blocked.status_code == 404


# --- Adversarial: the verifier on its own -----------------------------------

_NOW = 1_800_000_000


def _verify(body: bytes, header: str, now: float = _NOW) -> bool:
    from fastapi import HTTPException

    from app.api.webhooks.elevenlabs import verify_signature

    try:
        verify_signature(body, header, _SECRET, now=now)
    except HTTPException as exc:
        assert exc.status_code == 401
        return False
    return True


@given(body=st.binary(max_size=512), skew=st.integers(min_value=-1800, max_value=1800))
def test_any_body_signed_within_tolerance_verifies(body: bytes, skew: int) -> None:
    assert _verify(body, _sign(body, t=_NOW + skew))


@given(body=st.binary(max_size=512), skew=st.integers(min_value=1801, max_value=10**9))
def test_any_timestamp_outside_tolerance_is_refused_in_both_directions(
    body: bytes, skew: int
) -> None:
    assert not _verify(body, _sign(body, t=_NOW + skew))
    assert not _verify(body, _sign(body, t=_NOW - skew))


@given(body=st.binary(min_size=1, max_size=256), data=st.data())
def test_any_single_byte_change_to_the_body_is_refused(body: bytes, data: st.DataObject) -> None:
    index = data.draw(st.integers(min_value=0, max_value=len(body) - 1))
    tampered = bytearray(body)
    tampered[index] ^= data.draw(st.integers(min_value=1, max_value=255))

    assert not _verify(bytes(tampered), _sign(body, t=_NOW))


@given(header=st.text(max_size=200))
def test_arbitrary_headers_never_verify_and_never_crash(header: str) -> None:
    assert not _verify(b"{}", header)


@pytest.mark.parametrize(
    "header",
    [
        "t=-5,v0=abc",
        "t=1e9,v0=abc",
        "t= ,v0=abc",
        "v0=abc,t=",
        "t=1800000000,v1=abc",
        "t=１８００００００００,v0=abc",
        "",
        ",,,",
    ],
)
def test_malformed_headers_are_refused(header: str) -> None:
    assert not _verify(b"{}", header)


def test_absurdly_long_timestamp_is_401_not_500() -> None:
    """int() refuses >4300 digits with ValueError; that must not escape as a 500."""
    assert not _verify(b"{}", "t=" + "9" * 5000 + ",v0=abc")


def test_non_ascii_digit_timestamp_with_a_valid_hmac_is_refused() -> None:
    """str.isdigit() accepts full-width digits and int() parses them: only ASCII counts."""
    body = b"{}"
    t = "".join(chr(ord("０") + int(c)) for c in str(_NOW))
    digest = hmac.new(_SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()

    assert not _verify(body, f"t={t},v0={digest}")


@pytest.mark.parametrize("good_first", [True, False])
def test_a_matching_v0_verifies_wherever_it_sits_among_others(good_first: bool) -> None:
    body = b"{}"
    t_part, good = _sign(body, t=_NOW).split(",")
    bad = "v0=" + "0" * 64
    entries = [good, bad] if good_first else [bad, good]

    assert _verify(body, ",".join([t_part, *entries]))


def test_header_entry_order_and_spaces_do_not_matter() -> None:
    body = b'{"type":"x"}'
    t_part, v0_part = _sign(body, t=_NOW).split(",")

    assert _verify(body, f" {v0_part} , {t_part} ")


def test_uppercase_hex_digest_is_refused() -> None:
    """The digest is compared byte-for-byte as hexdigest() emits it (lower-case)."""
    body = b"{}"
    t_part, v0_part = _sign(body, t=_NOW).split(",")

    assert not _verify(body, f"{t_part},{v0_part.upper().replace('V0=', 'v0=')}")
