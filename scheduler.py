# scheduler.py - Enhanced with comprehensive logging

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Union

from models.block import Assignment, Block, BlockType
from models.halls import Hall
from models.labs import Lab
from models.scheduling_attempt import SchedulingAttempt
from models.staff_members import Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment, StudyPlan
from models.time_preferences import TimePreference
from schedule_format import (
    generate_schedule_json,
    generate_schedule_report,
    print_schedule_statistics,
)
from utils.room_utils import get_room_key


class SchedulingEngine:
    def __init__(self, constraint_manager, resource_manager):
        self.constraint_manager = constraint_manager
        self.resource_manager = resource_manager
        self.logger = logging.getLogger("scheduler")

    def schedule_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan_mapping: Dict[int, StudyPlan],
        max_attempts: int = 100,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Assignment]:
        """Main scheduling function with ConstraintManager as single source of truth"""
        self.logger.info("=== STARTING BLOCK SCHEDULING ===")

        # Convert CourseAssignments to Blocks
        blocks = self._convert_course_assignments_to_blocks(
            course_assignments, study_plan_mapping
        )
        total_blocks = len(blocks)

        self.logger.info(f"Converted to {total_blocks} blocks total")

        best_assignments = {}
        best_score = 0.0
        best_count = 0

        if progress_callback:
            progress_callback(0, total_blocks, "initializing", 1)

        for attempt in range(max_attempts):
            self.logger.info(
                f"\n=== SCHEDULING ATTEMPT {attempt + 1}/{max_attempts} ==="
            )

            # CRITICAL: Fresh state for each attempt
            self.constraint_manager.initialize_fresh_state()
            scheduled_count = 0

            # Sort blocks by priority (uses current empty state)
            sorted_blocks = self._sort_blocks_by_priority(blocks)

            # Schedule each block one by one
            for i, block in enumerate(sorted_blocks):
                self.logger.info(
                    f"\n--- Scheduling block {i+1}/{len(sorted_blocks)}: {block.id} ---"
                )

                assignment = self._schedule_single_block(block)

                if assignment:
                    # ATOMIC OPERATION: Check and commit in one step
                    success = self.constraint_manager.make_assignment(
                        block.id, assignment
                    )
                    if success:
                        scheduled_count += 1
                        self.logger.info(
                            f"SUCCESS: {block.id} scheduled ({scheduled_count}/{total_blocks})"
                        )

                        if progress_callback:
                            progress_callback(
                                scheduled_count, total_blocks, "scheduling", attempt + 1
                            )
                    else:
                        self.logger.error(f"FAILED to commit assignment for {block.id}")
                else:
                    self.logger.warning(f"FAILED to find assignment for {block.id}")

            # Evaluate this attempt
            current_assignments = self.constraint_manager.get_all_assignments()
            attempt_score = self._evaluate_schedule(current_assignments)

            self.logger.info(f"\n=== ATTEMPT {attempt + 1} RESULTS ===")
            self.logger.info(f"Scheduled: {scheduled_count}/{total_blocks}")
            self.logger.info(f"Score: {attempt_score:.3f}")

            # Update best if this is better
            if scheduled_count > best_count or (
                scheduled_count == best_count and attempt_score > best_score
            ):
                best_assignments = current_assignments.copy()
                best_score = attempt_score
                best_count = scheduled_count
                self.logger.info("NEW BEST ATTEMPT!")

            # Perfect schedule check
            if scheduled_count == total_blocks and attempt_score >= 0.95:
                self.logger.info("PERFECT SCHEDULE ACHIEVED!")
                if progress_callback:
                    progress_callback(
                        scheduled_count, total_blocks, "completed", attempt + 1
                    )
                break

        if not best_assignments:
            self.logger.error("SCHEDULING FAILED: Could not find any valid schedule")
            raise ValueError("Could not find a valid schedule")

        self.logger.info(f"\n=== FINAL RESULT ===")
        self.logger.info(f"Best schedule: {best_count}/{total_blocks} blocks")
        self.logger.info(f"Success rate: {(best_count/total_blocks)*100:.1f}%")

        # FINAL VERIFICATION
        self._verify_final_schedule(best_assignments)

        return best_assignments

    def _schedule_single_block(self, block: Block) -> Optional[Assignment]:
        possible_rooms = self.resource_manager.get_suitable_rooms(block)
        for room in possible_rooms:
            available_slots = self.resource_manager.get_available_slots(
                block, room, self.constraint_manager.get_all_assignments()
            )
            for slot in available_slots:
                is_valid, _ = self.constraint_manager.can_assign(block, slot, room)
                if is_valid:
                    return Assignment(block, slot, room)
        return None

    def _verify_no_conflicts_in_assignments(self, assignments):
        # Group by time slot
        by_time = {}
        for block_id, assignment in assignments.items():
            key = (assignment.time_slot.day, assignment.time_slot.start_time)
            if key not in by_time:
                by_time[key] = []
            by_time[key].append((block_id, assignment))

        # Check each time slot
        for time_key, time_assignments in by_time.items():
            rooms_used = set()
            staff_used = set()

            for block_id, assignment in time_assignments:
                room_key = get_room_key(assignment.room)
                staff_id = assignment.block.staff_member.id

                if room_key in rooms_used:
                    self.logger.error(
                        f"VERIFICATION FAILED: Room {room_key} double-booked at {time_key}"
                    )
                    return False

                if staff_id in staff_used:
                    self.logger.error(
                        f"VERIFICATION FAILED: Staff {staff_id} double-booked at {time_key}"
                    )
                    return False

                rooms_used.add(room_key)
                staff_used.add(staff_id)

        return True

    def _sort_blocks_by_priority(self, blocks: List[Block]) -> List[Block]:
        """Sort blocks by various constraints and priorities"""
        self.logger.debug("Sorting blocks by priority...")

        def get_block_score(block: Block) -> tuple:
            # Get current state for accurate calculations
            current_assignments = self.constraint_manager.get_all_assignments()

            # Get possible rooms for this block
            possible_rooms = self.resource_manager.get_suitable_rooms(block)

            # Calculate total available time slots across all possible rooms
            total_available_slots = 0
            for room in possible_rooms:
                available_slots = self.resource_manager.get_available_slots(
                    block, room, current_assignments
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

        sorted_blocks = sorted(blocks, key=get_block_score, reverse=True)

        self.logger.debug("Block priority order:")
        for i, block in enumerate(sorted_blocks[:5]):  # Log first 5
            score = get_block_score(block)
            self.logger.debug(f"  {i+1}. {block.id} - Score: {score}")

        return sorted_blocks

    def _verify_final_schedule(self, assignments: Dict[str, Assignment]):
        """Verify the final schedule has no conflicts"""
        self.logger.info("=== FINAL SCHEDULE VERIFICATION ===")

        conflicts_found = False

        # Group assignments by time slot
        time_slots = {}
        for block_id, assignment in assignments.items():
            time_key = (assignment.time_slot.day, assignment.time_slot.start_time)
            if time_key not in time_slots:
                time_slots[time_key] = []
            time_slots[time_key].append((block_id, assignment))

        # Check each time slot for conflicts
        for time_key, slot_assignments in time_slots.items():
            day, start_time = time_key

            if len(slot_assignments) > 1:
                self.logger.debug(
                    f"Checking time slot: {day.name} {start_time} ({len(slot_assignments)} assignments)"
                )

                # Check for room conflicts
                rooms_used = {}
                staff_used = {}

                for block_id, assignment in slot_assignments:
                    room_key = get_room_key(assignment.room)
                    staff_id = assignment.block.staff_member.id

                    # Room conflict check
                    if room_key in rooms_used:
                        conflicts_found = True
                        self.logger.error(
                            f"ROOM CONFLICT: {assignment.room.name} used by both {rooms_used[room_key]} and {block_id}"
                        )
                    else:
                        rooms_used[room_key] = block_id

                    # Staff conflict check
                    if staff_id in staff_used:
                        conflicts_found = True
                        self.logger.error(
                            f"STAFF CONFLICT: {assignment.block.staff_member.name} assigned to both {staff_used[staff_id]} and {block_id}"
                        )
                    else:
                        staff_used[staff_id] = block_id

        if conflicts_found:
            self.logger.error("CRITICAL: Final schedule contains conflicts!")
        else:
            self.logger.info(
                "SUCCESS: Final schedule verification passed - no conflicts found"
            )

        return not conflicts_found

    def _convert_course_assignments_to_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan_mapping: Dict[int, StudyPlan],
    ) -> List[Block]:
        """Convert CourseAssignment objects to Block objects"""
        self.logger.info("=== CONVERTING COURSE ASSIGNMENTS TO BLOCKS ===")
        self.logger.info(f"Course Assignments Count: {len(course_assignments)}")

        blocks = []

        for i, course in enumerate(course_assignments):
            # Get the correct study plan for this course
            study_plan = study_plan_mapping[i]

            self.logger.info(f"\n--- COURSE {i+1}: {course.course_code} ---")
            self.logger.info(
                f"Study Plan: {study_plan.academic_list.name} Level {study_plan.academic_level}"
            )
            self.logger.info(f"Expected Students: {study_plan.expected_students}")
            self.logger.info(f"Lecture Groups: {course.lecture_groups}")
            self.logger.info(f"Lab Groups: {course.lab_groups}")

            # Generate lecture blocks
            self.logger.info(f"\n  GENERATING LECTURE BLOCKS for {course.course_code}:")
            lecture_group_count = 1
            lecture_blocks_created = 0

            for lecturer_assignment in course.lecturers:
                try:
                    if "lecturer" not in lecturer_assignment:
                        self.logger.error(
                            f"    ERROR: No 'lecturer' key in assignment: {lecturer_assignment}"
                        )
                        continue

                    lecturer = lecturer_assignment["lecturer"]
                    num_groups = lecturer_assignment["num_of_groups"]

                    self.logger.info(
                        f"    Creating {num_groups} lecture blocks for {lecturer.name}"
                    )

                    for group_idx in range(num_groups):
                        block_id = f"L_{course.course_code}_{lecturer.id}_{lecture_group_count}"

                        lecture_block = Block(
                            id=block_id,
                            course_code=course.course_code,
                            course_object=course,
                            block_type=BlockType.LECTURE,
                            staff_member=lecturer,
                            student_count=study_plan.expected_students
                            // course.lecture_groups,  # Use correct study plan
                            required_room_type="hall",
                            group_number=lecture_group_count,
                            total_groups=course.lecture_groups,
                            is_single_group_course=course.lecture_groups == 1,
                            academic_list=study_plan.academic_list.name,  # Use correct study plan
                            academic_list_object=study_plan.academic_list,
                            academic_level=study_plan.academic_level,  # Use correct study plan
                        )

                        blocks.append(lecture_block)
                        lecture_blocks_created += 1

                        self.logger.info(f"    CREATED LECTURE BLOCK: {block_id}")
                        self.logger.info(
                            f"      Study Plan: {study_plan.academic_list.name} L{study_plan.academic_level}"
                        )
                        self.logger.info(f"      Staff: {lecturer.name}")
                        self.logger.info(
                            f"      Students: {lecture_block.student_count}"
                        )

                        lecture_group_count += 1

                except Exception as e:
                    self.logger.error(
                        f"    ERROR creating lecture block: {str(e)}", exc_info=True
                    )

            # Generate lab blocks if they exist
            if course.lab_groups and course.lab_groups > 0:
                self.logger.info(f"\n  GENERATING LAB BLOCKS for {course.course_code}:")

                if not course.teaching_assistants:
                    self.logger.error(
                        f"    ERROR: Course {course.course_code} has {course.lab_groups} lab groups but no teaching assistants!"
                    )
                    continue

                lab_group_count = 1
                lab_blocks_created = 0

                for ta_assignment in course.teaching_assistants:
                    try:
                        ta = None
                        if "teaching_assistant" in ta_assignment:
                            ta = ta_assignment["teaching_assistant"]
                        elif "teaching_assistant_id" in ta_assignment:
                            self.logger.error(
                                f"      Cannot create lab block without TA object!"
                            )
                            continue

                        if not ta:
                            continue

                        num_groups = ta_assignment["num_of_groups"]

                        for group_idx in range(num_groups):
                            block_id = (
                                f"P_{course.course_code}_{ta.id}_{lab_group_count}"
                            )

                            lab_block = Block(
                                id=block_id,
                                course_code=course.course_code,
                                course_object=course,
                                block_type=BlockType.LAB,
                                staff_member=ta,
                                student_count=study_plan.expected_students
                                // course.lab_groups,  # Use correct study plan
                                required_room_type=(
                                    "lab" if course.practical_in_lab else "hall"
                                ),
                                preferred_rooms=course.preferred_labs,
                                group_number=lab_group_count,
                                total_groups=course.lab_groups,
                                is_single_group_course=course.lab_groups == 1,
                                academic_list=study_plan.academic_list.name,  # Use correct study plan
                                academic_list_object=study_plan.academic_list,
                                academic_level=study_plan.academic_level,  # Use correct study plan
                                practical_in_lab=course.practical_in_lab,
                            )

                            blocks.append(lab_block)
                            lab_blocks_created += 1

                            self.logger.info(f"      CREATED LAB BLOCK: {block_id}")
                            self.logger.info(
                                f"        Study Plan: {study_plan.academic_list.name} L{study_plan.academic_level}"
                            )
                            self.logger.info(f"        Staff: {ta.name}")

                            lab_group_count += 1

                    except Exception as e:
                        self.logger.error(
                            f"      ERROR creating lab block: {str(e)}", exc_info=True
                        )

        # Final summary
        lecture_blocks = [b for b in blocks if b.block_type == BlockType.LECTURE]
        lab_blocks = [b for b in blocks if b.block_type == BlockType.LAB]

        self.logger.info(f"\n=== BLOCK GENERATION SUMMARY ===")
        self.logger.info(f"Total Blocks Created: {len(blocks)}")
        self.logger.info(f"Lecture Blocks: {len(lecture_blocks)}")
        self.logger.info(f"Lab Blocks: {len(lab_blocks)}")

        return blocks

    def get_block_score(self, block: Block) -> tuple:
        # Get possible rooms for this block
        possible_rooms = self._get_possible_rooms(block)

        # Calculate total available time slots across all possible rooms
        total_available_slots = 0
        current_assignments = (
            self.constraint_manager.get_all_assignments()
        )  # FIX: Get from constraint manager

        for room in possible_rooms:
            available_slots = self.resource_manager.get_available_slots(
                block,
                room,
                current_assignments,  # FIX: Use assignments from constraint manager
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

        sorted_blocks = sorted(blocks, key=get_block_score, reverse=True)

        self.logger.debug("Block priority order:")
        for i, block in enumerate(sorted_blocks[:5]):  # Log first 5
            score = get_block_score(block)
            self.logger.debug(f"  {i+1}. {block.id} - Score: {score}")

        return sorted_blocks

    def _get_possible_rooms(self, block: Block) -> List[Union[Hall, Lab]]:
        """Get list of possible rooms for a block"""
        rooms = self.resource_manager.get_suitable_rooms(block)
        self.logger.debug(f"Block {block.id} has {len(rooms)} possible rooms")
        return rooms

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
        self,
        block: Block,
        room: Union[Hall, Lab],
        current_assignments: Dict[str, Assignment],
    ) -> List[TimePreference]:
        """Get list of possible time slots for a block in a room"""
        slots = self.resource_manager.get_available_slots(
            block,
            room,
            current_assignments,
        )

        self.logger.debug(
            f"Block {block.id} in room {room.name} has {len(slots)} possible slots"
        )
        return slots

    def _debug_constraint_check(self, block, slot, room, current_assignments):
        """Debug version of constraint checking with detailed logging"""
        room_type, room_id = get_room_key(room)
        self.logger.debug(f"\n=== DEBUGGING CONSTRAINT CHECK ===")
        self.logger.debug(
            f"Block: {block.id} ({block.course_object.course_code} {block.block_type.value})"
        )
        self.logger.debug(
            f"Staff: {block.staff_member.name} (ID: {block.staff_member.id}, Type: {type(block.staff_member.id)})"
        )
        self.logger.debug(f"Room: {room.name} (ID: {room_id}, Type: {type(room_id)})")
        self.logger.debug(f"Time: {slot.day.name} {slot.start_time}")
        self.logger.debug(f"Current assignments: {len(current_assignments)}")

        # Manual conflict checking
        slot_key = (slot.day, slot.start_time)

        # Check for room conflicts manually
        room_conflicts = []
        staff_conflicts = []

        for existing_id, existing_assignment in current_assignments.items():
            existing_slot_key = (
                existing_assignment.time_slot.day,
                existing_assignment.time_slot.start_time,
            )

            if existing_slot_key == slot_key:
                # Same time slot - check for conflicts
                if get_room_key(existing_assignment.room) == get_room_key(room):
                    room_conflicts.append(existing_id)
                    self.logger.warning(
                        f"MANUAL CHECK: Room conflict with {existing_id}"
                    )

                if existing_assignment.block.staff_member.id == block.staff_member.id:
                    staff_conflicts.append(existing_id)
                    self.logger.warning(
                        f"MANUAL CHECK: Staff conflict with {existing_id}"
                    )

        # Compare with constraint manager result
        is_valid, violation = self.constraint_manager.check_all_constraints(
            block, slot, room, current_assignments
        )

        self.logger.debug(
            f"Constraint manager result: {'VALID' if is_valid else 'INVALID'}"
        )
        if not is_valid:
            self.logger.debug(f"Violation: {violation}")

        # Compare results
        expected_conflicts = len(room_conflicts) > 0 or len(staff_conflicts) > 0
        if expected_conflicts and is_valid:
            self.logger.error(
                f"CONSTRAINT MANAGER BUG: Should have detected conflicts but didn't!"
            )
            self.logger.error(f"Room conflicts: {room_conflicts}")
            self.logger.error(f"Staff conflicts: {staff_conflicts}")
        elif not expected_conflicts and not is_valid:
            self.logger.error(
                f"CONSTRAINT MANAGER BUG: Detected conflict when none exists!"
            )
            self.logger.error(f"Violation reported: {violation}")
        else:
            self.logger.debug(f"Constraint manager working correctly")

        return is_valid, violation

    def _local_search_with_constraint_manager(self, max_iterations: int = 50) -> bool:
        """Improve schedule using local search with ConstraintManager"""
        self.logger.debug("Starting local search optimization...")

        current_assignments = self.constraint_manager.get_all_assignments()
        current_score = self._evaluate_schedule(current_assignments)
        improved = False

        for iteration in range(max_iterations):
            assignment_ids = list(current_assignments.keys())

            for i in range(len(assignment_ids)):
                for j in range(i + 1, len(assignment_ids)):
                    block_id1, block_id2 = assignment_ids[i], assignment_ids[j]
                    assignment1 = current_assignments[block_id1]
                    assignment2 = current_assignments[block_id2]

                    # Try swapping rooms using ConstraintManager
                    if self._try_swap_rooms(
                        block_id1, block_id2, assignment1, assignment2
                    ):
                        new_score = self._evaluate_schedule(
                            self.constraint_manager.get_all_assignments()
                        )
                        if new_score > current_score:
                            current_score = new_score
                            current_assignments = (
                                self.constraint_manager.get_all_assignments()
                            )
                            improved = True
                            self.logger.debug(
                                f"Improved by swapping rooms: {block_id1} <-> {block_id2}"
                            )
                        else:
                            # Undo the swap
                            self._try_swap_rooms(
                                block_id1, block_id2, assignment2, assignment1
                            )

            if not improved:
                break

        return improved

    def _try_swap_rooms(
        self,
        block_id1: str,
        block_id2: str,
        assignment1: Assignment,
        assignment2: Assignment,
    ) -> bool:
        """Try to swap rooms between two assignments using ConstraintManager"""
        # Create new assignments with swapped rooms
        new_assignment1 = Assignment(
            assignment1.block, assignment1.time_slot, assignment2.room
        )
        new_assignment2 = Assignment(
            assignment2.block, assignment2.time_slot, assignment1.room
        )

        # Temporarily remove both assignments
        self.constraint_manager.undo_assignment(block_id1)
        self.constraint_manager.undo_assignment(block_id2)

        # Try to make new assignments
        success1 = self.constraint_manager.make_assignment(block_id1, new_assignment1)
        success2 = False
        if success1:
            success2 = self.constraint_manager.make_assignment(
                block_id2, new_assignment2
            )

        if success1 and success2:
            return True
        else:
            # Restore original assignments
            if success1:
                self.constraint_manager.undo_assignment(block_id1)
            self.constraint_manager.make_assignment(block_id1, assignment1)
            self.constraint_manager.make_assignment(block_id2, assignment2)
            return False

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
        if not self.best_assignments:
            return True

        # Fewer unassigned blocks is always better
        if len(attempt.unassigned_blocks) < len(
            self.best_assignments.unassigned_blocks
        ):
            return True

        # If same number of unassigned, compare scores
        if len(attempt.unassigned_blocks) == len(
            self.best_assignments.unassigned_blocks
        ):
            return attempt.score > self.best_assignments.score

        return False
