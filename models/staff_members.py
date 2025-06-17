# models/staff_members.py


from dataclasses import dataclass
from typing import List

from models.department import Department
from models.time_preferences import BaseAvailability, Day, TimePreference


@dataclass
class AcademicDegree:
    id: int
    name: str
    prefix: str

    def __str__(self):
        return self.name


@dataclass
class StaffMember:
    id: int
    name: str
    department: Department
    timing_preferences: List[TimePreference]
    academic_degree: AcademicDegree
    is_permanent: bool

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Staff member must have a name")
        self._validate_academic_degree()

    def _validate_academic_degree(self):
        raise NotImplementedError(
            "Subclasses must implement academic degree validation"
        )


class Lecturer(StaffMember):
    ALLOWED_DEGREES = {
        1,  # استاذ
        2,  # استاذ مساعد
        3,  # مدرس
    }

    def _validate_academic_degree(self):
        if self.academic_degree.id not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for lecturer: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )


class TeachingAssistant(StaffMember):
    ALLOWED_DEGREES = {
        4,
        5,
    }

    def _validate_academic_degree(self):
        if self.academic_degree.id not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for assistant: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )
