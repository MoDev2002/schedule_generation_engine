# utils/api_study_plans.py - Enhanced with comprehensive logging

import logging
from typing import Any, Dict, List, Optional

from backend.get_academics import get_academic_list_by_id
from backend.get_labs import get_labs
from backend.get_staff_members import get_staff_member_by_id
from models.academic_list import AcademicList
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment, StudyPlan
from utils.api_academics import convert_api_academic_list_summary

logger = logging.getLogger("utils.api_study_plans")


def extract_academic_level(level_str: str) -> int:
    """
    Extract numeric academic level from level string (e.g., "Level 1" -> 1)

    Args:
        level_str: String representation of academic level

    Returns:
        Integer academic level
    """
    logger.debug(f"Extracting academic level from: '{level_str}'")

    # Default to level 1 if parsing fails
    level = 1

    try:
        # Try to extract numeric part from string like "Level 1"
        if level_str:
            # Remove non-numeric characters and convert to int
            level_digits = "".join(filter(str.isdigit, level_str))
            if level_digits:
                level = int(level_digits)
    except (ValueError, TypeError) as e:
        logger.warning(
            f"Failed to parse academic level '{level_str}', defaulting to 1: {e}"
        )

    logger.debug(f"Extracted academic level: {level}")
    return level


def convert_api_lecturer_assignment(
    lecturer_data: Dict[str, Any], fetch_lecturer: bool = True
) -> Dict[str, Any]:
    """
    Convert API lecturer assignment data to lecturer assignment dictionary.

    Args:
        lecturer_data: Lecturer assignment data from API
        fetch_lecturer: Whether to fetch full lecturer details (if False, uses ID only)

    Returns:
        Dictionary with lecturer assignment information
    """
    logger.debug(f"Converting lecturer assignment: {lecturer_data}")

    lecturer_id = lecturer_data.get("id")
    num_groups = lecturer_data.get("num_groups", 1)

    logger.debug(
        f"Lecturer ID: {lecturer_id}, Groups: {num_groups}, Fetch: {fetch_lecturer}"
    )

    lecturer_assignment = {}

    if fetch_lecturer:
        # Fetch detailed lecturer information
        logger.debug(f"Fetching lecturer details for ID: {lecturer_id}")
        lecturer = get_staff_member_by_id(lecturer_id)
        if not lecturer or not isinstance(lecturer, Lecturer):
            logger.warning(
                f"Failed to fetch lecturer with ID {lecturer_id}, creating placeholder"
            )
            # Create a placeholder lecturer (will be populated later)
            lecturer_assignment["lecturer_id"] = lecturer_id
            lecturer_assignment["num_of_groups"] = num_groups
        else:
            logger.debug(f"Successfully fetched lecturer: {lecturer.name}")
            lecturer_assignment["lecturer"] = lecturer
            lecturer_assignment["num_of_groups"] = num_groups
    else:
        # Just store ID for later resolution
        logger.debug(f"Storing lecturer ID for later resolution: {lecturer_id}")
        lecturer_assignment["lecturer_id"] = lecturer_id
        lecturer_assignment["num_of_groups"] = num_groups

    logger.debug(f"Lecturer assignment result: {list(lecturer_assignment.keys())}")
    return lecturer_assignment


def convert_api_ta_assignment(
    ta_data: Dict[str, Any], fetch_ta: bool = True
) -> Dict[str, Any]:
    """
    Convert API teaching assistant assignment data to TA assignment dictionary.

    Args:
        ta_data: TA assignment data from API
        fetch_ta: Whether to fetch full TA details (if False, uses ID only)

    Returns:
        Dictionary with TA assignment information
    """
    logger.debug(f"Converting TA assignment: {ta_data}")

    ta_id = ta_data.get("id")
    num_groups = ta_data.get("num_groups", 1)

    logger.debug(f"TA ID: {ta_id}, Groups: {num_groups}, Fetch: {fetch_ta}")

    ta_assignment = {}

    if fetch_ta:
        # Fetch detailed TA information
        logger.debug(f"Fetching TA details for ID: {ta_id}")
        ta = get_staff_member_by_id(ta_id)
        if not ta or not isinstance(ta, TeachingAssistant):
            logger.warning(
                f"Failed to fetch teaching assistant with ID {ta_id}, creating placeholder"
            )
            logger.warning(f"TA fetch result: {ta}, Type: {type(ta) if ta else 'None'}")
            # Create a placeholder TA (will be populated later)
            ta_assignment["teaching_assistant_id"] = ta_id
            ta_assignment["num_of_groups"] = num_groups
        else:
            logger.debug(f"Successfully fetched TA: {ta.name}")
            ta_assignment["teaching_assistant"] = ta
            ta_assignment["num_of_groups"] = num_groups
    else:
        # Just store ID for later resolution
        logger.debug(f"Storing TA ID for later resolution: {ta_id}")
        ta_assignment["teaching_assistant_id"] = ta_id
        ta_assignment["num_of_groups"] = num_groups

    logger.debug(f"TA assignment result: {list(ta_assignment.keys())}")
    return ta_assignment


def convert_api_course_assignment(
    assignment_data: Dict[str, Any],
    resolve_refs: bool = True,
    academic_list: Optional[AcademicList] = None,
) -> CourseAssignment:
    """Convert API course assignment data to CourseAssignment object."""
    logger.info(f"=== CONVERTING COURSE ASSIGNMENT ===")

    # Extract course information
    course_id = assignment_data.get("course_id")
    course_data = assignment_data.get("course", {})
    course_code = None

    logger.info(f"Course ID: {course_id}")
    logger.info(f"Course data: {course_data}")

    # Try to find the course code
    if course_data.get("code"):
        course_code = course_data.get("code")
        logger.info(f"Found course code in course data: {course_code}")
    elif (
        resolve_refs and academic_list
    ):  # Use the passed academic_list instead of fetching
        logger.info(f"Attempting to resolve course code from provided academic list...")
        for course in academic_list.courses:
            if course.id == course_id:
                course_code = course.code
                logger.info(f"Found course code in academic list: {course_code}")
                break

        if not course_code:
            logger.warning(
                f"Course ID {course_id} not found in academic list {academic_list.name}"
            )
            logger.warning(
                f"Available courses: {[(c.id, c.code) for c in academic_list.courses]}"
            )

    if not course_code:
        # Use a placeholder code based on course ID if actual code not found
        course_code = f"COURSE_{course_id}"
        logger.warning(
            f"Could not find course code for course ID {course_id}, using placeholder {course_code}"
        )

    # Extract assignment details
    lecture_groups = assignment_data.get("lecture_groups", 1)
    lab_groups = assignment_data.get("lab_groups", 0)
    is_common = bool(assignment_data.get("is_common", False))
    practical_in_lab = bool(assignment_data.get("practical_in_labs", True))

    logger.info(f"Course code: {course_code}")
    logger.info(f"Lecture groups: {lecture_groups}")
    logger.info(f"Lab groups: {lab_groups}")
    logger.info(f"Is common: {is_common}")
    logger.info(f"Practical in lab: {practical_in_lab}")

    # Process lecturer assignments
    logger.info(f"\n--- PROCESSING LECTURERS ---")
    lecturers_data = assignment_data.get("lecturers", [])
    logger.info(f"Lecturers data count: {len(lecturers_data)}")
    logger.info(f"Lecturers data: {lecturers_data}")

    lecturers = []
    for i, lecturer_data in enumerate(lecturers_data):
        logger.info(f"Processing lecturer {i+1}: {lecturer_data}")
        lecturer_assignment = convert_api_lecturer_assignment(
            lecturer_data, fetch_lecturer=resolve_refs
        )
        lecturers.append(lecturer_assignment)
        logger.info(f"Lecturer {i+1} result: {list(lecturer_assignment.keys())}")

    logger.info(f"Total lecturers processed: {len(lecturers)}")

    # Process teaching assistant assignments
    logger.info(f"\n--- PROCESSING TEACHING ASSISTANTS ---")
    tas_data = assignment_data.get("teachingAssistants", [])
    logger.info(f"TAs data count: {len(tas_data)}")
    logger.info(f"TAs data: {tas_data}")

    teaching_assistants = []
    for i, ta_data in enumerate(tas_data):
        logger.info(f"Processing TA {i+1}: {ta_data}")
        ta_assignment = convert_api_ta_assignment(ta_data, fetch_ta=resolve_refs)
        teaching_assistants.append(ta_assignment)
        logger.info(f"TA {i+1} result: {list(ta_assignment.keys())}")

    logger.info(f"Total TAs processed: {len(teaching_assistants)}")

    # Process preferred labs
    logger.info(f"\n--- PROCESSING PREFERRED LABS ---")
    preferred_labs_data = assignment_data.get("preferredLabs", [])
    logger.info(f"Preferred labs data: {preferred_labs_data}")

    preferred_labs = []
    if resolve_refs and preferred_labs_data:
        # Fetch actual Lab objects
        logger.info("Resolving preferred labs...")
        all_labs = get_labs()
        logger.info(f"Available labs: {[lab.id for lab in all_labs]}")

        for lab_data in preferred_labs_data:
            lab_id = lab_data.get("id")
            logger.info(f"Looking for lab ID: {lab_id}")
            for lab in all_labs:
                if lab.id == lab_id:
                    preferred_labs.append(lab)
                    logger.info(f"Found preferred lab: {lab.name}")
                    break
            else:
                logger.warning(f"Preferred lab with ID {lab_id} not found")

    logger.info(f"Preferred labs resolved: {[lab.name for lab in preferred_labs]}")

    # Create the CourseAssignment object
    logger.info(f"\n--- CREATING COURSE ASSIGNMENT OBJECT ---")
    logger.info(f"Final parameters:")
    logger.info(f"  course_code: {course_code}")
    logger.info(f"  lecture_groups: {lecture_groups}")
    logger.info(f"  lecturers count: {len(lecturers)}")
    logger.info(f"  lab_groups: {lab_groups}")
    logger.info(f"  teaching_assistants count: {len(teaching_assistants)}")
    logger.info(f"  practical_in_lab: {practical_in_lab}")
    logger.info(f"  preferred_labs count: {len(preferred_labs)}")
    logger.info(f"  is_common: {is_common}")

    try:
        course_assignment = CourseAssignment(
            course_id=course_id,
            course_code=course_code,
            lecture_groups=lecture_groups,
            lecturers=lecturers,
            lab_groups=lab_groups,
            teaching_assistants=teaching_assistants if lab_groups > 0 else None,
            practical_in_lab=practical_in_lab,
            preferred_labs=preferred_labs if preferred_labs else None,
            is_common=is_common,
        )

        logger.info(f"Successfully created CourseAssignment for {course_code}")

        # Log final structure
        logger.info(f"Final CourseAssignment structure:")
        logger.info(f"  course_code: {course_assignment.course_code}")
        logger.info(f"  lecture_groups: {course_assignment.lecture_groups}")
        logger.info(f"  lab_groups: {course_assignment.lab_groups}")
        logger.info(f"  lecturers: {len(course_assignment.lecturers)}")
        logger.info(
            f"  teaching_assistants: {len(course_assignment.teaching_assistants) if course_assignment.teaching_assistants else 0}"
        )

        return course_assignment

    except Exception as e:
        logger.error(f"Failed to create CourseAssignment: {str(e)}", exc_info=True)
        raise


def convert_api_study_plan_summary(study_plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert API study plan summary data to a dictionary.

    Args:
        study_plan_data: Study plan summary data from the API

    Returns:
        Dictionary with study plan summary information
    """
    logger.debug(f"Converting study plan summary: {study_plan_data}")

    if not study_plan_data or not isinstance(study_plan_data, dict):
        raise ValueError(f"Invalid study plan data: {study_plan_data}")

    # Extract basic information
    plan_id = study_plan_data.get("id")
    name = study_plan_data.get("nameEn") or study_plan_data.get("name")
    name_ar = study_plan_data.get("nameAr")

    # Convert academic level string to integer
    academic_level_str = study_plan_data.get("academicLevel", "Level 1")
    academic_level = extract_academic_level(academic_level_str)

    # Convert expected students to integer
    expected_students = int(study_plan_data.get("expectedStudents", 0))

    # Get academic list information
    academic_list_data = study_plan_data.get("academicList", {})
    academic_list_summary = convert_api_academic_list_summary(academic_list_data)

    logger.debug(f"Converting study plan summary: {name} (ID: {plan_id})")

    return {
        "id": plan_id,
        "name": name,
        "name_ar": name_ar,
        "academic_list_id": academic_list_summary["id"],
        "academic_list_name": academic_list_summary["name"],
        "academic_level": academic_level,
        "expected_students": expected_students,
    }


def convert_api_study_plan_detail(
    study_plan_data: Dict[str, Any], resolve_refs: bool = True
) -> StudyPlan:
    """
    Convert API study plan detail data to StudyPlan object.

    Args:
        study_plan_data: Study plan detail data from the API
        resolve_refs: Whether to resolve references to other objects

    Returns:
        StudyPlan object
    """
    logger.info(f"=== CONVERTING STUDY PLAN DETAIL ===")
    logger.info(f"Study plan data keys: {list(study_plan_data.keys())}")
    logger.info(f"Resolve refs: {resolve_refs}")

    if not study_plan_data or not isinstance(study_plan_data, dict):
        raise ValueError(f"Invalid study plan data: {study_plan_data}")

    # Extract basic information
    name = study_plan_data.get("nameEn") or study_plan_data.get("name")

    logger.info(f"Study plan name: {name}")

    # Convert academic level string to integer
    academic_level_str = study_plan_data.get("academicLevel", "Level 1")
    academic_level = extract_academic_level(academic_level_str)

    # Convert expected students to integer
    expected_students = int(study_plan_data.get("expectedStudents", 0))

    logger.info(f"Academic level: {academic_level}")
    logger.info(f"Expected students: {expected_students}")

    # Get academic list
    academic_list_data = study_plan_data.get("academicList", {})
    academic_list_id = academic_list_data.get("id")

    logger.info(f"Academic list ID: {academic_list_id}")
    logger.info(f"Academic list data: {academic_list_data}")

    academic_list = None
    if resolve_refs:
        logger.info(f"Resolving academic list...")
        academic_list = get_academic_list_by_id(academic_list_id)

    if not academic_list:
        logger.warning(
            f"Could not fetch academic list with ID {academic_list_id} for study plan {name}"
        )
        # Create a minimal academic list with just the name
        academic_list_name = academic_list_data.get("nameEn") or academic_list_data.get(
            "name"
        )
        from models.department import Department

        academic_list = AcademicList(
            name=academic_list_name, department=Department(1, "general"), courses=[]
        )

    logger.info(f"Academic list: {academic_list.name}")

    # Process course assignments
    logger.info(f"\n--- PROCESSING COURSE ASSIGNMENTS ---")
    course_assignments_data = study_plan_data.get("courseAssignments", [])
    logger.info(f"Course assignments count: {len(course_assignments_data)}")

    course_assignments = []
    for i, assignment_data in enumerate(course_assignments_data):
        try:
            logger.info(
                f"\nProcessing course assignment {i+1}/{len(course_assignments_data)}"
            )
            assignment = convert_api_course_assignment(
                assignment_data, resolve_refs=resolve_refs, academic_list=academic_list
            )
            course_assignments.append(assignment)
            logger.info(
                f"Successfully processed course assignment {i+1}: {assignment.course_code}"
            )
        except Exception as e:
            logger.error(
                f"Error converting course assignment {i+1} in study plan {name}: {str(e)}",
                exc_info=True,
            )

    logger.info(f"Successfully processed {len(course_assignments)} course assignments")

    # Log summary
    total_lecture_groups = sum(ca.lecture_groups for ca in course_assignments)
    total_lab_groups = sum(ca.lab_groups or 0 for ca in course_assignments)

    logger.info(f"\n=== STUDY PLAN CONVERSION SUMMARY ===")
    logger.info(f"Study plan: {name}")
    logger.info(f"Academic level: {academic_level}")
    logger.info(f"Expected students: {expected_students}")
    logger.info(f"Course assignments: {len(course_assignments)}")
    logger.info(f"Total lecture groups: {total_lecture_groups}")
    logger.info(f"Total lab groups: {total_lab_groups}")

    # Create the StudyPlan object
    try:
        study_plan = StudyPlan(
            name=name,
            academic_list=academic_list,
            academic_level=academic_level,
            expected_students=expected_students,
            course_assignments=course_assignments,
        )

        logger.info(f"Successfully created StudyPlan object")
        return study_plan

    except Exception as e:
        logger.error(f"Failed to create StudyPlan object: {str(e)}", exc_info=True)
        raise
