from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import Dict, List

from models import Department
from time_preferences import BaseAvailability, Day, TimePreference


class AcademicDegree(Enum):
    PROFESSOR = "professor"
    ASSOCIATE_PROFESSOR = "associate_professor"
    ASSISTANT_PROFESSOR = "assistant_professor"
    ASSISTANT_LECTURER = "assistant_lecturer"
    TEACHING_ASSISTANT = "teaching_assistant"


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
        AcademicDegree.PROFESSOR,  # استاذ
        AcademicDegree.ASSOCIATE_PROFESSOR,  # استاذ مساعد
        AcademicDegree.ASSISTANT_PROFESSOR,  # مدرس
    }

    def _validate_academic_degree(self):
        if self.academic_degree not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for lecturer: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )


class TeachingAssistant(StaffMember):
    ALLOWED_DEGREES = {
        AcademicDegree.ASSISTANT_LECTURER,
        AcademicDegree.TEACHING_ASSISTANT,
    }

    def _validate_academic_degree(self):
        if self.academic_degree not in self.ALLOWED_DEGREES:
            raise ValueError(
                f"Invalid academic degree for assistant: {self.academic_degree}. "
                f"Must be one of: {', '.join(degree.value for degree in self.ALLOWED_DEGREES)}"
            )


# Create staff members with base availability
base_availability = BaseAvailability.generate_base_availability()

dr_tamer = Lecturer(
    id=1,
    name="Dr. Tamer Emara",
    department=Department.INFORMATION_SYSTEMS,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=True,
)

dr_aya = Lecturer(
    id=3,
    name="Dr. Aya Gamal",
    department=Department.INFORMATION_TECHNOLOGY,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=True,
)

dr_ahmed = Lecturer(
    id=4,
    name="Dr. Ahmed Rabiee",
    department=Department.INFORMATION_TECHNOLOGY,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=True,
)

dr_mona = Lecturer(
    id=8,
    name="Dr. Mona El Bedwehy",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=True,
)

dr_okasha = Lecturer(
    id=5,
    name="Dr. Mohamed Okasha",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=False,
)

dr_abeer = Lecturer(
    id=6,
    name="Dr. Abeer Saber",
    department=Department.INFORMATION_TECHNOLOGY,
    academic_degree=AcademicDegree.ASSISTANT_PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_ahmed_alharby = Lecturer(
    id=7,
    name="Dr. Ahmed Alharby",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_nesma = Lecturer(
    id=9,
    name="Dr. Nesma Ibrahim",
    department=Department.INFORMATION_SYSTEMS,
    academic_degree=AcademicDegree.ASSISTANT_PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_wael = Lecturer(
    id=10,
    name="Dr. Wael Awad",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_ali = Lecturer(
    id=11,
    name="Dr. Ali Al Baz",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_gamal = Lecturer(
    id=12,
    name="Dr. Gamal Behery",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_amira = Lecturer(
    id=13,
    name="Dr. Amira El Zeiny",
    department=Department.INFORMATION_SYSTEMS,
    academic_degree=AcademicDegree.ASSISTANT_PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_samar = Lecturer(
    id=14,
    name="Dr. Samar El Bedwehy",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.ASSISTANT_PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

dr_heba = Lecturer(
    id=15,
    name="Dr. Heba El Hadidy",
    department=Department.COMPUTER_SCIENCE,
    academic_degree=AcademicDegree.ASSOCIATE_PROFESSOR,
    is_permanent=True,
    timing_preferences=base_availability,
)

eng_ahmed = TeachingAssistant(
    id=6,
    name="Eng. Ahmed El Gabry",
    department=Department.INFORMATION_TECHNOLOGY,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)

eng_ibrahim = TeachingAssistant(
    id=2,
    name="Eng. Ibrahim El Gazar",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)

eng_sara = TeachingAssistant(
    id=7,
    name="Eng. Sara Lotfy",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)


eng_sayed = TeachingAssistant(
    id=9,
    name="Eng. Sayed El Baz",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)
eng_maya = TeachingAssistant(
    id=10,
    name="Eng. Maya Hesham",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)

eng_fatma = TeachingAssistant(
    id=11,
    name="Eng. Fatma Abd El Daym",
    department=Department.INFORMATION_SYSTEMS,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.ASSISTANT_LECTURER,
    is_permanent=False,
)

eng_sohaila = TeachingAssistant(
    id=12,
    name="Eng. Sohaila El Shamy",
    department=Department.INFORMATION_TECHNOLOGY,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)

eng_mohamed = TeachingAssistant(
    id=13,
    name="Eng. Mohamed El Masry",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)

eng_hanem = TeachingAssistant(
    id=14,
    name="Eng. Hanem",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)

eng_mariem = TeachingAssistant(
    id=15,
    name="Eng. Mariem Khashba",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=base_availability,
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)


# List of all lecturers and assistants
lecturers = [
    dr_tamer,
    dr_aya,
    dr_ahmed,
    dr_okasha,
    dr_mona,
    dr_amira,
    dr_samar,
    dr_heba,
    dr_ahmed_alharby,
    dr_gamal,
    dr_ali,
    dr_wael,
    dr_nesma,
    dr_abeer,
]
assistants = [
    eng_ibrahim,
    eng_ahmed,
    eng_sara,
    eng_sayed,
    eng_maya,
    eng_fatma,
    eng_sohaila,
    eng_mohamed,
    eng_hanem,
    eng_mariem,
]


# Print organized data
def print_staff_member(member: StaffMember):
    print(f"ID: {member.id}")
    print(f"Name: {member.name}")
    print(f"Department: {member.department.value}")
    print(f"Academic Degree: {member.academic_degree.value}")
    print(f"Permanent: {'Yes' if member.is_permanent else 'No'}")
    print("Timing Preferences:")
    for preference in member.timing_preferences:
        print(f"  - {preference}")
    print("-" * 40)


if __name__ == "__main__":
    print("Lecturers:")
    for lecturer in lecturers:
        print_staff_member(lecturer)

    print("Assistants:")
    for assistant in assistants:
        print_staff_member(assistant)
