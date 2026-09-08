# utils/common_course_processor.py

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from models.study_plan import CourseAssignment, StudyPlan


class ParentReferenceProcessor:
    """Processor for handling common courses using parent references"""

    def __init__(self):
        self.logger = logging.getLogger("ParentReferenceProcessor")

    def process_common_courses(self, study_plans: List[StudyPlan]) -> List[StudyPlan]:
        """Process study plans to merge common courses using parent references"""

        self.logger.info("=== PROCESSING COMMON COURSES WITH PARENT REFERENCES ===")

        # Step 1: Find all common assignments and group by parent reference
        common_groups = self._group_common_assignments(study_plans)

        if not common_groups:
            self.logger.info("No common courses found, returning original study plans")
            return study_plans

        # Step 2: Merge common assignments into their parents
        merged_assignments = self._merge_common_groups(common_groups, study_plans)

        # Step 3: Create modified study plans with merged assignments
        modified_study_plans = self._create_modified_study_plans(
            study_plans, merged_assignments, common_groups
        )

        return modified_study_plans

    def build_reference_coverage(
        self, study_plans: List[StudyPlan], requested_study_plan_ids: List[int]
    ) -> Dict[str, List[Dict]]:
        """Audit common references before merging or scheduling any blocks."""
        requested_ids = set(requested_study_plan_ids)
        loaded_plan_ids = {study_plan.id for study_plan in study_plans}
        parent_assignments = {
            (study_plan.id, assignment.id)
            for study_plan in study_plans
            for assignment in study_plan.course_assignments
            if not assignment.is_common
        }
        coverage = {
            "invalid_assignment": [],
            "unresolved_common_reference": [],
            "study_plan_not_requested": [],
        }

        for study_plan in study_plans:
            for assignment in study_plan.course_assignments:
                if not assignment.is_common:
                    continue

                context = {
                    "study_plan_id": study_plan.id,
                    "study_plan": study_plan.name,
                    "course": assignment.course_code,
                    "assignment_id": assignment.id,
                    "parent_study_plan_id": assignment.common_study_plan_id,
                    "parent_assignment_id": assignment.common_course_id,
                }

                if not (
                    assignment.common_study_plan_id
                    and assignment.common_course_id
                ):
                    coverage["invalid_assignment"].append(
                        {
                            "code": "INVALID_COMMON_ASSIGNMENT",
                            "message": "Common-course assignment is missing its parent reference",
                            "context": context,
                        }
                    )
                    continue

                parent_key = (
                    assignment.common_study_plan_id,
                    assignment.common_course_id,
                )
                if assignment.common_study_plan_id not in requested_ids:
                    coverage["study_plan_not_requested"].append(
                        {
                            "code": "PARENT_STUDY_PLAN_NOT_REQUESTED",
                            "message": "Common-course parent study plan was not requested",
                            "context": context,
                        }
                    )
                elif assignment.common_study_plan_id not in loaded_plan_ids:
                    coverage["unresolved_common_reference"].append(
                        {
                            "code": "PARENT_STUDY_PLAN_NOT_LOADED",
                            "message": "Common-course parent study plan could not be loaded",
                            "context": context,
                        }
                    )
                elif parent_key not in parent_assignments:
                    coverage["unresolved_common_reference"].append(
                        {
                            "code": "PARENT_ASSIGNMENT_NOT_FOUND",
                            "message": "Common-course parent assignment was not found",
                            "context": context,
                        }
                    )

        return coverage

    def _group_common_assignments(
        self, study_plans: List[StudyPlan]
    ) -> Dict[Tuple[int, int], List[Tuple[StudyPlan, CourseAssignment]]]:
        """Group common assignments by their parent reference"""

        common_groups = defaultdict(list)

        for study_plan in study_plans:
            for assignment in study_plan.course_assignments:
                if (
                    assignment.is_common
                    and assignment.common_study_plan_id
                    and assignment.common_course_id
                ):
                    parent_key = (
                        assignment.common_study_plan_id,
                        assignment.common_course_id,
                    )
                    common_groups[parent_key].append((study_plan, assignment))

                    self.logger.info(
                        f"Found common assignment {assignment.course_code} -> parent {parent_key}"
                    )

        return dict(common_groups)

    def _merge_common_groups(
        self, common_groups: Dict, study_plans: List[StudyPlan]
    ) -> Dict[Tuple[int, int], CourseAssignment]:
        """Merge each group of common assignments with their parent"""

        merged_assignments = {}

        for parent_key, child_assignments in common_groups.items():
            parent_study_plan_id, parent_course_id = parent_key

            # Find the parent assignment
            parent_assignment = self._find_parent_assignment(
                parent_study_plan_id, parent_course_id, study_plans
            )
            parent_study_plan = self._find_study_plan_by_id(
                parent_study_plan_id, study_plans
            )

            if parent_assignment and parent_study_plan:
                # Create merged assignment
                merged_assignment = self._merge_with_parent(
                    parent_study_plan, parent_assignment, child_assignments
                )
                merged_assignments[parent_key] = merged_assignment

                self.logger.info(
                    f"Merged common group {parent_key}: {len(child_assignments)} children"
                )
            else:
                self.logger.error(
                    f"Could not find parent assignment for key {parent_key}"
                )

        return merged_assignments

    def _find_parent_assignment(
        self,
        parent_study_plan_id: int,
        parent_course_id: int,
        study_plans: List[StudyPlan],
    ) -> CourseAssignment:
        """Find the parent assignment by ID"""

        for study_plan in study_plans:
            # Assume study plan ID matches some identifier - you might need to adjust this logic
            study_plan_id = study_plan.id

            if study_plan_id == parent_study_plan_id:
                for assignment in study_plan.course_assignments:
                    assignment_id = assignment.id
                    if assignment_id == parent_course_id and not assignment.is_common:
                        return assignment

        return None

    def _find_study_plan_by_id(
        self, study_plan_id: int, study_plans: List[StudyPlan]
    ) -> StudyPlan:
        """Find study plan by ID"""

        for study_plan in study_plans:
            plan_id = getattr(study_plan, "id", hash(study_plan.name))
            if plan_id == study_plan_id:
                return study_plan

        return None

    def _merge_with_parent(
        self,
        parent_study_plan: StudyPlan,
        parent_assignment: CourseAssignment,
        child_assignments: List[Tuple[StudyPlan, CourseAssignment]],
    ) -> CourseAssignment:
        """Merge child assignments into the parent assignment"""

        # Collect course information from parent and all children
        course_ids = []
        course_codes = []
        academic_lists = []
        academic_levels = []

        # Add parent course information
        parent_course = None
        for course in parent_study_plan.academic_list.courses:
            if course.code == parent_assignment.course_code:
                parent_course = course
                break

        if parent_course:
            course_ids.append(parent_course.id)
            course_codes.append(parent_course.code)
        else:
            course_ids.append(parent_assignment.course_id)
            course_codes.append(parent_assignment.course_code)

        academic_lists.append(parent_study_plan.academic_list)
        academic_levels.append(parent_study_plan.academic_level)

        # Add child course information
        total_students = parent_study_plan.expected_students
        for child_study_plan, child_assignment in child_assignments:
            child_course = None
            for course in child_study_plan.academic_list.courses:
                if course.code == child_assignment.course_code:
                    child_course = course
                    break

            if child_course:
                course_ids.append(child_course.id)
                course_codes.append(child_course.code)
            else:
                course_ids.append(child_assignment.course_id)
                course_codes.append(child_assignment.course_code)

            academic_lists.append(child_study_plan.academic_list)
            academic_levels.append(child_study_plan.academic_level)
            total_students += child_study_plan.expected_students

        # Create merged assignment based on parent structure (original fields unchanged)
        merged_assignment = CourseAssignment(
            id=parent_assignment.id,  # Original field unchanged
            course_id=parent_assignment.course_id,  # Original field unchanged
            course_code=parent_assignment.course_code,  # Original field unchanged
            lecture_groups=parent_assignment.lecture_groups,
            lecturers=parent_assignment.lecturers.copy(),
            lab_groups=parent_assignment.lab_groups,
            teaching_assistants=(
                parent_assignment.teaching_assistants.copy()
                if parent_assignment.teaching_assistants
                else None
            ),
            practical_in_lab=parent_assignment.practical_in_lab,
            preferred_labs=(
                parent_assignment.preferred_labs.copy()
                if parent_assignment.preferred_labs
                else None
            ),
            is_common=False,
            common_study_plan_id=None,
            common_course_id=None,
            course_codes=course_codes,
            course_ids=course_ids,
            total_students=total_students,
            academic_lists=academic_lists,
            academic_levels=academic_levels,
        )

        self.logger.info(
            f"Merged {parent_assignment.course_code}: {len(academic_lists)} academic lists, {total_students} total students"
        )
        self.logger.info(f"Course IDs: {course_ids}")
        self.logger.info(f"Course codes: {course_codes}")

        return merged_assignment

    def _create_modified_study_plans(
        self,
        study_plans: List[StudyPlan],
        merged_assignments: Dict,
        common_groups: Dict,
    ) -> List[StudyPlan]:
        """Create modified study plans with merged assignments and removed child assignments"""

        # Remove children only for groups that actually produced a merged parent.
        # A group whose parent could not be found keeps its children, so an
        # unresolved merge leaves those courses schedulable on their own instead
        # of deleting them from the schedule entirely.
        child_assignments_to_remove = set()
        for parent_key, child_assignments in common_groups.items():
            if parent_key not in merged_assignments:
                self.logger.error(
                    f"Keeping {len(child_assignments)} child assignment(s) for "
                    f"unmerged group {parent_key} - they will be scheduled "
                    f"separately rather than dropped"
                )
                continue
            for study_plan, assignment in child_assignments:
                child_assignments_to_remove.add(id(assignment))

        # Create modified study plans
        modified_study_plans = []

        for study_plan in study_plans:
            study_plan_id = getattr(study_plan, "id", id(study_plan))

            # Remove child assignments
            remaining_assignments = [
                assignment
                for assignment in study_plan.course_assignments
                if id(assignment) not in child_assignments_to_remove
            ]

            # Add merged assignments if this study plan contains parent assignments
            for parent_key, merged_assignment in merged_assignments.items():
                parent_study_plan_id, parent_course_id = parent_key
                if study_plan_id == parent_study_plan_id:
                    # Replace the original parent with the merged version
                    remaining_assignments = [
                        (
                            merged_assignment
                            if (a.id == parent_course_id and not a.is_common)
                            else a
                        )
                        for a in remaining_assignments
                    ]
            # Kept even when every course merged into another plan's parent: the
            # caller asked for this study plan, and dropping it here would remove
            # the cohort from the results and the posted metadata without a word.
            modified_study_plan = StudyPlan(
                id=study_plan.id,
                name=study_plan.name,
                academic_list=study_plan.academic_list,
                academic_level=study_plan.academic_level,
                expected_students=study_plan.expected_students,
                course_assignments=remaining_assignments,
            )
            modified_study_plans.append(modified_study_plan)

            if not remaining_assignments:
                self.logger.info(
                    f"{study_plan.academic_list.name} L{study_plan.academic_level}: "
                    f"all courses merged into other plans; kept with no own blocks"
                )

            self.logger.info(
                f"Modified {study_plan.academic_list.name}: {len(study_plan.course_assignments)} -> {len(remaining_assignments)} assignments"
            )

        return modified_study_plans
