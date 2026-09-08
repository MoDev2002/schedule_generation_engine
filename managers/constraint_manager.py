# managers/constraint_manager.py


import logging
from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Optional, Set, Tuple, Union

from models.block import Assignment, Block
from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.time_preferences import Day, TimePreference
from utils.room_utils import get_room_key
from utils.time_utils import intervals_overlap, preference_covers_slot

# A booked interval inside one day: (start, end, block_id)
Booking = Tuple[time, time, str]


@dataclass
class SchedulerState:
    """
    Current assignments, indexed for constraint checking.

    Bookings are stored as intervals per day rather than keyed on start time, so
    two sessions that overlap without starting at the same minute still collide.
    """

    room_bookings: Dict[
        Tuple[str, int], Dict[Day, List[Booking]]
    ]  # (room_type, room_id) -> {day -> [(start, end, block_id)]}
    staff_bookings: Dict[
        int, Dict[Day, List[Booking]]
    ]  # staff_id -> {day -> [(start, end, block_id)]}
    course_slots: Dict[
        str, Dict[Tuple[Day, time], int]
    ]  # course_code -> {(day, time) -> count}
    level_slots: Dict[
        Tuple[str, int], Dict[Day, List[time]]
    ]  # (academic_list, level) -> {day -> [times]}
    cohort_bookings: Dict[
        Tuple[str, int], Dict[Day, List[Booking]]
    ]  # (academic_list, level) -> {day -> [(start, end, block_id)]}

    @classmethod
    def create_empty(cls):
        return cls(
            room_bookings={},
            staff_bookings={},
            course_slots={},
            level_slots={},
            cohort_bookings={},
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

    def _get_course_ids_from_block(self, block: Block) -> Set[int]:
        """Extract all course IDs from a block."""
        course_ids = set()

        for course_assignment in block.course_objects:
            # Handle merged courses that have multiple course IDs
            if (
                course_assignment.course_ids is not None
                and course_assignment.course_ids
            ):
                course_ids.update(course_assignment.course_ids)
            else:
                # Regular course assignment with single course_id
                course_ids.add(course_assignment.course_id)

        return course_ids

    def _cohort_keys(self, block: Block) -> List[Tuple[str, int]]:
        """
        The (academic list, level) cohorts a block puts in a room.

        The level is part of the key: level 1 and level 4 of the same list are
        different groups of students and must not block each other's slots.
        """
        keys = []
        for i, academic_list_name in enumerate(block.academic_lists):
            level = (
                block.academic_levels[i]
                if i < len(block.academic_levels)
                else block.academic_levels[0]
            )
            keys.append((academic_list_name, level))
        return keys

    @staticmethod
    def _overlapping(
        index: Dict[Day, List[Booking]], slot: TimePreference
    ) -> List[Booking]:
        """Bookings in `index` that overlap `slot`."""
        return [
            booking
            for booking in index.get(slot.day, [])
            if intervals_overlap(
                slot.start_time, slot.end_time, booking[0], booking[1]
            )
        ]

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

        if block_id in self.current_assignments:
            self.logger.warning(
                f"Block {block_id} is already assigned - skipping reassignment."
            )
            return False

        # Re-run every hard constraint against live state before committing, so a
        # caller that skipped can_assign cannot slip past room availability, lab
        # rules or the student-clash check.
        is_valid, violation = self.can_assign(
            assignment.block, assignment.time_slot, assignment.room
        )
        if not is_valid:
            self.logger.error(f"Assignment rejected for {block_id}: {violation}")
            return False

        try:
            self._add_to_state(block_id, assignment)
            return True
        except Exception as e:
            # Roll back whatever was written before the failure.
            self.undo_assignment(block_id)
            self.logger.error(f"Assignment failed: {str(e)}")
            return False

    def seed_fixed_assignment(self, block_id: str, assignment: Assignment) -> bool:
        """Reserve an existing placement without re-validating historic availability."""
        if block_id in self.current_assignments:
            return False

        try:
            self._add_to_state(block_id, assignment)
            return True
        except Exception as error:
            self.undo_assignment(block_id)
            self.logger.error(f"Failed to seed fixed assignment {block_id}: {error}")
            return False

    def get_all_assignments(self) -> Dict[str, Assignment]:
        """Get all current assignments"""
        return self.current_assignments.copy()

    def _add_to_state(self, block_id: str, assignment: Assignment):
        """Add assignment to internal state tracking"""
        self._verify_no_conflicts_before_commit(assignment)

        block = assignment.block
        slot = assignment.time_slot
        day = slot.day
        interval = (slot.start_time, slot.end_time, block_id)
        room_key = get_room_key(assignment.room)  # (room_type, room_id)

        if block_id in self.current_assignments:
            raise Exception(f"Block {block_id} already assigned - invalid state")

        self.logger.debug(f"Adding {block_id} to state:")
        self.logger.debug(f"  Room key: {room_key}")
        self.logger.debug(f"  Slot: {day.name} {slot.start_time}-{slot.end_time}")
        self.logger.debug(f"  Staff ID: {block.staff_member.id}")

        # Store the assignment
        self.current_assignments[block_id] = assignment

        # Room bookings - interval indexed per day
        room_day = self.state.room_bookings.setdefault(room_key, {}).setdefault(day, [])
        clash = self._overlapping({day: room_day}, slot)
        if clash:
            raise Exception(
                f"Room conflict: {room_key} on {day.name} overlaps {clash[0][2]}"
            )
        room_day.append(interval)

        # Staff bookings - interval indexed per day
        staff_id = block.staff_member.id
        staff_day = self.state.staff_bookings.setdefault(staff_id, {}).setdefault(
            day, []
        )
        clash = self._overlapping({day: staff_day}, slot)
        if clash:
            raise Exception(
                f"Staff conflict: {staff_id} on {day.name} overlaps {clash[0][2]}"
            )
        staff_day.append(interval)

        # Informational: how often a course code lands on a slot
        primary_course_code = block.course_codes[0]
        slot_key = (day, slot.start_time)
        course_index = self.state.course_slots.setdefault(primary_course_code, {})
        course_index[slot_key] = course_index.get(slot_key, 0) + 1

        # Per-cohort tracking, keyed on (academic list, level)
        for cohort_key in self._cohort_keys(block):
            self.state.level_slots.setdefault(cohort_key, {}).setdefault(
                day, []
            ).append(slot.start_time)

            self.state.cohort_bookings.setdefault(cohort_key, {}).setdefault(
                day, []
            ).append(interval)

            self.logger.debug(f"Added block {block_id} to cohort {cohort_key} on {day}")

    def undo_assignment(self, block_id: str) -> bool:
        """
        Remove an assignment and every trace of it from the state indexes.

        Returns False when the block was not assigned, so callers can treat an
        unknown id as a no-op rather than an error.
        """
        assignment = self.current_assignments.pop(block_id, None)
        if assignment is None:
            return False

        block = assignment.block
        day = assignment.time_slot.day
        start = assignment.time_slot.start_time
        room_key = get_room_key(assignment.room)

        def drop(index: Dict, key):
            day_map = index.get(key)
            if not day_map or day not in day_map:
                return
            day_map[day] = [b for b in day_map[day] if b[2] != block_id]
            if not day_map[day]:
                del day_map[day]
            if not day_map:
                del index[key]

        drop(self.state.room_bookings, room_key)
        drop(self.state.staff_bookings, block.staff_member.id)

        primary_course_code = block.course_codes[0]
        course_index = self.state.course_slots.get(primary_course_code)
        if course_index is not None:
            slot_key = (day, start)
            if course_index.get(slot_key):
                course_index[slot_key] -= 1
                if course_index[slot_key] <= 0:
                    del course_index[slot_key]
            if not course_index:
                del self.state.course_slots[primary_course_code]

        for cohort_key in self._cohort_keys(block):
            drop(self.state.cohort_bookings, cohort_key)

            day_map = self.state.level_slots.get(cohort_key)
            if day_map and day in day_map and start in day_map[day]:
                day_map[day].remove(start)
                if not day_map[day]:
                    del day_map[day]
                if not day_map:
                    del self.state.level_slots[cohort_key]

        self.logger.debug(f"Undid assignment {block_id}")
        return True

    def _verify_no_conflicts_before_commit(self, new_assignment):
        """Explicitly check for conflicts before adding to state"""
        slot = new_assignment.time_slot
        room_key = get_room_key(new_assignment.room)
        staff_id = new_assignment.block.staff_member.id

        for block_id, assignment in self.current_assignments.items():
            if assignment.time_slot.day != slot.day:
                continue
            if not intervals_overlap(
                slot.start_time,
                slot.end_time,
                assignment.time_slot.start_time,
                assignment.time_slot.end_time,
            ):
                continue

            if get_room_key(assignment.room) == room_key:
                raise Exception(
                    f"Room {new_assignment.room.name} already booked at "
                    f"{slot.day.name} {assignment.time_slot.start_time}"
                    f"-{assignment.time_slot.end_time}"
                )

            if assignment.block.staff_member.id == staff_id:
                raise Exception(
                    f"Staff {new_assignment.block.staff_member.name} already booked at "
                    f"{slot.day.name} {assignment.time_slot.start_time}"
                    f"-{assignment.time_slot.end_time}"
                )

    def check_room_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if the room is free for the whole of the requested interval"""
        room_key = get_room_key(room)
        bookings = self.state.room_bookings.get(room_key, {})

        clash = self._overlapping(bookings, slot)
        if clash:
            self.logger.debug(
                f"Room {room_key} on {slot.day.name} overlaps {clash[0][2]} "
                f"({clash[0][0]}-{clash[0][1]}) - CONFLICT"
            )
            return False

        return True

    def check_staff_booking(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check if the staff member is free for the whole of the requested interval"""
        staff_id = block.staff_member.id
        bookings = self.state.staff_bookings.get(staff_id, {})

        clash = self._overlapping(bookings, slot)
        if clash:
            self.logger.debug(
                f"Staff {staff_id} on {slot.day.name} overlaps {clash[0][2]} "
                f"({clash[0][0]}-{clash[0][1]}) - CONFLICT"
            )
            return False

        return True

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
            self.check_student_conflict,
            "Students cannot be in two sessions at once",
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
        """Check the session fits inside one of the room's availability windows"""
        for pref in room.availability:
            if preference_covers_slot(pref, slot):
                return True
        return False

    def check_student_conflict(
        self, block: Block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """
        No cohort may be in two sessions at the same time.

        A cohort is one (academic list, level) pair. Two sessions overlapping for
        the same cohort are only legitimate when they are different parallel
        groups of the same course and the same session type - group 1 and group 2
        of a lab are different students, a lecture and a lab are not.
        """
        current_course_ids = self._get_course_ids_from_block(block)

        for cohort_key in self._cohort_keys(block):
            bookings = self.state.cohort_bookings.get(cohort_key, {})

            for existing_start, existing_end, existing_id in self._overlapping(
                bookings, slot
            ):
                existing_assignment = self.current_assignments.get(existing_id)
                if existing_assignment is None:
                    continue
                existing_block = existing_assignment.block

                # Different courses for the same students at the same hour.
                existing_course_ids = self._get_course_ids_from_block(existing_block)
                if not current_course_ids.intersection(existing_course_ids):
                    return False

                # Same course: students cannot attend a lecture and a lab at once.
                if existing_block.block_type != block.block_type:
                    return False

                # Same course, same session type: only distinct parallel groups
                # are allowed. A single-group course has nothing to run beside it.
                if block.is_single_group_course or existing_block.is_single_group_course:
                    return False

                if existing_block.group_number == block.group_number:
                    return False

        return True

    def check_lab_requirements(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> bool:
        """Check lab specialization and preferences"""
        if block.required_room_type == "lab":
            if not isinstance(room, Lab):
                return False

            # Check preferred labs. Compared by identity key, not by value: Lab is
            # a dataclass whose equality includes its availability list, so a
            # differently ordered fetch of the same lab would not compare equal.
            if block.preferred_rooms:
                preferred_keys = {get_room_key(r) for r in block.preferred_rooms}
                return get_room_key(room) in preferred_keys

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
            if preference_covers_slot(pref, slot):
                return 1.0
        return 0.0

    def evaluate_ta_preferences(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score teaching assistant timing preferences"""
        if not isinstance(block.staff_member, TeachingAssistant):
            return 0.0

        for pref in block.staff_member.timing_preferences:
            if preference_covers_slot(pref, slot):
                return 1.0
        return 0.0

    def evaluate_gaps(
        self, block, slot: TimePreference, room: Union[Hall, Lab]
    ) -> float:
        """Score schedule gaps (fewer gaps is better)"""
        gap_scores = []

        # Calculate gap score for each cohort this block serves
        for cohort_key in self._cohort_keys(block):
            if cohort_key not in self.state.level_slots:
                gap_scores.append(1.0)
                continue

            if slot.day not in self.state.level_slots[cohort_key]:
                gap_scores.append(1.0)
                continue

            day_slots = sorted(self.state.level_slots[cohort_key][slot.day])

            # Calculate gaps for this cohort
            max_gap = 0
            for j in range(len(day_slots) - 1):
                gap = day_slots[j + 1].hour - day_slots[j].hour
                max_gap = max(max_gap, gap)

            # Include potential new slot
            if day_slots:
                before_gap = abs(slot.start_time.hour - min(t.hour for t in day_slots))
                after_gap = abs(slot.start_time.hour - max(t.hour for t in day_slots))
                max_gap = max(max_gap, before_gap, after_gap)

            # Convert gap to score
            if max_gap <= 2:
                gap_scores.append(1.0)
            elif max_gap <= 4:
                gap_scores.append(0.5)
            else:
                gap_scores.append(0.0)

        # Return average gap score across all cohorts
        return sum(gap_scores) / len(gap_scores) if gap_scores else 1.0

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
