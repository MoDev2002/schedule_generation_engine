# models/study_plan.py


from dataclasses import dataclass
from typing import List, Optional, TypedDict

from models.academic_list import AcademicList, get_course_by_code
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


"""
def print_study_plan(study_plan: StudyPlan):
    print(
        f"Study Plan for {study_plan.academic_list.name} - Level {study_plan.academic_level}"
    )
    print("-" * 50)
    print(f"Expected Students: {study_plan.expected_students}")
    print("Courses and Assignments:")
    print("-" * 50)

    for course in study_plan.course_assignments:
        course_info = get_course_by_code(ai_academic_list, course.course_code)
        print(f"Course Code: {course.course_code}")
        print(f"Course Name: {course_info.name_en}")
        print(f"  Lecture Groups: {course.lecture_groups}")

        print("  Lecturers:")
        for lecturer in course.lecturers:
            print(
                f"    - {lecturer['lecturer'].name} ({lecturer['num_of_groups']} group(s))"
            )

        if hasattr(course, "lab_groups") and course.lab_groups:
            print(f"  Lab Groups: {course.lab_groups}")
            print("  Teaching Assistants:")
            for ta in course.teaching_assistants:
                print(
                    f"    - {ta['teaching_assistant'].name} ({ta['num_of_groups']} group(s))"
                )

        print("-" * 50)


ai_level1_study_plan = StudyPlan(
    academic_list=ai_academic_list,
    academic_level=1,
    expected_students=40,
    course_assignments=[
        CourseAssignment(
            course_code="UNV102",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_ahmed_alharby, "num_of_groups": 1},
            ],
        ),
        CourseAssignment(
            course_code="UNV103",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_abeer, "num_of_groups": 1},
            ],
        ),
        CourseAssignment(
            course_code="UNV104",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_nesma, "num_of_groups": 1},
            ],
        ),
        CourseAssignment(
            course_code="BS102",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_wael, "num_of_groups": 1},
            ],
            lab_groups=2,
            teaching_assistants=[
                {"teaching_assistant": eng_ibrahim, "num_of_groups": 2},
            ],
        ),
        CourseAssignment(
            course_code="BS103",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_ali, "num_of_groups": 1},
            ],
            lab_groups=2,
            teaching_assistants=[
                {"teaching_assistant": eng_sara, "num_of_groups": 2},
            ],
        ),
        CourseAssignment(
            course_code="CS103",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_gamal, "num_of_groups": 1},
            ],
            lab_groups=2,
            teaching_assistants=[
                {"teaching_assistant": eng_mohamed, "num_of_groups": 2},
            ],
        ),
        CourseAssignment(
            course_code="BS104",
            lecture_groups=1,
            lecturers=[
                {"lecturer": dr_mona, "num_of_groups": 1},
            ],
            lab_groups=2,
            teaching_assistants=[
                {"teaching_assistant": eng_ahmed, "num_of_groups": 2},
            ],
        ),
    ],
)


if __name__ == "__main__":
    print_study_plan(ai_level1_study_plan)
"""
