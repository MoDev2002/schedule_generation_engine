# tests/test_external_continuation.py
"""Continuation rules for external entries.

- A staffless external lab (v1) and a roomless external-venue lecture (v3)
  are PINNED on a continuation run, never regenerated every run.
- Room presence is part of the identity: flipping a lecture's venue or
  adding/removing its internal lecturer regenerates the affected entries.
- The lecturer_id-None -> stale rule applies only to course entries;
  external entries decide staleness through the identity match.
"""

import unittest
from datetime import time

from models.block import Block, BlockType
from models.department import Department
from models.external_course import ExternalCourse
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from scheduler import SchedulingEngine
from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from utils.schedule_continuation import (
    build_block_identity,
    build_entry_identity,
    reconcile_schedule,
)

DEPARTMENT = Department(1, "CS")
LECTURER_DEGREE = AcademicDegree(3, "L", "Dr.")
TA_DEGREE = AcademicDegree(4, "TA", "TA.")


def ta(staff_id):
    return TeachingAssistant(staff_id, f"TA {staff_id}", DEPARTMENT, [], TA_DEGREE, False)


def lecturer(staff_id):
    return Lecturer(staff_id, f"Lec {staff_id}", DEPARTMENT, [], LECTURER_DEGREE, True)


def morning():
    return [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]


def make_rooms():
    lab = Lab(1, "Lab1", 30, morning(), LabType.GENERAL)
    hall = Hall(1, "H1", 100, morning())
    return [lab], [hall]


def make_engine(labs, halls):
    return SchedulingEngine(
        ConstraintManager(),
        ResourceManager(halls=halls, labs=labs, reserved_period=None),
    )


def staffless_lab_block():
    course = ExternalCourse(
        id=7, name_en="Ext", name_ar="خارجي",
        requesting_entity_en="F", requesting_entity_ar="ك",
        lab_groups=1, lab_students_per_group=20,
    )
    labs, halls = make_rooms()
    return make_engine(labs, halls).build_external_blocks([course])[0]


def roomless_lecture_block(staff_id=None):
    staff = [
        {"staff": lecturer(staff_id), "role": "lecturer", "num_of_groups": 1}
    ] if staff_id is not None else []
    course = ExternalCourse(
        id=9, name_en="Ext Lecture", name_ar="محاضرة",
        requesting_entity_en="F", requesting_entity_ar="ك",
        lecture_groups=1, lecture_students_per_group=60,
        lecture_venue="external",
        staff_assignments=staff,
    )
    labs, halls = make_rooms()
    return make_engine(labs, halls).build_external_blocks([course])[0]


def external_entry(entry_id, block, lecturer_id="unset", hall_id=None, lab_id=1):
    """A raw generation-context row mirroring an external block."""
    if lecturer_id == "unset":
        lecturer_id = block.staff_member.id if block.staff_member else None
    return {
        "id": entry_id,
        "entry_kind": "external",
        "external_course_id": block.external_course_id,
        "course_ids": [],
        "session_type": block.block_type.value,
        "group_number": block.group_number,
        "total_groups": block.total_groups,
        "academic_ids": [],
        "academic_levels": [],
        "lecturer_id": lecturer_id,
        "day": "sunday",
        "start_time": "09:00",
        "end_time": "11:00",
        "hall_id": hall_id,
        "lab_id": lab_id,
    }


class ExternalContinuationTests(unittest.TestCase):
    def test_identity_matches_between_block_and_entry(self):
        block = staffless_lab_block()
        entry = external_entry(1, block)
        self.assertEqual(build_block_identity(block), build_entry_identity(entry))

    def test_staffless_external_lab_is_retained_not_regenerated(self):
        block = staffless_lab_block()
        labs, halls = make_rooms()
        result = reconcile_schedule([block], [external_entry(1, block)], halls, labs)
        self.assertEqual(result.retained_entries, 1)
        self.assertEqual(result.stale_entries_omitted, 0)
        self.assertEqual(result.out_of_pool_entries_omitted, 0)

    def test_roomless_external_lecture_is_pinned_without_a_room(self):
        block = roomless_lecture_block(staff_id=70)
        labs, halls = make_rooms()
        result = reconcile_schedule(
            [block],
            [external_entry(2, block, hall_id=None, lab_id=None)],
            halls,
            labs,
        )
        self.assertEqual(result.retained_entries, 1)
        self.assertEqual(result.out_of_pool_entries_omitted, 0)
        pinned = result.fixed_assignments["source_entry_2"]
        self.assertIsNone(pinned.room)

    def test_venue_flip_makes_the_old_entry_stale(self):
        # The block is now roomed (venue ours), the entry is roomless
        # (venue external): identity mismatch -> regenerate.
        roomed_block = ExternalCourse(
            id=9, name_en="Ext Lecture", name_ar="محاضرة",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="ours",
        )
        labs, halls = make_rooms()
        block = make_engine(labs, halls).build_external_blocks([roomed_block])[0]
        stale_entry = external_entry(3, block, hall_id=None, lab_id=None)

        result = reconcile_schedule([block], [stale_entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.stale_entries_omitted, 1)

    def test_staff_flip_makes_the_old_entry_stale(self):
        # The entry was retained under TA 7; the course's lab distribution
        # now holds TA 8, so the rebuilt block's identity differs -> stale.
        course = ExternalCourse(
            id=8, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=20,
            staff_assignments=[{"staff": ta(8), "role": "ta", "num_of_groups": 1}],
        )
        labs, halls = make_rooms()
        block = make_engine(labs, halls).build_external_blocks([course])[0]
        stale_entry = external_entry(4, block, lecturer_id=7)

        result = reconcile_schedule([block], [stale_entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.stale_entries_omitted, 1)

    def test_course_entry_without_lecturer_still_regenerates(self):
        # The historical rule survives for local course entries only.
        assignment = CourseAssignment(
            id=1, course_id=1920, course_code="IT202",
            lecture_groups=1, lecturers=[{"lecturer": lecturer(700), "num_of_groups": 1}],
        )
        block = Block(
            id="L_IT202", course_codes=["IT202"], course_objects=[assignment],
            block_type=BlockType.LECTURE, staff_member=lecturer(700),
            student_count=60, required_room_type="hall", group_number=1,
            total_groups=1, is_single_group_course=True,
            academic_lists=["AHLIA"], academic_list_objects=[],
            academic_levels=[2],
        )
        course_entry = {
            "id": 5,
            "course_ids": [1920],
            "session_type": "lecture",
            "group_number": 1,
            "total_groups": 1,
            "academic_ids": [],
            "academic_levels": [],
            "lecturer_id": None,
            "day": "sunday",
            "start_time": "09:00",
            "end_time": "11:00",
            "hall_id": 1,
            "lab_id": None,
        }
        labs, halls = make_rooms()
        result = reconcile_schedule([block], [course_entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.stale_entries_omitted, 1)

    def test_changed_group_count_regenerates_externals(self):
        block = staffless_lab_block()
        entry = external_entry(6, block)
        entry["total_groups"] = 4
        labs, halls = make_rooms()
        result = reconcile_schedule([block], [entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.stale_entries_omitted, 1)

    def test_out_of_pool_external_lab_still_regenerates(self):
        # A roomed external lab whose lab left the run's pool is out-of-pool
        # (regenerated), not pinned roomless.
        course = ExternalCourse(
            id=8, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=20,
            staff_assignments=[{"staff": ta(7), "role": "ta", "num_of_groups": 1}],
        )
        labs, halls = make_rooms()
        block = make_engine(labs, halls).build_external_blocks([course])[0]
        entry = external_entry(7, block, lab_id=999)

        result = reconcile_schedule([block], [entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(result.out_of_pool_entries_omitted, 1)


if __name__ == "__main__":
    unittest.main()
