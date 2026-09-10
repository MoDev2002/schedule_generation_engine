# tests/test_external_fixed_slots.py
"""Fixed/allowed time slots for external courses.

A declared slot set is a hard candidate restriction on BOTH placement paths
(CP-SAT and greedy): every placement of a constrained course sits inside the
declared set, and anything provably infeasible about the set is refused
preflight with DATA blockers - capacity, per-staff reach, room coverage,
grid membership. Continuation regenerates entries that fall outside the
current set instead of pinning them.
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
from models.time_preferences import Day, ReservedPeriod, TimePreference
from scheduler import SchedulingEngine
from schedule_validator import ScheduleValidator
from services.change_validator import _hard_conflicts
from utils.feasibility import assess_feasibility
from utils.insights import InsightSeverity
from utils.schedule_continuation import (
    build_block_identity,
    build_entry_identity,
    reconcile_schedule,
)

DEPARTMENT = Department(1, "CS")
LECTURER_DEGREE = AcademicDegree(3, "L", "Dr.")
TA_DEGREE = AcademicDegree(4, "TA", "TA.")


def ta(staff_id, prefs=None):
    return TeachingAssistant(
        staff_id, f"TA {staff_id}", DEPARTMENT, list(prefs or []), TA_DEGREE, False
    )


def lecturer(staff_id, prefs=None):
    return Lecturer(
        staff_id,
        f"Lec {staff_id}",
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
        Lab(index, f"Lab{index}", 30, list(windows), LabType.GENERAL)
        for index in (1, 2, 3)
    ]
    halls = [Hall(1, "H1", 100, list(windows)), Hall(2, "H2", 100, list(windows))]
    return labs, halls


def engine_for(labs, halls, use_cpsat=True):
    return SchedulingEngine(
        ConstraintManager(),
        ResourceManager(halls=halls, labs=labs, reserved_period=None),
        use_cpsat=use_cpsat,
    )


def two_slots():
    return [
        TimePreference(Day.SUNDAY, time(9, 0), time(11, 0)),
        TimePreference(Day.SUNDAY, time(11, 0), time(13, 0)),
    ]


def lab_course(course_id=7, groups=2, slots=None, staff=None, labs=None):
    return ExternalCourse(
        id=course_id,
        name_en="Ext Lab",
        name_ar="معمل",
        requesting_entity_en="F",
        requesting_entity_ar="ك",
        lab_groups=groups,
        lab_students_per_group=12,
        lab_time_slots=slots,
        eligible_labs=labs,
        staff_assignments=staff or [],
    )


def lecture_course(course_id=8, venue="ours", slots=None, staff=None):
    return ExternalCourse(
        id=course_id,
        name_en="Ext Lecture",
        name_ar="محاضرة",
        requesting_entity_en="F",
        requesting_entity_ar="ك",
        lecture_groups=1,
        lecture_students_per_group=60,
        lecture_venue=venue,
        lecture_time_slots=slots,
        staff_assignments=staff or [],
    )


def codes(insights):
    return {insight.code for insight in insights}


def local_lab_block(block_id, staff_member):
    """A minimal staffed local 2h lab, to interact with external sessions."""
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


class FixedSlotModelTests(unittest.TestCase):
    """Defence-in-depth validation of a declared slot set."""

    def test_declared_slots_are_accepted_and_carried(self):
        course = lab_course(slots=two_slots())
        self.assertEqual(len(course.lab_time_slots), 2)

    def test_slots_without_their_component_are_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="E", name_ar="خ",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lecture_groups=1, lecture_students_per_group=40,
                lecture_venue="ours",
                lab_time_slots=two_slots(),
            )
        with self.assertRaises(ExternalCourseValidationError):
            ExternalCourse(
                id=7, name_en="E", name_ar="خ",
                requesting_entity_en="F", requesting_entity_ar="ك",
                lab_groups=2, lab_students_per_group=12,
                lecture_time_slots=two_slots(),
            )

    def test_off_grid_slot_is_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            lab_course(slots=[TimePreference(Day.SUNDAY, time(10, 0), time(12, 0))])
        with self.assertRaises(ExternalCourseValidationError):
            lab_course(slots=[TimePreference(Day.SUNDAY, time(9, 0), time(10, 0))])

    def test_duplicate_slot_is_rejected(self):
        with self.assertRaises(ExternalCourseValidationError):
            lab_course(slots=two_slots() + [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))])


class FixedSlotPlacementTests(unittest.TestCase):
    """Both placement paths honour the declared set."""

    def _engine_and_blocks(self, courses, use_cpsat):
        labs, halls = make_rooms()
        engine = engine_for(labs, halls, use_cpsat=use_cpsat)
        blocks = engine.build_external_blocks(courses)
        assignments = engine.schedule_blocks([], {}, prebuilt_blocks=blocks)
        return assignments, blocks

    def test_lab_blocks_place_only_inside_declared_slots(self):
        course = lab_course(
            groups=3,
            slots=two_slots(),
            staff=[
                {"staff": ta(21), "role": "ta", "num_of_groups": 2},
                {"staff": ta(22), "role": "ta", "num_of_groups": 1},
            ],
        )
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                assignments, _ = self._engine_and_blocks([course], use_cpsat)
                self.assertEqual(len(assignments), 3)
                for assignment in assignments.values():
                    self.assertIn(
                        (assignment.time_slot.day, assignment.time_slot.start_time),
                        [
                            (slot.day, slot.start_time)
                            for slot in course.lab_time_slots
                        ],
                    )

    def test_lecture_block_places_only_inside_declared_slots(self):
        course = lecture_course(
            venue="external",
            slots=two_slots(),
            staff=[{"staff": lecturer(31), "role": "lecturer", "num_of_groups": 1}],
        )
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                assignments, _ = self._engine_and_blocks([course], use_cpsat)
                self.assertEqual(len(assignments), 1)
                slot = next(iter(assignments.values())).time_slot
                self.assertIn(
                    (slot.day, slot.start_time),
                    [(s.day, s.start_time) for s in course.lecture_time_slots],
                )

    def test_declared_slots_override_room_hours_outside_them(self):
        # The labs are open 13:00-15:00 only, but the faculty fixed the labs
        # to Sun 9-11: candidates outside the set are dropped, so placement
        # is impossible and nothing is scheduled (an ENGINE failure with the
        # blocks named, never a placement outside the set).
        windows = [TimePreference(Day.SUNDAY, time(13, 0), time(15, 0))]
        labs, halls = make_rooms(windows)
        course = lab_course(groups=1, slots=two_slots())
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                engine = engine_for(labs, halls, use_cpsat=use_cpsat)
                blocks = engine.build_external_blocks([course])
                try:
                    assignments = engine.schedule_blocks(
                        [], {}, prebuilt_blocks=blocks
                    )
                except ValueError:
                    # The greedy fallback raises when nothing fits at all.
                    assignments = {}
                self.assertEqual(len(assignments), 0)

    def test_unconstrained_course_still_places_anywhere(self):
        course = lab_course(groups=2, slots=None)
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                assignments, _ = self._engine_and_blocks([course], use_cpsat)
                self.assertEqual(len(assignments), 2)


class FixedSlotPreflightTests(unittest.TestCase):
    """Provable refusals for infeasible declared sets."""

    def _report(self, course, halls_extra_windows=None, reserved=None):
        labs, halls = make_rooms(halls_extra_windows)
        blocks = SchedulingEngine(
            ConstraintManager(),
            ResourceManager(halls=halls, labs=labs, reserved_period=None),
        ).build_external_blocks([course])
        return assess_feasibility(blocks, halls, labs, reserved)

    def test_capacity_exceeded_is_a_blocker(self):
        # 5 groups x 2h into a single slot with 3 labs: capacity 3 < 5.
        course = lab_course(
            groups=5,
            slots=[TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))],
        )
        report = self._report(course)
        self.assertIn("EXTERNAL_LAB_SLOT_CAPACITY_EXCEEDED", codes(report.insights))
        self.assertTrue(report.blockers)

    def test_ta_reach_exceeded_is_a_blocker(self):
        # One TA holding 3 groups across only 2 slots: at most 2 reachable.
        course = lab_course(
            groups=3,
            slots=two_slots(),
            staff=[{"staff": ta(21), "role": "ta", "num_of_groups": 3}],
        )
        report = self._report(course)
        self.assertIn("EXTERNAL_LAB_TA_REACH_EXCEEDED", codes(report.insights))

    def test_slot_outside_ta_hours_is_a_warning(self):
        # The TA's hours cover Monday only; the slots are Sunday.
        ta_hours = [TimePreference(Day.MONDAY, time(9, 0), time(15, 0))]
        course = lab_course(
            groups=1,
            slots=two_slots(),
            staff=[{"staff": ta(22, prefs=ta_hours), "role": "ta", "num_of_groups": 1}],
        )
        report = self._report(course)
        self.assertIn("EXTERNAL_LAB_SLOT_OUTSIDE_TA_HOURS", codes(report.insights))
        outside_hours = [
            insight
            for insight in report.insights
            if insight.code == "EXTERNAL_LAB_SLOT_OUTSIDE_TA_HOURS"
        ]
        self.assertEqual(
            outside_hours[0].severity, InsightSeverity.WARNING
        )

    def test_slot_without_available_room_is_a_blocker(self):
        # Every lab opens at 13:00, so the 9-11 slot has no host.
        windows = [TimePreference(Day.SUNDAY, time(13, 0), time(15, 0))]
        course = lab_course(groups=1, slots=two_slots())
        report = self._report(course, halls_extra_windows=windows)
        self.assertIn("EXTERNAL_LAB_SLOT_NO_ROOM", codes(report.insights))

    def test_slot_overlapping_reserved_period_is_a_blocker(self):
        course = lab_course(
            groups=1,
            slots=[TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))],
        )
        reserved = ReservedPeriod(Day.SUNDAY, time(9, 0), time(11, 0))
        report = self._report(course, reserved=reserved)
        self.assertIn("EXTERNAL_LAB_SLOT_NOT_ON_GRID", codes(report.insights))

    def test_lecturer_unavailable_for_declared_slots_is_a_blocker(self):
        lecturer_hours = [TimePreference(Day.MONDAY, time(9, 0), time(15, 0))]
        course = lecture_course(
            venue="external",
            slots=two_slots(),
            staff=[
                {
                    "staff": lecturer(32, prefs=lecturer_hours),
                    "role": "lecturer",
                    "num_of_groups": 1,
                }
            ],
        )
        report = self._report(course)
        self.assertIn(
            "EXTERNAL_LECTURE_SLOT_LECTURER_UNAVAILABLE", codes(report.insights)
        )

    def test_constrained_course_does_not_fire_the_unconstrained_bound(self):
        # The declared-slot capacity replaces the pool bound for this course:
        # a feasible set must not additionally trip EXTERNAL_LAB_DEMAND_EXCEEDS_POOL.
        course = lab_course(
            groups=2,
            slots=two_slots(),
            staff=[{"staff": ta(23), "role": "ta", "num_of_groups": 2}],
        )
        report = self._report(course)
        self.assertNotIn("EXTERNAL_LAB_DEMAND_EXCEEDS_POOL", codes(report.insights))
        self.assertEqual(report.blockers, [])

    def test_unconstrained_course_fires_no_slot_insights(self):
        course = lab_course(groups=2)
        report = self._report(course)
        slot_codes = {
            code
            for code in codes(report.insights)
            if code.startswith("EXTERNAL_LAB_SLOT_")
            or code.startswith("EXTERNAL_LECTURE_SLOT_")
            or code.endswith("TA_REACH_EXCEEDED")
        }
        self.assertEqual(slot_codes, set())


class FixedSlotContinuationTests(unittest.TestCase):
    """Entries outside the current declared set regenerate, not pin."""

    def _constrained_block(self):
        course = lab_course(
            course_id=11,
            groups=2,
            slots=two_slots(),
            staff=[{"staff": ta(41), "role": "ta", "num_of_groups": 2}],
        )
        labs, halls = make_rooms()
        return engine_for(labs, halls).build_external_blocks([course])[0]

    def test_entry_inside_declared_slots_is_retained(self):
        block = self._constrained_block()
        labs, halls = make_rooms()
        entry = {
            "id": 1,
            "entry_kind": "external",
            "external_course_id": block.external_course_id,
            "course_ids": [],
            "session_type": "lab",
            "group_number": 1,
            "total_groups": 2,
            "academic_ids": [],
            "academic_levels": [],
            "lecturer_id": 41,
            "day": "sunday",
            "start_time": "09:00",
            "end_time": "11:00",
            "hall_id": None,
            "lab_id": 1,
        }
        result = reconcile_schedule([block], [entry], halls, labs)
        self.assertEqual(result.retained_entries, 1)
        self.assertEqual(
            result.out_of_allowed_slot_entries_regenerated, 0
        )

    def test_entry_outside_declared_slots_is_regenerated(self):
        block = self._constrained_block()
        labs, halls = make_rooms()
        # Same identity, but the course's set no longer contains Sun 13-15.
        entry = {
            "id": 2,
            "entry_kind": "external",
            "external_course_id": block.external_course_id,
            "course_ids": [],
            "session_type": "lab",
            "group_number": 1,
            "total_groups": 2,
            "academic_ids": [],
            "academic_levels": [],
            "lecturer_id": 41,
            "day": "sunday",
            "start_time": "13:00",
            "end_time": "15:00",
            "hall_id": None,
            "lab_id": 1,
        }
        result = reconcile_schedule([block], [entry], halls, labs)
        self.assertEqual(result.retained_entries, 0)
        self.assertEqual(
            result.out_of_allowed_slot_entries_regenerated, 1
        )

    def test_unconstrained_block_retains_any_slot(self):
        block = engine_for(*make_rooms()).build_external_blocks(
            [lab_course(course_id=12, groups=1)]
        )[0]
        labs, halls = make_rooms()
        entry = {
            "id": 3,
            "entry_kind": "external",
            "external_course_id": block.external_course_id,
            "course_ids": [],
            "session_type": "lab",
            "group_number": 1,
            "total_groups": 1,
            "academic_ids": [],
            "academic_levels": [],
            "lecturer_id": None,
            "day": "sunday",
            "start_time": "09:00",
            "end_time": "11:00",
            "hall_id": None,
            "lab_id": 1,
        }
        result = reconcile_schedule([block], [entry], halls, labs)
        self.assertEqual(result.retained_entries, 1)


class FixedSlotChangeValidatorTests(unittest.TestCase):
    """The editor's hard checks enforce the declared set for moves and swaps."""

    def _constrained_block(self):
        course = lab_course(
            course_id=13,
            groups=1,
            slots=two_slots(),
            staff=[{"staff": ta(51), "role": "ta", "num_of_groups": 1}],
        )
        labs, halls = make_rooms()
        return engine_for(labs, halls).build_external_blocks([course])[0]

    def test_move_into_a_declared_slot_has_no_slot_conflict(self):
        block = self._constrained_block()
        labs, halls = make_rooms()
        cm = ConstraintManager()
        rm = ResourceManager(halls=halls, labs=labs, reserved_period=None)
        inside = TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))
        conflicts = _hard_conflicts(cm, block, inside, labs[0], rm)
        self.assertNotIn(
            "FIXED_SLOT_CONFLICT", [c["code"] for c in conflicts]
        )

    def test_move_outside_declared_slots_is_a_hard_conflict(self):
        block = self._constrained_block()
        labs, halls = make_rooms()
        cm = ConstraintManager()
        rm = ResourceManager(halls=halls, labs=labs, reserved_period=None)
        outside = TimePreference(Day.SUNDAY, time(13, 0), time(15, 0))
        conflicts = _hard_conflicts(cm, block, outside, labs[0], rm)
        self.assertIn("FIXED_SLOT_CONFLICT", [c["code"] for c in conflicts])

    def test_unconstrained_block_moves_freely(self):
        course = lab_course(course_id=14, groups=1)
        labs, halls = make_rooms()
        block = engine_for(labs, halls).build_external_blocks([course])[0]
        cm = ConstraintManager()
        rm = ResourceManager(halls=halls, labs=labs, reserved_period=None)
        anywhere = TimePreference(Day.MONDAY, time(9, 0), time(11, 0))
        conflicts = _hard_conflicts(cm, block, anywhere, labs[0], rm)
        self.assertNotIn(
            "FIXED_SLOT_CONFLICT", [c["code"] for c in conflicts]
        )


class OneHourSessionTests(unittest.TestCase):
    """1h sessions pack two groups into one 2h slot, rooms and staff alike."""

    def _one_hour_course(self, course_id=15, groups=2, slots=None, staff=None):
        return ExternalCourse(
            id=course_id,
            name_en="Packed Lab",
            name_ar="معمل مضغوط",
            requesting_entity_en="F",
            requesting_entity_ar="ك",
            lab_groups=groups,
            lab_students_per_group=12,
            lab_session_hours=1,
            lab_time_slots=slots,
            staff_assignments=staff or [],
        )

    def _place(self, courses, labs=None, halls=None, extra_blocks=()):
        labs = labs if labs is not None else make_rooms()[0]
        halls = halls if halls is not None else make_rooms()[1]
        engine = engine_for(labs, halls)
        blocks = engine.build_external_blocks(courses) + list(extra_blocks)
        assignments = engine.schedule_blocks([], {}, prebuilt_blocks=blocks)
        return blocks, assignments

    def test_two_one_hour_groups_pack_into_one_slot_with_one_ta_and_lab(self):
        single_slot = [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        course = self._one_hour_course(
            groups=2,
            slots=single_slot,
            staff=[{"staff": ta(61), "role": "ta", "num_of_groups": 2}],
        )
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                labs, halls = make_rooms()
                engine = engine_for(labs, halls, use_cpsat=use_cpsat)
                blocks = engine.build_external_blocks([course])
                assignments = engine.schedule_blocks(
                    [], {}, prebuilt_blocks=blocks
                )
                self.assertEqual(len(assignments), 2)
                placed = list(assignments.values())
                # Both inside the single declared slot...
                for assignment in placed:
                    self.assertEqual(assignment.time_slot.day, Day.SUNDAY)
                    self.assertIn(
                        assignment.time_slot.start_time,
                        (time(9, 0), time(10, 0)),
                    )
                    self.assertEqual(assignment.time_slot.end_time, time(11, 0)
                                     if assignment.time_slot.start_time == time(10, 0)
                                     else time(10, 0))
                # ...in the SAME half-periods (packing, not parallel rooms):
                starts = {a.time_slot.start_time for a in placed}
                self.assertEqual(starts, {time(9, 0), time(10, 0)})
                # One TA staffs both halves.
                self.assertEqual({a.block.staff_member.id for a in placed}, {61})

    def test_one_hour_lecture_with_declared_slot_places_in_it(self):
        course = ExternalCourse(
            id=16,
            name_en="Packed Lecture",
            name_ar="محاضرة",
            requesting_entity_en="F",
            requesting_entity_ar="ك",
            lecture_groups=2,
            lecture_students_per_group=60,
            lecture_venue="external",
            lecture_session_hours=1,
            lecture_time_slots=[TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))],
            staff_assignments=[
                {"staff": lecturer(62), "role": "lecturer", "num_of_groups": 2}
            ],
        )
        for use_cpsat in (True, False):
            with self.subTest(cpsat=use_cpsat):
                labs, halls = make_rooms()
                engine = engine_for(labs, halls, use_cpsat=use_cpsat)
                blocks = engine.build_external_blocks([course])
                assignments = engine.schedule_blocks(
                    [], {}, prebuilt_blocks=blocks
                )
                self.assertEqual(len(assignments), 2)
                starts = sorted(
                    a.time_slot.start_time for a in assignments.values()
                )
                # One lecturer teaches both groups back-to-back in the slot.
                self.assertEqual(starts, [time(9, 0), time(10, 0)])
                for assignment in assignments.values():
                    self.assertIsNone(assignment.room)
                    self.assertEqual(assignment.block.staff_member.id, 62)

    def test_two_hour_booking_excludes_both_halves_for_one_hour_sessions(self):
        single_slot = [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        course = self._one_hour_course(groups=1, slots=single_slot)
        labs, halls = make_rooms()
        engine = engine_for(labs, halls)
        external_blocks = engine.build_external_blocks([course])
        local = local_lab_block("LOC_1", ta(63))
        assignments = engine.schedule_blocks(
            [], {}, prebuilt_blocks=[local, *external_blocks]
        )
        self.assertEqual(len(assignments), 2)
        local_a = assignments["LOC_1"]
        external_a = assignments[external_blocks[0].id]
        # The invariant: a 1h session never shares a lab-period (or either
        # half of it) with the 2h booking that occupies that lab.
        same_room = (
            local_a.room is not None
            and external_a.room is not None
            and local_a.room.id == external_a.room.id
        )
        overlap = (
            external_a.time_slot.start_time < local_a.time_slot.end_time
            and local_a.time_slot.start_time < external_a.time_slot.end_time
        )
        self.assertFalse(same_room and overlap)
        # And the validator agrees there are no blocking conflicts at all.
        validator = ScheduleValidator()
        _, conflicts = validator.validate_schedule_comprehensive(assignments)
        blocking, _ = validator.split_conflicts(conflicts)
        self.assertEqual(blocking, [])

    def test_demand_bounds_credit_packed_halves(self):
        # 2 groups x 1h in ONE slot with ONE lab: feasible (the old
        # whole-period bound refused it as 2 > 1).
        single_slot = [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        course = self._one_hour_course(groups=2, slots=single_slot)
        labs, halls = make_rooms()
        blocks = SchedulingEngine(
            ConstraintManager(),
            ResourceManager(halls=halls, labs=labs, reserved_period=None),
        ).build_external_blocks([course])
        report = assess_feasibility(blocks, halls, labs, None)
        self.assertNotIn(
            "EXTERNAL_LAB_DEMAND_EXCEEDS_POOL", codes(report.insights)
        )
        self.assertNotIn(
            "EXTERNAL_LAB_SLOT_CAPACITY_EXCEEDED", codes(report.insights)
        )
        self.assertNotIn("EXTERNAL_LAB_TA_REACH_EXCEEDED", codes(report.insights))

    def test_staff_demand_counts_one_hour_halves_as_half_periods(self):
        # A TA holding 2 x 1h groups needs only ONE period of availability.
        ta_hours = [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        course = self._one_hour_course(
            groups=2,
            staff=[{"staff": ta(64, prefs=ta_hours), "role": "ta", "num_of_groups": 2}],
        )
        labs, halls = make_rooms()
        blocks = SchedulingEngine(
            ConstraintManager(),
            ResourceManager(halls=halls, labs=labs, reserved_period=None),
        ).build_external_blocks([course])
        report = assess_feasibility(blocks, halls, labs, None)
        self.assertNotIn(
            "STAFF_DEMAND_EXCEEDS_AVAILABILITY", codes(report.insights)
        )

    def test_session_hours_join_the_continuation_identity(self):
        labs, halls = make_rooms()
        engine = engine_for(labs, halls)
        one_hour = self._one_hour_course(
            groups=1, staff=[{"staff": ta(65), "role": "ta", "num_of_groups": 1}]
        )
        two_hour = lab_course(course_id=16, groups=1)
        one_hour_block = engine.build_external_blocks([one_hour])[0]
        two_hour_block = engine.build_external_blocks([two_hour])[0]
        self.assertNotEqual(
            build_block_identity(one_hour_block),
            build_block_identity(two_hour_block),
        )
        # A 2h entry never matches a 1h block: it goes stale and regenerates.
        one_hour_entry = {
            "id": 9,
            "entry_kind": "external",
            "external_course_id": one_hour_block.external_course_id,
            "course_ids": [],
            "session_type": "lab",
            "group_number": 1,
            "total_groups": 1,
            "academic_ids": [],
            "academic_levels": [],
            "lecturer_id": 65,
            "day": "sunday",
            "start_time": "09:00",
            "end_time": "10:00",
            "hall_id": None,
            "lab_id": 1,
        }
        self.assertEqual(
            build_block_identity(one_hour_block),
            build_entry_identity(one_hour_entry),
        )
        stretched = dict(one_hour_entry, end_time="11:00")
        self.assertNotEqual(
            build_block_identity(one_hour_block),
            build_entry_identity(stretched),
        )

    def test_require_grid_slot_accepts_halves_for_one_hour_blocks(self):
        from services.change_validator import _require_grid_slot

        labs, halls = make_rooms()
        rm = ResourceManager(halls=halls, labs=labs, reserved_period=None)
        first_half = TimePreference(Day.MONDAY, time(9, 0), time(10, 0))
        second_half = TimePreference(Day.MONDAY, time(10, 0), time(11, 0))
        # 1h blocks may target halves...
        _require_grid_slot(rm, first_half, session_hours=1)
        _require_grid_slot(rm, second_half, session_hours=1)
        # ...2h blocks may not.
        with self.assertRaises(ValueError):
            _require_grid_slot(rm, first_half, session_hours=2)


if __name__ == "__main__":
    unittest.main()
