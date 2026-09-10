# tests/test_sections.py
"""Section-model clash rules.

The confirmed requirement: within a cohort, group k of every course is the
same student section. Section 1 of IT202 and section 2 of BS215 are
disjoint students and may run in parallel (cross-course rotation); section
1 of both is the same students and must never overlap. Single-group
courses put their whole cohort in one room. A merged common-course group k
is section k of exactly the lists carried on the block - the builder
partitions the merged groups proportionally across the participants'
students, so the lists on a block are precisely those with students in
that group. strict_cohort_mode restores the historical conservative rule.
"""

import unittest
from datetime import time

from managers.constraint_manager import ConstraintManager
from models.academic_list import AcademicList, Course
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from schedule_validator import ScheduleValidator
from utils.sections import (
    blocks_clash,
    clash_reason,
    is_whole_cohort_session,
)

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")

WINDOWS = [
    TimePreference(day=day, start_time=time(9, 0), end_time=time(17, 0))
    for day in (Day.SUNDAY, Day.MONDAY)
]


def staff(staff_id, is_lecturer=False):
    if is_lecturer:
        return Lecturer(staff_id, f"Staff {staff_id}", DEPARTMENT, [], LECTURER_DEGREE, True)
    return TeachingAssistant(staff_id, f"Staff {staff_id}", DEPARTMENT, [], TA_DEGREE, False)


def make_block(block_id, course_code, course_id, block_type, staff_member,
               group_number, total_groups=5, merged=False):
    single = total_groups == 1
    if block_type is BlockType.LECTURE:
        assignment = CourseAssignment(
            id=1, course_id=course_id, course_code=course_code,
            lecture_groups=total_groups,
            lecturers=[{"lecturer": staff_member, "num_of_groups": total_groups}],
        )
        room_type = "hall"
    else:
        fallback_lecturer = staff(999, True)
        assignment = CourseAssignment(
            id=1, course_id=course_id, course_code=course_code,
            lecture_groups=1,
            lecturers=[{"lecturer": fallback_lecturer, "num_of_groups": 1}],
            lab_groups=total_groups,
            teaching_assistants=[
                {"teaching_assistant": staff_member, "num_of_groups": total_groups}
            ],
        )
        room_type = "lab"
    if merged:
        assignment.course_ids = [course_id, course_id + 100]
        assignment.course_codes = [course_code, f"{course_code}X"]

    return Block(
        id=block_id,
        course_codes=[course_code],
        course_objects=[assignment],
        block_type=block_type,
        staff_member=staff_member,
        student_count=25,
        required_room_type=room_type,
        group_number=group_number,
        total_groups=total_groups,
        is_single_group_course=single,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(1, "AHLIA", DEPARTMENT,
                         [Course(1, course_code, course_code, course_code, 3, 2, 4)])
        ],
        academic_levels=[2],
        is_merged_common_course=merged,
    )


def lab_block(block_id, course_code, course_id, staff_member, group_number, merged=False):
    return make_block(block_id, course_code, course_id, BlockType.LAB, staff_member,
                      group_number, merged=merged)


def make_merged_block(block_id, block_type, staff_member, group_number,
                      attendees, total_groups=14, course_code="CC101",
                      course_key=(500, 600)):
    """
    A merged common-course group attending exactly `attendees`.

    attendees: [(list_name, list_id, level, course_id), ...] - the per-group
    SUBSET the block builder produces (the lists whose own declared group
    count covers group_number). A list absent from the block has no students
    in this group; merged_course_ids carries the canonical participation set.
    """
    if block_type is BlockType.LECTURE:
        room_type = "hall"
        assignments = [
            CourseAssignment(
                id=1, course_id=course_id, course_code=f"{course_code}{course_id}",
                lecture_groups=total_groups,
                lecturers=[
                    {"lecturer": staff_member, "num_of_groups": total_groups}
                ],
            )
            for (_, _, _, course_id) in attendees
        ]
    else:
        room_type = "lab"
        assignments = [
            CourseAssignment(
                id=1, course_id=course_id, course_code=f"{course_code}{course_id}",
                lecture_groups=1,
                lecturers=[{"lecturer": staff(999, True), "num_of_groups": 1}],
                lab_groups=total_groups,
                teaching_assistants=[
                    {"teaching_assistant": staff_member, "num_of_groups": total_groups}
                ],
            )
            for (_, _, _, course_id) in attendees
        ]
    return Block(
        id=block_id,
        course_codes=[f"{course_code}{course_id}" for (_, _, _, course_id) in attendees],
        course_objects=assignments,
        block_type=block_type,
        staff_member=staff_member,
        student_count=25,
        required_room_type=room_type,
        group_number=group_number,
        total_groups=total_groups,
        is_single_group_course=total_groups == 1,
        academic_lists=[name for (name, _, _, _) in attendees],
        academic_list_objects=[
            AcademicList(
                list_id, name, DEPARTMENT,
                [Course(
                    course_id, f"{course_code}{course_id}",
                    f"{course_code}{course_id}", f"{course_code}{course_id}", 3, 2, 4,
                )],
            )
            for (name, list_id, _, course_id) in attendees
        ],
        academic_levels=[level for (_, _, level, _) in attendees],
        is_merged_common_course=True,
        merged_course_ids=list(course_key),
    )


def lecture_block(block_id, course_code, course_id, group_number, total_groups=1,
                  merged=False):
    return make_block(block_id, course_code, course_id, BlockType.LECTURE,
                      staff(700 + course_id, True), group_number, total_groups, merged)


SUNDAY_9 = TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(11, 0))


def make_rooms():
    return (
        [Hall(1, "H1", 120, WINDOWS)],
        [Lab(1, "Lab1", 30, WINDOWS, LabType.GENERAL),
         Lab(2, "Lab2", 30, WINDOWS, LabType.GENERAL)],
    )


class SectionRuleTests(unittest.TestCase):
    def test_rotation_different_sections_of_different_courses_may_overlap(self):
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        bs215_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        self.assertFalse(blocks_clash(it202_g1, bs215_g2))

    def test_same_section_of_different_courses_clash(self):
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        bs215_g1 = lab_block("bs1", "BS215", 2, staff(2), 1)
        self.assertTrue(blocks_clash(it202_g1, bs215_g1))
        self.assertIn("same student section", clash_reason(it202_g1, bs215_g1))

    def test_lecture_group_and_lab_group_are_sections_too(self):
        # A two-group lecture's group 1 is section 1 only; lab group 2 of
        # another course is section 2 - disjoint students.
        lect1 = lecture_block("l1", "IT101", 3, 1, total_groups=2)
        bs_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        self.assertFalse(blocks_clash(lect1, bs_g2))
        # Lecture and lab of the SAME course with the same group clash.
        same_lect1 = lecture_block("l1", "IT202", 1, 1, total_groups=2)
        it202_lab1 = lab_block("it1", "IT202", 1, staff(1), 1)
        self.assertTrue(blocks_clash(same_lect1, it202_lab1))

    def test_parallel_groups_of_same_course_may_overlap(self):
        g1 = lab_block("g1", "IT202", 1, staff(1), 1)
        g2 = lab_block("g2", "IT202", 1, staff(2), 2)
        self.assertFalse(blocks_clash(g1, g2))
        self.assertTrue(blocks_clash(g1, lab_block("g1b", "IT202", 1, staff(3), 1)))

    def test_single_group_course_attends_whole_cohort(self):
        single = lecture_block("s1", "IT401", 4, 1)  # whole cohort
        other_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        self.assertTrue(blocks_clash(single, other_g2))

    def test_merged_group_attends_sections_of_its_lists(self):
        # Merged group 1 attends AHLIA + OTHER; group 1 of a plain AHLIA
        # course is the same student section -> clash. Group 2 rotates.
        merged_g1 = make_merged_block(
            "m1", BlockType.LAB, staff(5), 1,
            [("AHLIA", 1, 2, 11), ("OTHER", 2, 2, 21)],
        )
        other_g1 = lab_block("bs1", "BS215", 2, staff(2), 1)
        other_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        self.assertTrue(blocks_clash(merged_g1, other_g1))
        self.assertIn("same student section", clash_reason(merged_g1, other_g1))
        self.assertFalse(blocks_clash(merged_g1, other_g2))
        # Its own parallel groups still partition the merged cohort.
        merged_g2 = make_merged_block(
            "m2", BlockType.LAB, staff(6), 2,
            [("AHLIA", 1, 2, 11), ("OTHER", 2, 2, 21)],
        )
        self.assertFalse(blocks_clash(merged_g1, merged_g2))

    def test_merged_group_beyond_a_list_sections_is_isolated(self):
        # Merged group 14 has only AHLIA students (OTHER declares fewer
        # sections than 14): it must NOT block OTHER's single-group course,
        # but it still blocks every AHLIA session.
        merged_g14 = make_merged_block(
            "m14", BlockType.LAB, staff(7), 14,
            [("AHLIA", 1, 2, 11)], total_groups=14,
        )
        ahlia_single = lecture_block("sa", "IT403", 9, 1)
        self.assertTrue(blocks_clash(merged_g14, ahlia_single))

        other_single = lecture_block("so", "IT402", 8, 1)
        other_single.academic_lists = ["OTHER"]
        self.assertFalse(blocks_clash(merged_g14, other_single))

    def test_is_whole_cohort_session_classifier(self):
        single = lecture_block("s1", "IT401", 4, 1)
        self.assertTrue(is_whole_cohort_session(single))

        plain_group = lab_block("it1", "IT202", 1, staff(1), 1)
        self.assertFalse(is_whole_cohort_session(plain_group))

        merged_multi = make_merged_block(
            "m1", BlockType.LAB, staff(5), 1,
            [("AHLIA", 1, 2, 11), ("OTHER", 2, 2, 21)],
        )
        self.assertFalse(is_whole_cohort_session(merged_multi))
        # Strict mode keeps merged groups whole-cohort (they serialize again).
        self.assertTrue(
            is_whole_cohort_session(merged_multi, strict_cohort_mode=True)
        )

    def test_display_label_resolves_course_name(self):
        # A bare code is not enough to act on: the label resolves the course
        # name from the attending lists' catalogues when it differs.
        block = lab_block("it1", "IT202", 1, staff(1), 1)
        catalog_course = Course(
            77, "IT202", "Introduction to Programming",
            "مقدمة في البرمجة", 3, 2, 4,
        )
        block.academic_list_objects = [
            AcademicList(1, "AHLIA", DEPARTMENT, [catalog_course])
        ]
        self.assertEqual(
            block.display_label(), "IT202 — Introduction to Programming"
        )
        # When the catalogue name equals the code, no suffix is added.
        block.academic_list_objects = [
            AcademicList(1, "AHLIA", DEPARTMENT,
                         [Course(1, "IT202", "IT202", "IT202", 3, 2, 4)])
        ]
        self.assertEqual(block.display_label(), "IT202")

    def test_different_cohorts_never_clash(self):
        a = lab_block("a", "IT202", 1, staff(1), 1)
        b = lab_block("b", "IT202", 1, staff(2), 1)
        b.academic_lists = ["OTHER"]
        self.assertFalse(blocks_clash(a, b))

    def test_strict_mode_restores_old_rule(self):
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        bs215_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        self.assertTrue(blocks_clash(it202_g1, bs215_g2, strict_cohort_mode=True))
        g2 = lab_block("g2", "IT202", 1, staff(2), 2)
        self.assertFalse(blocks_clash(it202_g1, g2, strict_cohort_mode=True))


class ConstraintManagerSectionTests(unittest.TestCase):
    def seed_it202_g1(self, cm, labs):
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        self.assertTrue(
            cm.make_assignment("it1", Assignment(it202_g1, SUNDAY_9, labs[0]))
        )
        return it202_g1

    def test_rotation_pair_passes_hard_constraints(self):
        halls, labs = make_rooms()
        cm = ConstraintManager()
        self.seed_it202_g1(cm, labs)

        bs215_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        is_valid, _ = cm.can_assign(bs215_g2, SUNDAY_9, labs[1])
        self.assertTrue(is_valid)

        bs215_g1 = lab_block("bs1", "BS215", 2, staff(3), 1)
        is_valid, reason = cm.can_assign(bs215_g1, SUNDAY_9, labs[1])
        self.assertFalse(is_valid)

    def test_strict_manager_forbids_rotation_pair(self):
        halls, labs = make_rooms()
        cm = ConstraintManager(strict_cohort_mode=True)
        self.seed_it202_g1(cm, labs)

        bs215_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        is_valid, _ = cm.can_assign(bs215_g2, SUNDAY_9, labs[1])
        self.assertFalse(is_valid)

    def test_cm_merged_beyond_section_rotates_with_other_list(self):
        # Merged group 14 (only AHLIA students) may sit beside a session of
        # another list in the same period, never beside an AHLIA session.
        halls, labs = make_rooms()
        cm = ConstraintManager()
        merged_g14 = make_merged_block(
            "m14", BlockType.LAB, staff(7), 14,
            [("AHLIA", 1, 2, 11)], total_groups=14,
        )
        self.assertTrue(
            cm.make_assignment("m14", Assignment(merged_g14, SUNDAY_9, labs[0]))
        )

        other_single = lecture_block("so", "IT402", 8, 1)
        other_single.academic_lists = ["OTHER"]
        is_valid, _ = cm.can_assign(other_single, SUNDAY_9, halls[0])
        self.assertTrue(is_valid)

        ahlia_single = lecture_block("sa", "IT403", 9, 1)
        is_valid, _ = cm.can_assign(ahlia_single, SUNDAY_9, halls[0])
        self.assertFalse(is_valid)


class ValidatorSectionTests(unittest.TestCase):
    def assignments_pair(self, block_a, block_b):
        halls, labs = make_rooms()
        return {
            "a": Assignment(block_a, SUNDAY_9, labs[0]),
            "b": Assignment(block_b, SUNDAY_9, labs[1]),
        }

    def test_validator_accepts_rotation_pair(self):
        validator = ScheduleValidator()
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        bs215_g2 = lab_block("bs2", "BS215", 2, staff(2), 2)
        _, conflicts = validator.validate_schedule_comprehensive(
            self.assignments_pair(it202_g1, bs215_g2)
        )
        student_conflicts = [c for c in conflicts if c.conflict_type == "STUDENT_CONFLICT"]
        self.assertEqual(student_conflicts, [])

    def test_validator_reports_same_section_clash(self):
        validator = ScheduleValidator()
        it202_g1 = lab_block("it1", "IT202", 1, staff(1), 1)
        bs215_g1 = lab_block("bs1", "BS215", 2, staff(2), 1)
        _, conflicts = validator.validate_schedule_comprehensive(
            self.assignments_pair(it202_g1, bs215_g1)
        )
        student_conflicts = [c for c in conflicts if c.conflict_type == "STUDENT_CONFLICT"]
        self.assertEqual(len(student_conflicts), 1)
        self.assertIn("same student section", student_conflicts[0].description)


if __name__ == "__main__":
    unittest.main()
