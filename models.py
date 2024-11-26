from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from typing import List, Optional

from labs import Lab
from time_prefrences import BaseAvailability, TimePreference


class AcademicDegree(Enum):
    PROFESSOR = "professor"
    ASSOCIATE_PROFESSOR = "associate_professor"
    ASSISTANT_PROFESSOR = "assistant_professor"
    ASSISTANT_LECTURER = "assistant_lecturer"
    TEACHING_ASSISTANT = "teaching_assistant"


class Department(Enum):
    COMPUTER_SCIENCE = "Computer Science"
    INFORMATION_TECHNOLOGY = "Information Technology"
    INFORMATION_SCIENCE = "Information Science"
    GENERAL = "General"
    ARTIFICIAL_INTELLIGENCE = "Artificial Intelligence"
    CYBERSECURITY = "Cybersecurity"

    def __post_init__(self):
        if self.start_time >= self.end_time:
            raise ValueError("End time must be after start time")


@dataclass
class StaffMember:
    id: int
    name: str
    department: Department
    timing_preferences: List[TimePreference]
    academic_degree: AcademicDegree
    is_permanent: bool

    def __post_init__(self):
        """Validate academic degree after initialization"""
        self._validate_academic_degree()
        if not self.name.strip():
            raise ValueError("Staff member must have a name")

    def _validate_academic_degree(self):
        raise NotImplementedError(
            "Subclasses must implement academic degree validation"
        )


class Lecturer(StaffMember):
    ALLOWED_DEGREES = {
        AcademicDegree.PROFESSOR,
        AcademicDegree.ASSOCIATE_PROFESSOR,
        AcademicDegree.ASSISTANT_PROFESSOR,
    }

    def _validate_academic_degree(self):
        if self.academic_degree not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for lecturer: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )


class TeachingAssistant(StaffMember):
    ALLOWED_DEGREES = {
        AcademicDegree.TEACHING_ASSISTANT,
        AcademicDegree.ASSISTANT_LECTURER,
    }

    def _validate_academic_degree(self):
        if self.academic_degree not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for teaching assistant: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )


@dataclass
class Course:
    code: str
    name_en: str
    name_ar: str
    lecture_hours: int
    practical_hours: int
    credit_hours: int
    prerequisite_course: Optional[str] = None

    def __post_init__(self):
        if self.lecture_hours < 0 or self.practical_hours < 0 or self.credit_hours < 0:
            raise ValueError("Hours cannot be negative")
        if self.credit_hours < self.lecture_hours + self.practical_hours:
            raise ValueError(
                "Credit hours cannot be less than sum of lecture and practical hours"
            )


@dataclass
class AcademicList:
    name: str
    department: Department
    courses: List[Course] = field(default_factory=list)

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Academic list must have a name")
        if not self.courses:
            raise ValueError("Academic list must have at least one course")


@dataclass
class CourseAssignment:
    course_code: str
    lecture_groups: int
    lecturers: List[tuple[Lecturer, int]]  # (lecturer, number_of_groups)
    lab_groups: Optional[int] = 0
    teaching_assistants: Optional[List[tuple[TeachingAssistant, int]]] = None
    practical_in_lab: bool = False
    preferred_labs: Optional[List[Lab]] = None
    is_common: bool = False

    def __post_init__(self):
        # Basic validation
        if self.lecture_groups <= 0:
            raise ValueError("Must have at least one lecture group")
        if not self.lecturers:
            raise ValueError("Must have at least one lecturer assigned")

        # Validate total lecturer groups matches lecture_groups
        total_lecturer_groups = sum(groups for _, groups in self.lecturers)
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
            total_ta_groups = sum(groups for _, groups in self.teaching_assistants)
            if total_ta_groups != self.lab_groups:
                raise ValueError(
                    f"Sum of teaching assistant groups ({total_ta_groups}) "
                    f"must equal total lab groups ({self.lab_groups})"
                )

        # Validate lab preferences
        if self.practical_in_lab and not self.preferred_labs and self.lab_groups > 0:
            raise ValueError("Must specify preferred labs if practical is in lab")


@dataclass
class StudyPlan:
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


# Example usage:
def create_staff_example():
    try:
        # Valid lecturer creation
        lecturer = Lecturer(
            id=1,
            name="Dr. Smith",
            department=Department.COMPUTER_SCIENCE,
            timing_preferences=[],
            academic_degree=AcademicDegree.PROFESSOR,
            is_permanent=True,
        )
        print("Successfully created lecturer:", lecturer)

        # Create a course
        course = Course(
            code="CS101",
            name_en="Introduction to Programming",
            name_ar="مقدمة في البرمجة",
            lecture_hours=2,
            practical_hours=2,
            credit_hours=3,
            department=Department.COMPUTER_SCIENCE,
        )
        print("Successfully created course:", course)

        # Create an academic list
        academic_list = AcademicList(
            name="Computer Science Year 1",
            department=Department.COMPUTER_SCIENCE,
            courses=[course],
        )
        print("Successfully created academic list:", academic_list)

        # # This will raise an error - invalid department string
        # invalid_lecturer = Lecturer(
        #     id=2,
        #     name="Dr. Jones",
        #     department="Biology",  # This will raise a TypeError
        #     timing_preferences=[],
        #     academic_degree=AcademicDegree.PROFESSOR,
        #     is_permanent=True,
        # )
    except (ValueError, TypeError) as e:
        print("Validation Error:", e)


if __name__ == "__main__":
    create_staff_example()
