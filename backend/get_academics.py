# backend/get_academics.py


import logging
import os
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token, login
from models.academic_list import AcademicList, Course
from models.department import Department
from utils.api_academics import (
    convert_api_academic_list_detail,
    convert_api_academic_list_summary,
    convert_api_course,
)

# Set up logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def get_academic_lists_summary() -> List[Dict]:
    """
    Fetch summary information about all academic lists (without courses).

    Returns:
        List of dictionaries with academic list summary information
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    response = requests.get(f"{url}/academics", headers=headers)
    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        academic_lists_data = data["data"]
    else:
        # Handle the case where the response structure is different
        academic_lists_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(
        f"Raw API response contained {len(academic_lists_data)} academic lists"
    )

    # Convert API data to academic list summary dictionaries
    academic_lists = []
    for academic_list_data in academic_lists_data:
        try:
            academic_list = convert_api_academic_list_summary(academic_list_data)
            academic_lists.append(academic_list)
            logging.debug(f"Converted academic list summary: {academic_list['name']}")
        except Exception as e:
            logging.error(f"Error converting academic list summary: {str(e)}")

    return academic_lists


def get_academic_list_by_id(academic_id: int) -> Optional[AcademicList]:
    """
    Fetch detailed information about a specific academic list by ID.

    Args:
        academic_id: The academic list ID

    Returns:
        AcademicList object or None if not found
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    response = requests.get(f"{url}/academics/{academic_id}", headers=headers)

    # Check if request was successful
    if response.status_code != 200:
        logging.error(
            f"Failed to fetch academic list with ID {academic_id}: Status {response.status_code}"
        )
        return None

    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        academic_list_data = data["data"]
    else:
        logging.error(
            f"Unexpected response format for academic list with ID {academic_id}"
        )
        return None

    # Convert API data to AcademicList object
    try:
        academic_list = convert_api_academic_list_detail(academic_list_data)
        logging.debug(
            f"Converted academic list: {academic_list.name} with {len(academic_list.courses)} courses"
        )
        return academic_list
    except Exception as e:
        logging.error(f"Error converting academic list with ID {academic_id}: {str(e)}")
        return None


def get_academic_lists() -> List[AcademicList]:
    """
    Fetch detailed information about all academic lists including their courses.

    This method fetches summary information first, then fetches details for each list.

    Returns:
        List of AcademicList objects
    """
    # First, get summary information
    academic_list_summaries = get_academic_lists_summary()

    # Then, fetch detailed information for each academic list
    academic_lists = []
    for summary in academic_list_summaries:
        academic_id = summary["id"]
        academic_list = get_academic_list_by_id(academic_id)
        if academic_list:
            academic_lists.append(academic_list)

    logging.debug(
        f"Fetched and converted {len(academic_lists)} complete academic lists"
    )
    return academic_lists


def get_academic_lists_by_department(department: Department) -> List[AcademicList]:
    """
    Get all academic lists for a specific department.

    Args:
        department: The Department enum value

    Returns:
        List of AcademicList objects for the specified department
    """
    all_lists = get_academic_lists()
    return [
        academic_list
        for academic_list in all_lists
        if academic_list.department == department
    ]


def get_course_by_code(code: str) -> Optional[Course]:
    """
    Find a course by its course code across all academic lists.

    Args:
        code: The course code to find

    Returns:
        Course object or None if not found
    """
    all_lists = get_academic_lists()
    for academic_list in all_lists:
        for course in academic_list.courses:
            if course.code == code:
                return course
    return None


if __name__ == "__main__":
    # Test the summary function
    summaries = get_academic_lists_summary()
    print(f"Retrieved {len(summaries)} academic list summaries")

    # Print summary information
    print("\nACADEMIC LIST SUMMARIES:")
    for summary in summaries:
        print(f"ID: {summary['id']} - {summary['name']} ({summary['department'].name})")
        print(f"  Number of courses: {summary['number_of_courses']}")

    # Test the detailed fetch function if there are any academic lists
    if summaries:
        first_id = summaries[0]["id"]
        academic_list = get_academic_list_by_id(first_id)
        if academic_list:
            print(f"\nDETAILED ACADEMIC LIST (ID {first_id}):")
            print(f"Name: {academic_list.name}")
            print(f"Department: {academic_list.department.name}")
            print(f"Courses ({len(academic_list.courses)}):")
            for course in academic_list.courses:
                print(
                    f"  - {course.code}: {course.name_en} ({course.lecture_hours}+{course.practical_hours} hrs, {course.credit_hours} credits)"
                )

    # Test fetching all academic lists
    all_lists = get_academic_lists()
    print(f"\nRetrieved {len(all_lists)} complete academic lists")

    # Test department filtering if possible
    if all_lists:
        first_dept = all_lists[0].department
        dept_lists = get_academic_lists_by_department(first_dept)
        print(f"\nAcademic lists in department {first_dept.name}: {len(dept_lists)}")

    # Test course lookup if possible
    if all_lists and all_lists[0].courses:
        first_code = all_lists[0].courses[0].code
        course = get_course_by_code(first_code)
        if course:
            print(f"\nFound course {first_code}: {course.name_en}")
