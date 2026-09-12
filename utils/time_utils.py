# utils/time_utils.py


from datetime import time
from typing import Iterable, List, Optional

from models.time_preferences import Day, StaffBlocker, TimePreference

# The nominal teaching grid: Sun-Thu 09:00-19:00 in whole 2h periods. A
# declared fixed slot must be exactly one of these periods.
GRID_SLOT_START_MINUTES = (540, 660, 780, 900, 1020)
GRID_SLOT_MINUTES = 120
GRID_LAST_DAY_VALUE = 4  # Day.THURSDAY - the teaching week ends here


def to_minutes(t: time) -> int:
    """Minutes since midnight - the canonical form for all interval arithmetic."""
    return t.hour * 60 + t.minute


def add_minutes(t: time, delta: int) -> Optional[time]:
    """Advance a time by `delta` minutes, or None if it would run past midnight."""
    total = to_minutes(t) + delta
    if total > 24 * 60:
        return None
    if total == 24 * 60:
        return time(23, 59)
    return time(total // 60, total % 60)


def intervals_overlap(
    a_start: time, a_end: time, b_start: time, b_end: time
) -> bool:
    """
    True when two same-day intervals share any time at all.

    Half-open comparison, so a session ending at 11:00 does not collide with one
    starting at 11:00. Sessions are compared as intervals rather than by start
    time alone, so overlapping availability windows cannot hide a double booking.
    """
    return to_minutes(a_start) < to_minutes(b_end) and to_minutes(
        b_start
    ) < to_minutes(a_end)


def window_covers(
    win_start: time, win_end: time, slot_start: time, slot_end: time
) -> bool:
    """True when [slot_start, slot_end] fits entirely inside [win_start, win_end]."""
    return (
        to_minutes(win_start) <= to_minutes(slot_start)
        and to_minutes(win_end) >= to_minutes(slot_end)
    )


def preference_covers_slot(pref: TimePreference, slot: TimePreference) -> bool:
    """
    True when an availability/preference window contains the whole session.

    Windows are stored as free-form {day, startTime, endTime} rows, so a single
    09:00-17:00 row has to count as cover for every session inside it - matching
    on start time alone would reject all but the 09:00 one.
    """
    return pref.day == slot.day and window_covers(
        pref.start_time, pref.end_time, slot.start_time, slot.end_time
    )


def slot_set_permits(
    allowed_slots: Optional[List[TimePreference]], slot: TimePreference
) -> bool:
    """
    True when a candidate slot sits wholly inside one of a course's declared
    fixed time slots. A None/empty set constrains nothing - the historical
    free-placement behaviour.

    This is the single membership test shared by the solver, the greedy
    fallback, and the change validator, so a declared set is a hard
    restriction everywhere in lockstep.
    """
    if not allowed_slots:
        return True
    return any(preference_covers_slot(allowed, slot) for allowed in allowed_slots)


def blocker_overlaps_slot(
    blocker: StaffBlocker, slot: TimePreference
) -> bool:
    """
    True when a session shares ANY time with a staff blocker.

    The mirror image of preference_covers_slot: availability windows bind by
    containment, blockers forbid by overlap - touching the blocked interval
    at all disqualifies the slot. Shared by the solver, the greedy fallback,
    the constraint manager, the validator, and the change validator.
    """
    return blocker.day == slot.day and intervals_overlap(
        blocker.start_time, blocker.end_time, slot.start_time, slot.end_time
    )


def staff_is_blocked(
    blockers: Iterable[StaffBlocker], slot: TimePreference
) -> bool:
    """True when any of the person's blockers overlaps the candidate slot."""
    return any(blocker_overlaps_slot(blocker, slot) for blocker in blockers)


def convert_api_day_to_enum(day_str: str) -> Day:
    """Convert day string from API to Day enum"""
    day_str = day_str.upper()
    for day in Day:
        if day.name == day_str:
            return day
    raise ValueError(f"Unknown day: {day_str}")


def convert_api_time_to_time_object(time_str: str) -> time:
    """Convert time string from API (HH:MM format) to time object"""
    hours, minutes = map(int, time_str.split(":"))
    return time(hours, minutes)


def convert_api_time_preference(time_pref_data: dict) -> TimePreference:
    """Convert API time preference to TimePreference object"""
    day = convert_api_day_to_enum(time_pref_data["day"])
    start_time = convert_api_time_to_time_object(time_pref_data["startTime"])
    end_time = convert_api_time_to_time_object(time_pref_data["endTime"])

    return TimePreference(day, start_time, end_time)


def convert_api_blocker(blocker_data: dict) -> StaffBlocker:
    """Convert an API blocker row ({day, startTime, endTime, label}) to a
    StaffBlocker. Same strictness as availability parsing: an unknown day
    raises, so malformed data surfaces loudly instead of silently
    unblocking time."""
    day = convert_api_day_to_enum(blocker_data["day"])
    start_time = convert_api_time_to_time_object(blocker_data["startTime"])
    end_time = convert_api_time_to_time_object(blocker_data["endTime"])

    return StaffBlocker(day, start_time, end_time, str(blocker_data.get("label", "")))
