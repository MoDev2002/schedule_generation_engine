# utils/api_staff.py


import logging
from datetime import time
from typing import Any, Dict, List, Optional

from models.department import Department
from models.staff_members import (
    AcademicDegree,
    Lecturer,
    StaffMember,
    TeachingAssistant,
)
from utils.api_departments import convert_api_department
from utils.time_utils import convert_api_time_preference


def convert_api_academic_degree(degree_data: Dict[str, Any]) -> AcademicDegree:
    """
    Convert API academic degree data to AcademicDegree enum.

    Maps the name from the API to your AcademicDegree enum.
    Falls back to TEACHING_ASSISTANT if no match is found.
    """
    if not degree_data or not isinstance(degree_data, dict):
        logging.warning(
            f"Invalid degree data: {degree_data}, defaulting to TEACHING_ASSISTANT"
        )
        return AcademicDegree.TEACHING_ASSISTANT

    degree_name = degree_data.get("name", "").upper().replace(" ", "_")
    logging.debug(f"Converting academic degree: '{degree_name}'")

    # Try direct mapping
    for degree in AcademicDegree:
        if degree.name == degree_name:
            logging.debug(f"Found direct match: {degree.name}")
            return degree

    # Special cases mapping
    special_cases = {
        "PROFESSOR": AcademicDegree.PROFESSOR,
        "ASSOCIATE_PROFESSOR": AcademicDegree.ASSOCIATE_PROFESSOR,
        "ASSISTANT_PROFESSOR": AcademicDegree.ASSISTANT_PROFESSOR,
        "ASSISTANT_LECTURER": AcademicDegree.ASSISTANT_LECTURER,
        "TEACHING_ASSISTANT": AcademicDegree.TEACHING_ASSISTANT,
    }

    # Handle common variations
    if "PROFESSOR" in degree_name:
        if "ASSISTANT" in degree_name and "ASSOCIATE" not in degree_name:
            logging.debug(f"Mapped to ASSISTANT_PROFESSOR")
            return AcademicDegree.ASSISTANT_PROFESSOR
        elif "ASSOCIATE" in degree_name or "ASSOC" in degree_name:
            logging.debug(f"Mapped to ASSOCIATE_PROFESSOR")
            return AcademicDegree.ASSOCIATE_PROFESSOR
        else:
            logging.debug(f"Mapped to PROFESSOR")
            return AcademicDegree.PROFESSOR
    elif "LECTURER" in degree_name:
        if "ASSISTANT" in degree_name:
            logging.debug(f"Mapped to ASSISTANT_LECTURER")
            return AcademicDegree.ASSISTANT_LECTURER
        else:
            logging.debug(f"Mapped to ASSISTANT_PROFESSOR (lecturer)")
            return AcademicDegree.ASSISTANT_PROFESSOR
    elif "TEACHING" in degree_name or "TA" in degree_name or "ASSISTANT" in degree_name:
        logging.debug(f"Mapped to TEACHING_ASSISTANT")
        return AcademicDegree.TEACHING_ASSISTANT

    # Fallback
    logging.warning(
        f"No match found for '{degree_name}', defaulting to TEACHING_ASSISTANT"
    )
    return AcademicDegree.TEACHING_ASSISTANT


def is_lecturer_degree(degree: AcademicDegree) -> bool:
    """Check if the academic degree corresponds to a lecturer role"""
    lecturer_degrees = {
        AcademicDegree.PROFESSOR,
        AcademicDegree.ASSOCIATE_PROFESSOR,
        AcademicDegree.ASSISTANT_PROFESSOR,
    }
    return degree in lecturer_degrees


def convert_api_staff_member(staff_data: Dict[str, Any]) -> StaffMember:
    """
    Convert API staff member data to either Lecturer or TeachingAssistant.

    Automatically determines the correct type based on the academic degree.
    """
    if not staff_data or not isinstance(staff_data, dict):
        raise ValueError(f"Invalid staff data: {staff_data}")

    staff_id = staff_data.get("id")
    name = staff_data.get("nameEn") or staff_data.get("name")

    # Convert department
    department_data = staff_data.get("department", {})
    department = convert_api_department(department_data)

    # Convert academic degree
    degree_data = staff_data.get("academic_degree", {})
    academic_degree = convert_api_academic_degree(degree_data)

    # Convert timing preferences
    timing_preferences = []
    for pref_data in staff_data.get("timingPreference", []):
        timing_preferences.append(convert_api_time_preference(pref_data))

    # Convert isPermanent to boolean (handles 0/1 values)
    is_permanent = bool(staff_data.get("isPermanent", 1))

    # Determine the correct staff type based on academic degree
    if is_lecturer_degree(academic_degree):
        logging.debug(
            f"Creating Lecturer: {name} (ID: {staff_id}), Degree: {academic_degree.name}"
        )
        return Lecturer(
            id=staff_id,
            name=name,
            department=department,
            timing_preferences=timing_preferences,
            academic_degree=academic_degree,
            is_permanent=is_permanent,
        )
    else:
        logging.debug(
            f"Creating TeachingAssistant: {name} (ID: {staff_id}), Degree: {academic_degree.name}"
        )
        return TeachingAssistant(
            id=staff_id,
            name=name,
            department=department,
            timing_preferences=timing_preferences,
            academic_degree=academic_degree,
            is_permanent=is_permanent,
        )


def convert_api_lecturer(staff_data: Dict[str, Any]) -> Lecturer:
    """
    Convert API staff data specifically to a Lecturer.

    Use this when you're certain the data represents a lecturer.
    Will raise an error if the academic degree doesn't match a lecturer role.
    """
    staff_member = convert_api_staff_member(staff_data)
    if not isinstance(staff_member, Lecturer):
        raise ValueError(
            f"Staff member {staff_data.get('name')} has degree {staff_data.get('academic_degree', {}).get('name')} which is not a lecturer role"
        )
    return staff_member


def convert_api_teaching_assistant(staff_data: Dict[str, Any]) -> TeachingAssistant:
    """
    Convert API staff data specifically to a TeachingAssistant.

    Use this when you're certain the data represents a teaching assistant.
    Will raise an error if the academic degree doesn't match a teaching assistant role.
    """
    staff_member = convert_api_staff_member(staff_data)
    if not isinstance(staff_member, TeachingAssistant):
        raise ValueError(
            f"Staff member {staff_data.get('name')} has degree {staff_data.get('academic_degree', {}).get('name')} which is not a teaching assistant role"
        )
    return staff_member
