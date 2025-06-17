# backend/get_staff_members.py


import logging
import os
from typing import Dict, List, Optional, Union

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token, login
from models.department import Department
from models.staff_members import Lecturer, StaffMember, TeachingAssistant
from utils.api_staff import (
    convert_api_lecturer,
    convert_api_staff_member,
    convert_api_teaching_assistant,
)

# Set up logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def get_all_staff_members() -> List[StaffMember]:
    """
    Fetch all staff members (both lecturers and teaching assistants) from the API.

    Returns:
        List of StaffMember objects (mix of Lecturers and TeachingAssistants)
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    response = requests.get(f"{url}/lecturers", headers=headers)
    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        staff_data = data["data"]
    else:
        # Handle the case where the response structure is different
        staff_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(f"Raw API response contained {len(staff_data)} staff members")

    # Convert API data to staff member objects
    staff_members = []
    for member_data in staff_data:
        try:
            staff_member = convert_api_staff_member(member_data)
            staff_members.append(staff_member)
            logging.debug(
                f"Converted {type(staff_member).__name__}: {staff_member.name}"
            )
        except Exception as e:
            logging.error(
                f"Error converting staff member {member_data.get('name', 'unknown')}: {str(e)}"
            )

    # Log statistics
    lecturers_count = sum(1 for m in staff_members if isinstance(m, Lecturer))
    tas_count = sum(1 for m in staff_members if isinstance(m, TeachingAssistant))
    logging.debug(
        f"Converted {lecturers_count} lecturers and {tas_count} teaching assistants"
    )

    return staff_members


def get_lecturers() -> List[Lecturer]:
    """
    Fetch only lecturers from the API.

    Returns:
        List of Lecturer objects
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    # Try the specific lecturers endpoint first
    response = requests.get(
        f"{url}/get-lecturers-ByType?type=lecturers", headers=headers
    )

    # If specific endpoint fails or returns empty, try filtering all staff
    if response.status_code != 200:
        logging.warning(
            f"Specific lecturers endpoint failed with status {response.status_code}, falling back to filtering all staff"
        )
        all_staff = get_all_staff_members()
        return [member for member in all_staff if isinstance(member, Lecturer)]

    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        lecturer_data = data["data"]
    else:
        # Handle the case where the response structure is different
        lecturer_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(f"Raw API response contained {len(lecturer_data)} lecturers")

    # Convert API data to Lecturer objects
    lecturers = []
    for lecturer_info in lecturer_data:
        try:
            lecturer = convert_api_lecturer(lecturer_info)
            lecturers.append(lecturer)
            logging.debug(f"Converted lecturer: {lecturer.name}")
        except Exception as e:
            logging.error(
                f"Error converting lecturer {lecturer_info.get('name', 'unknown')}: {str(e)}"
            )

    return lecturers


def get_teaching_assistants() -> List[TeachingAssistant]:
    """
    Fetch only teaching assistants from the API.

    Returns:
        List of TeachingAssistant objects
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    # Try the specific teaching assistants endpoint first
    response = requests.get(
        f"{url}/get-lecturers-ByType?type=teaching_assistant", headers=headers
    )

    # If specific endpoint fails or returns empty, try filtering all staff
    if response.status_code != 200:
        logging.warning(
            f"Specific teaching assistants endpoint failed with status {response.status_code}, falling back to filtering all staff"
        )
        all_staff = get_all_staff_members()
        return [member for member in all_staff if isinstance(member, TeachingAssistant)]

    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        ta_data = data["data"]
    else:
        # Handle the case where the response structure is different
        ta_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(f"Raw API response contained {len(ta_data)} teaching assistants")

    # Convert API data to TeachingAssistant objects
    teaching_assistants = []
    for ta_info in ta_data:
        try:
            ta = convert_api_teaching_assistant(ta_info)
            teaching_assistants.append(ta)
            logging.debug(f"Converted teaching assistant: {ta.name}")
        except Exception as e:
            logging.error(
                f"Error converting teaching assistant {ta_info.get('name', 'unknown')}: {str(e)}"
            )

    return teaching_assistants


def get_staff_member_by_id(staff_id: int) -> Optional[StaffMember]:
    """
    Get a staff member by their ID.

    Args:
        staff_id: The staff member ID to find

    Returns:
        StaffMember object or None if not found
    """
    all_staff = get_all_staff_members()
    for member in all_staff:
        if member.id == staff_id:
            return member
    return None


def get_staff_by_department(department: Department) -> List[StaffMember]:
    """
    Get all staff members in a specific department.

    Args:
        department: The Department enum value

    Returns:
        List of StaffMember objects in the specified department
    """
    all_staff = get_all_staff_members()
    return [member for member in all_staff if member.department == department]


if __name__ == "__main__":
    # Test the functions
    all_staff = get_all_staff_members()
    print(f"Retrieved {len(all_staff)} total staff members")

    lecturers = get_lecturers()
    print(f"Retrieved {len(lecturers)} lecturers")

    tas = get_teaching_assistants()
    print(f"Retrieved {len(tas)} teaching assistants")

    # Print detailed information for verification
    print("\nSTAFF DETAILS:")
    for i, member in enumerate(all_staff[:3]):  # Print first 3 for brevity
        print(f"\n{i+1}. {member.name} (ID: {member.id})")
        print(f"   Type: {type(member).__name__}")
        print(f"   Department: {member.department.name} ({member.department.name})")
        print(f"   Academic Degree: {member.academic_degree.name}")
        print(f"   Permanent: {member.is_permanent}")
        print(f"   Timing Preferences: {len(member.timing_preferences)} slots")
        if member.timing_preferences:
            days = set(pref.day.name for pref in member.timing_preferences)
            print(f"   Available days: {', '.join(days)}")

    # Test department filtering if possible
    if all_staff:
        first_dept = all_staff[0].department
        dept_staff = get_staff_by_department(first_dept)
        print(f"\nStaff in department {first_dept.name}: {len(dept_staff)} members")
