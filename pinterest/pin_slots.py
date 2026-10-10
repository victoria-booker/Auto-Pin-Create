"""
Daily posting slots for scheduled pins.

Instead of posting the moment a variant comes due, each variant is placed in
the first free daily slot at or after its earliest allowed time, so pins
spread evenly across the day no matter how many products are queued at once.

    PIN_DAILY_SLOTS  comma-separated local times, one pin per slot (default 09:00,14:00,20:00)
    PIN_TIMEZONE     IANA timezone the slots are in (default America/Chicago)
"""
import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_SLOTS = "09:00,14:00,20:00"
DEFAULT_TIMEZONE = "America/Chicago"
MAX_DAYS_AHEAD = 366


def slot_timezone():
    return ZoneInfo(os.environ.get("PIN_TIMEZONE", DEFAULT_TIMEZONE))


def daily_slots():
    slots = []
    for value in os.environ.get("PIN_DAILY_SLOTS", DEFAULT_SLOTS).split(","):
        hour, minute = value.strip().split(":")
        slots.append(time(int(hour), int(minute)))
    if not slots:
        raise ValueError("PIN_DAILY_SLOTS must list at least one HH:MM time.")
    return sorted(slots)


def next_free_slot(earliest, occupied):
    """First slot at or after `earliest` (aware datetime) that isn't in `occupied`
    (a set of UTC datetimes). Returns a UTC datetime."""
    tz = slot_timezone()
    slots = daily_slots()
    local_earliest = earliest.astimezone(tz)
    for day_offset in range(MAX_DAYS_AHEAD):
        day = local_earliest.date() + timedelta(days=day_offset)
        for slot in slots:
            candidate = datetime.combine(day, slot, tzinfo=tz).astimezone(timezone.utc)
            if candidate >= earliest and candidate not in occupied:
                return candidate
    raise ValueError(f"No free posting slot in the next {MAX_DAYS_AHEAD} days.")
