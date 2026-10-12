"""Clock-time helpers.

The planner works internally in "minutes since local midnight" because that is
what OR-Tools needs (integers), while the API speaks "HH:MM" because that is what
a UI renders. These two functions are the only place that conversion happens.
"""

from __future__ import annotations

MINUTES_PER_DAY = 24 * 60


def to_minutes(clock: str) -> int:
    """Parse ``"HH:MM"`` into minutes since midnight.

    Raises:
        ValueError: when the string is not a valid 24-hour clock time.
    """
    try:
        hour_text, minute_text = clock.split(":")
        hours, minutes = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{clock!r} is not a valid HH:MM time") from exc

    # Each component is range-checked separately: "9:70" sums to a total that
    # looks valid (610) while being a nonsense clock time.
    if not 0 <= hours <= 23:
        raise ValueError(f"{clock!r} has an hour outside 00-23")
    if not 0 <= minutes <= 59:
        raise ValueError(f"{clock!r} has a minute outside 00-59")
    return hours * 60 + minutes


def to_clock(minutes: int) -> str:
    """Format minutes since midnight as ``"HH:MM"``.

    Deliberately strict: a value outside the day raises rather than wrapping.
    Wrapping silently turns a schedule that overran midnight into one whose
    activities appear to end before they start, which surfaces far away from the
    code that actually miscounted. Callers that can legitimately overrun must
    detect it themselves - see ``itinerary_service.reorder_day``.

    Raises:
        ValueError: when ``minutes`` is negative or beyond 23:59.
    """
    minutes = int(minutes)
    if not 0 <= minutes < MINUTES_PER_DAY:
        raise ValueError(
            f"{minutes} is outside a single day (0-{MINUTES_PER_DAY - 1} minutes)"
        )
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def overflows_day(minutes: int) -> bool:
    """True when ``minutes`` would fall outside the day ``to_clock`` accepts."""
    return not 0 <= int(minutes) < MINUTES_PER_DAY


def clamp_minutes(minutes: int) -> int:
    return max(0, min(MINUTES_PER_DAY - 1, int(minutes)))
