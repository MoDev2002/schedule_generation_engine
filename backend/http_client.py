# backend/http_client.py


import logging
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger("backend.http")


def api_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_retries: int = 5,
    **kwargs,
) -> requests.Response:
    """
    GET with 429-aware retries.

    Study-plan resolution fires dozens of API calls in quick succession and
    trips Laravel's per-minute throttle; a single unhandled 429 there fails
    whole study plans or leaves entries unresolvable. Retry after the server's
    Retry-After hint (or a short exponential backoff) instead.
    """
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        response = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        if response.status_code != 429 or attempt == max_retries:
            return response

        retry_after = response.headers.get("Retry-After", "")
        wait = float(retry_after) if retry_after.isdigit() else delay
        logger.warning(
            "429 from %s - retrying in %.1fs (attempt %s/%s)",
            url,
            wait,
            attempt,
            max_retries,
        )
        time.sleep(wait)
        delay = min(delay * 2, 8.0)
    return response
