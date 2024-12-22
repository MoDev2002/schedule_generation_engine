from copy import deepcopy
from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import Dict, List, Optional, Set, Union

import networkx as nx

from halls import Hall, halls
from labs import Lab, Labs, LabType
from models import Department
from staff_members import Lecturer, StaffMember, TeachingAssistant
from study_plan import CourseAssignment, StudyPlan
from time_preferences import BaseAvailability, Day, TimePreference


class BlockType(Enum):
    LECTURE = "lecture"
    LAB = "lab"


@dataclass
class Block:
    id: str  # unique identifier
    course_code: str
    block_type: BlockType
    staff_member: Union[Lecturer, TeachingAssistant]
    student_count: int
    required_room_type: str  # 'hall' or 'lab'
    group_number: int  # which group this block represents
    total_groups: int  # total number of groups for this course
    is_single_group_course: bool  # if True, no parallel sessions allowed
    academic_list: str  # name of the academic list
    academic_level: int
    practical_in_lab: bool = True
    preferred_rooms: Optional[List[Union[Hall, Lab]]] = None


@dataclass
class Assignment:
    block: Block
    time_slot: TimePreference
    room: Union[Hall, Lab]


@dataclass
class SchedulingAttempt:
    """Represents a single scheduling attempt with its score"""

    assignments: Dict[str, Assignment]
    score: float
    unassigned_blocks: Set[str]


class SchedulingEngine:
    def __init__(self, constraint_manager, resource_manager):
        self.constraint_manager = constraint_manager
        self.resource_manager = resource_manager
        self.best_attempt = None
        self.current_attempt = None

    def _convert_course_assignments_to_blocks(
        self, course_assignments: List[CourseAssignment], study_plan: StudyPlan
    ) -> List[Block]:
        """Convert CourseAssignment objects to Block objects"""
        blocks = []

        for course in course_assignments:
            # Generate lecture blocks
            lecture_group_count = 1
            for lecturer_assignment in course.lecturers:
                lecturer = lecturer_assignment["lecturer"]
                num_groups = lecturer_assignment["num_of_groups"]

                for _ in range(num_groups):
                    block_id = (
                        f"L_{course.course_code}_{lecturer.id}_{lecture_group_count}"
                    )

                    blocks.append(
                        Block(
                            id=block_id,
                            course_code=course.course_code,
                            block_type=BlockType.LECTURE,
                            staff_member=lecturer,
                            student_count=study_plan.expected_students
                            // course.lecture_groups,
                            required_room_type="hall",
                            group_number=lecture_group_count,
                            total_groups=course.lecture_groups,
                            is_single_group_course=course.lecture_groups == 1,
                            academic_list=study_plan.academic_list.name,
                            academic_level=study_plan.academic_level,
                        )
                    )
                    lecture_group_count += 1

            # Generate lab blocks if they exist
            if course.lab_groups:
                lab_group_count = 1
                for ta_assignment in course.teaching_assistants:
                    ta = ta_assignment["teaching_assistant"]
                    num_groups = ta_assignment["num_of_groups"]

                    for _ in range(num_groups):
                        block_id = f"P_{course.course_code}_{ta.id}_{lab_group_count}"

                        blocks.append(
                            Block(
                                id=block_id,
                                course_code=course.course_code,
                                block_type=BlockType.LAB,
                                staff_member=ta,
                                student_count=study_plan.expected_students
                                // course.lab_groups,
                                required_room_type=(
                                    "lab" if course.practical_in_lab else "hall"
                                ),
                                preferred_rooms=course.preferred_labs,
                                group_number=lab_group_count,
                                total_groups=course.lab_groups,
                                is_single_group_course=course.lab_groups == 1,
                                academic_list=study_plan.academic_list.name,
                                academic_level=study_plan.academic_level,
                                practical_in_lab=course.practical_in_lab,
                            )
                        )
                        lab_group_count += 1

        return blocks

    def schedule_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan: StudyPlan,
        max_attempts: int = 100,
    ) -> Dict[str, Assignment]:
        """Main scheduling function using iterative improvement"""
        # Convert CourseAssignments to Blocks
        blocks = self._convert_course_assignments_to_blocks(
            course_assignments, study_plan
        )

        self.best_attempt = None

        for attempt in range(max_attempts):
            current_assignments = {}
            unassigned = set(block.id for block in blocks)

            # Sort blocks by priority and constraints
            sorted_blocks = self._sort_blocks_by_priority(blocks)

            # Try to schedule each block
            for block in sorted_blocks:
                assignment = self._schedule_single_block(block, current_assignments)

                if assignment:
                    current_assignments[block.id] = assignment
                    unassigned.remove(block.id)

            # Evaluate this attempt
            attempt_score = self._evaluate_schedule(current_assignments)
            current_attempt = SchedulingAttempt(
                assignments=current_assignments,
                score=attempt_score,
                unassigned_blocks=unassigned,
            )

            # Update best attempt if this is better
            if self._is_better_attempt(current_attempt):
                self.best_attempt = current_attempt

            # If we have a perfect schedule, stop
            if not unassigned and attempt_score >= 0.95:
                break

            # If we have a decent schedule but some issues,
            # try to improve it with local search
            if not unassigned and attempt_score >= 0.7:
                improved_assignments = self._local_search(current_assignments)
                improved_score = self._evaluate_schedule(improved_assignments)

                improved_attempt = SchedulingAttempt(
                    assignments=improved_assignments,
                    score=improved_score,
                    unassigned_blocks=set(),
                )

                if self._is_better_attempt(improved_attempt):
                    self.best_attempt = improved_attempt

        if not self.best_attempt:
            raise ValueError("Could not find a valid schedule")

        return self.best_attempt.assignments

    def _sort_blocks_by_priority(self, blocks: List[Block]) -> List[Block]:
        """Sort blocks by various constraints and priorities"""

        def get_block_score(block: Block) -> tuple:
            # Get possible rooms for this block
            possible_rooms = self._get_possible_rooms(block)

            # Calculate total available time slots across all possible rooms
            total_available_slots = 0
            for room in possible_rooms:
                available_slots = self.resource_manager.get_available_slots(
                    block,
                    room,
                    self.current_attempt.assignments if self.current_attempt else {},
                )
                total_available_slots += len(available_slots)

            # Calculate priority score
            priority_score = self._calculate_block_priority(block)

            return (
                # First priority: Single group courses
                block.is_single_group_course,
                # Second priority: Fewer room options means more constrained
                -len(possible_rooms),
                # Third priority: Fewer time slot options means more constrained
                -total_available_slots,
                # Fourth priority: Calculated priority score
                priority_score,
            )

        return sorted(blocks, key=get_block_score, reverse=True)

    def _get_possible_rooms(self, block: Block) -> List[Union[Hall, Lab]]:
        """Get list of possible rooms for a block"""
        return self.resource_manager.get_suitable_rooms(block)

    def _calculate_block_priority(self, block: Block) -> float:
        """Calculate priority score for a block"""
        score = 0.0

        # Preferred rooms increase priority
        if block.preferred_rooms:
            score += 10.0

        # Single group courses get higher priority
        if block.is_single_group_course:
            score += 20.0

        # Lecturer blocks get higher priority than TA blocks
        if isinstance(block.staff_member, Lecturer):
            score += 15.0

        # Lab requirements increase priority
        if block.required_room_type == "lab":
            score += 8.0

        # Larger groups get higher priority
        score += block.student_count / 100.0

        return score

    def _get_possible_slots(
        self, block: Block, room: Union[Hall, Lab]
    ) -> List[TimePreference]:
        """Get list of possible time slots for a block in a room"""
        return self.resource_manager.get_available_slots(
            block,
            room,
            self.current_attempt.assignments if self.current_attempt else {},
        )

    def _schedule_single_block(
        self, block: Block, current_assignments: Dict[str, Assignment]
    ) -> Optional[Assignment]:
        """Attempt to schedule a single block"""
        # Get possible rooms and slots
        possible_rooms = self._get_possible_rooms(block)

        # Try each room-slot combination
        best_assignment = None
        best_score = float("-inf")

        for room in possible_rooms:
            possible_slots = self._get_possible_slots(block, room)

            for slot in possible_slots:
                # Check if assignment is valid
                is_valid, violation = self.constraint_manager.check_all_constraints(
                    block, slot, room, current_assignments
                )

                if not is_valid:
                    continue

                # Calculate score for this assignment
                score = self.constraint_manager.evaluate_soft_constraints(
                    block, slot, room
                )

                if score > best_score:
                    best_score = score
                    best_assignment = Assignment(block, slot, room)

        return best_assignment

    def _local_search(
        self, initial_assignments: Dict[str, Assignment], max_iterations: int = 100
    ) -> Dict[str, Assignment]:
        """Improve schedule using local search"""
        current_assignments = deepcopy(initial_assignments)
        current_score = self._evaluate_schedule(current_assignments)

        for _ in range(max_iterations):
            improved = False

            # Try swapping pairs of assignments
            for block_id1, assignment1 in current_assignments.items():
                for block_id2, assignment2 in current_assignments.items():
                    if block_id1 >= block_id2:
                        continue

                    # Try swapping rooms
                    if self._can_swap_rooms(assignment1, assignment2):
                        new_assignments = deepcopy(current_assignments)
                        self._swap_rooms(new_assignments, block_id1, block_id2)
                        new_score = self._evaluate_schedule(new_assignments)

                        if new_score > current_score:
                            current_assignments = new_assignments
                            current_score = new_score
                            improved = True

                    # Try swapping time slots
                    if self._can_swap_times(assignment1, assignment2):
                        new_assignments = deepcopy(current_assignments)
                        self._swap_times(new_assignments, block_id1, block_id2)
                        new_score = self._evaluate_schedule(new_assignments)

                        if new_score > current_score:
                            current_assignments = new_assignments
                            current_score = new_score
                            improved = True

            if not improved:
                break

        return current_assignments

    def _calculate_block_priority(self, block: Block) -> float:
        """Calculate priority score for a block"""
        score = 0.0

        # Preferred rooms increase priority
        if block.preferred_rooms:
            score += 10.0

        # Single group courses get higher priority
        if block.is_single_group_course:
            score += 20.0

        # Lecturer blocks get higher priority than TA blocks
        if isinstance(block.staff_member, Lecturer):
            score += 15.0

        # Lab requirements increase priority
        if block.required_room_type == "lab":
            score += 8.0

        # Larger groups get higher priority
        score += block.student_count / 100.0

        return score

    def _get_possible_rooms(self, block: Block) -> List[Union[Hall, Lab]]:
        """Get list of possible rooms for a block"""
        return self.resource_manager.get_suitable_rooms(block)

    def _get_possible_slots(
        self, block: Block, room: Union[Hall, Lab]
    ) -> List[TimePreference]:
        """Get list of possible time slots for a block in a room"""
        return self.resource_manager.get_available_slots(
            block,
            room,
            self.current_attempt.assignments if self.current_attempt else {},
        )

    def _evaluate_schedule(self, assignments: Dict[str, Assignment]) -> float:
        """Evaluate overall schedule quality"""
        if not assignments:
            return 0.0

        total_score = 0.0
        for assignment in assignments.values():
            # Get soft constraint score
            score = self.constraint_manager.evaluate_soft_constraints(
                assignment.block, assignment.time_slot, assignment.room
            )
            total_score += score

        return total_score / len(assignments)

    def _is_better_attempt(self, attempt: SchedulingAttempt) -> bool:
        """Determine if new attempt is better than current best"""
        if not self.best_attempt:
            return True

        # Fewer unassigned blocks is always better
        if len(attempt.unassigned_blocks) < len(self.best_attempt.unassigned_blocks):
            return True

        # If same number of unassigned, compare scores
        if len(attempt.unassigned_blocks) == len(self.best_attempt.unassigned_blocks):
            return attempt.score > self.best_attempt.score

        return False

    def _can_swap_rooms(self, assignment1: Assignment, assignment2: Assignment) -> bool:
        """Check if two assignments can swap rooms"""
        # Check room type compatibility
        if assignment1.block.required_room_type != assignment2.block.required_room_type:
            return False

        # Check capacity requirements
        if (
            assignment1.room.capacity < assignment2.block.student_count
            or assignment2.room.capacity < assignment1.block.student_count
        ):
            return False

        return True

    def _can_swap_times(self, assignment1: Assignment, assignment2: Assignment) -> bool:
        """Check if two assignments can swap time slots"""
        # Create temporary assignments with swapped times
        temp_assignment1 = Assignment(
            assignment1.block, assignment2.time_slot, assignment1.room
        )
        temp_assignment2 = Assignment(
            assignment2.block, assignment1.time_slot, assignment2.room
        )

        # Check if both new assignments would be valid
        is_valid1, _ = self.constraint_manager.check_all_constraints(
            temp_assignment1.block,
            temp_assignment1.time_slot,
            temp_assignment1.room,
            {},  # Empty assignments as we're just checking these two
        )

        is_valid2, _ = self.constraint_manager.check_all_constraints(
            temp_assignment2.block,
            temp_assignment2.time_slot,
            temp_assignment2.room,
            {},
        )

        return is_valid1 and is_valid2

    def _swap_rooms(
        self, assignments: Dict[str, Assignment], block_id1: str, block_id2: str
    ):
        """Swap rooms between two assignments"""
        assignment1 = assignments[block_id1]
        assignment2 = assignments[block_id2]

        assignments[block_id1] = Assignment(
            assignment1.block, assignment1.time_slot, assignment2.room
        )
        assignments[block_id2] = Assignment(
            assignment2.block, assignment2.time_slot, assignment1.room
        )

    def _swap_times(
        self, assignments: Dict[str, Assignment], block_id1: str, block_id2: str
    ):
        """Swap time slots between two assignments"""
        assignment1 = assignments[block_id1]
        assignment2 = assignments[block_id2]

        assignments[block_id1] = Assignment(
            assignment1.block, assignment2.time_slot, assignment1.room
        )
        assignments[block_id2] = Assignment(
            assignment2.block, assignment1.time_slot, assignment2.room
        )
