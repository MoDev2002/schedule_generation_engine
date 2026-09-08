# tests/test_schedule_continuation.py
"""Reconciliation rules for continue-from-schedule.

A retained entry whose room-time no longer fits the room's CURRENT
availability (hours were edited since the source schedule was generated)
must be regenerated, exactly like out-of-pool entries - otherwise a
continued schedule silently carries placements the live data forbids.
"""

import unittest
from datetime import time

from models.academic_list import AcademicList, Course
from models.block import Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from utils.schedule_continuation import reconcile_schedule

DEPARTMENT = Department(1, "CS")
TA_DEGREE = AcademicDegree(4, "TA", "TA.")


def ta(staff_id):
    return TeachingAssistant(staff_id, f"TA {staff_id}", DEPARTMENT, [], TA_DEGREE, False)


def make_lab_block(block_id, group_number):
    assignment = CourseAssignment(
        id=1, course_id=1920, course_code="IT202",
        lecture_groups=1, lecturers=[{"lecturer": _lecturer(), "num_of_groups": 1}],
        lab_groups=5, teaching_assistants=[{"teaching_assistant": ta(7), "num_of_groups": 5}],
    )
    return Block(
        id=block_id,
        course_codes=["IT202"],
        course_objects=[assignment],
        block_type=BlockType.LAB,
        staff_member=ta(7),
        student_count=25,
        required_room_type="lab",
        group_number=group_number,
        total_groups=5,
        is_single_group_course=False,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(5, "AHLIA", DEPARTMENT,
                         [Course(1920, "IT202", "IT202", "IT202", 3, 2, 4)])
        ],
        academic_levels=[2],
    )


_LECTURER = None


def _lecturer():
    from models.staff_members import Lecturer
    global _LECTURER
    if _LECTURER is None:
        _LECTURER = Lecturer(
            700, "Lecturer", DEPARTMENT, [], AcademicDegree(3, "L", "Dr."), True
        )
    return _LECTURER


def make_rooms():
    lab = Lab(1, "Lab1", 30,
              [TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(11, 0))],
              LabType.GENERAL)
    hall = Hall(1, "H1", 100,
                [TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(11, 0))])
    return [lab], [hall]


def entry(entry_id, group_number, day="sunday", start="09:00", end="11:00",
          lab_id=1, hall_id=None, lecturer_id=7):
    return {
        "id": entry_id,
        "course_ids": [1920],
        "session_type": "lab",
        "group_number": group_number,
        "total_groups": 5,
        "academic_ids": [5],
        "academic_levels": [2],
        "lecturer_id": lecturer_id,
        "day": day,
        "start_time": start,
        "end_time": end,
        "hall_id": hall_id,
        # The generation-context field is lab_id (the DB column is lap_id).
        "lab_id": lab_id,
    }


class ReconciliationWindowTests(unittest.TestCase):
    def test_in_window_entry_is_retained(self):
        labs, halls = make_rooms()
        blocks = [make_lab_block("b1", 1)]
        result = reconcile_schedule(blocks, [entry(11, 1)], halls, labs)
        self.assertEqual(result.retained_entries, 1)
        self.assertEqual(result.missing_blocks, [])
        self.assertIn("source_entry_11", result.fixed_assignments)

    def test_out_of_window_entry_is_regenerated(self):
        # The entry sits Sunday 13:00 but the lab's current hours end at
        # 11:00 - availability was edited since the source schedule. The
        # block must go back into the regenerable pool.
        labs, halls = make_rooms()
        blocks = [make_lab_block("b1", 1)]
        result = reconcile_schedule(
            blocks, [entry(11, 1, day="sunday", start="13:00", end="15:00")],
            halls, labs,
        )
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.out_of_window_entries_regenerated, 1)
        self.assertEqual(result.missing_blocks, blocks)
        self.assertEqual(result.fixed_assignments, {})

    def test_out_of_pool_entry_still_counted_separately(self):
        labs, halls = make_rooms()
        blocks = [make_lab_block("b1", 1)]
        result = reconcile_schedule(
            blocks, [entry(11, 1, lab_id=999)], halls, labs,
        )
        self.assertEqual(result.out_of_pool_entries_omitted, 1)
        self.assertEqual(result.out_of_window_entries_regenerated, 0)
        self.assertEqual(result.missing_blocks, blocks)

    def test_missing_entry_fields_do_not_crash_retained_path(self):
        # The identity fields are required upstream; a malformed time on a
        # retained entry raises a clear ValueError naming the entry.
        labs, halls = make_rooms()
        blocks = [make_lab_block("b1", 1)]
        bad = entry(11, 1, start="09:00:00")
        with self.assertRaises(ValueError):
            reconcile_schedule(blocks, [bad], halls, labs)


if __name__ == "__main__":
    unittest.main()
