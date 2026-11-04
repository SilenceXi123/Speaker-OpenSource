"""Fixed voice-data lifetime; reading or reusing an asset never extends it."""
from datetime import datetime, timedelta, timezone

VOICE_RETENTION_HOURS = 72
VOICE_RETENTION = timedelta(hours=VOICE_RETENTION_HOURS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def voice_retention_cutoff(now: datetime | None = None) -> datetime:
    return normalize_utc(now or utc_now()) - VOICE_RETENTION


def voice_expires_at(created_at: datetime) -> datetime:
    return normalize_utc(created_at) + VOICE_RETENTION


def voice_is_expired(created_at: datetime, now: datetime | None = None) -> bool:
    return normalize_utc(created_at) <= voice_retention_cutoff(now)
