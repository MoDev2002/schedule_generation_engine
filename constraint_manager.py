from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Optional, Set, Tuple, Union

from halls import Hall
from labs import Lab
from models import Department
from scheduler import Assignment, Block
from staff_members import Lecturer, StaffMember, TeachingAssistant
from time_preferences import Day, TimePreference


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
        self.state = SchedulerState.create_empty()
        self.setup_constraints()

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

    def check_all_constraints(
        self,
        block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        assignments: Dict[str, "Assignment"],
    ) -> Tuple[bool, Optional[str]]:
        """Check all hard constraints and return (is_valid, violation_description)"""
        self.update_state(assignments)

        for constraint in self.hard_constraints:
            if not constraint["func"](block, slot, room):
                return False, constraint["description"]
        return True, None

    def evaluate_soft_constraints(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Evaluate all soft constraints and return total weighted score"""
        total_score = 0.0
        for constraint in self.soft_constraints:
            score = constraint["func"](block, slot, room)
            total_score += score * constraint["weight"]
        return total_score

    def update_state(self, assignments: Dict[str, Assignment]):
        """Update internal state based on current assignments"""
        self.state = SchedulerState.create_empty()
        self.current_assignments = assignments  # Store full assignments for reference

        for block_id, assignment in assignments.items():
            block = assignment.block
            slot_key = (assignment.time_slot.day, assignment.time_slot.start_time)

            # Update room bookings
            if assignment.room.id not in self.state.room_bookings:
                self.state.room_bookings[assignment.room.id] = {}
            self.state.room_bookings[assignment.room.id][slot_key] = block_id

            # Update staff bookings
            staff_id = block.staff_member.id
            if staff_id not in self.state.staff_bookings:
                self.state.staff_bookings[staff_id] = {}
            self.state.staff_bookings[staff_id][slot_key] = block_id

            # Update course slots
            if block.course_code not in self.state.course_slots:
                self.state.course_slots[block.course_code] = {}
            self.state.course_slots[block.course_code][slot_key] = (
                self.state.course_slots[block.course_code].get(slot_key, 0) + 1
            )

            # Update level slots
            level_key = (block.academic_list, block.academic_level)
            if level_key not in self.state.level_slots:
                self.state.level_slots[level_key] = {}
            if assignment.time_slot.day not in self.state.level_slots[level_key]:
                self.state.level_slots[level_key][assignment.time_slot.day] = []
            self.state.level_slots[level_key][assignment.time_slot.day].append(
                assignment.time_slot.start_time
            )

            # Update study plan slots
            study_plan_key = (
                block.academic_list,
                assignment.time_slot.day,
                assignment.time_slot.start_time,
            )
            if study_plan_key not in self.state.study_plan_slots:
                self.state.study_plan_slots[study_plan_key] = []
            self.state.study_plan_slots[study_plan_key].append(block_id)

    # Hard Constraints
    def check_room_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if room is already booked at the given time"""
        slot_key = (slot.day, slot.start_time)
        return slot_key not in self.state.room_bookings.get(room.id, {})

    def check_staff_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if staff member is already booked at the given time"""
        slot_key = (slot.day, slot.start_time)
        return slot_key not in self.state.staff_bookings.get(block.staff_member.id, {})

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
                if existing_block.course_code == block.course_code:
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
