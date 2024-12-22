import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from labs import Lab
from scheduler import Assignment, Block
from study_plan import CourseAssignment, StudyPlan


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
                        {"block": block_id, "required": "lab", "assigned": "hall"},
                    )

            # Validate room capacity
            if assignment.room.capacity < assignment.block.student_count:
                self._add_warning(
                    "Room capacity may be insufficient",
                    {
                        "block": block_id,
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
            self._add_error(
                "Invalid time slot assignment",
                {
                    "block": assignment.block.id,
                    "assigned_slot": str(assignment.time_slot),
                    "room": assignment.room.name,
                },
            )

    def _check_resource_conflicts(self, assignments: Dict[str, Assignment]):
        """Check for conflicts in resource usage"""
        # Track room usage
        room_usage = {}  # (room_id, day, time) -> block_id

        # Track staff usage
        staff_usage = {}  # (staff_id, day, time) -> block_id

        for block_id, assignment in assignments.items():
            # Check room conflicts
            room_key = (
                assignment.room.id,
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            if room_key in room_usage:
                self._add_error(
                    "Room double booking detected",
                    {
                        "room": assignment.room.name,
                        "time": str(assignment.time_slot),
                        "block1": block_id,
                        "block2": room_usage[room_key],
                    },
                )
            room_usage[room_key] = block_id

            # Check staff conflicts
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
