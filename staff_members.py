from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import List, Dict


class Day(Enum):
    SUNDAY = 0
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6


@dataclass
class TimePreference:
    day: Day
    start_time: time
    end_time: time

    def __str__(self):
        return (
            f"{self.day.name}: {self.start_time.strftime('%I:%M %p')} - "
            f"{self.end_time.strftime('%I:%M %p')}"
        )


class BaseAvailability:
    @staticmethod
    def generate_base_availability() -> List[TimePreference]:
        # Define start and end times for the day
        day_start = time(9, 0)  # 9 AM
        day_end = time(19, 0)  # 7 PM
        slot_duration = 2  # 2 hours per slot

        availability = []

        # Generate time slots for each day
        for day in [Day.SUNDAY, Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY, Day.THURSDAY]:
            current_time = day_start
            while current_time.hour + slot_duration <= day_end.hour:
                # Skip Monday 1-3 PM slot
                if (
                    day == Day.MONDAY
                    and current_time.hour == 13
                    or (day == Day.MONDAY and current_time.hour == 12)
                ):
                    current_time = time(current_time.hour + 1, 0)
                    continue

                slot_end = time(current_time.hour + slot_duration, 0)

                # Add the time slot
                availability.append(
                    TimePreference(day=day, start_time=current_time, end_time=slot_end)
                )

                # Move to next slot
                current_time = time(current_time.hour + slot_duration, 0)

        return availability


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
        raise NotImplementedError("Subclasses must implement academic degree validation")


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


class Assistant(StaffMember):
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
    name="Dr. Tamer Ali",
    department=Department.INFORMATION_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.MONDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
)

eng_ahmed = Assistant(
    id=2,
    name="Eng. Ahmed Hassan",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.MONDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)

dr_heba = Lecturer(
    id=1,
    name="Dr. heba alhadedy",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.SUNDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
)

eng_asmaa = Assistant(
    id=2,
    name="Eng. asmaa ahmed",
    department=Department.INFORMATION_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.MONDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)
dr_ali = Lecturer(
    id=1,
    name="Dr. Ali elbaz",
    department=Department.INFORMATION_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.MONDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
)

eng_maya = Assistant(
    id=2,
    name="Eng. maya",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.MONDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=True,
)

dr_nesma = Lecturer(
    id=1,
    name="Dr. nesma mohamed",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.SUNDAY, Day.WEDNESDAY} and slot.start_time.hour in {9, 11}
    ],
    academic_degree=AcademicDegree.PROFESSOR,
    is_permanent=True,
)

eng_mohamed_tamer = Assistant(
    id=2,
    name="Eng. mohamed tamer",
    department=Department.COMPUTER_SCIENCE,
    timing_preferences=[
        slot
        for slot in base_availability
        if slot.day in {Day.SUNDAY, Day.WEDNESDAY} and slot.start_time.hour in {10, 7}
    ],
    academic_degree=AcademicDegree.TEACHING_ASSISTANT,
    is_permanent=False,
)

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
    print("Lecturer Details:")
    print_staff_member(dr_tamer)
    print("Assistant Details:")
    print_staff_member(eng_ahmed)
