"""Per-team voice budget (req #548, d#1118) and post-call metering (req #549).

Redis is `FakeVoiceRedis` (`tests/support/fake_redis.py`), patched over
`app.rate_limit.redis_client` by the shared `fake_redis` fixture — the shared
Redis on :6379 belongs to another project and must never see test keys.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest
from fastapi import HTTPException
from hypothesis import given
from hypothesis import strategies as st

from app.config import settings
from app.voice import budget
from tests.support.fake_redis import FakeVoiceRedis

_TEAM = uuid.UUID("70ce7004" + uuid.uuid4().hex[8:])


def _at(year: int, month: int, day: int = 1, hour: int = 0, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC)


def _refusal(now: dt.datetime) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(budget.check_session_budget(_TEAM, now=now))
    return caught.value


def test_starts_under_the_hourly_limit_are_allowed_and_the_next_is_429(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_session_starts_per_hour", 3)
    now = _at(2026, 10, 13, 14, 20)

    for _ in range(3):
        asyncio.run(budget.check_session_budget(_TEAM, now=now))
    refused = _refusal(now)

    assert refused.status_code == 429
    assert refused.headers == {"Retry-After": str(40 * 60)}
    key = f"voice:starts:{_TEAM}:2026101314"
    assert fake_redis.values[key] == "4"
    assert fake_redis.ttls[key] == 3600


def test_hourly_window_resets_in_the_next_hour(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_session_starts_per_hour", 1)
    asyncio.run(budget.check_session_budget(_TEAM, now=_at(2026, 10, 13, 14, 59)))

    asyncio.run(budget.check_session_budget(_TEAM, now=_at(2026, 10, 13, 15, 0)))


def test_monthly_minutes_at_the_cap_refuse_with_seconds_until_next_month(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 2)
    fake_redis.values[f"voice:secs:{_TEAM}:2026-12"] = "120"
    now = _at(2026, 12, 31, 23, 59)

    refused = _refusal(now)

    assert refused.status_code == 429
    assert refused.headers == {"Retry-After": "60"}
    # A refused start never spends an hourly slot.
    assert not any(k.startswith("voice:starts:") for k in fake_redis.values)


def test_monthly_minutes_just_under_the_cap_are_allowed(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 2)
    fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] = "119"

    asyncio.run(budget.check_session_budget(_TEAM, now=_at(2026, 10, 13)))


def test_last_months_seconds_do_not_count_against_this_month(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 2)
    fake_redis.values[f"voice:secs:{_TEAM}:2026-09"] = "9999"

    asyncio.run(budget.check_session_budget(_TEAM, now=_at(2026, 10, 1)))


def test_zero_minute_cap_turns_voice_off(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 0)

    assert _refusal(_at(2026, 10, 13)).status_code == 429


def test_redis_outage_fails_closed_with_a_warning_and_an_alert(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(budget, "alert_redis_unavailable", lambda **kw: alerts.append(kw))
    fake_redis.fail = True

    with caplog.at_level("WARNING", logger="app.voice.budget"):
        refused = _refusal(_at(2026, 10, 13))

    assert refused.status_code == 503
    assert refused.detail == "voice unavailable"
    assert alerts == [{"scope": "voice:budget", "fail_closed": True}]
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert str(_TEAM) not in caplog.text


def test_record_call_seconds_counts_a_conversation_once(fake_redis: FakeVoiceRedis) -> None:
    now = _at(2026, 10, 13)

    first = asyncio.run(
        budget.record_call_seconds(_TEAM, 42, conversation_id="conv_1", now=now)
    )
    second = asyncio.run(
        budget.record_call_seconds(_TEAM, 42, conversation_id="conv_1", now=now)
    )

    assert (first, second) == (True, False)
    assert fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] == "42"
    assert fake_redis.ttls["voice:webhook:conv_1"] == 604800


def test_record_call_seconds_adds_up_distinct_conversations(fake_redis: FakeVoiceRedis) -> None:
    now = _at(2026, 10, 13)
    asyncio.run(budget.record_call_seconds(_TEAM, 30, conversation_id="a", now=now))
    asyncio.run(budget.record_call_seconds(_TEAM, 45, conversation_id="b", now=now))

    assert fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] == "75"


def test_failed_increment_releases_the_dedupe_marker_so_a_retry_counts(
    fake_redis: FakeVoiceRedis,
) -> None:
    now = _at(2026, 10, 13)
    fake_redis.fail_on = {"incrby"}
    with pytest.raises(ConnectionError):
        asyncio.run(budget.record_call_seconds(_TEAM, 30, conversation_id="c", now=now))

    fake_redis.fail_on = set()
    assert asyncio.run(budget.record_call_seconds(_TEAM, 30, conversation_id="c", now=now))
    assert fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] == "30"


def test_failed_expiry_after_the_count_keeps_the_marker_so_a_retry_cannot_double_count(
    fake_redis: FakeVoiceRedis,
) -> None:
    now = _at(2026, 10, 13)
    fake_redis.fail_on = {"expire"}

    assert asyncio.run(budget.record_call_seconds(_TEAM, 30, conversation_id="e", now=now))
    fake_redis.fail_on = set()
    assert not asyncio.run(budget.record_call_seconds(_TEAM, 30, conversation_id="e", now=now))
    assert fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] == "30"


# --- Adversarial: properties and boundaries --------------------------------

_utc_instants = st.datetimes(
    min_value=dt.datetime(2000, 1, 1), max_value=dt.datetime(2100, 1, 1), timezones=st.just(dt.UTC)
)


def _next_month_start(now: dt.datetime) -> dt.datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return dt.datetime(year, month, 1, tzinfo=dt.UTC)


@given(_utc_instants)
def test_monthly_retry_after_never_lands_before_the_new_month(now: dt.datetime) -> None:
    wait = budget.seconds_until_next_month(now)

    assert wait >= 1
    retry_at = now + dt.timedelta(seconds=wait)
    assert retry_at >= _next_month_start(now)
    assert retry_at - _next_month_start(now) < dt.timedelta(seconds=1)


@given(_utc_instants)
def test_hourly_retry_after_never_lands_before_the_next_hour(now: dt.datetime) -> None:
    wait = budget.seconds_until_next_hour(now)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)

    assert 1 <= wait <= 3600
    assert next_hour <= now + dt.timedelta(seconds=wait) < next_hour + dt.timedelta(seconds=1)


@given(_utc_instants)
def test_keys_follow_the_utc_calendar_of_the_instant(now: dt.datetime) -> None:
    assert budget.seconds_key(_TEAM, now) == f"voice:secs:{_TEAM}:{now.year:04d}-{now.month:02d}"
    assert budget.starts_key(_TEAM, now).endswith(
        f":{now.year:04d}{now.month:02d}{now.day:02d}{now.hour:02d}"
    )


def test_a_non_utc_instant_is_bucketed_by_its_utc_month(fake_redis: FakeVoiceRedis) -> None:
    """23:30 on Oct 31 in UTC-5 is already November in UTC."""
    local = dt.datetime(2026, 10, 31, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))

    asyncio.run(budget.record_call_seconds(_TEAM, 10, conversation_id="tz", now=local))

    assert fake_redis.values == {
        "voice:webhook:tz": "1",
        f"voice:secs:{_TEAM}:2026-11": "10",
    }


def test_month_rollover_starts_a_fresh_cap(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 1)
    asyncio.run(
        budget.record_call_seconds(_TEAM, 60, conversation_id="dec", now=_at(2026, 12, 31, 23, 59))
    )

    assert _refusal(_at(2026, 12, 31, 23, 59)).status_code == 429
    asyncio.run(budget.check_session_budget(_TEAM, now=_at(2027, 1, 1)))


def test_duplicate_delivery_across_a_month_boundary_still_counts_once(
    fake_redis: FakeVoiceRedis,
) -> None:
    """A retry landing next month must not credit the new month too."""
    asyncio.run(
        budget.record_call_seconds(_TEAM, 30, conversation_id="x", now=_at(2026, 10, 31, 23, 59))
    )
    again = asyncio.run(
        budget.record_call_seconds(_TEAM, 30, conversation_id="x", now=_at(2026, 11, 1, 0, 5))
    )

    assert again is False
    assert f"voice:secs:{_TEAM}:2026-11" not in fake_redis.values


def test_the_hourly_limit_is_exact_at_the_boundary(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_session_starts_per_hour", 1)
    now = _at(2026, 10, 13, 9)

    asyncio.run(budget.check_session_budget(_TEAM, now=now))

    assert _refusal(now).headers == {"Retry-After": "3600"}


# --- Monthly-only check for the Scribe route (decision 16) ------------------


def _monthly_refusal(now: dt.datetime) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(budget.check_monthly_budget(_TEAM, now=now))
    return caught.value


def test_monthly_check_refuses_at_the_cap_without_spending_a_session_start(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 2)
    fake_redis.values[f"voice:secs:{_TEAM}:2026-12"] = "120"

    refused = _monthly_refusal(_at(2026, 12, 31, 23, 59))

    assert refused.status_code == 429
    assert refused.headers == {"Retry-After": "60"}
    assert not any(k.startswith("voice:starts:") for k in fake_redis.values)


def test_monthly_check_under_the_cap_passes_and_counts_nothing(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_minutes_per_month", 2)
    monkeypatch.setattr(settings, "voice_session_starts_per_hour", 0)
    fake_redis.values[f"voice:secs:{_TEAM}:2026-10"] = "119"

    asyncio.run(budget.check_monthly_budget(_TEAM, now=_at(2026, 10, 13)))

    assert fake_redis.values == {f"voice:secs:{_TEAM}:2026-10": "119"}


def test_monthly_check_fails_closed_on_a_redis_outage(
    fake_redis: FakeVoiceRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(budget, "alert_redis_unavailable", lambda **kw: alerts.append(kw))
    fake_redis.fail = True

    refused = _monthly_refusal(_at(2026, 10, 13))

    assert refused.status_code == 503
    assert alerts == [{"scope": "voice:budget", "fail_closed": True}]
