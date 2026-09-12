# tests/test_common_course_merge.py
"""The merge pipeline's per-participant record and the per-group block subsets.

_merge_with_parent keeps the parent's groups and staffing but must now also
record each participating list's own declaration (CourseParticipant), and
build_blocks must subset every merged group's arrays to the lists whose
declared group count covers the group number - merged group k is section k
of exactly those lists.
"""

import math
import unittest

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList, Course
from models.department import Department
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment, CourseParticipant, StudyPlan
from scheduler import SchedulingEngine
from utils.common_course_processor import ParentReferenceProcessor

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")


def make_lecturer(staff_id):
    return Lecturer(
        staff_id, f"Lecturer {staff_id}", DEPARTMENT, [], LECTURER_DEGREE, True
    )


def make_ta(staff_id):
    return TeachingAssistant(
        staff_id, f"TA {staff_id}", DEPARTMENT, [], TA_DEGREE, False
    )


def make_course(code, course_id):
    return Course(course_id, code, code, code, 3, 2, 4)


def build_plans(child_lab_groups=3):
    """Parent (30 students, 17 lab groups) + child (40 students, 3)."""
    parent_list = AcademicList(
        1, "ParentList", DEPARTMENT, [make_course("CS101", 101)]
    )
    parent_assignment = CourseAssignment(
        id=11, course_id=101, course_code="CS101",
        lecture_groups=2,
        lecturers=[{"lecturer": make_lecturer(1), "num_of_groups": 2}],
        lab_groups=17,
        teaching_assistants=[
            {"teaching_assistant": make_ta(2), "num_of_groups": 9},
            {"teaching_assistant": make_ta(3), "num_of_groups": 8},
        ],
    )
    parent = StudyPlan(50, "Parent Plan", parent_list, 2, 30, [parent_assignment])

    child_list = AcademicList(
        2, "ChildList", DEPARTMENT, [make_course("CS101Z", 202)]
    )
    child_assignment = CourseAssignment(
        id=99, course_id=202, course_code="CS101Z",
        lecture_groups=1,
        lecturers=[{"lecturer": make_lecturer(5), "num_of_groups": 1}],
        lab_groups=child_lab_groups,
        teaching_assistants=[
            {"teaching_assistant": make_ta(6), "num_of_groups": child_lab_groups}
        ],
        is_common=True,
        common_study_plan_id=50,
        common_course_id=11,
    )
    child = StudyPlan(51, "Child Plan", child_list, 2, 40, [child_assignment])
    return parent, parent_assignment, child, child_assignment


class MergeParticipantsTests(unittest.TestCase):
    def test_merge_records_participants_in_parent_child_order(self):
        parent, parent_assignment, child, child_assignment = build_plans()
        processor = ParentReferenceProcessor()

        merged = processor._merge_with_parent(
            parent, parent_assignment, [(child, child_assignment)]
        )

        self.assertEqual(len(merged.participants), 2)
        self.assertIsInstance(merged.participants[0], CourseParticipant)
        self.assertEqual(
            [p.academic_list.name for p in merged.participants],
            ["ParentList", "ChildList"],
        )
        self.assertEqual([p.course_id for p in merged.participants], [101, 202])
        self.assertEqual(
            [p.expected_students for p in merged.participants], [30, 40]
        )
        self.assertEqual([p.lecture_groups for p in merged.participants], [2, 1])
        self.assertEqual([p.lab_groups for p in merged.participants], [17, 3])
        # Parent-authoritative merge is unchanged: parent groups and staffing,
        # students summed, lists combined in [parent, children...] order.
        self.assertEqual(merged.lab_groups, 17)
        self.assertEqual(
            merged.teaching_assistants, parent_assignment.teaching_assistants
        )
        self.assertEqual(merged.total_students, 70)
        self.assertEqual(
            merged.academic_lists, [parent.academic_list, child.academic_list]
        )
        self.assertEqual(merged.academic_levels, [2, 2])


class MergedBlockBuildTests(unittest.TestCase):
    def build_engine(self):
        return SchedulingEngine(
            ConstraintManager(),
            ResourceManager(halls=[], labs=[], reserved_period=None),
            use_cpsat=False,
        )

    def merged_assignment_and_mapping(self, child_lab_groups=3):
        parent, _, child, _ = build_plans(child_lab_groups)
        plans = ParentReferenceProcessor().process_common_courses([parent, child])
        merged_assignment = plans[0].course_assignments[0]
        mapping = {0: plans[0]}
        return merged_assignment, mapping

    def test_blocks_carry_per_group_attendee_subsets(self):
        merged_assignment, mapping = self.merged_assignment_and_mapping()
        engine = self.build_engine()

        blocks = engine.build_blocks([merged_assignment], mapping, include_labs=True)
        self.assertEqual(len(blocks), 2 + 17)  # 2 lectures + 17 labs

        by_kind = {}
        for block in blocks:
            by_kind.setdefault(block.block_type.value, []).append(block)

        # Lectures (2 groups over 30+40=70 students): quotas 1+1 - the
        # parent's slightly-larger share takes the leftover. Each group is
        # owned by exactly one list.
        lectures = sorted(by_kind["lecture"], key=lambda b: b.group_number)
        self.assertEqual(lectures[0].academic_lists, ["ParentList"])
        self.assertEqual(lectures[0].student_count, 30)
        self.assertEqual(lectures[1].academic_lists, ["ChildList"])
        self.assertEqual(lectures[1].student_count, 40)

        # Labs (17 groups over 70 students): largest-remainder quotas give
        # the parent 7 groups (share 7.29) and the child 10 (share 9.71).
        labs = sorted(by_kind["lab"], key=lambda b: b.group_number)
        group1 = labs[0]
        self.assertEqual(group1.academic_lists, ["ParentList"])
        self.assertEqual(group1.student_count, math.ceil(30 / 7))  # 5
        group7 = labs[6]
        self.assertEqual(group7.academic_lists, ["ParentList"])
        self.assertEqual(group7.student_count, math.ceil(30 / 7))
        group8 = labs[7]
        self.assertEqual(group8.academic_lists, ["ChildList"])
        self.assertEqual(group8.student_count, math.ceil(40 / 10))  # 4
        group17 = labs[16]
        self.assertEqual(group17.group_number, 17)
        self.assertEqual(group17.academic_lists, ["ChildList"])

        for block in blocks:
            arrays = (
                len(block.academic_lists),
                len(block.academic_levels),
                len(block.academic_list_objects),
                len(block.course_codes),
                len(block.course_objects),
            )
            self.assertEqual(len(set(arrays)), 1, block.id)
            self.assertTrue(block.is_merged_common_course)
            self.assertEqual(block.merged_course_ids, [101, 202])

    def tiny_plan(self, tiny_id, course_id, students):
        tiny_list = AcademicList(
            tiny_id, f"TinyList{tiny_id}", DEPARTMENT, [make_course("CS101T", course_id)]
        )
        tiny_assignment = CourseAssignment(
            id=98, course_id=course_id, course_code="CS101T",
            lecture_groups=1,
            lecturers=[{"lecturer": make_lecturer(7), "num_of_groups": 1}],
            lab_groups=0,
            is_common=True,
            common_study_plan_id=50,
            common_course_id=11,
        )
        return StudyPlan(
            52, f"Tiny Plan {tiny_id}", tiny_list, 2, students, [tiny_assignment]
        )

    def merged_lab_blocks(self, plans):
        processed = ParentReferenceProcessor().process_common_courses(plans)
        merged_assignment = processed[0].course_assignments[0]
        engine = self.build_engine()
        blocks = engine.build_blocks(
            [merged_assignment], {0: processed[0]}, include_labs=True
        )
        return sorted(
            (b for b in blocks if b.block_type.value == "lab"),
            key=lambda b: b.group_number,
        )

    def test_small_list_wins_its_own_group_by_largest_remainder(self):
        # 17 lab groups over 72 students: parent 30 -> 7.08 (floor 7),
        # child 40 -> 9.44 (floor 9), tiny 2 -> 0.47 (floor 0). One group
        # remains and the tiny list holds the largest fraction, so it gets
        # its own group.
        parent, _, child, _ = build_plans()
        tiny = self.tiny_plan(3, 203, 2)

        lab_blocks = self.merged_lab_blocks([parent, child, tiny])
        self.assertEqual(len(lab_blocks), 17)
        self.assertEqual(lab_blocks[0].academic_lists, ["ParentList"])
        self.assertEqual(lab_blocks[6].academic_lists, ["ParentList"])
        self.assertEqual(lab_blocks[7].academic_lists, ["ChildList"])
        self.assertEqual(lab_blocks[16].academic_lists, ["TinyList3"])
        self.assertEqual(lab_blocks[16].student_count, 2)

    def test_zero_share_list_folds_into_a_neighbours_group(self):
        # With only 1 student the tiny list's fraction (0.24) loses the
        # remainder round to the child (0.58), so it is folded into the
        # child's last group - that entry then shows a subset of two lists.
        parent, _, child, _ = build_plans()
        tiny = self.tiny_plan(3, 203, 1)

        lab_blocks = self.merged_lab_blocks([parent, child, tiny])
        self.assertEqual(len(lab_blocks), 17)
        self.assertEqual(lab_blocks[7].academic_lists, ["ChildList"])
        self.assertEqual(lab_blocks[16].academic_lists, ["ChildList", "TinyList3"])
        self.assertEqual(lab_blocks[16].student_count, math.ceil(40 / 10) + 1)

    def test_leading_zero_share_parent_folds_into_the_only_group(self):
        # The production drop (IT L4 IS429/CS436, 2026-09-12): a 1-group
        # merge whose parent's student share floors to zero groups. The
        # parent is a LEADING zero-quota participant and must fold into the
        # child's window instead of vanishing from the schedule.
        parent_list = AcademicList(
            1, "ParentList", DEPARTMENT, [make_course("IS429", 101)]
        )
        parent_assignment = CourseAssignment(
            id=11, course_id=101, course_code="IS429",
            lecture_groups=1,
            lecturers=[{"lecturer": make_lecturer(1), "num_of_groups": 1}],
            lab_groups=0,
        )
        parent = StudyPlan(50, "Parent Plan", parent_list, 4, 30, [parent_assignment])

        child_list = AcademicList(
            2, "ChildList", DEPARTMENT, [make_course("IS429", 202)]
        )
        child_assignment = CourseAssignment(
            id=99, course_id=202, course_code="IS429",
            lecture_groups=1,
            lecturers=[{"lecturer": make_lecturer(5), "num_of_groups": 1}],
            lab_groups=0,
            is_common=True,
            common_study_plan_id=50,
            common_course_id=11,
        )
        child = StudyPlan(51, "Child Plan", child_list, 4, 70, [child_assignment])

        processed = ParentReferenceProcessor().process_common_courses([parent, child])
        merged_assignment = processed[0].course_assignments[0]
        blocks = self.build_engine().build_blocks(
            [merged_assignment], {0: processed[0]}, include_labs=True
        )

        lectures = [b for b in blocks if b.block_type.value == "lecture"]
        self.assertEqual(len(lectures), 1)
        # Window owner first, folded participant after - same shape as the
        # trailing fold.
        self.assertEqual(lectures[0].academic_lists, ["ChildList", "ParentList"])
        self.assertEqual(lectures[0].student_count, 100)
        self.assertEqual(lectures[0].course_codes, ["IS429", "IS429"])

    def test_block_ids_are_distinct_and_deterministic(self):
        merged_assignment, mapping = self.merged_assignment_and_mapping()
        engine = self.build_engine()

        first = engine.build_blocks([merged_assignment], mapping, include_labs=True)
        second = engine.build_blocks([merged_assignment], mapping, include_labs=True)

        self.assertEqual(len({b.id for b in first}), len(first))
        self.assertEqual([b.id for b in first], [b.id for b in second])


class MergedGroupOwnershipTests(unittest.TestCase):
    """Direct coverage for the quota -> window partition invariants."""

    @staticmethod
    def participant(index, students):
        return CourseParticipant(
            academic_list=AcademicList(
                index, f"List{index}", DEPARTMENT, [make_course(f"CS{index}", index)]
            ),
            academic_level=2,
            course_id=100 + index,
            course_code=f"C{index}",
            expected_students=students,
            lecture_groups=3,
            lab_groups=0,
        )

    def test_mid_sequence_zero_share_folds_into_next_owned_window(self):
        # List2 is too small for its own window and sits BETWEEN two owners,
        # so it must fold into List3's first window (g3) - not vanish from
        # every group.
        participants = [
            self.participant(1, 100),
            self.participant(2, 1),
            self.participant(3, 100),
        ]

        ownership, quota_by_course_id = SchedulingEngine._merged_group_ownership(
            participants, 3
        )

        self.assertEqual(len(ownership), 3)
        self.assertEqual([p.course_id for p in ownership[0]], [101])
        self.assertEqual([p.course_id for p in ownership[1]], [101])
        self.assertEqual([p.course_id for p in ownership[2]], [103, 102])
        self.assertEqual(quota_by_course_id[102], 0)


if __name__ == "__main__":
    unittest.main()
