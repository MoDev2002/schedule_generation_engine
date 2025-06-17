# backend/post_schedule.py

import json
import logging
import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token

# Set up logging
logger = logging.getLogger("backend.post_schedule")


def post_schedule_to_backend(schedule_data: Dict[str, Any]) -> bool:
    """
    Send the schedule data to the backend via POST request.

    Args:
        schedule_data: Dictionary containing the schedule data in the required format

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("Posting schedule to backend")

    try:
        # Load environment variables
        load_dotenv()

        # Get authentication token
        token = get_auth_token()
        if not token:
            logger.error("Failed to get authentication token")
            return False

        # Prepare the request
        url = os.getenv("BACKEND_URL")
        endpoint = f"{url}/schedules"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Language": "en",
            "Authorization": f"Bearer {token}",
        }

        logger.info(f"Posting to: {endpoint}")
        logger.info(
            f"Schedule contains {len(schedule_data.get('schedule', []))} entries"
        )

        # Make the POST request
        response = requests.post(
            endpoint, headers=headers, data=json.dumps(schedule_data), timeout=30
        )

        # Check response
        if response.status_code in [200, 201]:
            logger.info("Schedule successfully posted to backend")
            try:
                response_data = response.json()
                if isinstance(response_data, dict) and "id" in response_data:
                    logger.info(f"Schedule created with ID: {response_data['id']}")

            except json.JSONDecodeError:
                logger.info("Backend returned non-JSON response")

            return True
        else:
            logger.error(f"Failed to post schedule. Status: {response.status_code}")
            logger.error(f"Response: {response.text}")

            try:
                error_data = response.json()
                logger.error(f"Error details: {error_data}")
            except json.JSONDecodeError:
                pass

            return False

    except requests.exceptions.Timeout:
        logger.error("Request timed out")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("Connection error - check if backend is running")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error posting schedule: {str(e)}")
        return False


def post_schedule_with_retry(
    schedule_data: Dict[str, Any], max_retries: int = 3
) -> bool:
    """
    Post schedule to backend with retry logic.

    Args:
        schedule_data: Dictionary containing the schedule data
        max_retries: Maximum number of retry attempts

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info(f"Attempting to post schedule with up to {max_retries} retries")

    for attempt in range(max_retries):
        logger.info(f"Attempt {attempt + 1}/{max_retries}")

        if post_schedule_to_backend(schedule_data):
            logger.info(f"Successfully posted schedule on attempt {attempt + 1}")
            return True

        if attempt < max_retries - 1:
            logger.warning(f"Attempt {attempt + 1} failed, retrying...")
        else:
            logger.error("All retry attempts failed")

    return False


def validate_schedule_data(schedule_data: Dict[str, Any]) -> bool:
    """
    Validate schedule data before sending to backend.

    Args:
        schedule_data: Dictionary containing the schedule data

    Returns:
        bool: True if valid, False otherwise
    """
    logger.info("Validating schedule data before posting")

    # Check required top-level fields
    required_fields = ["nameEn", "nameAr", "schedule"]
    for field in required_fields:
        if field not in schedule_data:
            logger.error(f"Missing required field: {field}")
            return False

    # Check schedule entries
    schedule_entries = schedule_data.get("schedule", [])
    if not isinstance(schedule_entries, list):
        logger.error("Schedule field must be a list")
        return False

    if len(schedule_entries) == 0:
        logger.warning("Schedule contains no entries")
        return False

    # Check each schedule entry
    required_entry_fields = [
        "course_id",
        "session_type",
        "group_info",
        "lecturer_id",
        "time_slot",
        "student_count",
        "academic_id",
        "academic_level",
        "department_id",
    ]

    for i, entry in enumerate(schedule_entries):
        logger.debug(f"Validating entry {i + 1}")

        for field in required_entry_fields:
            if field not in entry:
                logger.error(f"Entry {i + 1} missing required field: {field}")
                return False

        # Validate nested objects
        if not isinstance(entry.get("group_info"), dict):
            logger.error(f"Entry {i + 1}: group_info must be a dictionary")
            return False

        if not isinstance(entry.get("time_slot"), dict):
            logger.error(f"Entry {i + 1}: time_slot must be a dictionary")
            return False

        # Validate group_info structure
        group_info = entry["group_info"]
        if "group_number" not in group_info or "total_groups" not in group_info:
            logger.error(f"Entry {i + 1}: group_info missing required fields")
            return False

        # Validate time_slot structure
        time_slot = entry["time_slot"]
        required_time_fields = ["day", "start_time", "end_time"]
        for field in required_time_fields:
            if field not in time_slot:
                logger.error(
                    f"Entry {i + 1}: time_slot missing required field: {field}"
                )
                return False

    logger.info(f"Schedule data validation passed ({len(schedule_entries)} entries)")
    return True
