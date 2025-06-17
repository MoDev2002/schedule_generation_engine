# models/study_plan.py


from dataclasses import dataclass
from typing import List, Optional, TypedDict

from models.academic_list import AcademicList
from models.labs import Lab
from models.staff_members import *


# Define TypedDicts for better type hints
class LecturerAssignment(TypedDict):
    lecturer: Lecturer
    num_of_groups: int


class TAAssignment(TypedDict):
    teaching_assistant: TeachingAssistant
    num_of_groups: int


@dataclass
class CourseAssignment:
    course_id: int
    course_code: str
    lecture_groups: int
    lecturers: List[LecturerAssignment]
    lab_groups: Optional[int] = 0
    teaching_assistants: Optional[List[TAAssignment]] = None
    practical_in_lab: bool = True
    preferred_labs: Optional[List[Lab]] = None
    is_common: bool = False

    def __post_init__(self):
        # Basic validation
        if self.lecture_groups <= 0:
            raise ValueError("Must have at least one lecture group")
        if not self.lecturers:
            raise ValueError("Must have at least one lecturer assigned")

        # Validate total lecturer groups matches lecture_groups
        total_lecturer_groups = sum(
            assignment["num_of_groups"] for assignment in self.lecturers
        )
        if total_lecturer_groups != self.lecture_groups:
            raise ValueError(
                f"Sum of lecturer groups ({total_lecturer_groups}) "
                f"must equal total lecture groups ({self.lecture_groups})"
            )

        # Validate teaching assistants if lab groups exist
        if self.lab_groups > 0:
            if not self.teaching_assistants:
                raise ValueError("Must assign teaching assistants if lab groups exist")

            # Validate total teaching assistant groups matches lab_groups
            total_ta_groups = sum(
                assignment["num_of_groups"] for assignment in self.teaching_assistants
            )
            if total_ta_groups != self.lab_groups:
                raise ValueError(
                    f"Sum of teaching assistant groups ({total_ta_groups}) "
                    f"must equal total lab groups ({self.lab_groups})"
                )


@dataclass
class StudyPlan:
    name: str
    academic_list: AcademicList
    academic_level: int
    expected_students: int
    course_assignments: List[CourseAssignment]

    def __post_init__(self):
        if self.academic_level < 1:
            raise ValueError("Academic level must be positive")
        if self.expected_students <= 0:
            raise ValueError("Expected students must be positive")
        if not self.course_assignments:
            raise ValueError("Study plan must have at least one course assignment")
