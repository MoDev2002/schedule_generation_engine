# utils/api_schedule.py

import logging
from typing import Any, Dict, List

from models.halls import Hall
from models.labs import Lab
from models.time_preferences import ReservedPeriod
from scheduler import Assignment, BlockType

# Set up logging
logger = logging.getLogger("utils.api_schedule")


def convert_assignments_to_api_format(
    assignments: Dict[str, Assignment],
    schedule_name_en: str,
    schedule_name_ar: str,
    source_schedule_id: int | None = None,
    reserved_period: ReservedPeriod | None = None,
    available_hall_ids: List[int] | None = None,
    available_lab_ids: List[int] | None = None,
) -> Dict[str, Any]:
    """
    Convert scheduler assignments to the API format required by the backend.

    Args:
        assignments: Dictionary of assignments from the scheduler
        schedule_name_en: English name for the schedule
        schedule_name_ar: Arabic name for the schedule
        available_hall_ids: Halls this run was allowed to use (persisted on
            the schedule as its point-in-time room pool)
        available_lab_ids: Labs this run was allowed to use (same as above)

    Returns:
        Dictionary in the format expected by the backend API
    """
    logger.info(f"Converting {len(assignments)} assignments to API format")

    # Initialize the API format structure
    api_data = {"nameEn": schedule_name_en, "nameAr": schedule_name_ar, "schedule": []}
    if source_schedule_id is not None:
        api_data["source_schedule_id"] = source_schedule_id
    if reserved_period is not None:
        api_data["reserved_period"] = {
            "day": reserved_period.day.name.lower(),
            "start_time": reserved_period.start_time.strftime("%H:%M"),
            "end_time": reserved_period.end_time.strftime("%H:%M"),
        }
    if available_hall_ids is not None:
        api_data["available_hall_ids"] = available_hall_ids
    if available_lab_ids is not None:
        api_data["available_lab_ids"] = available_lab_ids

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

    # External entries branch first: they carry the external identity, empty
    # local arrays, a nullable lecturer, and component-appropriate room rules
    # (labs book a lab, our-halls lectures a hall, external-venue lectures no
    # room at all).
    if getattr(block, "source_kind", "course") == "external":
        return _convert_external_assignment(assignment)

    # Get course ID from CourseAssignment object
    course_ids = [course.course_id for course in block.course_objects]

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
    academic_ids = [al.id for al in block.academic_list_objects]
    department_ids = [al.department.id for al in block.academic_list_objects]

    # Check for merged courses
    logger.debug(f"Assignment {block.course_codes}:")
    logger.debug(f"  Academic IDs: {academic_ids}")
    logger.debug(f"  Academic Levels: {block.academic_levels}")
    logger.debug(f"  Department IDs: {department_ids}")
    logger.debug(f"  Is merged: {getattr(block, 'is_merged_common_course', False)}")

    # Validate array consistency. Written as an explicit set of lengths: a
    # comparison chain (a != b != c) means "all adjacent pairs differ", which is
    # not the same question and stays silent for the mismatch that matters.
    lengths = {
        "academic_ids": len(academic_ids),
        "academic_levels": len(block.academic_levels),
        "department_ids": len(department_ids),
        "course_codes": len(block.course_codes),
        "course_ids": len(course_ids),
    }
    if len(set(lengths.values())) != 1:
        raise ValueError(
            f"Array length mismatch for {block.course_codes}: "
            + ", ".join(f"{name}={n}" for name, n in lengths.items())
        )

    # Create the API entry
    api_entry = {
        "course_ids": course_ids,
        "session_type": session_type,
        "group_info": group_info,
        "hall_id": hall_id,
        "lab_id": lab_id,
        "lecturer_id": lecturer_id,
        "time_slot": time_slot_info,
        "student_count": block.student_count,
        "academic_ids": academic_ids,
        "academic_levels": block.academic_levels,
        "department_ids": department_ids,
    }

    return api_entry


def _convert_external_assignment(assignment: Assignment) -> Dict[str, Any]:
    """
    Convert an external assignment to the API payload shape.

    - `entry_kind: "external"` + `external_course_id` carry the identity;
    - `course_ids` and the academic/department arrays are empty by design -
      the array-length consistency check does not apply to them;
    - `lecturer_id` is nullable (staffless labs, external-lecturer lectures);
    - rooms: exactly one of `hall_id`/`lab_id`, except both null reserved to
      the external-venue lecture (requires_room=False).
    """
    block = assignment.block
    room = assignment.room
    time_slot = assignment.time_slot

    hall_id = None
    lab_id = None
    if isinstance(room, Hall):
        hall_id = room.id
    elif isinstance(room, Lab):
        lab_id = room.id
    elif room is not None:
        raise ValueError(f"Unknown room type for room: {room}")

    if room is None and block.requires_room:
        raise ValueError(
            f"External block {block.id} has no room but requires one"
        )

    return {
        "entry_kind": "external",
        "external_course_id": block.external_course_id,
        "course_ids": [],
        "session_type": "lecture" if block.block_type == BlockType.LECTURE else "lab",
        "group_info": {
            "group_number": block.group_number,
            "total_groups": block.total_groups,
        },
        "hall_id": hall_id,
        "lab_id": lab_id,
        "lecturer_id": block.staff_member.id if block.staff_member else None,
        "time_slot": {
            "day": time_slot.day.name.lower(),
            "start_time": time_slot.start_time.strftime("%H:%M"),
            "end_time": time_slot.end_time.strftime("%H:%M"),
        },
        "student_count": block.student_count,
        "academic_ids": [],
        "academic_levels": [],
        "department_ids": [],
    }
