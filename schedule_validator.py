# schedule_validator.py


import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Set, Tuple

from models.block import Assignment, Block
from models.labs import Lab
from models.study_plan import CourseAssignment, StudyPlan
from utils.room_utils import get_room_key
from utils.sections import clash_reason, cohort_keys
from utils.time_utils import (
    blocker_overlaps_slot,
    intervals_overlap,
    preference_covers_slot,
)

# Conflict types that make a schedule unusable. Anything else is reported but
# does not block publication.
BLOCKING_CONFLICT_TYPES = {
    "ROOM_CONFLICT",
    "STAFF_CONFLICT",
    "STUDENT_CONFLICT",
    "STAFF_BLOCKER_CONFLICT",
}


class ValidationLevel(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationMessage:
    level: ValidationLevel
    message: str
    context: dict
    # default_factory, so each message is stamped when it is created rather than
    # all of them sharing the moment the module was imported.
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ConflictReport:
    conflict_type: str
    description: str
    affected_assignments: List[str]
    details: Dict


class ScheduleValidator:
    def __init__(self, strict_cohort_mode: bool = False):
        self.logger = self._setup_logger()
        # False (default): the section model (group k = section k per cohort);
        # True: the historical conservative rule. See utils/sections.py.
        self.strict_cohort_mode = strict_cohort_mode
        self.validation_messages: List[ValidationMessage] = []

    def _setup_logger(self):
        """
        Use this module's own logger and inherit the app's handlers.

        Previously this took the "scheduler" logger - the engine's - and attached
        a fresh file handler on every construction, so engine output was
        duplicated into a second log file that setup_logging knew nothing about.
        """
        return logging.getLogger("schedule_validator")

    def validate_input_data(
        self, study_plans: List[StudyPlan]
    ) -> List[ValidationMessage]:
        """Validate input data before scheduling"""
        self.validation_messages = []

        for plan in study_plans:
            self._validate_study_plan(plan)

        return self.validation_messages

    def _validate_study_plan(self, plan: StudyPlan):
        """Validate a single study plan"""
        # Validate basic study plan properties
        if plan.expected_students <= 0:
            self._add_error(
                "Invalid expected students count",
                {
                    "study_plan": plan.academic_list.name,
                    "count": plan.expected_students,
                },
            )

        if plan.academic_level < 1:
            self._add_error(
                "Invalid academic level",
                {"study_plan": plan.academic_list.name, "level": plan.academic_level},
            )

        # Validate course assignments
        for course in plan.course_assignments:
            self._validate_course_assignment(course, plan)

    def _validate_course_assignment(self, course: CourseAssignment, plan: StudyPlan):
        """Validate a course assignment"""
        if course.is_common:
            self._add_error(
                "Unresolved common-course reference",
                {
                    "course": course.course_code,
                    "study_plan_id": plan.id,
                    "parent_study_plan_id": course.common_study_plan_id,
                    "parent_assignment_id": course.common_course_id,
                },
            )
            return

        # Validate group numbers
        if course.lecture_groups < 1:
            self._add_error(
                "Invalid lecture groups count",
                {"course": course.course_code, "groups": course.lecture_groups},
            )

        if course.lab_groups and course.lab_groups < 1:
            self._add_error(
                "Invalid lab groups count",
                {"course": course.course_code, "groups": course.lab_groups},
            )

        # Validate lecturer assignments
        total_lecturer_groups = sum(la["num_of_groups"] for la in course.lecturers)
        if total_lecturer_groups != course.lecture_groups:
            self._add_error(
                "Mismatch in lecturer group assignments",
                {
                    "course": course.course_code,
                    "expected": course.lecture_groups,
                    "assigned": total_lecturer_groups,
                },
            )

        # Validate TA assignments if lab groups exist
        if course.lab_groups:
            if not course.teaching_assistants:
                self._add_error(
                    "Missing TA assignments for lab groups",
                    {"course": course.course_code},
                )
            else:
                total_ta_groups = sum(
                    ta["num_of_groups"] for ta in course.teaching_assistants
                )
                if total_ta_groups != course.lab_groups:
                    self._add_error(
                        "Mismatch in TA group assignments",
                        {
                            "course": course.course_code,
                            "expected": course.lab_groups,
                            "assigned": total_ta_groups,
                        },
                    )

    def validate_schedule(
        self, assignments: Dict[str, Assignment], blocks: List[Block]
    ) -> List[ValidationMessage]:
        """Validate the generated schedule"""
        self.validation_messages = []

        # Check if all blocks are assigned
        assigned_blocks = set(assignments.keys())
        all_blocks = set(block.id for block in blocks)
        unassigned = all_blocks - assigned_blocks

        if unassigned:
            self._add_error(
                "Unassigned blocks found", {"unassigned_blocks": list(unassigned)}
            )

        # Validate individual assignments
        self._validate_assignments(assignments)

        # Check for resource conflicts
        self._check_resource_conflicts(assignments)

        return self.validation_messages

    def _validate_assignments(self, assignments: Dict[str, Assignment]):
        """Validate individual assignments"""
        for block_id, assignment in assignments.items():
            # Roomless external-venue lectures book no room: no room type,
            # capacity, or window checks apply.
            if assignment.room is None:
                continue

            # Validate room type
            if assignment.block.required_room_type == "lab":
                if not isinstance(assignment.room, Lab):
                    self._add_error(
                        "Invalid room type assignment",
                        {
                            "block": block_id,
                            "required": "lab",
                            "assigned": "hall",
                            "room_composite_id": f"{get_room_key(assignment.room)[0]}_{get_room_key(assignment.room)[1]}",
                        },
                    )

            # Validate room capacity
            if assignment.room.capacity < assignment.block.student_count:
                room_type, room_id = get_room_key(assignment.room)
                self._add_warning(
                    "Room capacity may be insufficient",
                    {
                        "block": block_id,
                        "room_composite_id": f"{room_type}_{room_id}",
                        "room_name": assignment.room.name,
                        "capacity": assignment.room.capacity,
                        "students": assignment.block.student_count,
                    },
                )

            # Validate time slot
            self._validate_time_slot(assignment)

    def _validate_time_slot(self, assignment: Assignment):
        """Validate time slot assignment"""
        if assignment.room is None:
            return

        # Check if time slot is within room availability
        slot_valid = any(
            preference_covers_slot(available, assignment.time_slot)
            for available in assignment.room.availability
        )

        if not slot_valid:
            room_type, room_id = get_room_key(assignment.room)
            self._add_error(
                "Invalid time slot assignment",
                {
                    "block": assignment.block.id,
                    "assigned_slot": str(assignment.time_slot),
                    "room_composite_id": f"{room_type}_{room_id}",
                    "room_name": assignment.room.name,
                },
            )

    def _check_resource_conflicts(self, assignments: Dict[str, Assignment]):
        """Record room, staff and student conflicts as validation errors"""
        for conflict in self.validate_schedule_comprehensive(assignments)[1]:
            if conflict.conflict_type in BLOCKING_CONFLICT_TYPES:
                self._add_error(conflict.description, conflict.details)
            else:
                self._add_warning(conflict.description, conflict.details)

    def validate_schedule_comprehensive(
        self, assignments: Dict[str, Assignment]
    ) -> Tuple[bool, List[ConflictReport]]:
        """
        Comprehensive validation of the final schedule to detect all types of conflicts.

        Returns:
            Tuple of (is_valid, list_of_conflicts)
        """
        conflicts = []

        # 1. Check room double-booking
        room_conflicts = self._check_room_conflicts(assignments)
        conflicts.extend(room_conflicts)

        # 2. Check staff double-booking
        staff_conflicts = self._check_staff_conflicts(assignments)
        conflicts.extend(staff_conflicts)

        # 3. Check student schedule conflicts
        student_conflicts = self._check_student_conflicts(assignments)
        conflicts.extend(student_conflicts)

        # 4. Check room availability constraints
        room_availability_conflicts = self._check_room_availability_conflicts(
            assignments
        )
        conflicts.extend(room_availability_conflicts)

        # 5. Check staff blocker overlaps (hard for lecturers and TAs alike)
        blocker_conflicts = self._check_staff_blocker_conflicts(assignments)
        conflicts.extend(blocker_conflicts)

        # 6. Check capacity violations
        capacity_conflicts = self._check_capacity_violations(assignments)
        conflicts.extend(capacity_conflicts)

        is_valid = len(conflicts) == 0

        return is_valid, conflicts

    @staticmethod
    def split_conflicts(
        conflicts: List[ConflictReport],
    ) -> Tuple[List[ConflictReport], List[ConflictReport]]:
        """
        Separate conflicts that must block publication from those that need not.

        Blocking means a real double booking - a room, a staff member or a cohort
        in two places at once. Capacity overruns and out-of-hours TA placements
        are reported for a human to accept or rework.
        """
        blocking = [
            c for c in conflicts if c.conflict_type in BLOCKING_CONFLICT_TYPES
        ]
        advisory = [
            c for c in conflicts if c.conflict_type not in BLOCKING_CONFLICT_TYPES
        ]
        return blocking, advisory

    @staticmethod
    def _overlapping_pairs(assignments: Dict[str, Assignment]):
        """
        Every pair of assignments that share any time.

        Pairwise rather than grouped by start time: two sessions can overlap
        without starting at the same minute, and grouping would miss those.
        """
        items = list(assignments.items())
        for i in range(len(items)):
            id_a, a = items[i]
            for j in range(i + 1, len(items)):
                id_b, b = items[j]
                if a.time_slot.day != b.time_slot.day:
                    continue
                if intervals_overlap(
                    a.time_slot.start_time,
                    a.time_slot.end_time,
                    b.time_slot.start_time,
                    b.time_slot.end_time,
                ):
                    yield id_a, a, id_b, b

    @staticmethod
    def _when(a: Assignment, b: Assignment) -> str:
        return (
            f"{a.time_slot.day.name} "
            f"{a.time_slot.start_time}-{a.time_slot.end_time} / "
            f"{b.time_slot.start_time}-{b.time_slot.end_time}"
        )

    @staticmethod
    def _cohorts(block: Block) -> Set[Tuple[str, int]]:
        """(academic list, level) pairs a block puts in a room."""
        return set(cohort_keys(block))

    def _check_room_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check for room double-booking conflicts"""
        conflicts = []

        for id_a, a, id_b, b in self._overlapping_pairs(assignments):
            # A roomless session occupies no room, so it cannot double-book
            # one; two roomless sessions share no room key either.
            if a.room is None or b.room is None:
                continue
            if get_room_key(a.room) != get_room_key(b.room):
                continue

            conflicts.append(
                ConflictReport(
                    conflict_type="ROOM_CONFLICT",
                    description=(
                        f"Room {a.room.name} double-booked at {self._when(a, b)}"
                    ),
                    affected_assignments=[id_a, id_b],
                    details={
                        "room_id": get_room_key(a.room),
                        "room_name": a.room.name,
                        "day": a.time_slot.day.name,
                        "time": self._when(a, b),
                        "conflicting_courses": [
                            a.block.display_label(),
                            b.block.display_label(),
                        ],
                    },
                )
            )

        return conflicts

    def _check_staff_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check for staff double-booking conflicts"""
        conflicts = []

        for id_a, a, id_b, b in self._overlapping_pairs(assignments):
            # Only compare when BOTH sides have staff: two staffless
            # externals would otherwise false-positive as "Staff None
            # double-booked".
            if a.block.staff_member is None or b.block.staff_member is None:
                continue
            if a.block.staff_member.id != b.block.staff_member.id:
                continue

            conflicts.append(
                ConflictReport(
                    conflict_type="STAFF_CONFLICT",
                    description=(
                        f"Staff {a.block.staff_member.name} double-booked at "
                        f"{self._when(a, b)}"
                    ),
                    affected_assignments=[id_a, id_b],
                    details={
                        "staff_id": a.block.staff_member.id,
                        "staff_name": a.block.staff_member.name,
                        "day": a.time_slot.day.name,
                        "time": self._when(a, b),
                        "conflicting_courses": [
                            a.block.display_label(),
                            b.block.display_label(),
                        ],
                    },
                )
            )

        return conflicts

    def _check_student_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """
        Check for student schedule conflicts.

        The rule lives in utils/sections.py - the single source of truth the
        constraint manager, repair and final verification all share: sections
        mode (default) forbids overlapping the same student section of a
        cohort; strict_cohort_mode restores the historical conservative rule.
        """
        conflicts = []

        for id_a, a, id_b, b in self._overlapping_pairs(assignments):
            shared = self._cohorts(a.block) & self._cohorts(b.block)
            if not shared:
                continue

            reason = clash_reason(a.block, b.block, self.strict_cohort_mode)
            if reason is None:
                continue

            for academic_list, level in sorted(shared):
                conflicts.append(
                    ConflictReport(
                        conflict_type="STUDENT_CONFLICT",
                        description=(
                            f"Student conflict for {academic_list} Level {level} "
                            f"at {self._when(a, b)} ({reason})"
                        ),
                        affected_assignments=[id_a, id_b],
                        details={
                            "academic_list": academic_list,
                            "academic_level": level,
                            "day": a.time_slot.day.name,
                            "time": self._when(a, b),
                            "reason": reason,
                            "conflicting_courses": [
                                a.block.display_label(),
                                b.block.display_label(),
                            ],
                        },
                    )
                )

        return conflicts


    def _check_room_availability_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check if rooms are used outside their availability"""
        conflicts = []

        for assignment_id, assignment in assignments.items():
            room = assignment.room
            time_slot = assignment.time_slot

            # Roomless sessions sit on the grid, not in any room's windows.
            if room is None:
                continue

            # Check if the assignment time falls within room availability
            is_available = any(
                preference_covers_slot(availability, time_slot)
                for availability in room.availability
            )

            if not is_available:
                conflicts.append(
                    ConflictReport(
                        conflict_type="ROOM_AVAILABILITY_CONFLICT",
                        description=f"Room {room.name} used outside availability",
                        affected_assignments=[assignment_id],
                        details={
                            "room_name": room.name,
                            "assigned_time": f"{time_slot.day.name} {time_slot.start_time}-{time_slot.end_time}",
                            "course": assignment.block.display_label(),
                        },
                    )
                )

        return conflicts

    def _check_staff_blocker_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check if any session overlaps a blocker of its staff member.

        The mirror of the room-availability rule for people: blockers bind
        BOTH roles by overlap, so any shared time is a blocking conflict.
        """
        conflicts = []

        for assignment_id, assignment in assignments.items():
            staff_member = assignment.block.staff_member
            time_slot = assignment.time_slot

            # Staffless sessions violate nobody's blocked time.
            if staff_member is None:
                continue

            blocker = next(
                (
                    b
                    for b in staff_member.blockers
                    if blocker_overlaps_slot(b, time_slot)
                ),
                None,
            )

            if blocker is not None:
                label = f" ({blocker.label})" if blocker.label else ""
                conflicts.append(
                    ConflictReport(
                        conflict_type="STAFF_BLOCKER_CONFLICT",
                        description=(
                            f"{staff_member.name} scheduled inside a blocker"
                            f"{label}"
                        ),
                        affected_assignments=[assignment_id],
                        details={
                            "staff_id": staff_member.id,
                            "staff_name": staff_member.name,
                            "label": blocker.label,
                            "assigned_time": (
                                f"{time_slot.day.name} "
                                f"{time_slot.start_time}-{time_slot.end_time}"
                            ),
                            "course": assignment.block.display_label(),
                        },
                    )
                )

        return conflicts

    def _check_capacity_violations(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check for room capacity violations"""
        conflicts = []

        for assignment_id, assignment in assignments.items():
            # Roomless sessions occupy no seats of ours; they cannot overflow.
            if assignment.room is None:
                continue

            room_capacity = assignment.room.capacity
            student_count = assignment.block.student_count

            if student_count > room_capacity:
                conflicts.append(
                    ConflictReport(
                        conflict_type="CAPACITY_VIOLATION",
                        description=f"Room {assignment.room.name} capacity exceeded",
                        affected_assignments=[assignment_id],
                        details={
                            "room_name": assignment.room.name,
                            "room_capacity": room_capacity,
                            "student_count": student_count,
                            "course": assignment.block.display_label(),
                        },
                    )
                )

        return conflicts

    def print_conflict_report(self, conflicts: List[ConflictReport]):
        """Print a detailed conflict report"""
        if not conflicts:
            print("✅ No conflicts found - schedule is valid!")
            return

        print(f"❌ Found {len(conflicts)} conflicts:")
        print("=" * 60)

        # Group conflicts by type
        by_type = defaultdict(list)
        for conflict in conflicts:
            by_type[conflict.conflict_type].append(conflict)

        for conflict_type, type_conflicts in by_type.items():
            print(f"\n{conflict_type} ({len(type_conflicts)} conflicts):")
            print("-" * 40)

            for i, conflict in enumerate(type_conflicts, 1):
                print(f"{i}. {conflict.description}")
                if conflict.details.get("conflicting_courses"):
                    courses = ", ".join(conflict.details["conflicting_courses"])
                    print(f"   Courses: {courses}")
                if conflict.details.get("staff_name"):
                    print(f"   Staff: {conflict.details['staff_name']}")
                if conflict.details.get("room_name"):
                    print(f"   Room: {conflict.details['room_name']}")
                print(f"   Affected assignments: {len(conflict.affected_assignments)}")
                print()

    def _add_error(self, message: str, context: dict):
        """Add error level validation message"""
        self.validation_messages.append(
            ValidationMessage(ValidationLevel.ERROR, message, context)
        )
        self.logger.error(f"{message} - Context: {context}")

    def _add_warning(self, message: str, context: dict):
        """Add warning level validation message"""
        self.validation_messages.append(
            ValidationMessage(ValidationLevel.WARNING, message, context)
        )
        self.logger.warning(f"{message} - Context: {context}")

    def _add_info(self, message: str, context: dict):
        """Add info level validation message"""
        self.validation_messages.append(
            ValidationMessage(ValidationLevel.INFO, message, context)
        )
        self.logger.info(f"{message} - Context: {context}")

    def get_validation_summary(self) -> Dict:
        """Generate summary of validation results"""
        return {
            "total_messages": len(self.validation_messages),
            "errors": len(
                [
                    m
                    for m in self.validation_messages
                    if m.level == ValidationLevel.ERROR
                ]
            ),
            "warnings": len(
                [
                    m
                    for m in self.validation_messages
                    if m.level == ValidationLevel.WARNING
                ]
            ),
            "info": len(
                [m for m in self.validation_messages if m.level == ValidationLevel.INFO]
            ),
            "messages": [
                {
                    "level": m.level.value,
                    "message": m.message,
                    "context": m.context,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in self.validation_messages
            ],
        }
