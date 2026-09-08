# tests/test_feasibility.py
"""Offline preflight feasibility checks.

The plan-116-style fixture mirrors the audited failure: five lab courses
with five groups each, three of them with every group on a single TA, plus
five lectures - 26 required periods against a 19-period grid (five days,
09:00-17:00, two rooms, Tuesday 13:00-15:00 reserved). The preflight must
refuse that input with a DATA blocker before any search runs, and must
pass a redistributed variant.
"""

import unittest
from datetime import time

from models.academic_list import AcademicList, Course
from models.block import Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import (
    BaseAvailability,
    Day,
    ReservedPeriod,
    TimePreference,
)
from utils.feasibility import assess_feasibility
from utils.insights import InsightSeverity

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")
FULL_DAY_WINDOWS = BaseAvailability.generate_base_availability()


def make_windows(days, start=time(9, 0), end=time(17, 0)):
    return [TimePreference(day=day, start_time=start, end_time=end) for day in days]


def make_staff(staff_id, name, is_lecturer=False, windows=None):
    if windows is None:
        windows = FULL_DAY_WINDOWS
    if is_lecturer:
        return Lecturer(
            staff_id, name, DEPARTMENT, windows, LECTURER_DEGREE, True
        )
    return TeachingAssistant(staff_id, name, DEPARTMENT, windows, TA_DEGREE, False)


def make_course_assignment(course_code, lecture_groups=1, lab_groups=0, lecturers=None,
                           teaching_assistants=None):
    default_lecturers = [
        {"lecturer": make_staff(900, f"Prof {course_code}", True),
         "num_of_groups": lecture_groups}
    ]
    return CourseAssignment(
        id=1,
        course_id=1,
        course_code=course_code,
        lecture_groups=lecture_groups,
        lecturers=lecturers or default_lecturers,
        lab_groups=lab_groups,
        teaching_assistants=teaching_assistants,
    )


def make_block(course_code, block_type, staff, group_number, total_groups,
               student_count=25, preferred_rooms=None):
    if block_type is BlockType.LECTURE:
        assignment = make_course_assignment(
            course_code, lecture_groups=total_groups, lecturers=[
                {"lecturer": staff, "num_of_groups": total_groups}
            ]
        )
        room_type = "hall"
    else:
        assignment = make_course_assignment(
            course_code,
            lab_groups=total_groups,
            teaching_assistants=[{"teaching_assistant": staff, "num_of_groups": total_groups}],
        )
        room_type = "lab"

    return Block(
        id=f"P_{course_code}_{block_type.value}_{group_number}",
        course_codes=[course_code],
        course_objects=[assignment],
        block_type=block_type,
        staff_member=staff,
        student_count=student_count,
        required_room_type=room_type,
        group_number=group_number,
        total_groups=total_groups,
        is_single_group_course=total_groups == 1,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(
                1,
                "AHLIA",
                DEPARTMENT,
                [Course(1, course_code, course_code, course_code, 3, 2, 4)],
            )
        ],
        academic_levels=[2],
        preferred_rooms=preferred_rooms,
    )


def make_lab_course_blocks(course_code, ta_groups, start_id=100):
    """One lab course: ta_groups is a list of (ta_id_suffix, group_count)."""
    blocks = []
    group = 1
    for index, (ta_name, count) in enumerate(ta_groups):
        ta = make_staff(start_id + index, f"TA {ta_name} ({course_code})")
        for _ in range(count):
            blocks.append(make_block(course_code, BlockType.LAB, ta, group, 5))
            group += 1
    return blocks


def plan116_like_blocks():
    blocks = []
    # Three courses with all five groups on one TA - the audited bottleneck.
    constrained = {"BS215": 200, "IT226": 210, "CS205": 220}
    for course_code, base_id in constrained.items():
        ta = make_staff(base_id, f"TA {course_code}")
        blocks.extend(
            make_block(course_code, BlockType.LAB, ta, group, 5)
            for group in range(1, 6)
        )
    blocks.extend(make_lab_course_blocks("IT202", [("A", 2), ("B", 3)], 230))
    blocks.extend(make_lab_course_blocks("IT205", [("C", 3), ("D", 2)], 240))
    for index in range(5):
        lecturer = make_staff(300 + index, f"Lecturer {index}", True)
        blocks.append(make_block(f"LECT{index}", BlockType.LECTURE, lecturer, 1, 1))
    return blocks


def make_rooms():
    # Aligned 09:00-17:00 calendars, like the audited production rooms:
    # four 2-hour periods per day, five days, minus the reserved period.
    day_windows = make_windows(
        [Day.SUNDAY, Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY, Day.THURSDAY]
    )
    labs = [
        Lab(index, f"Lab {index}", 30, day_windows, LabType.GENERAL)
        for index in (1, 2)
    ]
    halls = [Hall(index, f"Hall {index}", 120, day_windows) for index in (1, 2)]
    return labs, halls


RESERVED = ReservedPeriod(Day.TUESDAY, time(13, 0), time(15, 0))


class TestFeasibilityPreflight(unittest.TestCase):
    def test_strict_mode_blocks_infeasible_cohort_with_numbers(self):
        # Under the historical conservative rule the audited bound applies:
        # 21 lab periods (three courses stuck on one TA) + 5 lectures = 26.
        labs, halls = make_rooms()
        report = assess_feasibility(
            plan116_like_blocks(), halls, labs, RESERVED, strict_cohort_mode=True
        )

        blockers = report.blockers
        self.assertEqual(len(blockers), 1)
        blocker = blockers[0]
        self.assertEqual(blocker.code, "COHORT_PERIOD_BUDGET_EXCEEDED")
        self.assertEqual(
            blocker.evidence["required_periods_cohort_atomic"], 26
        )
        # 5 days x 4 periods, minus the reserved Tuesday 13:00-15:00.
        self.assertEqual(blocker.evidence["available_periods_union"], 19)
        offender_codes = [e["name"] for e in blocker.entities if e["kind"] == "course"]
        for expected in ("BS215", "IT226", "CS205"):
            self.assertIn(expected, offender_codes)
        self.assertIn("26", blocker.verdict)
        self.assertIn("19", blocker.verdict)

    def test_sections_mode_does_not_block_the_same_input(self):
        # With the section model the same data is feasible: 5 whole-cohort
        # lectures + max(staff bottleneck 5, ceil(25 labs / 2 labs) = 13) = 18.
        labs, halls = make_rooms()
        report = assess_feasibility(plan116_like_blocks(), halls, labs, RESERVED)

        self.assertEqual(report.blockers, [])
        cohort = report.summary["cohorts"][0]
        self.assertEqual(cohort["whole_cohort_sessions"], 5)
        self.assertEqual(cohort["required_periods_sections_bound"], 18)
        self.assertEqual(cohort["required_periods_cohort_atomic"], 26)

    def test_redistributed_staff_has_no_blocker(self):
        labs, halls = make_rooms()
        blocks = []
        for index, course_code in enumerate(
            ("BS215", "IT226", "CS205", "IT202", "IT205")
        ):
            blocks.extend(
                make_lab_course_blocks(
                    course_code,
                    [(str(ta), 1) for ta in range(5)],
                    400 + index * 10,
                )
            )
        for index in range(5):
            lecturer = make_staff(500 + index, f"Lecturer {index}", True)
            blocks.append(make_block(f"LECT{index}", BlockType.LECTURE, lecturer, 1, 1))

        report = assess_feasibility(blocks, halls, labs, RESERVED)
        self.assertEqual(report.blockers, [])
        # 5 courses x 1 period + 5 lectures = 10 against 19 available.
        cohort = report.summary["cohorts"][0]
        self.assertEqual(cohort["required_periods_cohort_atomic"], 10)

    def test_overflow_capacity_warns_but_does_not_block(self):
        labs, halls = make_rooms()
        big_halls = [Hall(1, "Hall 1", 400, FULL_DAY_WINDOWS)]
        lecturer = make_staff(600, "Lecturer CS430", True)
        blocks = [
            make_block(
                "CS430",
                BlockType.LECTURE,
                lecturer,
                1,
                1,
                student_count=430,
            )
        ]
        report = assess_feasibility(blocks, big_halls, labs, RESERVED)
        self.assertEqual(report.blockers, [])
        warnings = [w for w in report.warnings if w.code == "ROOM_CAPACITY_SHORTFALL"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].evidence["student_count"], 430)
        self.assertEqual(warnings[0].evidence["largest_room_capacity"], 400)

    def test_lecturer_demand_shortfall_blocks_but_ta_only_warns(self):
        labs, halls = make_rooms()
        one_period_windows = make_windows([Day.SUNDAY], time(9, 0), time(11, 0))

        lecturer = make_staff(
            700, "Lecturer scarce", True, windows=one_period_windows
        )
        lecture_blocks = [
            make_block("IT101", BlockType.LECTURE, lecturer, group, 2)
            for group in (1, 2)
        ]

        ta = make_staff(701, "TA scarce", windows=one_period_windows)
        lab_blocks = [
            make_block("IT101LAB", BlockType.LAB, ta, group, 5) for group in range(1, 6)
        ]

        report = assess_feasibility(
            lecture_blocks + lab_blocks, halls, labs, RESERVED
        )
        staff_insights = [
            i for i in report.insights if i.code == "STAFF_DEMAND_EXCEEDS_AVAILABILITY"
        ]
        by_severity = {i.severity: i for i in staff_insights}
        self.assertIn(InsightSeverity.BLOCKER, by_severity)
        self.assertIn(InsightSeverity.WARNING, by_severity)
        self.assertIn("Lecturer", by_severity[InsightSeverity.BLOCKER].verdict)
        self.assertIn("TA", by_severity[InsightSeverity.WARNING].verdict)


if __name__ == "__main__":
    unittest.main()
