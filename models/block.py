# models/block.py

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Union

from models.academic_list import AcademicList, Course
from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import TimePreference


class BlockType(Enum):
    LECTURE = "lecture"
    LAB = "lab"


SOURCE_COURSE = "course"
SOURCE_EXTERNAL = "external"


@dataclass
class Block:
    id: str  # unique identifier
    course_codes: List[str]  # Always a list, min 1 - same order as academic_lists
    course_objects: List[
        CourseAssignment
    ]  # Always a list, min 1 - same order as academic_lists
    block_type: BlockType
    # Staff is optional: external labs may run without internal staff and a
    # lecture in our halls may be delivered by an external lecturer (v5).
    staff_member: Optional[Union[Lecturer, TeachingAssistant]]
    student_count: int
    required_room_type: str  # 'hall' or 'lab'
    group_number: int  # which group this block represents
    total_groups: int  # total number of groups for this course
    is_single_group_course: bool  # if True, no parallel sessions allowed
    academic_lists: List[str]  # Academic list names (always a list, min 1)
    academic_list_objects: List[
        AcademicList
    ]  # Academic list objects (always a list, min 1)
    academic_levels: List[int]  # Academic levels in same order as academic_lists
    practical_in_lab: bool = True
    preferred_rooms: Optional[List[Union[Hall, Lab]]] = None
    is_merged_common_course: bool = False  # Flag to identify merged courses

    # Source discriminator: local curriculum course (default) or external
    # course. External blocks carry empty course_objects/academic arrays, so
    # get_all_course_ids() is empty and the section model derives no cohorts -
    # no student-clash constraints, with zero changes to the clash rule.
    source_kind: str = SOURCE_COURSE
    external_course_id: Optional[int] = None
    # Display-only copies of the external course identity for labels; never
    # used for scheduling decisions.
    external_course_name: Optional[str] = None
    external_course_code: Optional[str] = None
    # False only for external-venue lectures: they reserve a period and their
    # lecturer's time but no room of ours. Their Assignment.room is None.
    requires_room: bool = True
    # Hard candidate restriction (external courses only): when set, every
    # candidate period must sit wholly inside one of these declared 2h grid
    # slots - the requesting faculty's fixed slot set. None = free placement.
    allowed_slots: Optional[List[TimePreference]] = None
    # Placement span in hours. 2 for every local block and 2h external
    # sessions; 1h external sessions occupy half a grid period, so two of
    # them may pack back-to-back into one 2h slot (rooms and staff alike).
    session_hours: int = 2
    # Reserved-period override (external courses only): this block may sit in
    # the global reserved period. Scoped to the single course that opted in -
    # its assigned staff are exempt only in THIS course's sessions.
    allow_reserved_period: bool = False
    # Merged common courses only. The per-group arrays above carry the lists
    # actually attending THIS group (proportional per-list ownership of the
    # merged course's groups); merged_course_ids keeps the canonical full
    # participation set so identity, statistics, and the same-course-parallel
    # rule stay subset-proof.
    merged_course_ids: Optional[List[int]] = None

    def get_all_course_ids(self) -> Set[int]:
        """Get all course IDs associated with this block."""
        course_ids = set()
        for course_assignment in self.course_objects:
            if (
                hasattr(course_assignment, "course_ids")
                and course_assignment.course_ids
            ):
                course_ids.update(course_assignment.course_ids)
            else:
                course_ids.add(course_assignment.course_id)
        return course_ids

    def all_participating_course_ids(self) -> Set[int]:
        """
        Canonical course ids: the full merged participation set when present,
        else whatever the block arrays carry (plain and external blocks).
        """
        merged_ids = getattr(self, "merged_course_ids", None)
        return set(merged_ids) if merged_ids else self.get_all_course_ids()

    @property
    def is_external(self) -> bool:
        return self.source_kind == SOURCE_EXTERNAL

    def display_label(self) -> str:
        """
        Human label for failure records, insights, and reports.

        Every site that used to print course_codes[0] routes through here:
        external blocks have empty course_codes by design, so their identity
        comes from the external course instead of a fake local code. Local
        blocks resolve the course NAME from the participating lists' course
        catalogues - a bare code ("IT202") is not enough to act on.
        """
        if self.is_external:
            name = self.external_course_name or f"course {self.external_course_id}"
            code = (
                f" ({self.external_course_code})"
                if self.external_course_code
                else ""
            )
            return (
                f"External — {name}{code} {self.block_type.value} "
                f"G{self.group_number}"
            )
        if self.course_codes:
            primary = self.course_codes[0]
            name = self._course_name(primary)
            if name and name != primary:
                return f"{primary} — {name}"
            return primary
        return self.id

    def _course_name(self, code: str) -> Optional[str]:
        """Catalogue name for a course code, via the attending lists' course
        catalogues; None when no catalogue carries the code."""
        for academic_list in self.academic_list_objects or []:
            for course in getattr(academic_list, "courses", None) or []:
                if course.code == code:
                    return course.name_en or course.name_ar or None
        return None


@dataclass
class Assignment:
    block: Block
    time_slot: TimePreference
    # None only for roomless external-venue lectures (requires_room=False).
    room: Optional[Union[Hall, Lab]]
