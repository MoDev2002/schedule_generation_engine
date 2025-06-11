# utils/api_academics.py

import logging
from typing import Any, Dict, List, Optional

from models.academic_list import AcademicList, Course
from models.department import Department
from utils.api_departments import convert_api_department


def convert_api_course(course_data: Dict[str, Any]) -> Course:
    """
    Convert API course data to Course object.

    Args:
        course_data: Course data from the API

    Returns:
        Course object
    """
    if not course_data or not isinstance(course_data, dict):
        raise ValueError(f"Invalid course data: {course_data}")

    # Extract required fields
    id = course_data.get("id")
    code = course_data.get("code")
    name_en = course_data.get("nameEn") or course_data.get("name")
    name_ar = course_data.get("nameAr")

    # Convert hours ensuring correct types (API might return strings)
    try:
        lecture_hours = int(course_data.get("lectureHours", 0))
        practical_hours = int(course_data.get("practicalHours", 0))
        credit_hours = int(course_data.get("creditHours", 0))
    except (ValueError, TypeError) as e:
        logging.error(f"Error converting hours for course {name_en}: {str(e)}")
        lecture_hours = 0
        practical_hours = 0
        credit_hours = 0

    # Check for prerequisite course (if available)
    prerequisite_course = course_data.get("prerequisiteCourse", None)

    logging.debug(
        f"Converting course: {code} - {name_en} ({lecture_hours}+{practical_hours} hrs)"
    )

    # Create and return the Course object
    return Course(
        id=id,
        code=code,
        name_en=name_en,
        name_ar=name_ar,
        lecture_hours=lecture_hours,
        practical_hours=practical_hours,
        credit_hours=credit_hours,
        prerequisite_course=prerequisite_course,
    )


def convert_api_academic_list_summary(
    academic_list_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convert API academic list summary data (without courses) to a dictionary.

    Args:
        academic_list_data: Academic list summary data from the API

    Returns:
        Dictionary with academic list information
    """
    if not academic_list_data or not isinstance(academic_list_data, dict):
        raise ValueError(f"Invalid academic list data: {academic_list_data}")

    # Extract basic information
    academic_id = academic_list_data.get("id")
    name = academic_list_data.get("nameEn") or academic_list_data.get("name")
    name_ar = academic_list_data.get("nameAr")
    number_of_courses = academic_list_data.get("number_of_courses", 0)

    # Convert department
    department_data = academic_list_data.get("department", {})
    department = convert_api_department(department_data)

    logging.debug(f"Converting academic list summary: {name} (ID: {academic_id})")

    return {
        "id": academic_id,
        "name": name,
        "name_ar": name_ar,
        "department": department,
        "number_of_courses": number_of_courses,
    }


def convert_api_academic_list_detail(
    academic_list_data: Dict[str, Any],
) -> AcademicList:
    """
    Convert API academic list detail data (with courses) to AcademicList object.

    Args:
        academic_list_data: Academic list detail data from the API

    Returns:
        AcademicList object
    """
    if not academic_list_data or not isinstance(academic_list_data, dict):
        raise ValueError(f"Invalid academic list data: {academic_list_data}")

    # Extract basic information
    name = academic_list_data.get("nameEn") or academic_list_data.get("name")

    # Convert department
    department_data = academic_list_data.get("department", {})
    department = convert_api_department(department_data)

    # Convert courses
    courses = []
    for course_data in academic_list_data.get("courses", []):
        try:
            course = convert_api_course(course_data)
            courses.append(course)
        except Exception as e:
            logging.error(f"Error converting course in academic list {name}: {str(e)}")

    logging.debug(f"Converting academic list: {name} with {len(courses)} courses")

    return AcademicList(name=name, department=department, courses=courses)
