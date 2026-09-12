# scheduler.py - Enhanced with comprehensive logging

import hashlib
import json
import logging
import math
import os
import random
import time as time_module
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from models.academic_list import AcademicList
from models.block import SOURCE_EXTERNAL, Assignment, Block, BlockType
from models.external_course import ExternalCourse
from models.halls import Hall
from models.labs import Lab
from models.scheduling_attempt import SchedulingAttempt
from models.staff_members import Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment, CourseParticipant, StudyPlan
from models.time_preferences import TimePreference
from utils.insights import Insight, InsightCategory, InsightSeverity, entity
from utils.room_utils import get_room_key
from utils.sections import blocks_clash, cohort_keys as sections_cohort_keys, shared_cohorts
from utils.time_utils import intervals_overlap, preference_covers_slot

try:
    from solver.cpsat_scheduler import solve_schedule
    CPSAT_AVAILABLE = True
except ImportError:  # ortools not installed - greedy fallback stays usable
    solve_schedule = None
    CPSAT_AVAILABLE = False


class FailureReason(Enum):
    NO_SUITABLE_ROOMS = "no_suitable_rooms"
    NO_AVAILABLE_TIME_SLOTS = "no_available_time_slots"
    CONSTRAINT_VIOLATIONS = "constraint_violations"
    STAFF_UNAVAILABLE = "staff_unavailable"
    ASSIGNMENT_COMMIT_FAILED = "assignment_commit_failed"


@dataclass
class BlockFailure:
    block_id: str
    block_info: str  # Human readable block description
    reason: FailureReason
    details: str
    attempted_rooms: int = 0
    attempted_slots: int = 0


class SchedulingEngine:
    def __init__(
        self,
        constraint_manager,
        resource_manager,
        seed: int = 20260902,
        use_cpsat: bool = True,
    ):
        self.constraint_manager = constraint_manager
        self.resource_manager = resource_manager
        self.logger = logging.getLogger("scheduler")
        # Failures from the best attempt only - see schedule_blocks.
        self.failed_blocks = []
        # Blocks that could not even be built, usually unresolved staff. Recorded
        # at conversion time and kept across attempts, since retrying cannot help.
        self.unbuildable_blocks = []
        # Placements made outside a TA's recorded hours (advisory, not a conflict).
        self.advisory_warnings = []
        self.seed = seed
        # CP-SAT is the default placement core; the greedy loop is the
        # fallback when ortools is missing or the solver crashes.
        self.use_cpsat = use_cpsat and CPSAT_AVAILABLE
        # ENGINE insights produced by the solver (conflict sets), merged into
        # the run's insight list by the caller.
        self.solve_insights = []
        if use_cpsat and not CPSAT_AVAILABLE:
            self.logger.warning(
                "ortools is not installed in this interpreter - falling back "
                "to the greedy search, which packs and repairs far worse. "
                "Activate the project venv or run: pip install ortools"
            )
            self.solve_insights.append(
                Insight(
                    code="PLACEMENT_CORE_FALLBACK",
                    category=InsightCategory.ENGINE,
                    severity=InsightSeverity.WARNING,
                    phase="solve",
                    verdict=(
                        "ortools is not importable from this Python, so the run "
                        "used the greedy fallback - expect weaker packing and "
                        "more unscheduled blocks than the solver delivers."
                    ),
                    entities=[],
                    evidence={"ortools_available": False},
                    recommended_action=(
                        "Run the engine with the project venv "
                        "(.venv/Scripts/python.exe main.py) or pip install ortools."
                    ),
                )
            )
        # Per-attempt RNG used for candidate tie-breaking so retries explore
        # genuinely different choices (audit finding #4).
        self._attempt_rng: Optional[random.Random] = None

    @property
    def _strict_cohort_mode(self) -> bool:
        """Whether the constraint manager runs the historical conservative rule."""
        return bool(getattr(self.constraint_manager, "strict_cohort_mode", False))

    def _record_unbuildable(
        self, course: CourseAssignment, session_type: str, groups: int, reason: str
    ):
        """Record sessions that could not be turned into blocks at all."""
        failure = BlockFailure(
            block_id=f"{course.course_code}_{session_type}_unbuilt",
            block_info=f"{course.course_code} {session_type} x{groups}",
            reason=FailureReason.STAFF_UNAVAILABLE,
            details=reason,
        )
        self.unbuildable_blocks.append(failure)
        self.logger.error(
            f"UNBUILDABLE: {failure.block_info} - {reason} "
            f"({groups} session(s) will be missing from the schedule)"
        )

    @staticmethod
    def _build_block_id(
        block_type: BlockType,
        course_code: str,
        course_objects: List[CourseAssignment],
        academic_list_objects: List[AcademicList],
        academic_levels: List[int],
        group_number: int,
    ) -> str:
        """Build a stable ID from the curriculum session represented by a block.

        Course codes and staff assignments are labels or allocations, respectively;
        neither identifies a teaching session. The normalized curriculum scope does.
        Keeping the course code in the result makes logs readable, while the digest
        prevents collisions and keeps IDs reasonably short for merged courses.
        """
        if not (
            len(course_objects)
            == len(academic_list_objects)
            == len(academic_levels)
        ):
            raise ValueError(
                "Cannot build block ID with mismatched course, academic-list, and level counts"
            )

        curriculum_scope = sorted(
            (
                course_assignment.id,
                course_assignment.course_id,
                academic_list.id,
                academic_level,
            )
            for course_assignment, academic_list, academic_level in zip(
                course_objects,
                academic_list_objects,
                academic_levels,
                strict=True,
            )
        )
        identity = {
            "curriculum_scope": curriculum_scope,
            "group_number": group_number,
            "session_type": block_type.value,
        }
        encoded_identity = json.dumps(
            identity, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        scope_digest = hashlib.sha256(encoded_identity).hexdigest()[:16]
        prefix = "L" if block_type == BlockType.LECTURE else "P"
        return f"{prefix}_{course_code}_{scope_digest}_{group_number}"

    def _validate_unique_block_ids(self, blocks: List[Block]) -> None:
        """Fail before scheduling when inputs describe the same session twice."""
        blocks_by_id: Dict[str, List[Block]] = {}
        for block in blocks:
            blocks_by_id.setdefault(block.id, []).append(block)

        duplicate_groups = {
            block_id: matching_blocks
            for block_id, matching_blocks in blocks_by_id.items()
            if len(matching_blocks) > 1
        }
        if not duplicate_groups:
            return

        details = []
        for block_id, matching_blocks in duplicate_groups.items():
            sources = []
            for block in matching_blocks:
                lists = ", ".join(block.academic_lists)
                levels = ", ".join(str(level) for level in block.academic_levels)
                courses = ", ".join(str(course_id) for course_id in block.get_all_course_ids())
                sources.append(
                    f"courses=[{courses}], academic_lists=[{lists}], "
                    f"levels=[{levels}], group={block.group_number}"
                )
            details.append(f"{block_id}: " + " | ".join(sources))

        message = "Duplicate curriculum session blocks were generated: " + "; ".join(
            details
        )
        self.logger.error(message)
        raise ValueError(message)

    def schedule_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan_mapping: Dict[int, StudyPlan],
        max_attempts: int = 100,
        progress_callback: Optional[Callable] = None,
        prebuilt_blocks: Optional[List[Block]] = None,
        fixed_assignments: Optional[Dict[str, Assignment]] = None,
        time_budget_s: float = 300.0,
    ) -> Dict[str, Assignment]:
        """Main scheduling function with ConstraintManager as single source of truth"""
        self.logger.info("=== STARTING BLOCK SCHEDULING ===")
        fixed_assignments = fixed_assignments or {}

        if prebuilt_blocks is None:
            self.unbuildable_blocks = []
            blocks = self.build_blocks(course_assignments, study_plan_mapping)
        else:
            blocks = prebuilt_blocks

        self._validate_unique_block_ids(blocks)
        total_blocks = len(blocks)

        self.logger.info(f"Converted to {total_blocks} blocks total")

        # Placement core: CP-SAT first (exact, deterministic, proven coverage
        # or a named minimal conflict set); the greedy loop below is the
        # fallback.
        if self.use_cpsat:
            solved = self._solve_with_cpsat(
                blocks, fixed_assignments, progress_callback
            )
            if solved is not None:
                return solved

        best_assignments = {}
        best_score = 0.0
        best_count = 0
        best_failures = []

        if progress_callback:
            progress_callback(0, total_blocks, "initializing", 1)

        if not blocks:
            self.constraint_manager.initialize_fresh_state()
            for block_id, assignment in fixed_assignments.items():
                if not self.constraint_manager.seed_fixed_assignment(block_id, assignment):
                    raise ValueError(f"Could not preserve source entry {block_id}")
            return self.constraint_manager.get_all_assignments()

        # Constrainedness is measured against an empty schedule, so it is the same
        # for every attempt. Compute it once instead of once per attempt.
        block_constraints = self._measure_block_constraints(blocks)

        deadline = time_module.monotonic() + time_budget_s

        for attempt in range(max_attempts):
            if time_module.monotonic() > deadline:
                self.logger.warning(
                    f"Greedy time budget of {time_budget_s:.0f}s exhausted after "
                    f"{attempt} attempt(s) - keeping the best result found"
                )
                break

            self.logger.info(
                f"\n=== SCHEDULING ATTEMPT {attempt + 1}/{max_attempts} ==="
            )

            # CRITICAL: Fresh state for each attempt
            self.constraint_manager.initialize_fresh_state()
            for block_id, assignment in fixed_assignments.items():
                if not self.constraint_manager.seed_fixed_assignment(block_id, assignment):
                    raise ValueError(f"Could not preserve source entry {block_id}")
            scheduled_count = 0
            # Per-attempt, so the summary counts each block once rather than once
            # per attempt.
            self.failed_blocks = []
            self.advisory_warnings = []

            # Sort blocks by priority. Ties are broken by a per-attempt random
            # draw, which is what makes a second attempt explore anything new.
            rng = random.Random(self.seed + attempt)
            self._attempt_rng = rng
            sorted_blocks = self._sort_blocks_by_priority(
                blocks, block_constraints, rng
            )

            # Schedule each block one by one
            for i, block in enumerate(sorted_blocks):
                self.logger.info(
                    f"\n--- Scheduling block {i+1}/{len(sorted_blocks)}: {block.id} ---"
                )

                assignment, failure = self._schedule_single_block(block)

                if assignment:
                    success = self.constraint_manager.make_assignment(
                        block.id, assignment
                    )
                    if success:
                        scheduled_count += 1
                        self.logger.info(
                            f"SUCCESS: {block.id} scheduled ({scheduled_count}/{total_blocks})"
                        )
                        # ... rest of success handling ...
                    else:
                        commit_failure = BlockFailure(
                            block_id=block.id,
                            block_info=(
                                f"{block.display_label()} {block.block_type.value}"
                            ),
                            reason=FailureReason.ASSIGNMENT_COMMIT_FAILED,
                            details="Assignment found but was rejected during commit; see constraint-manager logs",
                        )
                        self.failed_blocks.append(commit_failure)
                        self.logger.error(
                            f"FAILED to commit assignment for {block.id}: {commit_failure.details}"
                        )
                else:
                    self.failed_blocks.append(failure)
                    self.logger.error(
                        f"FAILED to schedule {failure.block_info}: {failure.details}"
                    )

            # Second chance: let failed blocks displace an already-placed,
            # easily-movable block instead of leaving the attempt short.
            scheduled_count += self._repair_failed_blocks(
                sorted_blocks, fixed_assignments
            )

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
                best_failures = list(self.failed_blocks)
                self.logger.info("NEW BEST ATTEMPT!")

            # Complete coverage ends the search. Quality never claims
            # "perfect" - the unnormalized score only ranks attempts against
            # each other, so a 0.95 threshold on it was meaningless.
            if scheduled_count == total_blocks:
                self.logger.info(
                    f"ALL {total_blocks} BLOCKS PLACED on attempt {attempt + 1}"
                )
                if progress_callback:
                    progress_callback(
                        scheduled_count, total_blocks, "completed", attempt + 1
                    )
                break

        if not best_assignments:
            self.logger.error("SCHEDULING FAILED: Could not find any valid schedule")
            raise ValueError("Could not find a valid schedule")

        # Report the best attempt's failures, not every attempt's, plus the
        # sessions that never became blocks in the first place.
        self.failed_blocks = self.unbuildable_blocks + best_failures

        # Advisory warnings are derived once from the FINAL placements and
        # the input preferences - never from search history (audit finding
        # #8: blocks that were moved used to leave their stale warnings in
        # the report).
        self.advisory_warnings = []
        for final_assignment in best_assignments.values():
            self._record_advisory_warnings(final_assignment)

        self.logger.info(f"\n=== FINAL RESULT ===")
        self.logger.info(f"Best schedule: {best_count}/{total_blocks} blocks")
        self.logger.info(f"Success rate: {(best_count/total_blocks)*100:.1f}%")

        # FINAL VERIFICATION
        self._verify_final_schedule(best_assignments)
        self._log_failure_summary()

        return best_assignments

    def build_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan_mapping: Dict[int, StudyPlan],
        include_labs: bool = True,
    ) -> List[Block]:
        """Build the current expected block inventory without scheduling it."""
        self.unbuildable_blocks = []
        return self._convert_course_assignments_to_blocks(
            course_assignments,
            study_plan_mapping,
            include_labs=include_labs,
        )

    def build_external_blocks(
        self,
        external_courses: List[ExternalCourse],
        include_labs: bool = True,
    ) -> List[Block]:
        """
        Build the expected block inventory for external courses.

        Expansion mirrors the study-plan logic: one lecture block per lecture
        group (staffed from the lecturer distribution, or staffless for the
        external-lecturer case) and one lab block per lab group (TA
        distribution, or staffless). External blocks never pass through
        common-course merging or study-plan validation - they carry no local
        curriculum data at all.
        """
        blocks: List[Block] = []

        for course in external_courses:
            course_label = course.display_name()
            before_count = len(blocks)

            # Lecture component: external-venue lectures reserve time and
            # their lecturer's time but no room (requires_room=False).
            if course.lecture_groups > 0:
                lecturer_rows = [
                    a for a in course.staff_assignments if a["role"] == "lecturer"
                ]
                requires_room = course.lecture_venue != "external"
                student_count = max(
                    1, math.ceil(course.lecture_students_per_group or 1)
                )
                lecture_group_number = 1

                for row in lecturer_rows:
                    lecturer = row["staff"]
                    for _ in range(row["num_of_groups"]):
                        blocks.append(self._external_block(
                            course, BlockType.LECTURE, lecture_group_number,
                            lecturer, student_count, requires_room,
                            course.lecture_time_slots,
                            course.lecture_session_hours,
                        ))
                        lecture_group_number += 1

                if not lecturer_rows:
                    # The external-lecturer case (v5): hall + time only, no
                    # internal staff.
                    for _ in range(course.lecture_groups):
                        blocks.append(self._external_block(
                            course, BlockType.LECTURE, lecture_group_number,
                            None, student_count, requires_room,
                            course.lecture_time_slots,
                            course.lecture_session_hours,
                        ))
                        lecture_group_number += 1

            # Lab component: always scheduled by SchedU, staffed or not.
            if include_labs and course.lab_groups > 0:
                ta_rows = [a for a in course.staff_assignments if a["role"] == "ta"]
                student_count = max(
                    1, math.ceil(course.lab_students_per_group or 1)
                )
                lab_group_number = 1

                for row in ta_rows:
                    ta = row["staff"]
                    for _ in range(row["num_of_groups"]):
                        blocks.append(self._external_block(
                            course, BlockType.LAB, lab_group_number,
                            ta, student_count, True,
                            course.lab_time_slots,
                            course.lab_session_hours,
                        ))
                        lab_group_number += 1

                if not ta_rows:
                    # The staffless-labs case (v1): reserve lab + time only.
                    for _ in range(course.lab_groups):
                        blocks.append(self._external_block(
                            course, BlockType.LAB, lab_group_number,
                            None, student_count, True,
                            course.lab_time_slots,
                            course.lab_session_hours,
                        ))
                        lab_group_number += 1

            self.logger.info(
                "EXTERNAL COURSE %s: built %d block(s)", course_label,
                len(blocks) - before_count,
            )

        return blocks

    def _external_block(
        self,
        course: ExternalCourse,
        block_type: BlockType,
        group_number: int,
        staff_member,
        student_count: int,
        requires_room: bool,
        allowed_slots: Optional[List[TimePreference]] = None,
        session_hours: int = 2,
    ) -> Block:
        """One external session block with the external identity fields set."""
        prefix = "EXT_L" if block_type == BlockType.LECTURE else "EXT_P"
        return Block(
            id=f"{prefix}{course.id}_G{group_number}",
            course_codes=[],
            course_objects=[],
            block_type=block_type,
            staff_member=staff_member,
            student_count=student_count,
            required_room_type="hall" if block_type == BlockType.LECTURE else "lab",
            group_number=group_number,
            total_groups=(
                course.lecture_groups
                if block_type == BlockType.LECTURE
                else course.lab_groups
            ),
            is_single_group_course=False,
            academic_lists=[],
            academic_list_objects=[],
            academic_levels=[],
            preferred_rooms=list(course.eligible_labs) if course.eligible_labs else None,
            source_kind=SOURCE_EXTERNAL,
            external_course_id=course.id,
            external_course_name=course.name_en,
            external_course_code=course.code,
            requires_room=requires_room,
            allowed_slots=list(allowed_slots) if allowed_slots else None,
            session_hours=session_hours if session_hours in (1, 2) else 2,
            allow_reserved_period=bool(getattr(course, "allow_reserved_period", False)),
        )

    def _solve_with_cpsat(
        self,
        blocks: List[Block],
        fixed_assignments: Dict[str, Assignment],
        progress_callback: Optional[Callable],
    ) -> Optional[Dict[str, Assignment]]:
        """
        Exact placement via CP-SAT; None falls back to the greedy loop.

        Commits the solution through the ConstraintManager so every hard rule
        is re-validated on the way in. Skipped blocks and the minimal
        conflicting set become failures and an ENGINE insight, which the
        completeness gate turns into a loud, non-posted run.
        """
        self.logger.info(f"=== CP-SAT PLACEMENT: {len(blocks)} block(s) ===")
        if progress_callback:
            progress_callback(0, len(blocks), "scheduling", 1)

        try:
            budget = float(os.environ.get("SCHEDU_SOLVER_BUDGET_S", "60"))
        except ValueError:
            budget = 60.0

        self.failed_blocks = []
        self.advisory_warnings = []
        self.solve_insights = []

        try:
            result = solve_schedule(
                blocks=blocks,
                halls=list(self.resource_manager.halls.values()),
                labs=list(self.resource_manager.labs.values()),
                reserved_period=self.resource_manager.reserved_period,
                constraint_manager=self.constraint_manager,
                fixed_assignments=fixed_assignments,
                seed=self.seed,
                time_budget_s=budget,
            )
        except Exception as error:
            self.logger.exception(
                "CP-SAT solve crashed - falling back to the greedy search"
            )
            self.solve_insights.append(
                Insight(
                    code="PLACEMENT_CORE_FALLBACK",
                    category=InsightCategory.ENGINE,
                    severity=InsightSeverity.WARNING,
                    phase="solve",
                    verdict=(
                        f"The CP-SAT solver crashed ({error.__class__.__name__}: "
                        f"{error}) and the run fell back to the greedy search."
                    ),
                    entities=[],
                    evidence={"error": f"{error.__class__.__name__}: {error}"},
                    recommended_action=(
                        "Report this run's snapshot - the solver must never "
                        "crash. The greedy fallback still enforces every hard "
                        "rule."
                    ),
                )
            )
            return None

        self.logger.info(
            f"CP-SAT result: {result.status} in {result.stats.get('elapsed_s')}s "
            f"({len(result.assignments)} placed, {len(result.skipped_block_ids)} "
            f"skipped)"
        )

        self.constraint_manager.initialize_fresh_state()
        for block_id, assignment in fixed_assignments.items():
            if not self.constraint_manager.seed_fixed_assignment(
                block_id, assignment
            ):
                raise ValueError(f"Could not preserve source entry {block_id}")

        blocks_by_id = {block.id: block for block in blocks}
        for block_id, assignment in result.assignments.items():
            if self.constraint_manager.make_assignment(block_id, assignment):
                # Warnings are computed from the FINAL placements only.
                self._record_advisory_warnings(assignment)
            else:
                # The solver mirrors the constraint rules, so this should be
                # unreachable; treat it as a failed block rather than a crash.
                block = blocks_by_id.get(block_id)
                self.failed_blocks.append(
                    BlockFailure(
                        block_id=block_id,
                        block_info=(
                            f"{block.display_label()} {block.block_type.value}"
                            if block
                            else block_id
                        ),
                        reason=FailureReason.ASSIGNMENT_COMMIT_FAILED,
                        details="CP-SAT placement rejected by the constraint manager",
                    )
                )

        if result.skipped_block_ids:
            explanation = (
                f"No complete placement exists"
                if result.conflicting_block_ids
                else "Solver budget exhausted"
            )
            if result.conflicting_block_ids:
                explanation += (
                    f"; minimal conflicting block set: "
                    f"{result.conflicting_block_ids}"
                )
            for block_id in result.skipped_block_ids:
                block = blocks_by_id.get(block_id)
                self.failed_blocks.append(
                    BlockFailure(
                        block_id=block_id,
                        block_info=(
                            f"{block.display_label()} "
                            f"Group {block.group_number}"
                            if block
                            else block_id
                        ),
                        reason=FailureReason.CONSTRAINT_VIOLATIONS,
                        details=explanation,
                    )
                )
            self.solve_insights.append(
                Insight(
                    code="PLACEMENT_CONFLICT_SET",
                    category=InsightCategory.ENGINE,
                    severity=InsightSeverity.BLOCKER
                    if result.status in ("INFEASIBLE", "PARTIAL")
                    else InsightSeverity.WARNING,
                    phase="solve",
                    verdict=(
                        f"{len(result.skipped_block_ids)} block(s) could not be "
                        f"placed: {explanation}"
                    ),
                    entities=[
                        entity(
                            "course",
                            (
                                blocks_by_id[b].display_label()
                                if b in blocks_by_id
                                else b
                            ),
                            (
                                blocks_by_id[b].display_label()
                                if b in blocks_by_id
                                else b
                            ),
                        )
                        for b in (result.conflicting_block_ids
                                  or result.skipped_block_ids)[:6]
                    ],
                    evidence={
                        "solver_status": result.status,
                        "skipped_block_ids": result.skipped_block_ids,
                        "conflicting_block_ids": result.conflicting_block_ids,
                        "solver_stats": result.stats,
                    },
                    recommended_action=(
                        "Check the conflicting blocks' staff allocations and room "
                        "availability; the feasibility preflight passed, so this "
                        "is finer-grained than the aggregate bound. Redistribute "
                        "groups or widen availability for the named blocks."
                    ),
                )
            )
            self.logger.error(f"INSIGHT: {self.solve_insights[-1].render()}")

        if progress_callback:
            progress_callback(
                len(result.assignments), len(blocks), "completed", 1
            )

        return self.constraint_manager.get_all_assignments()

    def _record_advisory_warnings(self, assignment: Assignment):
        """
        Note placements that fall outside a TA's recorded hours.

        TA availability is advisory by design - it orders the candidate slots but
        does not veto them - so the only way it stays visible is to record when a
        placement steps outside it.
        """
        staff = assignment.block.staff_member
        if not isinstance(staff, TeachingAssistant) or not staff.timing_preferences:
            return

        if any(
            preference_covers_slot(pref, assignment.time_slot)
            for pref in staff.timing_preferences
        ):
            return

        message = (
            f"{staff.name} (TA, ID {staff.id}) placed on "
            f"{assignment.time_slot.day.name} {assignment.time_slot.start_time}"
            f"-{assignment.time_slot.end_time} for {assignment.block.id}, "
            f"outside their recorded hours"
        )
        if message in self.advisory_warnings:
            return
        self.advisory_warnings.append(message)
        self.logger.warning(f"ADVISORY: {message}")

    def _schedule_single_block(
        self, block: Block
    ) -> Tuple[Optional[Assignment], Optional[BlockFailure]]:
        """
        Schedule a single block, returning assignment or detailed failure info.

        Candidates are scored jointly across rooms and slots (audit finding
        #2): a placement that joins a period already running this course's
        parallel groups wins, so two groups take two rooms at one hour
        instead of serializing into two hours and wasting cohort availability.
        Ties are broken by the attempt RNG so retries explore genuinely
        different choices (audit finding #4).

        Roomless blocks (external-venue lectures) take a period-only path:
        candidates come from the working grid, not from any room's windows,
        and the placement reserves no room.
        """
        label = block.display_label()
        possible_rooms = self.resource_manager.get_suitable_rooms(block)

        if not block.requires_room:
            assignments = self.constraint_manager.get_all_assignments()
            joinable_periods = self._joinable_course_periods(block, assignments)

            scored: List[Tuple[tuple, TimePreference]] = []
            for slot in self.resource_manager.get_available_periods(block, assignments):
                is_valid, violation_reason = self.constraint_manager.can_assign(
                    block, slot, None
                )
                if is_valid:
                    scored.append(
                        (self._candidate_score(block, slot, None, joinable_periods), slot)
                    )

            if scored:
                best_score = min(score for score, _ in scored)
                best = [slot for score, slot in scored if score == best_score]
                if self._attempt_rng is not None and len(best) > 1:
                    slot = self._attempt_rng.choice(best)
                else:
                    slot = best[0]
                return Assignment(block, slot, None), None

            failure = BlockFailure(
                block_id=block.id,
                block_info=f"{label} Group {block.group_number}",
                reason=FailureReason.NO_AVAILABLE_TIME_SLOTS,
                details=(
                    "No available grid period for this roomless session"
                    + (
                        f" within {block.staff_member.name}'s recorded hours"
                        if block.staff_member is not None
                        else ""
                    )
                ),
            )
            return None, failure

        if not possible_rooms:
            failure = BlockFailure(
                block_id=block.id,
                block_info=f"{label} Group {block.group_number}",
                reason=FailureReason.NO_SUITABLE_ROOMS,
                details=f"No rooms found with capacity >= {block.student_count} for type '{block.required_room_type}'",
            )
            return None, failure

        assignments = self.constraint_manager.get_all_assignments()
        joinable_periods = self._joinable_course_periods(block, assignments)

        scored: List[Tuple[tuple, Tuple[Union[Hall, Lab], TimePreference]]] = []
        total_slots_tried = 0
        constraint_violations = []

        for room in possible_rooms:
            available_slots = self.resource_manager.get_available_slots(
                block, room, assignments
            )

            for slot in available_slots:
                total_slots_tried += 1
                is_valid, violation_reason = self.constraint_manager.can_assign(
                    block, slot, room
                )

                if is_valid:
                    score = self._candidate_score(
                        block, slot, room, joinable_periods
                    )
                    scored.append((score, (room, slot)))
                else:
                    constraint_violations.append(
                        f"{room.name} at {slot}: {violation_reason}"
                    )

        if scored:
            best_score = min(score for score, _ in scored)
            best = [pair for score, pair in scored if score == best_score]
            if self._attempt_rng is not None and len(best) > 1:
                room, slot = self._attempt_rng.choice(best)
            else:
                room, slot = best[0]
            return Assignment(block, slot, room), None

        # Determine specific failure reason
        if total_slots_tried == 0:
            failure = BlockFailure(
                block_id=block.id,
                block_info=f"{label} Group {block.group_number}",
                reason=FailureReason.NO_AVAILABLE_TIME_SLOTS,
                details=f"No available time slots found in {len(possible_rooms)} suitable rooms",
                attempted_rooms=len(possible_rooms),
            )
        else:
            failure = BlockFailure(
                block_id=block.id,
                block_info=f"{label} Group {block.group_number}",
                reason=FailureReason.CONSTRAINT_VIOLATIONS,
                details=f"All {total_slots_tried} slots failed constraints. Examples: {'; '.join(constraint_violations[:3])}",
                attempted_rooms=len(possible_rooms),
                attempted_slots=total_slots_tried,
            )

        return None, failure

    @staticmethod
    def _joinable_course_periods(
        block: Block, assignments: Dict[str, Assignment]
    ) -> Set[Tuple]:
        """Periods where this course's same-type sessions already run."""
        periods: Set[Tuple] = set()
        for existing in assignments.values():
            if existing.block.id == block.id:
                continue
            if existing.block.block_type != block.block_type:
                continue
            if not (
                block.get_all_course_ids() & existing.block.get_all_course_ids()
            ):
                continue
            periods.add(
                (
                    existing.time_slot.day,
                    existing.time_slot.start_time,
                    existing.time_slot.end_time,
                )
            )
        return periods

    @staticmethod
    def _candidate_score(
        block: Block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        joinable_periods: Set[Tuple],
    ) -> tuple:
        """
        Joint (room, slot) placement score - lower is better.

        Order: join a period already running this course's parallel groups,
        honor a recorded staff preference, tightest room fit, then earlier in
        the week/day for compaction. Deterministic without the RNG.
        """
        joins_parallel = (
            0
            if (slot.day, slot.start_time, slot.end_time) in joinable_periods
            else 1
        )
        if block.staff_member is None:
            # Staffless blocks have no hours to honour.
            preferred = 0
        else:
            preferred = (
                0
                if any(
                    preference_covers_slot(pref, slot)
                    for pref in block.staff_member.timing_preferences
                )
                else 1
            )
        room_fit = 0 if room is None else room.capacity - block.student_count
        return (joins_parallel, preferred, room_fit, slot.day.value, slot.start_time)

    def _repair_failed_blocks(
        self,
        attempt_blocks: List[Block],
        fixed_assignments: Dict[str, Assignment],
    ) -> int:
        """
        Give this attempt's failed blocks a second pass that may displace
        already-placed blocks.

        Greedy placement leaves a block unscheduled whenever every candidate
        slot is taken, even when an easily-movable neighbor could step aside.
        Each failed block therefore picks the contended slot with the fewest
        conflicting placements, evicts the cheapest of them, and the evicted
        block is re-placed through the normal path. A swap that strands the
        victim is rolled back exactly, so a repair can never make an attempt
        worse. Returns how many previously-failed blocks ended up scheduled.
        """
        if not self.failed_blocks:
            return 0

        blocks_by_id = {block.id: block for block in attempt_blocks}
        repaired = 0

        for failure in list(self.failed_blocks):
            block = blocks_by_id.get(failure.block_id)
            if block is None:
                # Unbuildable blocks have no Block object to place.
                continue

            if self._try_displacement_repair(block, fixed_assignments):
                self.failed_blocks.remove(failure)
                repaired += 1

        return repaired

    def _try_displacement_repair(
        self, block: Block, fixed_assignments: Dict[str, Assignment]
    ) -> bool:
        """Place `block` by displacing one placed block, or leave state untouched."""
        assignments = self.constraint_manager.get_all_assignments()
        possible_rooms = self.resource_manager.get_suitable_rooms(block)

        # Roomless blocks repair against grid periods with no room; roomed
        # blocks repair against (room, slot) pairs.
        if not block.requires_room:
            room_slot_pairs: List[Tuple[Optional[Union[Hall, Lab]], TimePreference]] = [
                (None, slot)
                for slot in self.resource_manager.get_available_periods(block, {})
            ]
        else:
            room_slot_pairs = [
                (room, slot)
                for room in possible_rooms
                # Raw availability windows, deliberately NOT filtered by
                # room/staff business: a slot held by another block is exactly
                # the candidate a displacement can free. can_assign decides
                # each one.
                for slot in self.resource_manager.get_available_slots(block, room, {})
            ]

        candidates: List[Tuple[TimePreference, Union[Hall, Lab, None], Set[str]]] = []
        for room, slot in room_slot_pairs:
            is_valid, _ = self.constraint_manager.can_assign(block, slot, room)
            if is_valid:
                # State shifted since the main loop (an earlier repair in
                # this pass freed a slot) - place directly.
                free_assignment = Assignment(block, slot, room)
                if self.constraint_manager.make_assignment(
                    block.id, free_assignment
                ):
                    self.logger.info(
                        f"REPAIR: {block.id} placed in a slot freed by an "
                        f"earlier repair ({slot.day.name} "
                        f"{slot.start_time}-{slot.end_time}, "
                        f"{room.name if room else 'no room'})"
                    )
                    return True
                continue

            victims = self._conflict_causes(block, slot, room, assignments)
            if victims:
                # Candidates without causes fail constraints no move can
                # repair (room windows, lab rules), so they are not useful.
                candidates.append((slot, room, victims))

        # Fewest victims first; the sort is stable, so within a tie the
        # room/slot iteration order above decides - keeping runs reproducible.
        candidates.sort(key=lambda candidate: len(candidate[2]))

        attempted = 0
        for slot, room, victims in candidates:
            if attempted >= 40:
                break
            victim_id = min(
                (
                    block_id
                    for block_id in victims
                    # Retained source entries are never displaced, and a victim
                    # of an earlier candidate in this loop is back in place
                    # after rollback, so re-displacing it stays legitimate.
                    if block_id not in fixed_assignments
                ),
                key=lambda block_id: self._calculate_block_priority(
                    assignments[block_id].block
                ),
                default=None,
            )
            if victim_id is None:
                continue

            attempted += 1
            if self._swap_with_victim(block, slot, room, victim_id, assignments):
                return True

        # Bounded two-victim displacement (audit finding #3): a contended
        # period may hold two parallel groups that only make room together.
        for slot, room, victims in candidates:
            if attempted >= 60:
                break
            movable = [v for v in victims if v not in fixed_assignments]
            if len(victims) != 2 or len(movable) != 2:
                continue
            attempted += 1
            if self._swap_with_victims(block, slot, room, movable, assignments):
                return True

        self.logger.debug(
            f"REPAIR: no displacement worked for {block.id} "
            f"({len(candidates)} contended slot(s) tried)"
        )
        return False

    def _swap_with_victims(
        self,
        block: Block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        victim_ids: List[str],
        assignments: Dict[str, Assignment],
    ) -> bool:
        """
        Move `block` into a slot held by exactly the given victims.

        Every path either ends with the block and all victims placed (new
        state) or with the state restored to exactly what it was, so callers
        can treat failure as a no-op.
        """
        snapshot = dict(self.constraint_manager.get_all_assignments())
        victim_assignments = {vid: assignments[vid] for vid in victim_ids}

        try:
            for victim_id in victim_ids:
                self.constraint_manager.undo_assignment(victim_id)
            if not self.constraint_manager.make_assignment(
                block.id, Assignment(block, slot, room)
            ):
                self._rebuild_state(snapshot)
                return False

            replacements = {}
            for victim_id, victim_assignment in victim_assignments.items():
                replacement, _ = self._schedule_single_block(
                    victim_assignment.block
                )
                if replacement is None or not self.constraint_manager.make_assignment(
                    victim_id, replacement
                ):
                    self._rebuild_state(snapshot)
                    return False
                replacements[victim_id] = replacement

            self.logger.info(
                f"REPAIR: {block.id} placed {slot.day.name} "
                f"{slot.start_time}-{slot.end_time} in {room.name}, displacing "
                f"{len(victim_ids)} block(s): "
                + "; ".join(
                    f"{vid} re-placed {r.time_slot.day.name} "
                    f"{r.time_slot.start_time}-{r.time_slot.end_time}"
                    for vid, r in replacements.items()
                )
            )
            return True
        except Exception:
            self.logger.exception(
                f"REPAIR: unexpected error displacing {len(victim_ids)} blocks "
                f"for {block.id}"
            )
            self._rebuild_state(snapshot)
            return False

    def _swap_with_victim(
        self,
        block: Block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        victim_id: str,
        assignments: Dict[str, Assignment],
    ) -> bool:
        """Move `block` into a slot held by `victim_id` and re-place the victim.

        Every path either ends with both blocks placed (new state) or with the
        state restored to exactly what it was, so callers can treat failure as
        a no-op.
        """
        victim_assignment = assignments[victim_id]
        snapshot = dict(self.constraint_manager.get_all_assignments())

        try:
            if not self.constraint_manager.undo_assignment(victim_id):
                return False
            if not self.constraint_manager.make_assignment(
                block.id, Assignment(block, slot, room)
            ):
                self._restore_victim(victim_id, victim_assignment, snapshot)
                return False

            replacement, _ = self._schedule_single_block(victim_assignment.block)
            if replacement is None or not self.constraint_manager.make_assignment(
                victim_id, replacement
            ):
                self.constraint_manager.undo_assignment(block.id)
                self._restore_victim(victim_id, victim_assignment, snapshot)
                return False

            self.logger.info(
                f"REPAIR: {block.id} placed {slot.day.name} "
                f"{slot.start_time}-{slot.end_time} in {room.name}, displacing "
                f"{victim_id}; victim re-placed {replacement.time_slot.day.name} "
                f"{replacement.time_slot.start_time}-"
                f"{replacement.time_slot.end_time} in {replacement.room.name}"
            )
            return True
        except Exception:
            self.logger.exception(f"REPAIR: unexpected error swapping {block.id}")
            self._rebuild_state(snapshot)
            return False

    def _restore_victim(
        self,
        victim_id: str,
        victim_assignment: Assignment,
        snapshot: Dict[str, Assignment],
    ) -> None:
        """Put a displaced victim back; rebuild from the snapshot if that fails."""
        if self.constraint_manager.make_assignment(victim_id, victim_assignment):
            return
        self.logger.error(
            f"REPAIR: could not restore {victim_id} to its original placement - "
            f"rebuilding state"
        )
        self._rebuild_state(snapshot)

    def _rebuild_state(self, snapshot: Dict[str, Assignment]) -> None:
        """Last-resort recovery: rebuild constraint state from a snapshot."""
        self.constraint_manager.initialize_fresh_state()
        for block_id, assignment in snapshot.items():
            if not self.constraint_manager.make_assignment(block_id, assignment):
                self.logger.critical(
                    f"REPAIR: could not restore {block_id} during state rebuild - "
                    f"schedule state may be inconsistent"
                )

    def _conflict_causes(
        self,
        block: Block,
        slot: TimePreference,
        room: Union[Hall, Lab],
        assignments: Dict[str, Assignment],
    ) -> Set[str]:
        """
        Placed blocks that make `slot` invalid for this block in this room.

        Mirrors the state-dependent hard constraints (room, staff, cohort), so a
        candidate whose failure no move can repair yields no causes and is
        never considered for displacement.
        """
        room_key = get_room_key(room) if room is not None else None
        staff_id = block.staff_member.id if block.staff_member is not None else None

        causes: Set[str] = set()
        for existing_id, existing in assignments.items():
            existing_slot = existing.time_slot
            if existing_slot.day != slot.day:
                continue
            if not intervals_overlap(
                slot.start_time,
                slot.end_time,
                existing_slot.start_time,
                existing_slot.end_time,
            ):
                continue

            # Rooms only collide when both sides have one, staff only when
            # both sides have staff.
            shares_room = (
                room_key is not None
                and existing.room is not None
                and get_room_key(existing.room) == room_key
            )
            shares_staff = (
                staff_id is not None
                and existing.block.staff_member is not None
                and existing.block.staff_member.id == staff_id
            )
            if (
                shares_room
                or shares_staff
                or blocks_clash(block, existing.block, self._strict_cohort_mode)
            ):
                causes.add(existing_id)

        return causes

    def _measure_block_constraints(
        self, blocks: List[Block]
    ) -> Dict[str, Tuple[int, int]]:
        """
        How many rooms and slots each block could use in an empty schedule.

        Measured once: the schedule is empty at sort time on every attempt, so
        re-measuring per attempt costs O(blocks x rooms) and returns the same
        numbers.
        """
        self.logger.debug("Measuring block constrainedness...")
        measurements = {}

        for block in blocks:
            if not block.requires_room:
                # Roomless blocks compete on periods, not rooms.
                periods = self.resource_manager.get_available_periods(block, {})
                measurements[block.id] = (0, len(periods))
                continue

            possible_rooms = self.resource_manager.get_suitable_rooms(block)
            total_available_slots = sum(
                len(self.resource_manager.get_available_slots(block, room, {}))
                for room in possible_rooms
            )
            measurements[block.id] = (len(possible_rooms), total_available_slots)

        return measurements

    def _sort_blocks_by_priority(
        self,
        blocks: List[Block],
        block_constraints: Dict[str, Tuple[int, int]],
        rng: Optional[random.Random] = None,
    ) -> List[Block]:
        """Sort blocks by various constraints and priorities, most constrained first"""
        self.logger.debug("Sorting blocks by priority...")

        jitter = {block.id: (rng.random() if rng else 0.0) for block in blocks}

        def get_block_score(block: Block) -> tuple:
            room_count, slot_count = block_constraints.get(block.id, (0, 0))
            # A merged block puts several cohorts in one room at once, so its
            # options collapse much faster than room/slot counts alone suggest.
            cohort_count = len(self._cohort_keys(block))

            return (
                # First priority: Single group courses
                block.is_single_group_course,
                # Second priority: More cohorts means more constrained - every
                # (list, level) pair must be free at the same time
                cohort_count,
                # Third priority: Fewer room options means more constrained
                -room_count,
                # Fourth priority: Fewer time slot options means more constrained
                -slot_count,
                # Fifth priority: Calculated priority score
                self._calculate_block_priority(block),
                # Tiebreak: varies per attempt so retries explore a new ordering
                jitter[block.id],
            )

        sorted_blocks = sorted(blocks, key=get_block_score, reverse=True)

        self.logger.debug("Block priority order:")
        for i, block in enumerate(sorted_blocks[:5]):  # Log first 5
            self.logger.debug(f"  {i+1}. {block.id} - Score: {get_block_score(block)}")

        return sorted_blocks

    def _verify_final_schedule(self, assignments: Dict[str, Assignment]):
        """
        Verify the final schedule has no room, staff or student conflicts.

        Compares every overlapping pair rather than grouping on start time, so
        sessions that overlap without sharing a start time are still caught.
        """
        self.logger.info("=== FINAL SCHEDULE VERIFICATION ===")

        conflicts_found = False
        items = list(assignments.items())

        for i in range(len(items)):
            block_id_a, a = items[i]
            for j in range(i + 1, len(items)):
                block_id_b, b = items[j]

                if a.time_slot.day != b.time_slot.day:
                    continue
                if not intervals_overlap(
                    a.time_slot.start_time,
                    a.time_slot.end_time,
                    b.time_slot.start_time,
                    b.time_slot.end_time,
                ):
                    continue

                when = (
                    f"{a.time_slot.day.name} "
                    f"{a.time_slot.start_time}-{a.time_slot.end_time} / "
                    f"{b.time_slot.start_time}-{b.time_slot.end_time}"
                )

                if (
                    a.room is not None
                    and b.room is not None
                    and get_room_key(a.room) == get_room_key(b.room)
                ):
                    conflicts_found = True
                    self.logger.error(
                        f"ROOM CONFLICT: {a.room.name} used by both {block_id_a} "
                        f"and {block_id_b} ({when})"
                    )

                if (
                    a.block.staff_member is not None
                    and b.block.staff_member is not None
                    and a.block.staff_member.id == b.block.staff_member.id
                ):
                    conflicts_found = True
                    self.logger.error(
                        f"STAFF CONFLICT: {a.block.staff_member.name} assigned to "
                        f"both {block_id_a} and {block_id_b} ({when})"
                    )

                if blocks_clash(a.block, b.block, self._strict_cohort_mode):
                    conflicts_found = True
                    self.logger.error(
                        f"STUDENT CONFLICT: shared cohorts "
                        f"{sorted(shared_cohorts(a.block, b.block))} scheduled for "
                        f"both {block_id_a} ({a.block.display_label()}) and "
                        f"{block_id_b} ({b.block.display_label()}) ({when})"
                    )

        if conflicts_found:
            self.logger.error("CRITICAL: Final schedule contains conflicts!")
        else:
            self.logger.info(
                "SUCCESS: Final schedule verification passed - no conflicts found"
            )

        return not conflicts_found

    @staticmethod
    def _cohort_keys(block: Block) -> Set[Tuple[str, int]]:
        """
        The (academic list, level) cohorts a block puts in a room.

        Delegates to utils/sections.py, the single source of truth for the
        cohort and section rules.
        """
        return set(sections_cohort_keys(block))

    @staticmethod
    def _merged_group_ownership(
        participants: List[CourseParticipant], total_groups: int
    ) -> Tuple[List[Tuple[CourseParticipant, ...]], Dict[int, int]]:
        """
        Which participants attend each merged group (index 0 = group 1), and
        each participant's whole-group quota by course id.

        Common-course children declare no groups of their own (they are pure
        pointers in AHLIA's data), so ownership is derived from each
        participant's share of the merged students: the total_groups windows
        are partitioned contiguously across participants in participant
        order, sized by largest-remainder quotas. A participant whose share
        rounds down to zero groups is folded into its neighbour's group, so
        some groups legitimately carry a small subset of two lists. The
        partition depends only on (participant order, student shares, group
        count), so every merged course over the same cohort set aligns group
        k to the same students - which is what keeps the positional section
        model valid across courses.
        """
        counts = [
            (p, getattr(p, "expected_students", 0) or 0) for p in participants
        ]
        total = sum(count for _, count in counts)
        if total <= 0 or total_groups <= 0:
            # No student data: every list attends every group (legacy shape).
            everyone = tuple(participants)
            return (
                [everyone for _ in range(max(total_groups, 0))],
                {},
            )

        exact = [total_groups * count / total for _, count in counts]
        quotas = [int(share) for share in exact]  # floor
        remainder_order = deque(
            sorted(range(len(counts)), key=lambda i: (-(exact[i] - quotas[i]), i))
        )
        remaining = total_groups - sum(quotas)
        while remaining > 0:
            quotas[remainder_order[0]] += 1
            remainder_order.rotate(-1)
            remaining -= 1

        # Expand contiguous windows in participant order; a zero-quota
        # participant with students folds into the nearest owned window -
        # the next participant's first window, or the last window when no
        # owned window follows.
        ownership: List[List[CourseParticipant]] = []
        pending_zero = []  # zero-quota participants waiting for an owner
        for index, (participant, count) in enumerate(counts):
            if quotas[index] <= 0:
                if count > 0:
                    pending_zero.append(participant)
                continue
            for window_index in range(quotas[index]):
                if window_index == 0 and pending_zero:
                    ownership.append([participant, *pending_zero])
                    pending_zero = []
                else:
                    ownership.append([participant])
        if ownership and pending_zero:
            # Trailing zero-quota participants join the last group.
            ownership[-1].extend(pending_zero)

        quota_by_course_id = {
            participant.course_id: quotas[index]
            for index, (participant, _) in enumerate(counts)
        }
        return (
            [tuple(attendees) for attendees in ownership],
            quota_by_course_id,
        )

    @staticmethod
    def _merged_group_scope(
        ownership: Tuple[CourseParticipant, ...],
        quota_by_course_id: Dict[int, int],
        parent_course: CourseAssignment,
    ) -> Dict:
        """
        Block scope for one merged group: the attending participants (group
        k = section k of exactly those lists) with their summed section
        sizes, plus the per-participant CourseAssignment stubs. A folded
        participant (quota 0) puts all its students in the shared group.
        """
        courses = [
            CourseAssignment(
                id=parent_course.id,
                course_id=participant.course_id,
                course_code=participant.course_code,
                lecture_groups=parent_course.lecture_groups,
                lecturers=parent_course.lecturers,
            )
            for participant in ownership
        ]
        student_count = sum(
            (
                math.ceil(
                    participant.expected_students
                    / quota_by_course_id[participant.course_id]
                )
                if quota_by_course_id.get(participant.course_id)
                else participant.expected_students
            )
            for participant in ownership
        )
        return {
            "lists": [p.academic_list.name for p in ownership],
            "list_objects": [p.academic_list for p in ownership],
            "levels": [p.academic_level for p in ownership],
            "codes": [p.course_code for p in ownership],
            "courses": courses,
            "student_count": student_count,
        }

    def _convert_course_assignments_to_blocks(
        self,
        course_assignments: List[CourseAssignment],
        study_plan_mapping: Dict[int, StudyPlan],
        include_labs: bool = True,
    ) -> List[Block]:
        """Convert CourseAssignment objects to Block objects"""
        self.logger.info("=== CONVERTING COURSE ASSIGNMENTS TO BLOCKS ===")
        self.logger.info(f"Course Assignments Count: {len(course_assignments)}")

        blocks = []

        for i, course in enumerate(course_assignments):
            # Get the correct study plan for this course
            study_plan = study_plan_mapping[i]

            # Check if this is a merged common course assignment
            if (
                course.total_students is not None
                and course.total_students > 0
                and course.academic_lists is not None
                and len(course.academic_lists) > 1
                and course.course_codes is not None
                and len(course.course_codes) > 1
                and course.course_ids is not None
                and len(course.course_ids) > 1
            ):
                # This is a merged assignment - group ownership is derived
                # proportionally from each participant's student share (see
                # _merged_group_ownership), and each group's arrays carry
                # only the lists attending it.
                expected_students = course.total_students
                academic_lists = [al.name for al in course.academic_lists]
                academic_list_objects = course.academic_lists
                academic_levels = course.academic_levels  # Already a list
                is_merged = True
                course_codes = course.course_codes
                courses = []
                participants = getattr(course, "participants", None) or []
                self.logger.info(
                    f"Processing merged assignment: {course.course_code} for {len(course.academic_lists)} academic lists: {academic_lists}"
                )
            else:
                # Regular assignment - create lists with single study plan
                expected_students = study_plan.expected_students
                academic_lists = [study_plan.academic_list.name]
                academic_list_objects = [study_plan.academic_list]
                academic_levels = [study_plan.academic_level]  # Single item in list
                is_merged = False
                course_codes = [course.course_code]
                courses = [course]
                self.logger.info(
                    f"Processing regular assignment: {course.course_code} for {study_plan.academic_list.name}"
                )

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
            if is_merged:
                lecture_ownership, lecture_quotas = self._merged_group_ownership(
                    participants, course.lecture_groups
                )

            for lecturer_assignment in course.lecturers:
                try:
                    if "lecturer" not in lecturer_assignment:
                        # The staff fetch failed upstream and left only an id, so
                        # no block can be built. Recorded rather than skipped
                        # silently - otherwise the sessions just go missing while
                        # the run still reports a success rate.
                        self._record_unbuildable(
                            course,
                            "lecture",
                            lecturer_assignment.get("num_of_groups", 1),
                            f"lecturer {lecturer_assignment.get('lecturer_id', 'unknown')} "
                            f"could not be resolved to a staff record",
                        )
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
                        if is_merged:
                            if lecture_group_count > len(lecture_ownership):
                                self._record_unbuildable(
                                    course,
                                    "lecture",
                                    1,
                                    f"merged group {lecture_group_count} has "
                                    f"no owning participant",
                                )
                                continue
                            scope = self._merged_group_scope(
                                lecture_ownership[lecture_group_count - 1],
                                lecture_quotas,
                                course,
                            )
                            group_courses = scope["courses"]
                            group_lists = scope["lists"]
                            group_list_objects = scope["list_objects"]
                            group_levels = scope["levels"]
                            group_codes = scope["codes"]
                            group_students = scope["student_count"]
                        else:
                            group_courses = courses
                            group_lists = academic_lists
                            group_list_objects = academic_list_objects
                            group_levels = academic_levels
                            group_codes = course_codes
                            group_students = math.ceil(
                                expected_students / course.lecture_groups
                            )

                        block_id = self._build_block_id(
                            BlockType.LECTURE,
                            course.course_code,
                            group_courses,
                            group_list_objects,
                            group_levels,
                            lecture_group_count,
                        )

                        lecture_block = Block(
                            id=block_id,
                            course_codes=group_codes,
                            course_objects=group_courses,
                            block_type=BlockType.LECTURE,
                            staff_member=lecturer,
                            student_count=group_students,
                            required_room_type="hall",
                            group_number=lecture_group_count,
                            total_groups=course.lecture_groups,
                            is_single_group_course=course.lecture_groups == 1,
                            academic_lists=group_lists,
                            academic_list_objects=group_list_objects,
                            academic_levels=group_levels,
                            is_merged_common_course=is_merged,
                            merged_course_ids=(
                                list(course.course_ids) if is_merged else None
                            ),
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
            if include_labs and course.lab_groups and course.lab_groups > 0:
                self.logger.info(f"\n  GENERATING LAB BLOCKS for {course.course_code}:")

                if not course.teaching_assistants:
                    self._record_unbuildable(
                        course,
                        "lab",
                        course.lab_groups,
                        "course has lab groups but no teaching assistants assigned",
                    )
                    self.logger.error(
                        f"    ERROR: Course {course.course_code} has {course.lab_groups} lab groups but no teaching assistants!"
                    )
                    continue

                lab_group_count = 1
                lab_blocks_created = 0
                if is_merged:
                    lab_ownership, lab_quotas = self._merged_group_ownership(
                        participants, course.lab_groups
                    )

                for ta_assignment in course.teaching_assistants:
                    try:
                        ta = None
                        if "teaching_assistant" in ta_assignment:
                            ta = ta_assignment["teaching_assistant"]
                        elif "teaching_assistant_id" in ta_assignment:
                            self._record_unbuildable(
                                course,
                                "lab",
                                ta_assignment.get("num_of_groups", 1),
                                f"teaching assistant "
                                f"{ta_assignment.get('teaching_assistant_id', 'unknown')} "
                                f"could not be resolved to a staff record",
                            )
                            self.logger.error(
                                f"      Cannot create lab block without TA object!"
                            )
                            continue

                        if not ta:
                            self._record_unbuildable(
                                course,
                                "lab",
                                ta_assignment.get("num_of_groups", 1),
                                "teaching assistant record was empty",
                            )
                            continue

                        num_groups = ta_assignment["num_of_groups"]

                        for group_idx in range(num_groups):
                            if is_merged:
                                if lab_group_count > len(lab_ownership):
                                    self._record_unbuildable(
                                        course,
                                        "lab",
                                        1,
                                        f"merged group {lab_group_count} has "
                                        f"no owning participant",
                                    )
                                    continue
                                scope = self._merged_group_scope(
                                    lab_ownership[lab_group_count - 1],
                                    lab_quotas,
                                    course,
                                )
                                group_courses = scope["courses"]
                                group_lists = scope["lists"]
                                group_list_objects = scope["list_objects"]
                                group_levels = scope["levels"]
                                group_codes = scope["codes"]
                                group_students = scope["student_count"]
                            else:
                                group_courses = courses
                                group_lists = academic_lists
                                group_list_objects = academic_list_objects
                                group_levels = academic_levels
                                group_codes = course_codes
                                group_students = math.ceil(
                                    expected_students / course.lab_groups
                                )

                            block_id = self._build_block_id(
                                BlockType.LAB,
                                course.course_code,
                                group_courses,
                                group_list_objects,
                                group_levels,
                                lab_group_count,
                            )

                            lab_block = Block(
                                id=block_id,
                                course_codes=group_codes,
                                course_objects=group_courses,
                                block_type=BlockType.LAB,
                                staff_member=ta,
                                student_count=group_students,
                                required_room_type=(
                                    "lab" if course.practical_in_lab else "hall"
                                ),
                                preferred_rooms=course.preferred_labs,
                                group_number=lab_group_count,
                                total_groups=course.lab_groups,
                                is_single_group_course=course.lab_groups == 1,
                                academic_lists=group_lists,
                                academic_list_objects=group_list_objects,
                                academic_levels=group_levels,
                                practical_in_lab=course.practical_in_lab,
                                is_merged_common_course=is_merged,
                                merged_course_ids=(
                                    list(course.course_ids) if is_merged else None
                                ),
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
            f"Block: {block.id} ({block.course_codes[0]} {block.block_type.value})"
        )
        self.logger.debug(
            f"Staff: {block.staff_member.name} (ID: {block.staff_member.id}, Type: {type(block.staff_member.id)})"
        )
        self.logger.debug(f"Room: {room.name} (ID: {room_id}, Type: {type(room_id)})")
        self.logger.debug(f"Time: {slot.day.name} {slot.start_time}")
        self.logger.debug(f"Current assignments: {len(current_assignments)}")

        # Manual conflict checking. Overlap, not start-time equality, so this
        # cross-check agrees with what the constraint manager actually enforces.
        room_conflicts = []
        staff_conflicts = []

        for existing_id, existing_assignment in current_assignments.items():
            existing_slot = existing_assignment.time_slot

            if existing_slot.day != slot.day:
                continue
            if not intervals_overlap(
                slot.start_time,
                slot.end_time,
                existing_slot.start_time,
                existing_slot.end_time,
            ):
                continue

            if get_room_key(existing_assignment.room) == get_room_key(room):
                room_conflicts.append(existing_id)
                self.logger.warning(f"MANUAL CHECK: Room conflict with {existing_id}")

            if existing_assignment.block.staff_member.id == block.staff_member.id:
                staff_conflicts.append(existing_id)
                self.logger.warning(f"MANUAL CHECK: Staff conflict with {existing_id}")

        # Compare with constraint manager result
        is_valid, violation = self.constraint_manager.can_assign(block, slot, room)

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
        """
        Try to swap rooms between two assignments.

        Either both new placements pass every hard constraint or the originals go
        back exactly as they were - a half-applied swap would leave the schedule
        holding a block the constraints never approved.
        """
        new_assignment1 = Assignment(
            assignment1.block, assignment1.time_slot, assignment2.room
        )
        new_assignment2 = Assignment(
            assignment2.block, assignment2.time_slot, assignment1.room
        )

        # Temporarily remove both assignments
        self.constraint_manager.undo_assignment(block_id1)
        self.constraint_manager.undo_assignment(block_id2)

        # make_assignment re-checks the hard constraints, so a rejected swap
        # simply reports False here.
        if self.constraint_manager.make_assignment(
            block_id1, new_assignment1
        ) and self.constraint_manager.make_assignment(block_id2, new_assignment2):
            return True

        # Roll back to the originals.
        self.constraint_manager.undo_assignment(block_id1)
        self.constraint_manager.undo_assignment(block_id2)

        restored1 = self.constraint_manager.make_assignment(block_id1, assignment1)
        restored2 = self.constraint_manager.make_assignment(block_id2, assignment2)
        if not (restored1 and restored2):
            self.logger.error(
                f"Failed to restore {block_id1}/{block_id2} after a rejected room "
                f"swap - schedule state may be missing these blocks"
            )

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

    def _log_failure_summary(self):
        """Log detailed summary of scheduling failures"""
        if self.advisory_warnings:
            self.logger.warning(
                f"\n=== ADVISORY PLACEMENTS ({len(self.advisory_warnings)}) ==="
            )
            for warning in self.advisory_warnings:
                self.logger.warning(f"  - {warning}")

        if not self.failed_blocks:
            self.logger.info("All blocks scheduled successfully!")
            return

        self.logger.error(
            f"\n=== SCHEDULING FAILURES SUMMARY ({len(self.failed_blocks)} blocks) ==="
        )

        # Group by failure reason
        by_reason = {}
        for failure in self.failed_blocks:
            reason = failure.reason
            if reason not in by_reason:
                by_reason[reason] = []
            by_reason[reason].append(failure)

        for reason, failures in by_reason.items():
            self.logger.error(
                f"\n{reason.value.upper().replace('_', ' ')} ({len(failures)} blocks):"
            )
            for failure in failures:
                self.logger.error(f"  - {failure.block_info}: {failure.details}")
