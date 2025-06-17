# schedule_validator.py


import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Tuple

from models.block import Assignment, Block
from models.labs import Lab
from models.study_plan import CourseAssignment, StudyPlan
from utils.room_utils import get_room_key


class ValidationLevel(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationMessage:
    level: ValidationLevel
    message: str
    context: dict
    timestamp: datetime = datetime.now()


@dataclass
class ConflictReport:
    conflict_type: str
    description: str
    affected_assignments: List[str]
    details: Dict


class ScheduleValidator:
    def __init__(self):
        self.logger = self._setup_logger()
        self.validation_messages: List[ValidationMessage] = []

    def _setup_logger(self):
        """Configure logging system"""
        logger = logging.getLogger("scheduler")
        logger.setLevel(logging.DEBUG)

        # File handler for detailed logging
        fh = logging.FileHandler("scheduler.log")
        fh.setLevel(logging.DEBUG)

        # Console handler for important messages
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)

        # Formatting
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)

        logger.addHandler(fh)
        logger.addHandler(ch)

        return logger

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
        # Check if time slot is within room availability
        slot_valid = False
        for available in assignment.room.availability:
            if (
                available.day == assignment.time_slot.day
                and available.start_time <= assignment.time_slot.start_time
                and available.end_time >= assignment.time_slot.end_time
            ):
                slot_valid = True
                break

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
        """Check for conflicts in resource usage"""
        # Track room usage with composite keys
        room_usage = {}  # (room_type, room_id, day, time) -> block_id

        # Track staff usage
        staff_usage = {}  # (staff_id, day, time) -> block_id

        for block_id, assignment in assignments.items():
            # Check room conflicts with composite key
            room_key = get_room_key(assignment.room)
            room_time_key = (
                room_key[0],  # room_type
                room_key[1],  # room_id
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )

            if room_time_key in room_usage:
                room_type, room_id = room_key
                self._add_error(
                    "Room double booking detected",
                    {
                        "room": assignment.room.name,
                        "room_type": room_type,
                        "room_id": room_id,
                        "time": str(assignment.time_slot),
                        "block1": block_id,
                        "block2": room_usage[room_time_key],
                    },
                )
            room_usage[room_time_key] = block_id

            # Check staff conflicts (unchanged logic)
            staff_key = (
                assignment.block.staff_member.id,
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            if staff_key in staff_usage:
                self._add_error(
                    "Staff double booking detected",
                    {
                        "staff": assignment.block.staff_member.name,
                        "time": str(assignment.time_slot),
                        "block1": block_id,
                        "block2": staff_usage[staff_key],
                    },
                )
            staff_usage[staff_key] = block_id

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

        # 5. Check capacity violations
        capacity_conflicts = self._check_capacity_violations(assignments)
        conflicts.extend(capacity_conflicts)

        is_valid = len(conflicts) == 0

        return is_valid, conflicts

    def _check_room_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check for room double-booking conflicts"""
        conflicts = []
        room_bookings = defaultdict(list)  # (room_id, day, time) -> [assignment_ids]

        # Group assignments by room and time
        for assignment_id, assignment in assignments.items():
            key = (
                get_room_key(assignment.room),
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            room_bookings[key].append(assignment_id)

        # Find conflicts
        for (room_id, day, time), assignment_ids in room_bookings.items():
            if len(assignment_ids) > 1:
                # Get room name for better reporting
                room_name = assignments[assignment_ids[0]].room.name

                conflicts.append(
                    ConflictReport(
                        conflict_type="ROOM_CONFLICT",
                        description=f"Room {room_name} double-booked at {day.name} {time}",
                        affected_assignments=assignment_ids,
                        details={
                            "room_id": room_id,
                            "room_name": room_name,
                            "day": day.name,
                            "time": str(time),
                            "conflicting_courses": [
                                assignments[aid].block.course_object.course_code
                                for aid in assignment_ids
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
        staff_bookings = defaultdict(list)  # (staff_id, day, time) -> [assignment_ids]

        # Group assignments by staff and time
        for assignment_id, assignment in assignments.items():
            key = (
                assignment.block.staff_member.id,
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            staff_bookings[key].append(assignment_id)

        # Find conflicts
        for (staff_id, day, time), assignment_ids in staff_bookings.items():
            if len(assignment_ids) > 1:
                # Get staff name for better reporting
                staff_name = assignments[assignment_ids[0]].block.staff_member.name

                conflicts.append(
                    ConflictReport(
                        conflict_type="STAFF_CONFLICT",
                        description=f"Staff {staff_name} double-booked at {day.name} {time}",
                        affected_assignments=assignment_ids,
                        details={
                            "staff_id": staff_id,
                            "staff_name": staff_name,
                            "day": day.name,
                            "time": str(time),
                            "conflicting_courses": [
                                assignments[aid].block.course_object.course_code
                                for aid in assignment_ids
                            ],
                        },
                    )
                )

        return conflicts

    def _check_student_conflicts(
        self, assignments: Dict[str, Assignment]
    ) -> List[ConflictReport]:
        """Check for student schedule conflicts"""
        conflicts = []
        student_bookings = defaultdict(
            list
        )  # (academic_list, level, day, time) -> [assignment_ids]

        # Group assignments by academic level and time
        for assignment_id, assignment in assignments.items():
            key = (
                assignment.block.academic_list,
                assignment.block.academic_level,
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            student_bookings[key].append(assignment_id)

        # Find conflicts
        for (
            academic_list,
            level,
            day,
            time,
        ), assignment_ids in student_bookings.items():
            if len(assignment_ids) > 1:
                # Check if these are different courses (conflict) or different groups of same course (ok)
                courses = set(
                    assignments[aid].block.course_object.course_code
                    for aid in assignment_ids
                )

                if len(courses) > 1:  # Different courses = student conflict
                    conflicts.append(
                        ConflictReport(
                            conflict_type="STUDENT_CONFLICT",
                            description=f"Student conflict for {academic_list} Level {level} at {day.name} {time}",
                            affected_assignments=assignment_ids,
                            details={
                                "academic_list": academic_list,
                                "academic_level": level,
                                "day": day.name,
                                "time": str(time),
                                "conflicting_courses": list(courses),
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

            # Check if the assignment time falls within room availability
            is_available = False
            for availability in room.availability:
                if (
                    availability.day == time_slot.day
                    and availability.start_time <= time_slot.start_time
                    and availability.end_time >= time_slot.end_time
                ):
                    is_available = True
                    break

            if not is_available:
                conflicts.append(
                    ConflictReport(
                        conflict_type="ROOM_AVAILABILITY_CONFLICT",
                        description=f"Room {room.name} used outside availability",
                        affected_assignments=[assignment_id],
                        details={
                            "room_name": room.name,
                            "assigned_time": f"{time_slot.day.name} {time_slot.start_time}-{time_slot.end_time}",
                            "course": assignment.block.course_object.course_code,
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
                            "course": assignment.block.course_object.course_code,
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
