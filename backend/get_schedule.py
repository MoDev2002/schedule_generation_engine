import os
from typing import Any, Dict


from backend.http_client import api_get
from dotenv import load_dotenv

from backend.login import get_auth_token


def get_schedule_generation_context(schedule_id: int) -> Dict[str, Any]:
    """Fetch the normalized source entries used to continue a schedule."""
    load_dotenv()

    response = api_get(
        f"{os.getenv('BACKEND_URL')}/schedules/{schedule_id}/generation-context",
        headers={
            "Accept": "application/json",
            "Accept-Language": "en",
            "Authorization": f"Bearer {get_auth_token()}",
        },
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ValueError("Schedule generation context has an unexpected response shape")

    return data["data"]
