from datetime import time

from models.time_preferences import Day, TimePreference


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
