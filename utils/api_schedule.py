# utils/api_schedule.py

import logging
from datetime import datetime
from typing import Any, Dict, List

from models.halls import Hall
from models.labs import Lab
from scheduler import Assignment, BlockType

# Set up logging
logger = logging.getLogger("utils.api_schedule")


def convert_assignments_to_api_format(
    assignments: Dict[str, Assignment], schedule_name_en: str, schedule_name_ar: str
) -> Dict[str, Any]:
    """
    Convert scheduler assignments to the API format required by the backend.

    Args:
        assignments: Dictionary of assignments from the scheduler
        schedule_name_en: English name for the schedule
        schedule_name_ar: Arabic name for the schedule

    Returns:
        Dictionary in the format expected by the backend API
    """
    logger.info(f"Converting {len(assignments)} assignments to API format")

    # Initialize the API format structure
    api_data = {"nameEn": schedule_name_en, "nameAr": schedule_name_ar, "schedule": []}

    # Convert each assignment
    for block_id, assignment in assignments.items():
        try:
            api_entry = convert_single_assignment(assignment)
            api_data["schedule"].append(api_entry)

        except Exception as e:
            logger.error(f"Failed to convert assignment {block_id}: {str(e)}")
            raise  # Re-raise to stop processing on any error

    logger.info(f"Successfully converted {len(api_data['schedule'])} assignments")
    return api_data


def convert_single_assignment(assignment: Assignment) -> Dict[str, Any]:
    """
    Convert a single assignment to API format using the new object structure.

    Args:
        assignment: A single Assignment object from the scheduler

    Returns:
        Dictionary representing the assignment in API format
    """
    block = assignment.block
    room = assignment.room
    time_slot = assignment.time_slot

    # Get course ID from CourseAssignment object
    course_id = block.course_object.course_id

    # Determine session type
    session_type = "lecture" if block.block_type == BlockType.LECTURE else "lab"

    # Create group info
    group_info = {
        "group_number": block.group_number,
        "total_groups": block.total_groups,
    }

    # Determine room assignments
    hall_id = None
    lab_id = None

    if isinstance(room, Hall):
        hall_id = room.id
        lab_id = None
    elif isinstance(room, Lab):
        hall_id = None
        lab_id = room.id
    else:
        raise ValueError(f"Unknown room type for room: {room}")

    # Get lecturer/TA ID
    lecturer_id = block.staff_member.id

    # Create time slot info
    time_slot_info = {
        "day": time_slot.day.name.lower(),
        "start_time": time_slot.start_time.strftime("%H:%M"),
        "end_time": time_slot.end_time.strftime("%H:%M"),
    }

    # Get academic information from AcademicList object
    academic_id = block.academic_list_object.id
    department_id = block.academic_list_object.department.id

    # Create the API entry
    api_entry = {
        "course_id": course_id,
        "session_type": session_type,
        "group_info": group_info,
        "hall_id": hall_id,
        "lab_id": lab_id,
        "lecturer_id": lecturer_id,
        "time_slot": time_slot_info,
        "student_count": block.student_count,
        "academic_id": academic_id,
        "academic_level": block.academic_level,
        "department_id": department_id,
    }

    return api_entry
