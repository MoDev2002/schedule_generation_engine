# backend/get_study_plans.py - Enhanced with comprehensive logging

import logging
import os
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token, login
from models.study_plan import StudyPlan
from utils.api_study_plans import (
    convert_api_study_plan_detail,
    convert_api_study_plan_summary,
)

# Set up logging
logger = logging.getLogger("backend.get_study_plans")


def get_study_plans_summary() -> List[Dict]:
    """
    Fetch summary information about all study plans (without course assignments).

    Returns:
        List of dictionaries with study plan summary information
    """
    logger.info("=== FETCHING STUDY PLANS SUMMARY ===")
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    logger.info(f"Making request to: {url}/study-plans")
    response = requests.get(f"{url}/study-plans", headers=headers)
    logger.info(f"Response status: {response.status_code}")

    data = response.json()
    logger.info(
        f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}"
    )

    # Check if response has the expected structure
    if "data" in data:
        study_plans_data = data["data"]
    else:
        # Handle the case where the response structure is different
        study_plans_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logger.info(f"Study plans data type: {type(study_plans_data)}")
    logger.info(f"Raw API response contained {len(study_plans_data)} study plans")

    # Log first study plan structure if available
    if study_plans_data:
        logger.info(f"First study plan keys: {list(study_plans_data[0].keys())}")
        logger.info(f"First study plan: {study_plans_data[0]}")

    # Convert API data to study plan summary dictionaries
    study_plans = []
    for i, study_plan_data in enumerate(study_plans_data):
        try:
            study_plan = convert_api_study_plan_summary(study_plan_data)
            study_plans.append(study_plan)
            logger.info(f"Converted study plan summary {i+1}: {study_plan['name']}")
        except Exception as e:
            logger.error(
                f"Error converting study plan summary {i+1}: {str(e)}", exc_info=True
            )

    logger.info(f"Successfully converted {len(study_plans)} study plan summaries")
    return study_plans


def get_study_plan_by_id(
    plan_id: int, resolve_refs: bool = True
) -> Optional[StudyPlan]:
    """
    Fetch detailed information about a specific study plan by ID.

    Args:
        plan_id: The study plan ID
        resolve_refs: Whether to resolve references to other objects (academic lists, staff, etc.)

    Returns:
        StudyPlan object or None if not found
    """
    logger.info(f"=== FETCHING STUDY PLAN BY ID: {plan_id} ===")
    logger.info(f"Resolve references: {resolve_refs}")

    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    logger.info(f"Making request to: {url}/study-plans/{plan_id}")
    response = requests.get(f"{url}/study-plans/{plan_id}", headers=headers)

    # Check if request was successful
    if response.status_code != 200:
        logger.error(
            f"Failed to fetch study plan with ID {plan_id}: Status {response.status_code}"
        )
        logger.error(f"Response content: {response.text}")
        return None

    data = response.json()
    logger.info(
        f"Response data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}"
    )

    # Check if response has the expected structure
    if "data" in data:
        study_plan_data = data["data"]
    else:
        logger.error(f"Unexpected response format for study plan with ID {plan_id}")
        logger.error(f"Response: {data}")
        return None

    logger.info(f"Study plan data keys: {list(study_plan_data.keys())}")
    logger.info(f"Study plan data: {study_plan_data}")

    logger.info(f"=== RAW STUDY PLAN DATA FROM BACKEND ===")
    logger.info(f"Study plan ID requested: {plan_id}")
    logger.info(f"Full response keys: {list(study_plan_data.keys())}")

    if "academicList" in study_plan_data:
        academic_list_data = study_plan_data["academicList"]
        logger.info(f"Academic list data found: {academic_list_data}")
        logger.info(
            f"Academic list keys: {list(academic_list_data.keys()) if isinstance(academic_list_data, dict) else 'Not a dict'}"
        )
        logger.info(
            f"Academic list ID: {academic_list_data.get('id') if isinstance(academic_list_data, dict) else 'N/A'}"
        )
    else:
        logger.error(f"NO 'academicList' field found in study plan data!")
        logger.info(f"Available fields: {list(study_plan_data.keys())}")

    if "courseAssignments" in study_plan_data:
        course_assignments = study_plan_data["courseAssignments"]
        logger.info(f"Course assignments count: {len(course_assignments)}")
        if course_assignments:
            first_course = course_assignments[0]
            logger.info(f"First course assignment keys: {list(first_course.keys())}")
            logger.info(
                f"First course assignment academic_list_id: {first_course.get('academic_list_id')}"
            )
    else:
        logger.error(f"NO 'courseAssignments' field found!")

    # Convert API data to StudyPlan object
    try:
        study_plan = convert_api_study_plan_detail(
            study_plan_data, resolve_refs=resolve_refs
        )
        logger.info(
            f"Converted study plan: {study_plan.academic_list.name} Level {study_plan.academic_level}"
        )
        logger.info(f"Course assignments: {len(study_plan.course_assignments)}")

        # Log each course assignment
        for i, ca in enumerate(study_plan.course_assignments):
            logger.info(f"  Course {i+1}: {ca.course_code}")
            logger.info(f"    Lecture groups: {ca.lecture_groups}")
            logger.info(f"    Lab groups: {ca.lab_groups}")
            logger.info(f"    Lecturers: {len(ca.lecturers)}")
            logger.info(
                f"    TAs: {len(ca.teaching_assistants) if ca.teaching_assistants else 0}"
            )

        return study_plan
    except Exception as e:
        logger.error(
            f"Error converting study plan with ID {plan_id}: {str(e)}", exc_info=True
        )
        return None


def get_study_plans(resolve_refs: bool = True) -> List[StudyPlan]:
    """
    Fetch detailed information about all study plans including their course assignments.

    This method fetches summary information first, then fetches details for each plan.

    Args:
        resolve_refs: Whether to resolve references to other objects

    Returns:
        List of StudyPlan objects
    """
    logger.info("=== FETCHING ALL STUDY PLANS ===")

    # First, get summary information
    study_plan_summaries = get_study_plans_summary()

    # Then, fetch detailed information for each study plan
    study_plans = []
    for summary in study_plan_summaries:
        plan_id = summary["id"]
        study_plan = get_study_plan_by_id(plan_id, resolve_refs=resolve_refs)
        if study_plan:
            study_plans.append(study_plan)

    logger.info(f"Fetched and converted {len(study_plans)} complete study plans")
    return study_plans


def get_study_plan_by_academic_list(academic_list_id: int) -> List[StudyPlan]:
    """
    Get all study plans for a specific academic list.

    Args:
        academic_list_id: The academic list ID

    Returns:
        List of StudyPlan objects for the specified academic list
    """
    logger.info(f"=== FETCHING STUDY PLANS BY ACADEMIC LIST: {academic_list_id} ===")

    all_plans = get_study_plans_summary()
    matching_ids = [
        plan["id"] for plan in all_plans if plan["academic_list_id"] == academic_list_id
    ]

    logger.info(
        f"Found {len(matching_ids)} study plans for academic list {academic_list_id}"
    )

    # Fetch detailed plans for matching IDs
    matching_plans = []
    for plan_id in matching_ids:
        plan = get_study_plan_by_id(plan_id)
        if plan:
            matching_plans.append(plan)

    return matching_plans


def get_study_plans_by_ids(
    plan_ids: List[int], resolve_refs: bool = True
) -> List[StudyPlan]:
    """
    Fetch detailed information about specific study plans by their IDs.

    This is the main entry point for the scheduling engine to get required study plans.

    Args:
        plan_ids: List of study plan IDs to fetch
        resolve_refs: Whether to resolve references to other objects (academic lists, staff, etc.)

    Returns:
        List of StudyPlan objects with complete information
    """
    if not plan_ids:
        logger.warning("No study plan IDs provided to fetch")
        return []

    # Log the request
    logger.info(f"=== FETCHING STUDY PLANS BY IDS ===")
    logger.info(f"Requested IDs: {plan_ids}")
    logger.info(f"Resolve references: {resolve_refs}")

    # Fetch each study plan
    study_plans = []
    for plan_id in plan_ids:
        logger.info(f"\nFetching study plan ID: {plan_id}")
        study_plan = get_study_plan_by_id(plan_id, resolve_refs=resolve_refs)
        if study_plan:
            study_plans.append(study_plan)
            logger.info(f"Successfully fetched study plan {plan_id}")
        else:
            logger.error(f"Failed to fetch study plan with ID {plan_id}")

    logger.info(
        f"Successfully fetched {len(study_plans)} out of {len(plan_ids)} requested study plans"
    )

    # Validate the fetched study plans
    for i, plan in enumerate(study_plans):
        logger.info(f"\nValidating study plan {i+1}:")
        validate_study_plan(plan)

    return study_plans


def validate_study_plan(study_plan: StudyPlan) -> bool:
    """
    Validate a study plan for completeness and correctness.
    Logs warnings for any issues found.

    Args:
        study_plan: The StudyPlan object to validate

    Returns:
        True if the study plan is valid, False otherwise
    """
    logger.info(
        f"Validating study plan: {study_plan.academic_list.name} Level {study_plan.academic_level}"
    )
    valid = True

    # Check for basic properties
    if study_plan.expected_students <= 0:
        logger.warning(
            f"Study plan for {study_plan.academic_list.name} has invalid expected students: {study_plan.expected_students}"
        )
        valid = False

    if study_plan.academic_level < 1:
        logger.warning(
            f"Study plan for {study_plan.academic_list.name} has invalid academic level: {study_plan.academic_level}"
        )
        valid = False

    # Check for course assignments
    if not study_plan.course_assignments:
        logger.warning(
            f"Study plan for {study_plan.academic_list.name} has no course assignments"
        )
        valid = False

    # Check individual course assignments
    for i, course in enumerate(study_plan.course_assignments):
        logger.info(f"  Validating course {i+1}: {course.course_code}")

        # Lecture groups validation
        if course.lecture_groups <= 0:
            logger.warning(
                f"Course {course.course_code} in study plan has invalid lecture groups: {course.lecture_groups}"
            )
            valid = False

        # Lecturer assignments validation
        total_lecturer_groups = sum(la["num_of_groups"] for la in course.lecturers)
        if total_lecturer_groups != course.lecture_groups:
            logger.warning(
                f"Course {course.course_code} has mismatched lecturer assignments: {total_lecturer_groups} vs {course.lecture_groups}"
            )
            valid = False

        # Teaching assistant validation
        if course.lab_groups > 0:
            logger.info(f"    Course has {course.lab_groups} lab groups")
            if not course.teaching_assistants:
                logger.warning(
                    f"Course {course.course_code} has lab groups but no teaching assistants"
                )
                valid = False
            else:
                logger.info(
                    f"    Course has {len(course.teaching_assistants)} TA assignments"
                )
                total_ta_groups = sum(
                    ta["num_of_groups"] for ta in course.teaching_assistants
                )
                if total_ta_groups != course.lab_groups:
                    logger.warning(
                        f"Course {course.course_code} has mismatched TA assignments: {total_ta_groups} vs {course.lab_groups}"
                    )
                    valid = False
        else:
            logger.info(f"    Course has no lab groups")

    logger.info(f"Validation result: {'VALID' if valid else 'INVALID'}")
    return valid


if __name__ == "__main__":
    # Test the summary function
    get_study_plan_by_id(6)
