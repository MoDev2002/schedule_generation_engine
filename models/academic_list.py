# models/academic_list.py


from dataclasses import dataclass, field
from typing import List, Optional

from models.department import Department


@dataclass
class Course:
    id: int
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
        if self.credit_hours != self.lecture_hours + (self.practical_hours / 2):
            raise ValueError(
                "Credit hours cannot be less than sum of lecture and practical hours"
            )


@dataclass
class AcademicList:
    id: int
    name: str
    department: Department
    courses: List[Course] = field(default_factory=list)

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Academic list must have a name")
        if not self.courses:
            raise ValueError("Academic list must have at least one course")


def print_course(course: Course):
    print(
        f"- {course.name_en} ({course.code}): "
        f"{course.lecture_hours} lecture hours / {course.practical_hours} practical hours / {course.credit_hours} credit hours "
    )
