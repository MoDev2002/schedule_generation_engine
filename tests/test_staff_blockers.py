# tests/test_staff_blockers.py
"""Per-staff blockers: parsing, overlap semantics, and hard enforcement.

A blocker forbids by OVERLAP (the mirror of availability's containment)
and binds lecturers and TAs alike - unlike TA timing preferences, which
stay advisory. Every enforcement surface is covered here: the CP-SAT
core, the greedy fallback, the constraint manager's commit-time rule,
the final validator, continuation reconciliation, the preflight count,
and the run snapshot.
"""

import tempfile
import unittest
from datetime import time
from pathlib import Path

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList, Course
from models.block import Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, StaffBlocker, TimePreference
from services.change_validator import HARD_CONFLICT_CODES
from solver.cpsat_scheduler import solve_schedule
from utils.schedule_continuation import reconcile_schedule
from utils.time_utils import (
    blocker_overlaps_slot,
    convert_api_blocker,
    staff_is_blocked,
)

DEPARTMENT = Department(1, "CS")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "TA", "TA.")

ALL_WEEK = [
    TimePreference(day=day, start_time=time(9, 0), end_time=time(19, 0))
    for day in (
        Day.SUNDAY,
        Day.MONDAY,
        Day.TUESDAY,
        Day.WEDNESDAY,
        Day.THURSDAY,
    )
]


def lecturer(staff_id, timing_preferences=None, blockers=None):
    return Lecturer(
        staff_id,
        f"Lecturer {staff_id}",
        DEPARTMENT,
        list(timing_preferences or []),
        LECTURER_DEGREE,
        True,
        list(blockers or []),
    )


def ta(staff_id, blockers=None):
    return TeachingAssistant(
        staff_id,
        f"TA {staff_id}",
        DEPARTMENT,
        [],
        TA_DEGREE,
        False,
        list(blockers or []),
    )


def sunday_morning_blocker(label="Meeting"):
    return StaffBlocker(
        day=Day.SUNDAY,
        start_time=time(13, 0),
        end_time=time(15, 0),
        label=label,
    )


def lecture_block(block_id, staff_member):
    assignment = CourseAssignment(
        id=1,
        course_id=10,
        course_code="CS101",
        lecture_groups=1,
        lecturers=[{"lecturer": staff_member, "num_of_groups": 1}],
    )
    return Block(
        id=block_id,
        course_codes=["CS101"],
        course_objects=[assignment],
        block_type=BlockType.LECTURE,
        staff_member=staff_member,
        student_count=60,
        required_room_type="hall",
        group_number=1,
        total_groups=1,
        is_single_group_course=True,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(
                1,
                "AHLIA",
                DEPARTMENT,
                [Course(10, "CS101", "CS101", "CS101", 3, 2, 4)],
            )
        ],
        academic_levels=[2],
    )


class ParsingTests(unittest.TestCase):
    def test_api_blocker_round_trips(self):
        blocker = convert_api_blocker(
            {"day": "sunday", "startTime": "13:00", "endTime": "15:00", "label": "Meeting"}
        )
        self.assertEqual(blocker.day, Day.SUNDAY)
        self.assertEqual(blocker.start_time, time(13, 0))
        self.assertEqual(blocker.end_time, time(15, 0))
        self.assertEqual(blocker.label, "Meeting")

    def test_api_blocker_unknown_day_raises(self):
        with self.assertRaises(ValueError):
            convert_api_blocker(
                {"day": "funday", "startTime": "13:00", "endTime": "15:00", "label": "x"}
            )

    def test_staff_conversion_attaches_blockers(self):
        from utils.api_staff import convert_api_staff_member

        payload = {
            "id": 5,
            "nameEn": "Dr. Smith",
            "department": {"id": 1, "name": "CS"},
            "academic_degree": {"id": 3, "name": "Lecturer", "prefix": "Dr."},
            "isPermanent": 1,
            "timingPreference": [],
            "blockers": [
                {"day": "sunday", "startTime": "13:00", "endTime": "15:00", "label": "M"}
            ],
        }
        member = convert_api_staff_member(payload)
        self.assertIsInstance(member, Lecturer)
        self.assertEqual(len(member.blockers), 1)
        self.assertEqual(member.blockers[0].label, "M")

        payload["academic_degree"]["id"] = 4
        assistant = convert_api_staff_member(payload)
        self.assertIsInstance(assistant, TeachingAssistant)
        self.assertEqual(len(assistant.blockers), 1)

    def test_staff_conversion_without_blockers_key_is_empty(self):
        from utils.api_staff import convert_api_staff_member

        payload = {
            "id": 5,
            "nameEn": "Dr. Smith",
            "department": {"id": 1, "name": "CS"},
            "academic_degree": {"id": 3, "name": "Lecturer", "prefix": "Dr."},
            "isPermanent": 1,
            "timingPreference": [],
        }
        member = convert_api_staff_member(payload)
        self.assertEqual(member.blockers, [])


class OverlapSemanticsTests(unittest.TestCase):
    def test_overlap_forbids_by_any_shared_time(self):
        blocker = sunday_morning_blocker()
        # Inside the blocker.
        self.assertTrue(
            blocker_overlaps_slot(
                blocker,
                TimePreference(day=Day.SUNDAY, start_time=time(13, 0), end_time=time(15, 0)),
            )
        )
        # A session ending exactly when the blocker starts does not collide
        # (half-open intervals, mirroring the double-booking rule).
        self.assertFalse(
            blocker_overlaps_slot(
                blocker,
                TimePreference(day=Day.SUNDAY, start_time=time(11, 0), end_time=time(13, 0)),
            )
        )
        # Other days are untouched.
        self.assertFalse(
            blocker_overlaps_slot(
                blocker,
                TimePreference(day=Day.MONDAY, start_time=time(13, 0), end_time=time(15, 0)),
            )
        )

    def test_staff_is_blocked_scans_the_whole_set(self):
        member = lecturer(1, blockers=[sunday_morning_blocker()])
        self.assertTrue(
            staff_is_blocked(
                member.blockers,
                TimePreference(day=Day.SUNDAY, start_time=time(14, 0), end_time=time(15, 0)),
            )
        )
        self.assertFalse(
            staff_is_blocked(
                member.blockers,
                TimePreference(day=Day.MONDAY, start_time=time(13, 0), end_time=time(15, 0)),
            )
        )


class SolverTests(unittest.TestCase):
    """The CP-SAT core never places blocked time, for either role."""

    def _solve_one_lecture(self, staff_member):
        hall = Hall(1, "H1", 100, ALL_WEEK)
        blocks = [lecture_block("b1", staff_member)]
        manager = ConstraintManager()
        manager.setup_constraints()
        result = solve_schedule(
            blocks=blocks,
            halls=[hall],
            labs=[],
            reserved_period=None,
            constraint_manager=manager,
        )
        return result

    def test_lecturer_with_only_a_blocker_avoids_it(self):
        # No timing preferences: without the blocker the lecturer is fully
        # available; with it, Sunday 13-15 must never appear.
        member = lecturer(1, blockers=[sunday_morning_blocker()])
        result = self._solve_one_lecture(member)
        self.assertEqual(result.status, "OPTIMAL")
        placement = result.assignments["b1"].time_slot
        self.assertFalse(blocker_overlaps_slot(sunday_morning_blocker(), placement))

    def test_ta_blocker_is_hard_not_advisory(self):
        lab = Lab(1, "Lab1", 30, ALL_WEEK, LabType.GENERAL)
        member = ta(7, blockers=[sunday_morning_blocker()])
        assignment = CourseAssignment(
            id=1,
            course_id=10,
            course_code="CS101",
            lecture_groups=1,
            lecturers=[{"lecturer": lecturer(2), "num_of_groups": 1}],
            lab_groups=6,
            teaching_assistants=[{"teaching_assistant": member, "num_of_groups": 6}],
        )
        block = Block(
            id="lab1",
            course_codes=["CS101"],
            course_objects=[assignment],
            block_type=BlockType.LAB,
            staff_member=member,
            student_count=25,
            required_room_type="lab",
            group_number=1,
            total_groups=6,
            is_single_group_course=False,
            academic_lists=["AHLIA"],
            academic_list_objects=[
                AcademicList(
                    1,
                    "AHLIA",
                    DEPARTMENT,
                    [Course(10, "CS101", "CS101", "CS101", 3, 2, 4)],
                )
            ],
            academic_levels=[2],
        )
        manager = ConstraintManager()
        manager.setup_constraints()
        result = solve_schedule(
            blocks=[block],
            halls=[],
            labs=[lab],
            reserved_period=None,
            constraint_manager=manager,
        )
        self.assertEqual(result.status, "OPTIMAL")
        placement = result.assignments["lab1"].time_slot
        self.assertFalse(blocker_overlaps_slot(sunday_morning_blocker(), placement))


class GreedyTests(unittest.TestCase):
    """The greedy fallback filters blocked slots before preference ordering."""

    def _resource_manager(self):
        hall = Hall(1, "H1", 100, ALL_WEEK)
        return ResourceManager([hall], [], None)

    def test_blocked_slot_excluded_for_ta(self):
        member = ta(7, blockers=[sunday_morning_blocker()])
        block = lecture_block("b1", member)
        slots = self._resource_manager().get_available_slots(block, Hall(1, "H1", 100, ALL_WEEK), {})
        self.assertFalse(
            any(blocker_overlaps_slot(sunday_morning_blocker(), slot) for slot in slots)
        )
        self.assertTrue(slots)  # the rest of the week is still available

    def test_blocked_slot_excluded_for_lecturer_without_preferences(self):
        member = lecturer(1, blockers=[sunday_morning_blocker()])
        block = lecture_block("b1", member)
        slots = self._resource_manager().get_available_slots(block, Hall(1, "H1", 100, ALL_WEEK), {})
        self.assertFalse(
            any(blocker_overlaps_slot(sunday_morning_blocker(), slot) for slot in slots)
        )


class ConstraintManagerTests(unittest.TestCase):
    def test_blocker_rule_is_registered_and_hard(self):
        manager = ConstraintManager()
        manager.setup_constraints()
        descriptions = [c["description"] for c in manager.hard_constraints]
        self.assertIn(
            "No session may overlap a blocker of its staff member", descriptions
        )
        self.assertEqual(
            HARD_CONFLICT_CODES["No session may overlap a blocker of its staff member"],
            "STAFF_BLOCKER_CONFLICT",
        )

    def test_check_staff_blocker_rejects_overlap_only(self):
        manager = ConstraintManager()
        block = lecture_block("b1", lecturer(1, blockers=[sunday_morning_blocker()]))
        blocked_slot = TimePreference(
            day=Day.SUNDAY, start_time=time(13, 0), end_time=time(15, 0)
        )
        free_slot = TimePreference(
            day=Day.SUNDAY, start_time=time(15, 0), end_time=time(17, 0)
        )
        self.assertFalse(manager.check_staff_blocker(block, blocked_slot, None))
        self.assertTrue(manager.check_staff_blocker(block, free_slot, None))

    def test_staffless_block_has_no_blocker_conflict(self):
        manager = ConstraintManager()
        block = lecture_block("b1", None)
        blocked_slot = TimePreference(
            day=Day.SUNDAY, start_time=time(13, 0), end_time=time(15, 0)
        )
        self.assertTrue(manager.check_staff_blocker(block, blocked_slot, None))


class ContinuationTests(unittest.TestCase):
    def _lab_and_block(self):
        lab = Lab(
            1,
            "Lab1",
            30,
            [TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(19, 0))],
            LabType.GENERAL,
        )
        member = ta(7)
        assignment = CourseAssignment(
            id=1,
            course_id=1920,
            course_code="IT202",
            lecture_groups=1,
            lecturers=[{"lecturer": lecturer(9), "num_of_groups": 1}],
            lab_groups=5,
            teaching_assistants=[{"teaching_assistant": member, "num_of_groups": 5}],
        )
        block = Block(
            id="b1",
            course_codes=["IT202"],
            course_objects=[assignment],
            block_type=BlockType.LAB,
            staff_member=member,
            student_count=25,
            required_room_type="lab",
            group_number=1,
            total_groups=5,
            is_single_group_course=False,
            academic_lists=["AHLIA"],
            academic_list_objects=[
                AcademicList(
                    5,
                    "AHLIA",
                    DEPARTMENT,
                    [Course(1920, "IT202", "IT202", "IT202", 3, 2, 4)],
                )
            ],
            academic_levels=[2],
        )
        return [lab], block

    @staticmethod
    def _entry(entry_id, lecturer_id=7):
        return {
            "id": entry_id,
            "course_ids": [1920],
            "session_type": "lab",
            "group_number": 1,
            "total_groups": 5,
            "academic_ids": [5],
            "academic_levels": [2],
            "lecturer_id": lecturer_id,
            "day": "sunday",
            "start_time": "13:00",
            "end_time": "15:00",
            "hall_id": None,
            "lab_id": 1,
        }

    def test_blocked_retained_entry_is_regenerated(self):
        labs, block = self._lab_and_block()
        blockers = {7: [sunday_morning_blocker()]}
        result = reconcile_schedule(
            [block], [self._entry(1)], [], labs, blockers_by_staff=blockers
        )
        self.assertEqual(result.fixed_assignments, {})
        self.assertEqual(result.blocked_entries_regenerated, 1)
        self.assertEqual(result.missing_blocks, [block])

    def test_unblocked_retained_entry_survives(self):
        labs, block = self._lab_and_block()
        result = reconcile_schedule(
            [block], [self._entry(1)], [], labs, blockers_by_staff={7: []}
        )
        self.assertEqual(len(result.fixed_assignments), 1)
        self.assertEqual(result.blocked_entries_regenerated, 0)

    def test_staffless_entry_is_never_blocked(self):
        labs, block = self._lab_and_block()
        entry = self._entry(1, lecturer_id=None)
        entry["lecturer_id"] = None
        result = reconcile_schedule(
            [block], [entry], [], labs, blockers_by_staff={7: [sunday_morning_blocker()]}
        )
        self.assertEqual(result.blocked_entries_regenerated, 0)


class PreflightCountTests(unittest.TestCase):
    def _periods(self):
        return {
            (Day.SUNDAY, time(13, 0), time(15, 0)),
            (Day.SUNDAY, time(15, 0), time(17, 0)),
            (Day.MONDAY, time(9, 0), time(11, 0)),
        }

    def test_windows_minus_blockers(self):
        member = lecturer(1, timing_preferences=ALL_WEEK, blockers=[sunday_morning_blocker()])
        from utils.feasibility import _staff_available_period_count

        self.assertEqual(_staff_available_period_count(member, self._periods()), 2)

    def test_blockers_only_staff_still_counts(self):
        # No declared windows: fully available except blocked time.
        member = lecturer(1, blockers=[sunday_morning_blocker()])
        from utils.feasibility import _staff_available_period_count

        self.assertEqual(_staff_available_period_count(member, self._periods()), 2)

    def test_unconstrained_staff_returns_none(self):
        member = lecturer(1)
        from utils.feasibility import _staff_available_period_count

        self.assertIsNone(_staff_available_period_count(member, self._periods()))


class SnapshotTests(unittest.TestCase):
    def test_snapshot_records_effective_blockers(self):
        import utils.run_snapshot as run_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            original_dir = run_snapshot.SNAPSHOT_DIR
            run_snapshot.SNAPSHOT_DIR = str(Path(tmp))
            try:
                path = run_snapshot.write_run_snapshot(
                    blocks=[],
                    halls=[],
                    labs=[],
                    reserved_period=None,
                    meta={},
                    staff_blockers={7: [sunday_morning_blocker()]},
                )
            finally:
                run_snapshot.SNAPSHOT_DIR = original_dir

            import json

            with open(path, encoding="utf-8") as file:
                payload = json.load(file)
            self.assertEqual(len(payload["staff_blockers"]), 1)
            record = payload["staff_blockers"][0]
            self.assertEqual(record["staff_id"], 7)
            self.assertEqual(record["blockers"][0]["label"], "Meeting")
            self.assertEqual(record["blockers"][0]["day"], "SUNDAY")
            self.assertEqual(record["blockers"][0]["start"], "13:00")


if __name__ == "__main__":
    unittest.main()
