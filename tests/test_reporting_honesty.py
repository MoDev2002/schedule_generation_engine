# tests/test_reporting_honesty.py
"""Honest scoring and reporting (audit findings #5, #7, #8).

- The gap metric measures real idle minutes between merged session
  intervals: a contiguous day scores full marks (the old hour-span
  heuristic scored it 0.5).
- Advisory warnings come from the FINAL placements only: a TA placed
  outside their recorded hours is warned about where they actually
  teach, and a TA inside their hours is never warned about.
"""

import unittest
from datetime import time

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList, Course
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from scheduler import SchedulingEngine

DEPARTMENT = Department(1, "Computer Science")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")
SUNDAY = Day.SUNDAY


def ta(staff_id, timing_preferences=None):
    return TeachingAssistant(
        staff_id, f"TA {staff_id}", DEPARTMENT,
        list(timing_preferences or []), TA_DEGREE, False,
    )


def lab_block(block_id, staff_member, group_number=1):
    assignment = CourseAssignment(
        id=1, course_id=1, course_code="IT202",
        lecture_groups=1, lecturers=[{"lecturer": _any_lecturer(), "num_of_groups": 1}],
        lab_groups=5,
        teaching_assistants=[{"teaching_assistant": staff_member, "num_of_groups": 5}],
    )
    return Block(
        id=block_id,
        course_codes=["IT202"],
        course_objects=[assignment],
        block_type=BlockType.LAB,
        staff_member=staff_member,
        student_count=25,
        required_room_type="lab",
        group_number=group_number,
        total_groups=5,
        is_single_group_course=False,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(1, "AHLIA", DEPARTMENT,
                         [Course(1, "IT202", "IT202", "IT202", 3, 2, 4)])
        ],
        academic_levels=[2],
    )


_LECTURER_CACHE = {}


def _any_lecturer():
    from models.staff_members import Lecturer
    if 999 not in _LECTURER_CACHE:
        _LECTURER_CACHE[999] = Lecturer(
            999, "Any Lecturer", DEPARTMENT, [], AcademicDegree(3, "L", "Dr."), True
        )
    return _LECTURER_CACHE[999]


def slot(start_hour):
    return TimePreference(day=SUNDAY, start_time=time(start_hour, 0),
                          end_time=time(start_hour + 2, 0))


def make_room(day_window_end=17):
    return Lab(
        1, "Lab1", 30,
        [TimePreference(day=SUNDAY, start_time=time(9, 0), end_time=time(day_window_end, 0))],
        LabType.GENERAL,
    )


class GapMetricTests(unittest.TestCase):
    def setUp(self):
        self.cm = ConstraintManager()
        self.room = make_room()
        self.block = lab_block("probe", ta(1), 1)

    def _seed(self, *start_hours):
        seeded = lab_block(f"seed{start_hours}", ta(2), 2)
        for index, hour in enumerate(start_hours):
            self.assertTrue(
                self.cm.make_assignment(
                    f"seed{index}", Assignment(seeded, slot(hour), self.room)
                )
            )

    def test_contiguous_day_scores_full_marks(self):
        # 09-11 + 11-13 placed; candidate 13-15 closes the day contiguously.
        # The old hour-span heuristic scored this exact case 0.5.
        self._seed(9, 11)
        self.assertEqual(self.cm.evaluate_gaps(self.block, slot(13), self.room), 1.0)

    def test_lone_session_on_empty_day_scores_full_marks(self):
        self.assertEqual(self.cm.evaluate_gaps(self.block, slot(9), self.room), 1.0)

    def test_four_hour_idle_gap_scores_zero(self):
        self._seed(9)
        self.assertEqual(self.cm.evaluate_gaps(self.block, slot(15), self.room), 0.0)

    def test_two_hour_idle_gap_scores_quarter(self):
        self._seed(9)
        self.assertEqual(self.cm.evaluate_gaps(self.block, slot(13), self.room), 0.25)


class AdvisoryWarningTests(unittest.TestCase):
    def _engine(self, lab):
        cm = ConstraintManager()
        rm = ResourceManager(halls=[], labs=[lab], reserved_period=None)
        return SchedulingEngine(cm, rm)

    def test_out_of_hours_final_placement_is_warned_about(self):
        # The TA's recorded hours cover only 11:00-13:00, but the room is
        # only free 09:00-11:00 - TA hours are advisory, so the block lands
        # at 09:00 and the warning must name that final placement.
        lab = make_room(day_window_end=11)
        scarce = ta(1, [TimePreference(day=SUNDAY, start_time=time(11, 0),
                                       end_time=time(13, 0))])
        engine = self._engine(lab)
        assignments = engine.schedule_blocks([], {}, prebuilt_blocks=[lab_block("b1", scarce)])

        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments["b1"].time_slot.start_time, time(9, 0))
        self.assertEqual(len(engine.advisory_warnings), 1)
        self.assertIn("09:00", engine.advisory_warnings[0])

    def test_in_hours_final_placement_is_never_warned_about(self):
        lab = make_room()
        comfortable = ta(1, [TimePreference(day=SUNDAY, start_time=time(9, 0),
                                            end_time=time(17, 0))])
        engine = self._engine(lab)
        engine.schedule_blocks([], {}, prebuilt_blocks=[lab_block("b1", comfortable)])
        self.assertEqual(engine.advisory_warnings, [])


if __name__ == "__main__":
    unittest.main()
