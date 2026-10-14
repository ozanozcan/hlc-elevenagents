"""Per-team voice budget (req #548, d#1118) and post-call metering (req #549).

Two Redis counters per team, both in UTC:

- `voice:starts:{team}:{YYYYMMDDHH}` — session starts this hour. Its own fixed
  hourly window because `enforce_rate_limit`'s window is 60 s. Counted with
  `app.rate_limit`'s atomic INCR+EXPIRE script, so a crash between the two
  can never leave a counter without an expiry.
- `voice:secs:{team}:{YYYY-MM}` — real call seconds, credited only by the
  HMAC-verified post-call webhook, once per conversation.

Redis errors on the session check fail **closed** (503) with a warning and an
alert: the session route cannot bind the conversation without Redis either.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from uuid import UUID

from fastapi import HTTPException

from app import rate_limit
from app.alerts import alert_redis_unavailable
from app.config import settings

logger = logging.getLogger(__name__)

HOUR_SECONDS = 3600
WEBHOOK_DEDUPE_SECONDS = 604800
# Outlives the longest month, so a month's total is still readable on its last day.
SECONDS_KEY_TTL = 40 * 86400
ALERT_SCOPE = "voice:budget"


def _now(now: dt.datetime | None) -> dt.datetime:
    return (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)


def seconds_key(team_id: UUID, now: dt.datetime) -> str:
    return f"voice:secs:{team_id}:{now:%Y-%m}"


def starts_key(team_id: UUID, now: dt.datetime) -> str:
    return f"voice:starts:{team_id}:{now:%Y%m%d%H}"


def seconds_until_next_month(now: dt.datetime) -> int:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    start = dt.datetime(year, month, 1, tzinfo=dt.UTC)
    return max(1, math.ceil((start - now).total_seconds()))


def seconds_until_next_hour(now: dt.datetime) -> int:
    start = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return max(1, math.ceil((start - now).total_seconds()))


def _refuse(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="voice budget exceeded",
        headers={"Retry-After": str(retry_after)},
    )


async def check_session_budget(team_id: UUID, *, now: dt.datetime | None = None) -> None:
    """429 when the team is over its monthly minutes or its hourly session starts."""
    now = _now(now)
    try:
        async with rate_limit.redis_client() as client:
            used = int(await client.get(seconds_key(team_id, now)) or 0)
            if used >= settings.voice_minutes_per_month * 60:
                raise _refuse(seconds_until_next_month(now))
            # Checked after the monthly cap so a refused start never spends a slot.
            # debt: reuses app.rate_limit's private script runner (atomic INCR+EXPIRE);
            # promote it to public API when a lane next owns app/rate_limit.py
            starts = int(
                await rate_limit._run_script(
                    client, rate_limit.FIXED_WINDOW_LUA, starts_key(team_id, now), str(HOUR_SECONDS)
                )
            )
    except HTTPException:
        raise
    except Exception:
        # Fail closed: the session route needs Redis for the conversation binding
        # anyway, so letting this check pass would buy no availability.
        logger.warning("voice budget check failed: Redis unavailable", exc_info=True)
        alert_redis_unavailable(scope=ALERT_SCOPE, fail_closed=True)
        raise HTTPException(status_code=503, detail="voice unavailable") from None
    if starts > settings.voice_session_starts_per_hour:
        raise _refuse(seconds_until_next_hour(now))


async def check_monthly_budget(team_id: UUID, *, now: dt.datetime | None = None) -> None:
    """429 when the team is over its monthly minutes. Reads only; counts nothing.

    For the Scribe route (decision 16): a voice note is not a session start, but
    its audio is billed, so the monthly cap applies. Fails closed like the
    session check — Scribe bills per audio hour.
    """
    now = _now(now)
    try:
        async with rate_limit.redis_client() as client:
            used = int(await client.get(seconds_key(team_id, now)) or 0)
    except Exception:
        logger.warning("voice budget check failed: Redis unavailable", exc_info=True)
        alert_redis_unavailable(scope=ALERT_SCOPE, fail_closed=True)
        raise HTTPException(status_code=503, detail="voice unavailable") from None
    if used >= settings.voice_minutes_per_month * 60:
        raise _refuse(seconds_until_next_month(now))


async def record_call_seconds(
    team_id: UUID, seconds: int, *, conversation_id: str, now: dt.datetime | None = None
) -> bool:
    """Credit `seconds` to the team's month once per conversation. False if already counted."""
    now = _now(now)
    marker = f"voice:webhook:{conversation_id}"
    async with rate_limit.redis_client() as client:
        if not await client.set(marker, "1", nx=True, ex=WEBHOOK_DEDUPE_SECONDS):
            return False
        key = seconds_key(team_id, now)
        try:
            await client.incrby(key, seconds)
        except Exception:
            # Release the marker so ElevenLabs' retry can still count the call.
            await client.delete(marker)
            raise
        # After the count landed: a failure here must not let a retry count it twice.
        try:
            await client.expire(key, SECONDS_KEY_TTL)
        except Exception:
            logger.warning("voice seconds key expiry not set", exc_info=True)
    return True
