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
    """Convert API academic degree data to AcademicDegree object."""
    return AcademicDegree(
        id=degree_data["id"], name=degree_data["name"], prefix=degree_data["prefix"]
    )


def is_lecturer_degree(degree: AcademicDegree) -> bool:
    """Check if the academic degree corresponds to a lecturer role"""
    lecturer_degree_ids = {
        1,
        2,
        3,
    }
    return degree in lecturer_degree_ids


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
    if is_lecturer_degree(academic_degree.id):
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
