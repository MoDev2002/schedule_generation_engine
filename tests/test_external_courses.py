# tests/test_external_courses.py
"""External course scheduling - variants, both placement paths, in lockstep.

Each variant (1-5) runs end-to-end through block building, the CP-SAT
placement core, and the greedy fallback, then through the validator and the
report formatter. The variant guarantees under test:

- v1 staffless labs reserve lab + time only: they may share a period with a
  staffed class in another lab and never overlap a booking in their own lab.
- v3 external-venue lectures reserve lecturer time but no room: they are
  blocked by their lecturer's other sessions, free of room clashes, and are
  serialized with room=None end to end.
- v5 lectures in our halls without an internal lecturer reserve hall + time
  only and interact with no staff member.
"""

import unittest
from datetime import time

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.block import Block, BlockType
from models.department import Department
from models.external_course import (
    ExternalCourse,
    ExternalCourseValidationError,
)
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from schedule_format import format_schedule
from schedule_validator import ScheduleValidator
from scheduler import SchedulingEngine
from utils.api_schedule import convert_assignments_to_api_format
from utils.feasibility import assess_feasibility
from utils.room_selection import external_labs_outside_pool
from utils.sections import cohort_keys

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")


def ta(staff_id, prefs=None):
    return TeachingAssistant(
        staff_id, f"TA {staff_id}", DEPARTMENT, list(prefs or []), TA_DEGREE, False
    )


def lecturer(staff_id, prefs=None):
    return Lecturer(
        staff_id,
        f"Lecturer {staff_id}",
        DEPARTMENT,
        list(prefs or []),
        LECTURER_DEGREE,
        True,
    )


def all_day():
    return [TimePreference(Day.SUNDAY, time(9, 0), time(15, 0))]


def make_rooms(day_windows=None):
    windows = day_windows or all_day()
    labs = [
        Lab(index, f"Lab{index}", 30, windows, LabType.GENERAL) for index in (1, 2, 3)
    ]
    halls = [Hall(1, "H1", 100, windows), Hall(2, "H2", 100, windows)]
    return labs, halls


def engine_for(labs, halls, use_cpsat=True):
    return SchedulingEngine(
        ConstraintManager(),
        ResourceManager(halls=halls, labs=labs, reserved_period=None),
        use_cpsat=use_cpsat,
    )


def local_lab_block(block_id, staff_member):
    """A minimal staffed local lab, to interact with external sessions."""
    assignment = CourseAssignment(
        id=1, course_id=1, course_code="IT202",
        lecture_groups=1, lecturers=[{"lecturer": lecturer(900), "num_of_groups": 1}],
        lab_groups=1,
        teaching_assistants=[{"teaching_assistant": staff_member, "num_of_groups": 1}],
    )
    return Block(
        id=block_id,
        course_codes=["IT202"],
        course_objects=[assignment],
        block_type=BlockType.LAB,
        staff_member=staff_member,
        student_count=25,
        required_room_type="lab",
        group_number=1,
        total_groups=1,
        is_single_group_course=True,
        academic_lists=["AHLIA"],
        academic_list_objects=[],
        academic_levels=[2],
    )


def place_all(blocks, labs, halls, use_cpsat=True):
    engine = engine_for(labs, halls, use_cpsat=use_cpsat)
    assignments = engine.schedule_blocks([], {}, prebuilt_blocks=list(blocks))
    return engine, assignments


class ExternalCourseModelTests(unittest.TestCase):
    def test_v1_staffless_labs_shape_is_valid(self):
        course = ExternalCourse(
            id=7, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=2, lab_students_per_group=12,
        )
        self.assertEqual(course.lab_groups, 2)
        self.assertIsNone(course.lecture_venue)

    def test_course_without_any_component_is_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
            )

    def test_partial_lab_coverage_is_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lab_groups=5, lab_students_per_group=10,
                staff_assignments=[
                    {"staff": ta(1), "role": "ta", "num_of_groups": 3}
                ],
            )

    def test_external_venue_lecture_without_lecturer_is_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=1, lecture_students_per_group=40,
                lecture_venue="external",
            )

    def test_ta_rows_without_lab_groups_are_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=1, lecture_students_per_group=40,
                lecture_venue="ours",
                staff_assignments=[
                    {"staff": ta(1), "role": "ta", "num_of_groups": 1}
                ],
            )

    def test_venue_forbidden_without_lecture_groups(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lab_groups=2, lab_students_per_group=10,
                lecture_venue="ours",
            )

    def test_display_label_uses_external_identity(self):
        course = ExternalCourse(
            id=7, name_en="Quantum", name_ar="كم",
            requesting_entity_en="F", requesting_entity_ar="ك",
            code="QX-1",
            lab_groups=1, lab_students_per_group=10,
        )
        engine = SchedulingEngine(ConstraintManager(), ResourceManager([], [], None))
        blocks = engine.build_external_blocks([course])
        self.assertEqual(blocks[0].display_label(), "External — Quantum (QX-1) lab G1")
        self.assertEqual(blocks[0].course_codes, [])
        self.assertEqual(cohort_keys(blocks[0]), [])


class VariantPlacementTests(unittest.TestCase):
    """Every variant, through both placement cores and the validator."""

    def _check_placed(self, assignments, total):
        validator = ScheduleValidator()
        _, conflicts = validator.validate_schedule_comprehensive(assignments)
        blocking, _ = validator.split_conflicts(conflicts)
        self.assertEqual(blocking, [])
        self.assertEqual(len(assignments), total)

    def test_v1_staffless_labs_share_time_with_staffed_lab_elsewhere(self):
        course = ExternalCourse(
            id=7, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=20,
        )
        labs, halls = make_rooms()
        ext_blocks = engine_for(labs, halls).build_external_blocks([course])
        staffed = local_lab_block("local_lab", ta(50))

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(
                    ext_blocks + [staffed], labs, halls, use_cpsat=use_cpsat
                )
                self._check_placed(assignments, 2)
                ext_assignment = assignments[ext_blocks[0].id]
                self.assertIsNone(ext_assignment.block.staff_member)
                self.assertIsNotNone(ext_assignment.room)
                # Both sessions may share the same period in different labs.
                self.assertEqual(
                    assignments["local_lab"].time_slot.start_time,
                    ext_assignment.time_slot.start_time,
                )

    def test_v2_ta_distribution_demand_is_honoured(self):
        course = ExternalCourse(
            id=8, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=5, lab_students_per_group=10,
            staff_assignments=[
                {"staff": ta(60, [TimePreference(Day.SUNDAY, time(9, 0), time(15, 0))]),
                 "role": "ta", "num_of_groups": 3},
                {"staff": ta(61, [TimePreference(Day.SUNDAY, time(9, 0), time(15, 0))]),
                 "role": "ta", "num_of_groups": 2},
            ],
        )
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks([course])
        self.assertEqual(len(blocks), 5)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self._check_placed(assignments, 5)
                periods_of_ta60 = {
                    (a.time_slot.day, a.time_slot.start_time)
                    for a in assignments.values()
                    if a.block.staff_member is not None and a.block.staff_member.id == 60
                }
                # One TA holding 3 groups needs >= 3 distinct periods.
                self.assertGreaterEqual(len(periods_of_ta60), 3)

    def test_v3_roomless_lecture_reserves_time_not_room(self):
        course = ExternalCourse(
            id=9, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=10,
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="external",
            staff_assignments=[
                {"staff": lecturer(70, [TimePreference(Day.SUNDAY, time(9, 0), time(15, 0))]),
                 "role": "lecturer", "num_of_groups": 1},
            ],
        )
        labs, halls = make_rooms()
        engine = engine_for(labs, halls)
        blocks = engine.build_external_blocks([course])
        lecture = [b for b in blocks if b.block_type == BlockType.LECTURE][0]
        self.assertFalse(lecture.requires_room)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self._check_placed(assignments, 2)
                self.assertIsNone(assignments[lecture.id].room)

    def test_v3_lecture_blocked_by_lecturers_other_session(self):
        scarce = lecturer(70, [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))])
        course = ExternalCourse(
            id=9, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="external",
            staff_assignments=[
                {"staff": scarce, "role": "lecturer", "num_of_groups": 1},
            ],
        )
        labs, halls = make_rooms()
        ext_lecture = engine_for(labs, halls).build_external_blocks([course])[0]

        # The same lecturer also holds a local lab, and their recorded hours
        # cover exactly one period - the roomless external lecture and the
        # local session compete for that lecturer-hour, so only one of the
        # two can ever be placed (the lecturer's time is ours even when the
        # hall is not).
        busy_local = local_lab_block("local_lab", scarce)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(
                    [ext_lecture, busy_local], labs, halls, use_cpsat=use_cpsat
                )
                self.assertEqual(len(assignments), 1)
                placed = next(iter(assignments.values()))
                self.assertEqual(placed.time_slot.start_time, time(9, 0))
                self.assertEqual(placed.block.staff_member.id, 70)

    def test_v3_lecture_free_of_room_clashes(self):
        course = ExternalCourse(
            id=9, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="external",
            staff_assignments=[
                {"staff": lecturer(71), "role": "lecturer", "num_of_groups": 1},
            ],
        )
        labs, halls = make_rooms(
            [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        )
        engine = engine_for(labs, halls)
        ext_lecture = engine.build_external_blocks([course])[0]
        # A local lab occupies the only period that exists in every room.
        occupied = local_lab_block("local_lab", ta(52))

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(
                    [ext_lecture, occupied], labs, halls, use_cpsat=use_cpsat
                )
                self._check_placed(assignments, 2)
                # Same single period, no room on the external side.
                self.assertIsNone(assignments[ext_lecture.id].room)
                self.assertIsNotNone(assignments["local_lab"].room)

    def test_v4_fully_regular_shape_places_hall_and_labs(self):
        course = ExternalCourse(
            id=10, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=2, lab_students_per_group=10,
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="ours",
            staff_assignments=[
                {"staff": ta(80), "role": "ta", "num_of_groups": 2},
                {"staff": lecturer(81), "role": "lecturer", "num_of_groups": 1},
            ],
        )
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks([course])
        self.assertEqual(len(blocks), 3)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self._check_placed(assignments, 3)
                lecture = [a for a in assignments.values()
                           if a.block.block_type == BlockType.LECTURE][0]
                self.assertIsNotNone(lecture.room)

    def test_v5_hall_and_time_only_without_staff(self):
        course = ExternalCourse(
            id=11, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="ours",
        )
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks([course])
        self.assertIsNone(blocks[0].staff_member)
        self.assertTrue(blocks[0].requires_room)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self._check_placed(assignments, 1)
                v5 = assignments[blocks[0].id]
                self.assertIsNotNone(v5.room)
                self.assertIsNone(v5.block.staff_member)

    def test_two_staffless_v5_lectures_do_not_report_staff_none_conflict(self):
        courses = [
            ExternalCourse(
                id=11 + i, name_en=f"Ext{i}", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=1, lecture_students_per_group=60,
                lecture_venue="ours",
            )
            for i in range(2)
        ]
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks(courses)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self.assertEqual(len(assignments), 2)
                # Even in the same period, different halls: no STAFF None.
                validator = ScheduleValidator()
                _, conflicts = validator.validate_schedule_comprehensive(assignments)
                blocking, _ = validator.split_conflicts(conflicts)
                self.assertEqual(blocking, [])

    def test_mixed_shapes_one_course_one_run(self):
        # v1-style staffless labs beside a v3-style staffed external-venue
        # lecture... cannot exist on ONE course (external venue requires a
        # lecturer), so: staffless labs + a staffed external-venue lecture on
        # one course, which is the v3 mixed shape.
        course = ExternalCourse(
            id=12, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=2, lab_students_per_group=10,
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="external",
            staff_assignments=[
                {"staff": lecturer(90), "role": "lecturer", "num_of_groups": 1},
            ],
        )
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks([course])
        self.assertEqual(len(blocks), 3)

        for use_cpsat in (True, False):
            with self.subTest(placement_core="cpsat" if use_cpsat else "greedy"):
                _, assignments = place_all(blocks, labs, halls, use_cpsat=use_cpsat)
                self._check_placed(assignments, 3)
                labs_without_staff = [
                    a for a in assignments.values()
                    if a.block.block_type == BlockType.LAB
                    and a.block.staff_member is None
                ]
                self.assertEqual(len(labs_without_staff), 2)
                self.assertIsNone(
                    [
                        a for a in assignments.values()
                        if a.block.block_type == BlockType.LECTURE
                    ][0].room
                )


class PreflightAndPoolTests(unittest.TestCase):
    def test_roomless_blocks_survive_per_block_room_preflight(self):
        # No rooms at all: a roomless external lecture is still schedulable.
        course = ExternalCourse(
            id=20, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lecture_groups=1, lecture_students_per_group=60,
            lecture_venue="external",
            staff_assignments=[
                {"staff": lecturer(95), "role": "lecturer", "num_of_groups": 1},
            ],
        )
        engine = SchedulingEngine(ConstraintManager(), ResourceManager([], [], None))
        blocks = engine.build_external_blocks([course])
        rm = ResourceManager(halls=[], labs=[], reserved_period=None)
        self.assertEqual(rm.get_suitable_rooms(blocks[0]), [])

        # Rooms exist in the run; the roomless lecture simply ignores them.
        labs, halls = make_rooms()
        _, assignments = place_all(blocks, labs, halls, use_cpsat=True)
        self.assertEqual(len(assignments), 1)
        self.assertIsNone(assignments[blocks[0].id].room)

    def test_external_lab_demand_exceeds_pool_is_a_data_blocker(self):
        course = ExternalCourse(
            id=21, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=10, lab_students_per_group=10,
        )
        engine = SchedulingEngine(ConstraintManager(), ResourceManager([], [], None))
        blocks = engine.build_external_blocks([course])
        labs, halls = make_rooms(
            [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        )

        report = assess_feasibility(
            blocks=blocks, halls=halls, labs=labs, reserved_period=None
        )
        codes = [insight.code for insight in report.insights]
        self.assertIn("EXTERNAL_LAB_DEMAND_EXCEEDS_POOL", codes)
        self.assertTrue(report.blockers)

    def test_external_roomed_lecture_demand_exceeds_pool_is_a_data_blocker(self):
        courses = [
            ExternalCourse(
                id=22, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=4, lecture_students_per_group=60,
                lecture_venue="ours",
            )
        ]
        engine = SchedulingEngine(ConstraintManager(), ResourceManager([], [], None))
        blocks = engine.build_external_blocks(courses)
        labs, halls = make_rooms(
            [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        )
        halls = halls[:1]

        report = assess_feasibility(
            blocks=blocks, halls=halls, labs=labs, reserved_period=None
        )
        codes = [insight.code for insight in report.insights]
        self.assertIn("EXTERNAL_LECTURE_DEMAND_EXCEEDS_POOL", codes)

    def test_declared_labs_outside_run_pool_are_refused(self):
        course = ExternalCourse(
            id=23, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=10,
            eligible_labs=[Lab(99, "Special", 30, all_day(), LabType.GENERAL)],
        )
        engine = SchedulingEngine(ConstraintManager(), ResourceManager([], [], None))
        blocks = engine.build_external_blocks([course])
        self.assertEqual(external_labs_outside_pool(blocks, [1, 2]), {23})
        # No declared labs or no pool restriction: never refused.
        course.eligible_labs = None
        plain = engine.build_external_blocks([course])
        self.assertEqual(external_labs_outside_pool(plain, [1, 2]), set())
        self.assertEqual(external_labs_outside_pool(blocks, None), set())

    def test_external_only_lectures_only_without_lecture_component_is_rejected(self):
        from main import ScheduleGenerationEngine

        run = ScheduleGenerationEngine(
            study_plan_ids=[],
            schedule_name_en="T", schedule_name_ar="T",
            lectures_only=True,
            external_course_ids=[7],
        )
        run.external_courses = [
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lab_groups=2, lab_students_per_group=10,
            )
        ]
        self.assertTrue(run._external_only_lectures_only_rejected())

        # With a lecture component present (v3), the same run is valid.
        run.external_courses = [
            ExternalCourse(
                id=7, name_en="Ext", name_ar="خارجي",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=1, lecture_students_per_group=60,
                lecture_venue="external",
                staff_assignments=[
                    {"staff": lecturer(96), "role": "lecturer", "num_of_groups": 1},
                ],
            )
        ]
        self.assertFalse(run._external_only_lectures_only_rejected())


class PayloadAndReportTests(unittest.TestCase):
    def make_v1_run(self):
        course = ExternalCourse(
            id=7, name_en="Ext", name_ar="خارجي",
            requesting_entity_en="F", requesting_entity_ar="ك",
            lab_groups=1, lab_students_per_group=20,
        )
        labs, halls = make_rooms()
        blocks = engine_for(labs, halls).build_external_blocks([course])
        _, assignments = place_all(blocks, labs, halls, use_cpsat=True)
        return blocks, assignments

    def test_payload_for_v1_lab_is_external_with_null_lecturer(self):
        blocks, assignments = self.make_v1_run()
        api_data = convert_assignments_to_api_format(
            assignments=assignments,
            schedule_name_en="T", schedule_name_ar="T",
        )
        entry = api_data["schedule"][0]
        self.assertEqual(entry["entry_kind"], "external")
        self.assertEqual(entry["external_course_id"], 7)
        self.assertEqual(entry["course_ids"], [])
        self.assertEqual(entry["academic_ids"], [])
        self.assertIsNone(entry["lecturer_id"])
        self.assertIsNone(entry["hall_id"])
        self.assertIsNotNone(entry["lab_id"])

    def test_report_renders_external_session_without_crashing(self):
        blocks, assignments = self.make_v1_run()
        text = format_schedule(assignments)
        self.assertIn("External — Ext lab G1", text)
        self.assertIn("No internal staff assigned", text)
        self.assertIn("External venue" if "External venue" in text else "Room: Lab", text)


if __name__ == "__main__":
    unittest.main()
