import os
from typing import Any, Optional


from backend.http_client import api_get
from dotenv import load_dotenv

from backend.login import get_auth_token
from models.time_preferences import Day, ReservedPeriod
from utils.time_utils import convert_api_day_to_enum, convert_api_time_to_time_object


def get_schedule_settings() -> Optional[ReservedPeriod]:
    """Fetch and validate the global reserved period before scheduling begins."""
    load_dotenv()

    backend_url = os.getenv("BACKEND_URL")
    if not backend_url:
        raise RuntimeError("BACKEND_URL is required to fetch schedule settings")

    token = get_auth_token()
    if not token:
        raise RuntimeError("Unable to authenticate while fetching schedule settings")

    response = api_get(
        f"{backend_url}/schedule-settings",
        headers={
            "Accept": "application/json",
            "Accept-Language": "en",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    response.raise_for_status()

    payload: Any = response.json()
    period = payload.get("data", {}).get("reserved_period")
    if period is None:
        return None

    try:
        day = convert_api_day_to_enum(period["day"])
        start_time = convert_api_time_to_time_object(period["start_time"])
        end_time = convert_api_time_to_time_object(period["end_time"])
        label = period["label_ar"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Schedule settings response has an invalid reserved period") from error

    if day not in {
        Day.SUNDAY,
        Day.MONDAY,
        Day.TUESDAY,
        Day.WEDNESDAY,
        Day.THURSDAY,
    }:
        raise ValueError("Reserved period day must be a working day")
    if not isinstance(label, str) or not label:
        raise ValueError("Schedule settings response is missing the reserved-period label")
    if end_time <= start_time:
        raise ValueError("Reserved period end time must be after its start time")

    return ReservedPeriod(day=day, start_time=start_time, end_time=end_time)
