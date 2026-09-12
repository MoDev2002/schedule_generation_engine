# models/external_course.py

"""Engine-side input model for an external course.

An external course is taught to an outside cohort using SchedU rooms, staff,
and grid without belonging to a local academic list, study plan, or course
catalogue. The model mirrors the study-plan staff-distribution shape so block
building and preflight logic can be shared, but carries no academic-list,
level, department, or course-catalogue data - those have no truth here.

The configuration is orthogonal axes, never a variant enum:

- lab axis:    no labs / staffless labs / TA-distributed labs
- lecture axis: no lecture / external venue (time + lecturer, no room) /
  our halls (optionally with no internal lecturer - the external-lecturer
  case)
"""

from dataclasses import dataclass, field
from typing import List, Optional

from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.time_preferences import TimePreference
from utils.time_utils import (
    GRID_SLOT_MINUTES,
    GRID_SLOT_START_MINUTES,
    to_minutes,
)

StaffDistribution = List[dict]  # [{staff: Lecturer|TeachingAssistant, num_of_groups: int}]


class ExternalCourseValidationError(ValueError):
    """Raised when an external course input cannot be scheduled as declared."""


@dataclass
class ExternalCourse:
    id: int
    name_en: str
    name_ar: str
    requesting_entity_en: str
    requesting_entity_ar: str
    lab_groups: int = 0
    lab_students_per_group: Optional[int] = None
    lecture_groups: int = 0
    lecture_students_per_group: Optional[int] = None
    lecture_venue: Optional[str] = None  # 'ours' | 'external'
    staff_assignments: StaffDistribution = field(default_factory=list)
    eligible_labs: Optional[List[Lab]] = None
    code: Optional[str] = None
    # Per-session span per component: 1h sessions may pack two groups
    # back-to-back into one 2h grid slot (rooms and staff included).
    lab_session_hours: int = 2
    lecture_session_hours: int = 2
    # Fixed/allowed 2h grid slots requested by the outside faculty, per
    # component. None/empty = the component may be scheduled anywhere - the
    # historical behaviour. A declared set is a hard candidate restriction.
    lab_time_slots: Optional[List[TimePreference]] = None
    lecture_time_slots: Optional[List[TimePreference]] = None
    # Reserved-period override: when set, THIS course's sessions - and only
    # those, i.e. only the staff assigned to it - may sit in the global
    # reserved period. Every other course, lecturer, and lab in the run still
    # avoids it.
    allow_reserved_period: bool = False

    def __post_init__(self):
        self._validate()

    # ------------------------------------------------------------------
    # Defence-in-depth validation (the API validates the same rules; the
    # engine refuses to schedule a shape it could never honour).
    # ------------------------------------------------------------------
    def _validate(self):
        if self.lab_groups < 0 or self.lecture_groups < 0:
            raise ExternalCourseValidationError(
                f"External course {self.id}: group counts cannot be negative"
            )

        if self.lab_groups == 0 and self.lecture_groups == 0:
            raise ExternalCourseValidationError(
                f"External course {self.id}: must request at least one "
                f"component (lab groups or lecture groups)"
            )

        if self.lab_groups > 0:
            if not self.lab_students_per_group or self.lab_students_per_group < 1:
                raise ExternalCourseValidationError(
                    f"External course {self.id}: lab_students_per_group is "
                    f"required when the course has lab groups"
                )

        if self.lecture_groups > 0:
            if not self.lecture_students_per_group or self.lecture_students_per_group < 1:
                raise ExternalCourseValidationError(
                    f"External course {self.id}: lecture_students_per_group is "
                    f"required when the course has lecture groups"
                )
            if self.lecture_venue not in ("ours", "external"):
                raise ExternalCourseValidationError(
                    f"External course {self.id}: lecture_venue must be "
                    f"'ours' or 'external' when lecture groups exist"
                )
        elif self.lecture_venue is not None:
            raise ExternalCourseValidationError(
                f"External course {self.id}: lecture_venue must be empty "
                f"when the course has no lecture groups"
            )

        self._validate_distributions()
        self._validate_session_hours("lab", self.lab_groups, self.lab_session_hours)
        self._validate_session_hours(
            "lecture", self.lecture_groups, self.lecture_session_hours
        )
        self._validate_time_slots("lab", self.lab_groups, self.lab_time_slots)
        self._validate_time_slots(
            "lecture", self.lecture_groups, self.lecture_time_slots
        )

    def _validate_session_hours(self, component: str, group_count: int, hours: int):
        if hours == 2:
            return
        if group_count == 0:
            raise ExternalCourseValidationError(
                f"External course {self.id}: {component} session hours are "
                f"not allowed without {component} groups"
            )
        if hours != 1:
            raise ExternalCourseValidationError(
                f"External course {self.id}: {component} session hours must "
                f"be 1 or 2, got {hours}"
            )

    def _validate_time_slots(
        self,
        component: str,
        group_count: int,
        slots: Optional[List[TimePreference]],
    ):
        """Defence in depth mirroring the API: slots belong to a component
        that exists, and each is a whole 2h grid period with no duplicates."""
        if not slots:
            return

        if group_count == 0:
            raise ExternalCourseValidationError(
                f"External course {self.id}: {component} time slots are not "
                f"allowed without {component} groups"
            )

        seen = set()
        for slot in slots:
            start = to_minutes(slot.start_time)
            end = to_minutes(slot.end_time)
            if (
                start not in GRID_SLOT_START_MINUTES
                or end - start != GRID_SLOT_MINUTES
            ):
                raise ExternalCourseValidationError(
                    f"External course {self.id}: {component} time slots must "
                    f"be whole 2h grid periods (09:00-19:00), got "
                    f"{slot.start_time.strftime('%H:%M')}-"
                    f"{slot.end_time.strftime('%H:%M')}"
                )
            key = (slot.day, slot.start_time)
            if key in seen:
                raise ExternalCourseValidationError(
                    f"External course {self.id}: the {component} time slot "
                    f"{slot.day.name} {slot.start_time.strftime('%H:%M')} is "
                    f"declared twice"
                )
            seen.add(key)

    def _validate_distributions(self):
        ta_groups = 0
        lecturer_groups = 0
        seen = set()

        for assignment in self.staff_assignments:
            staff = assignment["staff"]
            role = assignment["role"]
            key = (role, staff.id)
            if key in seen:
                raise ExternalCourseValidationError(
                    f"External course {self.id}: staff {staff.id} is assigned "
                    f"twice for the same role"
                )
            seen.add(key)

            num = assignment["num_of_groups"]
            if role == "ta":
                if not isinstance(staff, TeachingAssistant):
                    raise ExternalCourseValidationError(
                        f"External course {self.id}: role 'ta' staff "
                        f"{staff.id} is not a teaching assistant"
                    )
                if self.lab_groups == 0:
                    raise ExternalCourseValidationError(
                        f"External course {self.id}: TA assignments are not "
                        f"allowed without lab groups"
                    )
                ta_groups += num
            elif role == "lecturer":
                if not isinstance(staff, Lecturer):
                    raise ExternalCourseValidationError(
                        f"External course {self.id}: role 'lecturer' staff "
                        f"{staff.id} is not a lecturer"
                    )
                if self.lecture_groups == 0:
                    raise ExternalCourseValidationError(
                        f"External course {self.id}: lecturer assignments are "
                        f"not allowed without lecture groups"
                    )
                lecturer_groups += num
            else:
                raise ExternalCourseValidationError(
                    f"External course {self.id}: unknown staff role {role!r}"
                )

        # No partial coverage: a present distribution covers every group of
        # its component exactly - mixing staffed and staffless groups inside
        # one component is rejected.
        if ta_groups and ta_groups != self.lab_groups:
            raise ExternalCourseValidationError(
                f"External course {self.id}: TA assignments cover {ta_groups} "
                f"of {self.lab_groups} lab groups - partial coverage is not allowed"
            )

        if lecturer_groups and lecturer_groups != self.lecture_groups:
            raise ExternalCourseValidationError(
                f"External course {self.id}: lecturer assignments cover "
                f"{lecturer_groups} of {self.lecture_groups} lecture groups - "
                f"partial coverage is not allowed"
            )

        # A roomless (external-venue) lecture still reserves its internal
        # lecturer's time: roomless AND staffless would reserve nothing.
        if (
            self.lecture_groups > 0
            and self.lecture_venue == "external"
            and lecturer_groups == 0
        ):
            raise ExternalCourseValidationError(
                f"External course {self.id}: an external-venue lecture "
                f"requires at least one lecturer assignment"
            )

    # ------------------------------------------------------------------
    # Derived accessors
    # ------------------------------------------------------------------
    @property
    def has_external_lecturer(self) -> bool:
        """Lecture exists but no internal lecturer covers it (v5 when ours)."""
        return self.lecture_groups > 0 and not any(
            a["role"] == "lecturer" for a in self.staff_assignments
        )

    def display_name(self) -> str:
        label = self.name_en or f"course {self.id}"
        if self.code:
            return f"{label} ({self.code})"
        return label
