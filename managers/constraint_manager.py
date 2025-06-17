# managers/constraint_manager.py


import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Optional, Tuple, Union

from models.block import Assignment, Block
from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.time_preferences import Day, TimePreference
from utils.room_utils import get_room_key


@dataclass
class SchedulerState:
    """Maintains the current state of assignments for efficient constraint checking"""

    room_bookings: Dict[
        str, Dict[Tuple[Day, time], str]
    ]  # room_id -> {(day, time) -> block_id}
    staff_bookings: Dict[
        int, Dict[Tuple[Day, time], str]
    ]  # staff_id -> {(day, time) -> block_id}
    course_slots: Dict[
        str, Dict[Tuple[Day, time], int]
    ]  # course_code -> {(day, time) -> count}
    level_slots: Dict[
        Tuple[str, int], Dict[Day, List[time]]
    ]  # (academic_list, level) -> {day -> [times]}
    study_plan_slots: Dict[
        Tuple[str, Day, time], List[str]
    ]  # (academic_list, day, time) -> [block_ids]

    @classmethod
    def create_empty(cls):
        return cls(
            room_bookings={},
            staff_bookings={},
            course_slots={},
            level_slots={},
            study_plan_slots={},
        )


class ConstraintManager:
    def __init__(self):
        self.hard_constraints = []
        self.soft_constraints = []
        self.logger = logging.getLogger("constraint_manager")
        self.initialize_fresh_state()
        self.setup_constraints()

    def initialize_fresh_state(self):
        """Initialize completely empty state"""
        self.state = SchedulerState.create_empty()
        self.current_assignments = {}
        self.logger.info("ConstraintManager state initialized fresh")

    def can_assign(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> Tuple[bool, Optional[str]]:
        """Check if assignment is possible WITHOUT modifying state"""
        self.logger.debug(f"=== CHECKING IF CAN ASSIGN {block.id} ===")
        self.logger.debug(f"Current assignments: {len(self.current_assignments)}")

        for constraint in self.hard_constraints:
            try:
                is_valid = constraint["func"](block, slot, room)
                if not is_valid:
                    self.logger.debug(
                        f"CONSTRAINT VIOLATION: {constraint['description']}"
                    )
                    return False, constraint["description"]
            except Exception as e:
                self.logger.error(
                    f"Error in constraint {constraint['description']}: {str(e)}"
                )
                return False, f"Error in {constraint['description']}: {str(e)}"

        self.logger.debug(f"All constraints PASSED for {block.id}")
        return True, None

    def make_assignment(self, block_id: str, assignment: Assignment) -> bool:
        """ATOMIC OPERATION: Check constraints and commit assignment in one step"""
        self.logger.debug(f"=== MAKING ASSIGNMENT {block_id} ===")

        current_state_snapshot = deepcopy(self.state)
        current_assignments_snapshot = self.current_assignments.copy()

        if block_id in self.current_assignments:
            self.logger.warning(
                f"Block {block_id} is already assigned — skipping reassignment."
            )
            return False

        try:
            self._add_to_state(block_id, assignment)
            return True
        except Exception as e:
            # CRITICAL: Rollback to snapshot on ANY failure
            self.state = current_state_snapshot
            self.current_assignments = current_assignments_snapshot
            self.logger.error(f"Assignment failed: {str(e)}")
            return False

    def _verify_no_conflicts_in_slot(self, slot: TimePreference):
        """Verify no conflicts exist in the given time slot"""
        slot_key = (slot.day, slot.start_time)

        # Check all assignments in this time slot
        assignments_in_slot = []
        for block_id, assignment in self.current_assignments.items():
            if (assignment.time_slot.day, assignment.time_slot.start_time) == slot_key:
                assignments_in_slot.append((block_id, assignment))

        if len(assignments_in_slot) <= 1:
            return  # No conflicts possible

        # Check for room conflicts
        rooms_used = set()
        staff_used = set()

        for block_id, assignment in assignments_in_slot:
            room_key = get_room_key(assignment.room)
            staff_id = assignment.block.staff_member.id

            if room_key in rooms_used:
                self.logger.error(
                    f"DETECTED ROOM CONFLICT in slot {slot_key}: Room {room_key} used multiple times"
                )
                raise Exception(f"Room conflict detected for {room_key}")

            if staff_id in staff_used:
                self.logger.error(
                    f"DETECTED STAFF CONFLICT in slot {slot_key}: Staff {staff_id} used multiple times"
                )
                raise Exception(f"Staff conflict detected for {staff_id}")

            rooms_used.add(room_key)
            staff_used.add(staff_id)

    def get_all_assignments(self) -> Dict[str, Assignment]:
        """Get all current assignments"""
        return self.current_assignments.copy()

    def _add_to_state(self, block_id: str, assignment: Assignment):
        """Add assignment to internal state tracking"""
        self._verify_no_conflicts_before_commit(assignment)

        block = assignment.block
        slot_key = (assignment.time_slot.day, assignment.time_slot.start_time)
        room_key = get_room_key(
            assignment.room
        )  # This returns (room_type, room_id) tuple

        if block_id in self.current_assignments:
            raise Exception(f"Block {block_id} already assigned — invalid state")

        self.logger.debug(f"Adding {block_id} to state:")
        self.logger.debug(f"  Room key: {room_key}")
        self.logger.debug(f"  Slot key: {slot_key}")
        self.logger.debug(f"  Staff ID: {block.staff_member.id}")

        # Store the assignment
        self.current_assignments[block_id] = assignment

        # Update room bookings (using composite key)
        if room_key not in self.state.room_bookings:
            self.state.room_bookings[room_key] = {}

        if slot_key in self.state.room_bookings[room_key]:
            existing_block = self.state.room_bookings[room_key][slot_key]
            raise Exception(
                f"Room conflict: {room_key} at {slot_key} already has {existing_block}"
            )

        self.state.room_bookings[room_key][slot_key] = block_id

        # Update staff bookings
        staff_id = block.staff_member.id
        if staff_id not in self.state.staff_bookings:
            self.state.staff_bookings[staff_id] = {}

        if slot_key in self.state.staff_bookings[staff_id]:
            existing_block = self.state.staff_bookings[staff_id][slot_key]
            raise Exception(
                f"Staff conflict: {staff_id} at {slot_key} already has {existing_block}"
            )

        self.state.staff_bookings[staff_id][slot_key] = block_id

        # Update other state tracking (unchanged)
        if block.course_object.course_code not in self.state.course_slots:
            self.state.course_slots[block.course_object.course_code] = {}
        self.state.course_slots[block.course_object.course_code][slot_key] = (
            self.state.course_slots[block.course_object.course_code].get(slot_key, 0)
            + 1
        )

        level_key = (block.academic_list, block.academic_level)
        if level_key not in self.state.level_slots:
            self.state.level_slots[level_key] = {}
        if assignment.time_slot.day not in self.state.level_slots[level_key]:
            self.state.level_slots[level_key][assignment.time_slot.day] = []
        self.state.level_slots[level_key][assignment.time_slot.day].append(
            assignment.time_slot.start_time
        )

        study_plan_key = (
            block.academic_list,
            assignment.time_slot.day,
            assignment.time_slot.start_time,
        )
        if study_plan_key not in self.state.study_plan_slots:
            self.state.study_plan_slots[study_plan_key] = []
        self.state.study_plan_slots[study_plan_key].append(block_id)

    def _verify_no_conflicts_before_commit(self, new_assignment):
        """Explicitly check for conflicts before adding to state"""
        day, time = new_assignment.time_slot.day, new_assignment.time_slot.start_time
        room_key = get_room_key(new_assignment.room)
        staff_id = new_assignment.block.staff_member.id

        # Check room booking at this time
        for block_id, assignment in self.current_assignments.items():
            if (
                assignment.time_slot.day == day
                and assignment.time_slot.start_time == time
            ):

                # Check room conflict
                if get_room_key(assignment.room) == room_key:
                    raise Exception(
                        f"Room {new_assignment.room.name} already booked at {day.name} {time}"
                    )

                # Check staff conflict
                if assignment.block.staff_member.id == staff_id:
                    raise Exception(
                        f"Staff {new_assignment.block.staff_member.name} already booked at {day.name} {time}"
                    )

    def check_room_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if room is already booked at the given time"""
        slot_key = (slot.day, slot.start_time)
        room_key = get_room_key(room)  # Returns (room_type, room_id) tuple

        self.logger.debug(f"Checking room booking: {room_key} at {slot_key}")

        # Check if room exists in bookings
        if room_key not in self.state.room_bookings:
            self.logger.debug(f"Room {room_key} not in bookings - AVAILABLE")
            return True

        # Check if slot is available
        is_available = slot_key not in self.state.room_bookings[room_key]

        if not is_available:
            existing_block = self.state.room_bookings[room_key][slot_key]
            self.logger.debug(
                f"Room {room_key} at {slot_key} already booked by {existing_block} - CONFLICT"
            )
        else:
            self.logger.debug(f"Room {room_key} at {slot_key} is available")

        return is_available

    def check_staff_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if staff member is already booked at the given time"""
        slot_key = (slot.day, slot.start_time)
        staff_id = block.staff_member.id

        self.logger.debug(f"Checking staff booking: Staff {staff_id} at {slot_key}")

        # Check if staff exists in bookings
        if staff_id not in self.state.staff_bookings:
            self.logger.debug(f"Staff {staff_id} not in bookings - AVAILABLE")
            return True

        # Check if slot is available
        is_available = slot_key not in self.state.staff_bookings[staff_id]

        if not is_available:
            existing_block = self.state.staff_bookings[staff_id][slot_key]
            self.logger.debug(
                f"Staff {staff_id} at {slot_key} already booked for {existing_block} - CONFLICT"
            )
        else:
            self.logger.debug(f"Staff {staff_id} at {slot_key} is available")

        return is_available

    def setup_constraints(self):
        """Initialize all constraints with their weights and descriptions"""
        # Hard Constraints
        self.add_hard_constraint(self.check_room_booking, "No double room booking")
        self.add_hard_constraint(self.check_staff_booking, "No double staff booking")
        self.add_hard_constraint(
            self.check_room_availability,
            "Room must be available in the given time slot",
        )
        self.add_hard_constraint(
            self.check_single_group_conflict,
            "Single group courses cannot have parallel sessions",
        )
        self.add_hard_constraint(
            self.check_lab_requirements,
            "Lab specialization and preferences must be met",
        )

        # Soft Constraints (with weights)
        self.add_soft_constraint(
            self.evaluate_lecturer_preferences,
            weight=5.0,
            description="Lecturer timing preferences",
        )
        self.add_soft_constraint(
            self.evaluate_ta_preferences,
            weight=3.0,
            description="Teaching Assistant timing preferences",
        )
        self.add_soft_constraint(
            self.evaluate_gaps, weight=2.0, description="Minimize schedule gaps"
        )
        self.add_soft_constraint(
            self.evaluate_room_capacity,
            weight=1.5,
            description="Room capacity utilization",
        )

    def add_hard_constraint(self, constraint_func, description: str):
        """Add a hard constraint with its description"""
        self.hard_constraints.append(
            {"func": constraint_func, "description": description}
        )

    def add_soft_constraint(self, constraint_func, weight: float, description: str):
        """Add a soft constraint with its weight and description"""
        self.soft_constraints.append(
            {"func": constraint_func, "weight": weight, "description": description}
        )

    def evaluate_soft_constraints(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Evaluate all soft constraints and return total weighted score"""
        total_score = 0.0
        for constraint in self.soft_constraints:
            score = constraint["func"](block, slot, room)
            total_score += score * constraint["weight"]
        return total_score

    # Hard Constraints
    def check_room_availability(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if room is available at the given time based on its availability schedule"""
        for pref in room.availability:
            if (
                pref.day == slot.day
                and pref.start_time <= slot.start_time
                and pref.end_time >= slot.end_time
            ):
                return True
        return False

    def check_single_group_conflict(
        self, block: Block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """
        Check for conflicts with single-group course constraint at study plan level.
        A time slot cannot have multiple sessions (lectures or labs) for the same study plan
        if any of the involved courses has a single lecture group.
        """
        slot_key = (block.academic_list, slot.day, slot.start_time)

        # Check existing assignments for this time slot and study plan
        if slot_key in self.state.study_plan_slots:
            # Get all blocks scheduled in this slot for this study plan
            existing_blocks = []
            for block_id in self.state.study_plan_slots[slot_key]:
                # Look up the existing block from assignments
                for assignment in self.current_assignments.values():
                    if assignment.block.id == block_id:
                        existing_blocks.append(assignment.block)
                        break

            # If current block is from a single-group course, reject any parallel sessions
            if block.is_single_group_course:
                return False

            # Check if any existing block is from a single-group course
            for existing_block in existing_blocks:
                if existing_block.is_single_group_course:
                    return False

                # Check if current block and existing block are from the same course
                if existing_block.course_code == block.course_object.course_code:
                    # For same course, ensure both blocks allow parallel sessions
                    if block.total_groups == 1 or existing_block.total_groups == 1:
                        return False

        return True

    def check_lab_requirements(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check lab specialization and preferences"""
        if block.required_room_type == "lab":
            if not isinstance(room, Lab):
                return False

            # Check preferred labs
            if block.preferred_rooms:
                return room in block.preferred_rooms

            # Check lab specialization
            if not room.used_in_non_specialist_courses:
                return False

        elif block.required_room_type == "hall" and not isinstance(room, Hall):
            return False

        return True

    def check_student_schedule_conflict(
        self,
        block: Block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        assignments: Dict[str, "Assignment"],
    ) -> bool:
        """
        Stateless and stricter check for student schedule conflicts.
        Prevents any two blocks for the same academic group from being in the same time slot,
        unless they are different parallel groups of the same course and block type.
        """
        for existing_assignment in assignments.values():
            existing_block = existing_assignment.block

            # Check if it's the same academic group at the same time slot
            if (
                existing_block.academic_list == block.academic_list
                and existing_block.academic_level == block.academic_level
                and existing_assignment.time_slot.day == slot.day
                and existing_assignment.time_slot.start_time == slot.start_time
            ):

                # If we found any session for the same student group at the same time,
                # we need to determine if it's a legitimate parallel group or a conflict.

                # If the blocks are for different courses, it's a definite conflict.
                if existing_block.course_code != block.course_object.course_code:
                    return False

                # If the blocks are for the same course:
                # A group of students cannot be in two different types of sessions (e.g., lecture and lab) at once.
                if existing_block.block_type != block.block_type:
                    return False

                # If it's the same block type (e.g., two lectures or two labs),
                # it's a conflict if they are for the exact same group number.
                # It's only allowed if they are different groups (e.g., group 1 vs group 2).
                if existing_block.group_number == block.group_number:
                    return False

        # If no conflicts were found after checking all existing assignments, the slot is valid.
        return True

    # Soft Constraints
    def evaluate_lecturer_preferences(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score lecturer timing preferences"""
        if not isinstance(block.staff_member, Lecturer):
            return 0.0

        for pref in block.staff_member.timing_preferences:
            if pref.day == slot.day and pref.start_time == slot.start_time:
                return 1.0
        return 0.0

    def evaluate_ta_preferences(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score teaching assistant timing preferences"""
        if not isinstance(block.staff_member, TeachingAssistant):
            return 0.0

        for pref in block.staff_member.timing_preferences:
            if pref.day == slot.day and pref.start_time == slot.start_time:
                return 1.0
        return 0.0

    def evaluate_gaps(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score schedule gaps (fewer gaps is better)"""
        level_key = (block.academic_list, block.academic_level)
        if level_key not in self.state.level_slots:
            return 1.0

        if slot.day not in self.state.level_slots[level_key]:
            return 1.0

        day_slots = sorted(self.state.level_slots[level_key][slot.day])

        # Check gaps between existing slots
        max_gap = 0
        for i in range(len(day_slots) - 1):
            gap = day_slots[i + 1].hour - day_slots[i].hour
            max_gap = max(max_gap, gap)

        # Include potential new slot
        if day_slots:
            before_gap = abs(slot.start_time.hour - min(t.hour for t in day_slots))
            after_gap = abs(slot.start_time.hour - max(t.hour for t in day_slots))
            max_gap = max(max_gap, before_gap, after_gap)

        # Score inversely to gap size (larger gaps = lower score)
        if max_gap <= 2:  # 2-hour gap is acceptable
            return 1.0
        elif max_gap <= 4:
            return 0.5
        else:
            return 0.0

    def evaluate_room_capacity(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score room capacity utilization"""
        # Calculate required capacity (assuming equal distribution among groups)
        required_capacity = block.student_count

        # Calculate utilization ratio
        utilization = required_capacity / room.capacity

        # Score based on utilization
        if 0.5 <= utilization <= 0.9:  # Ideal utilization
            return 1.0
        elif 0.3 <= utilization < 0.5:  # Slightly under-utilized
            return 0.7
        elif 0.9 < utilization <= 1.0:  # Nearly full
            return 0.7
        elif utilization < 0.3:  # Severely under-utilized
            return 0.3
        else:  # Over-utilized
            return 0.0
