# backend/get_external_courses.py

"""Fetch selected external courses from the Laravel API.

Follows the same pattern as the study-plan fetchers: authenticated GET,
response-shape tolerance, and conversion into engine models with full
reference resolution (staff through the shared cached catalogue, labs through
the lab catalogue). Staff are never synthesized - an unresolvable staff id is
a conversion error that fails the run with a structured issue.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.get_labs import get_labs
from backend.get_staff_members import get_staff_member_by_id
from backend.http_client import api_get
from backend.login import get_auth_token
from dotenv import load_dotenv
from models.external_course import (
    ExternalCourse,
    ExternalCourseValidationError,
)
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.time_preferences import TimePreference
from utils.time_utils import (
    convert_api_day_to_enum,
    convert_api_time_to_time_object,
)

logger = logging.getLogger("backend.get_external_courses")


@dataclass
class ExternalCourseLoadResult:
    """All courses that loaded, plus actionable errors for any that did not."""

    external_courses: List[ExternalCourse]
    errors: List[Dict] = field(default_factory=list)


def _fetch_raw_external_courses(course_ids: List[int]) -> List[Dict[str, Any]]:
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    params = [("ids[]", course_id) for course_id in course_ids]
    response = api_get(f"{url}/external-courses", headers=headers, params=params)

    if response.status_code != 200:
        logger.error(
            "Failed to fetch external courses %s: status %s - %s",
            course_ids,
            response.status_code,
            response.text,
        )
        raise RuntimeError(
            f"External courses fetch failed with status {response.status_code}"
        )

    data = response.json()
    if "data" in data:
        return data["data"] if isinstance(data["data"], list) else []
    return data if isinstance(data, list) else []


def _resolve_staff(assignment_data: Dict[str, Any], course_id: int) -> Dict[str, Any]:
    """Resolve one distribution row to a full staff object, never a placeholder."""
    staff_id = assignment_data.get("staff_id")
    role = assignment_data.get("role")
    num_of_groups = int(assignment_data.get("num_of_groups", 1))

    staff = get_staff_member_by_id(int(staff_id)) if staff_id is not None else None
    if staff is None:
        raise ExternalCourseValidationError(
            f"External course {course_id}: staff {staff_id} could not be "
            f"resolved to a staff record"
        )

    expected_type = Lecturer if role == "lecturer" else TeachingAssistant
    if not isinstance(staff, expected_type):
        raise ExternalCourseValidationError(
            f"External course {course_id}: staff {staff_id} is declared as "
            f"{role!r} but their academic degree resolves to "
            f"{type(staff).__name__}"
        )

    return {"staff": staff, "role": role, "num_of_groups": num_of_groups}


def _resolve_eligible_labs(lab_data: List[Dict[str, Any]]) -> List[Lab]:
    if not lab_data:
        return []

    catalogue = get_labs()
    by_id = {lab.id: lab for lab in catalogue}

    labs: List[Lab] = []
    for entry in lab_data:
        lab_id = entry.get("id")
        lab = by_id.get(int(lab_id)) if lab_id is not None else None
        if lab is None:
            logger.warning(
                "Eligible lab %s not found in the lab catalogue; skipped", lab_id
            )
            continue
        labs.append(lab)
    return labs


def _convert_time_slots(
    raw_slots: Any, course_id: Any, component: str
) -> Optional[List[TimePreference]]:
    """Parse one component's declared fixed-slot array into TimePreference
    windows. Absent/null means free placement; shape errors surface through
    the caller's malformed-payload conversion."""
    if raw_slots is None:
        return None

    slots: List[TimePreference] = []
    for raw in raw_slots:
        day = convert_api_day_to_enum(str(raw.get("day", "")))
        start = convert_api_time_to_time_object(
            str(raw.get("start_time") or raw.get("startTime"))
        )
        end = convert_api_time_to_time_object(
            str(raw.get("end_time") or raw.get("endTime"))
        )
        slots.append(TimePreference(day=day, start_time=start, end_time=end))

    logger.info(
        "External course %s declares %d %s time slot(s)",
        course_id,
        len(slots),
        component,
    )
    return slots


def convert_api_external_course(course_data: Dict[str, Any]) -> ExternalCourse:
    """Convert one API external-course payload into the engine model."""
    course_id = course_data.get("id")

    staff_assignments = []
    for assignment_data in course_data.get("staff") or []:
        staff_assignments.append(_resolve_staff(assignment_data, course_id))

    try:
        return ExternalCourse(
            id=course_id,
            name_en=course_data.get("name_en") or course_data.get("nameEn") or "",
            name_ar=course_data.get("name_ar") or course_data.get("nameAr") or "",
            requesting_entity_en=(
                course_data.get("requesting_entity_en")
                or course_data.get("requestingEntityEn")
                or ""
            ),
            requesting_entity_ar=(
                course_data.get("requesting_entity_ar")
                or course_data.get("requestingEntityAr")
                or ""
            ),
            lab_groups=int(course_data.get("lab_groups") or 0),
            lab_students_per_group=course_data.get("lab_students_per_group"),
            lecture_groups=int(course_data.get("lecture_groups") or 0),
            lecture_students_per_group=course_data.get("lecture_students_per_group"),
            lecture_venue=course_data.get("lecture_venue"),
            lab_session_hours=int(course_data.get("lab_session_hours") or 2),
            lecture_session_hours=int(
                course_data.get("lecture_session_hours") or 2
            ),
            staff_assignments=staff_assignments,
            eligible_labs=_resolve_eligible_labs(
                course_data.get("eligible_labs") or []
            ),
            code=course_data.get("code"),
            lab_time_slots=_convert_time_slots(
                course_data.get("lab_time_slots"), course_id, "lab"
            ),
            lecture_time_slots=_convert_time_slots(
                course_data.get("lecture_time_slots"), course_id, "lecture"
            ),
        )
    except ExternalCourseValidationError:
        raise
    except (TypeError, ValueError) as error:
        raise ExternalCourseValidationError(
            f"External course {course_id}: malformed payload ({error})"
        ) from error


def get_external_courses_by_ids(
    course_ids: List[int],
) -> ExternalCourseLoadResult:
    """Fetch and convert the selected external courses for a run."""
    if not course_ids:
        return ExternalCourseLoadResult(external_courses=[], errors=[])

    logger.info("=== FETCHING EXTERNAL COURSES %s ===", course_ids)
    raw_courses = _fetch_raw_external_courses(course_ids)

    courses: List[ExternalCourse] = []
    errors: List[Dict] = []
    for course_data in raw_courses:
        course_id = course_data.get("id")
        try:
            course = convert_api_external_course(course_data)
        except ExternalCourseValidationError as error:
            logger.error("External course %s failed conversion: %s", course_id, error)
            errors.append(
                {
                    "code": "EXTERNAL_COURSE_INVALID",
                    "message": str(error),
                    "context": {"external_course_id": course_id},
                }
            )
            continue

        courses.append(course)
        logger.info(
            "Loaded external course %s: labs=%s lecture_groups=%s venue=%s "
            "staff=%s",
            course.id,
            course.lab_groups,
            course.lecture_groups,
            course.lecture_venue,
            [
                (a["role"], a["staff"].id, a["num_of_groups"])
                for a in course.staff_assignments
            ],
        )

    missing = sorted(set(course_ids) - {course.id for course in courses} - {
        issue["context"]["external_course_id"] for issue in errors
    })
    for course_id in missing:
        errors.append(
            {
                "code": "EXTERNAL_COURSE_LOAD_FAILED",
                "message": "External course could not be loaded",
                "context": {"external_course_id": course_id},
            }
        )

    logger.info(
        "Loaded %d of %d requested external course(s)", len(courses), len(course_ids)
    )
    return ExternalCourseLoadResult(external_courses=courses, errors=errors)
