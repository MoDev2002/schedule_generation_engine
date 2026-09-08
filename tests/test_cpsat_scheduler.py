# tests/test_cpsat_scheduler.py
"""CP-SAT placement core regressions - the audited failing fixtures.

Each fixture here failed under the old greedy loop (audit findings #2-#4)
while a valid schedule existed. The solver must complete all of them, and
the committed result must satisfy the ConstraintManager's hard rules.
"""

import unittest
from datetime import time

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList, Course
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, ReservedPeriod, TimePreference
from scheduler import SchedulingEngine
from solver.cpsat_scheduler import solve_schedule

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")


def ta(staff_id):
    return TeachingAssistant(staff_id, f"TA {staff_id}", DEPARTMENT, [], TA_DEGREE, False)


def lecturer(staff_id, timing_preferences=None):
    return Lecturer(
        staff_id, f"Lecturer {staff_id}", DEPARTMENT,
        list(timing_preferences or []), LECTURER_DEGREE, True,
    )


def windows(day, start_hour, end_hour):
    return [TimePreference(day=day, start_time=time(start_hour, 0), end_time=time(end_hour, 0))]


def lab_block(block_id, course_code, staff_member, group_number, student_count=25):
    assignment = CourseAssignment(
        id=1, course_id=1, course_code=course_code,
        lecture_groups=1, lecturers=[{"lecturer": lecturer(900), "num_of_groups": 1}],
        lab_groups=6,
        teaching_assistants=[{"teaching_assistant": staff_member, "num_of_groups": 6}],
    )
    return Block(
        id=block_id,
        course_codes=[course_code],
        course_objects=[assignment],
        block_type=BlockType.LAB,
        staff_member=staff_member,
        student_count=student_count,
        required_room_type="lab",
        group_number=group_number,
        total_groups=6,
        is_single_group_course=False,
        academic_lists=["AHLIA"],
        academic_list_objects=[
            AcademicList(1, "AHLIA", DEPARTMENT,
                         [Course(1, course_code, course_code, course_code, 3, 2, 4)])
        ],
        academic_levels=[2],
    )


def lecture_block(block_id, course_code, course_id, staff_member):
    assignment = CourseAssignment(
        id=1, course_id=course_id, course_code=course_code,
        lecture_groups=1, lecturers=[{"lecturer": staff_member, "num_of_groups": 1}],
    )
    return Block(
        id=block_id,
        course_codes=[course_code],
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
            AcademicList(1, "AHLIA", DEPARTMENT,
                         [Course(1, course_code, course_code, course_code, 3, 2, 4)])
        ],
        academic_levels=[2],
    )


def make_rooms(day_windows):
    labs = [
        Lab(index, f"Lab{index}", 30, day_windows, LabType.GENERAL)
        for index in (1, 2, 3, 4)
    ]
    halls = [Hall(1, "H1", 100, day_windows)]
    return labs, halls


def build_cm():
    return ConstraintManager()


def solve(blocks, labs, halls, cm, **kwargs):
    return solve_schedule(
        blocks=blocks,
        halls=halls,
        labs=labs,
        reserved_period=kwargs.pop("reserved_period", None),
        constraint_manager=cm,
        **kwargs,
    )


class ParallelPackingTests(unittest.TestCase):
    def test_two_groups_pack_into_one_period_across_two_rooms(self):
        # Audit finding #2 fixture: two groups of one lab course, two rooms
        # free 09:00-13:00. The old loop serialized them; the solver runs
        # them in parallel (same period, different rooms).
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        groups = [lab_block(f"g{i}", "IT202", ta(10 + i), i) for i in (1, 2)]

        result = solve(groups, labs, halls, build_cm())
        self.assertIn(result.status, ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(len(result.assignments), 2)
        periods = {
            (a.time_slot.day, a.time_slot.start_time)
            for a in result.assignments.values()
        }
        rooms = {a.room.id for a in result.assignments.values()}
        self.assertEqual(len(periods), 1, "groups must run in parallel")
        self.assertEqual(len(rooms), 2, "groups must use different rooms")

    def test_audit_six_group_fixture_completes(self):
        # Audit finding #4 fixture: four 25-student A groups + two 20-student
        # B groups, six distinct TAs, four 30-seat rooms, two periods. The
        # old loop reached 4/6 while 6/6 existed.
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        blocks = [lab_block(f"A{i}", "CS205A", ta(20 + i), i) for i in (1, 2, 3, 4)]
        blocks += [lab_block(f"B{i}", "CS205B", ta(30 + i), i) for i in (1, 2)]

        cm = build_cm()
        result = solve(blocks, labs, halls, cm)
        self.assertEqual(len(result.assignments), 6)
        period_load: dict = {}
        for a in result.assignments.values():
            key = (a.time_slot.day, a.time_slot.start_time)
            period_load[key] = period_load.get(key, 0) + 1
        self.assertEqual(sum(period_load.values()), 6)

    def test_lecture_displaces_two_parallel_labs(self):
        # Audit finding #3 fixture: two parallel labs hold both rooms at
        # 09:00; a single-group lecture only fits 09:00. The solver moves
        # both labs to 11:00 - something single-victim repair could not do.
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        lab_groups = [lab_block(f"P{i}", "IT202", ta(40 + i), i) for i in (1, 2)]
        # The lecture's lecturer only works 09:00-11:00 (lecturer hours are
        # binding), so the lecture has exactly one option - 09:00.
        scarce_lecturer = lecturer(
            50, [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        )
        the_lecture = lecture_block("L1", "IT202", 1, scarce_lecturer)

        cm = build_cm()
        result = solve(lab_groups + [the_lecture], labs, halls, cm)
        self.assertEqual(len(result.assignments), 3)
        lecture_assignment = result.assignments["L1"]
        lab_assignments = [a for bid, a in result.assignments.items() if bid != "L1"]
        self.assertEqual(lecture_assignment.time_slot.start_time, time(9, 0))
        for lab_assignment in lab_assignments:
            self.assertEqual(lab_assignment.time_slot.start_time, time(11, 0))
            self.assertTrue(
                lab_assignment.time_slot.day == Day.SUNDAY
            )

    def test_fixed_assignment_is_respected(self):
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        groups = [lab_block(f"g{i}", "IT202", ta(60 + i), i) for i in (1, 2)]
        pinned = Assignment(groups[0], TimePreference(Day.SUNDAY, time(9, 0), time(11, 0)), labs[1])

        result = solve(groups, labs, halls, build_cm(), fixed_assignments={"g1": pinned})
        self.assertEqual(len(result.assignments), 2)
        # The pin holds exactly.
        self.assertEqual(result.assignments["g1"].room.id, 2)
        self.assertEqual(result.assignments["g1"].time_slot.start_time, time(9, 0))
        # g2 packs into the same period in a different room (parallel groups
        # of one course are disjoint students), never on top of the pin.
        self.assertEqual(result.assignments["g2"].time_slot.start_time, time(9, 0))
        self.assertNotEqual(result.assignments["g2"].room.id, 2)

    def test_infeasible_reports_conflict_set_and_best_partial(self):
        # One room, one period, three blocks that all need it: proven
        # infeasible with a minimal conflicting set, then a best partial.
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        labs, halls = make_rooms(sunday)
        labs = labs[:1]
        blocks = [lab_block(f"g{i}", f"C{i}", ta(70 + i), 1) for i in (1, 2, 3)]

        result = solve(blocks, labs, halls, build_cm())
        self.assertEqual(result.status, "PARTIAL")
        self.assertTrue(set(result.conflicting_block_ids).issubset({"g1", "g2", "g3"}))
        self.assertEqual(len(result.conflicting_block_ids), 3)
        self.assertEqual(len(result.assignments), 1)
        self.assertEqual(len(result.skipped_block_ids), 2)

    def test_reserved_period_is_never_used(self):
        reserved = ReservedPeriod(Day.SUNDAY, time(9, 0), time(11, 0))
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        blocks = [lab_block(f"g{i}", "IT202", ta(80 + i), i) for i in (1, 2)]

        result = solve(blocks, labs, halls, build_cm(), reserved_period=reserved)
        self.assertEqual(len(result.assignments), 2)
        for a in result.assignments.values():
            self.assertEqual(a.time_slot.start_time, time(11, 0))

    def test_deterministic_same_seed_same_schedule(self):
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        blocks = [lab_block(f"g{i}", "IT202", ta(90 + i), i) for i in (1, 2, 3, 4)]

        first = solve(blocks, labs, halls, build_cm(), seed=7)
        second = solve(blocks, labs, halls, build_cm(), seed=7)

        def key(result):
            return sorted(
                (bid, a.room.id, a.time_slot.day.value,
                 a.time_slot.start_time.strftime("%H%M"))
                for bid, a in result.assignments.items()
            )

        self.assertEqual(key(first), key(second))


class GreedyFallbackTests(unittest.TestCase):
    """The fixed greedy loop must also pass the audited fixtures."""

    def _engine(self, labs, halls):
        cm = ConstraintManager()
        rm = ResourceManager(halls=halls, labs=labs, reserved_period=None)
        return SchedulingEngine(cm, rm, use_cpsat=False)

    def test_greedy_packs_parallel_groups_into_one_period(self):
        # Audit finding #2: joint candidate scoring joins an existing
        # course period, so four A groups share period 1 across four rooms.
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        blocks = [lab_block(f"A{i}", "CS205A", ta(20 + i), i) for i in (1, 2, 3, 4)]
        blocks += [lab_block(f"B{i}", "CS205B", ta(30 + i), i) for i in (1, 2)]

        engine = self._engine(labs, halls)
        assignments = engine.schedule_blocks([], {}, prebuilt_blocks=blocks)
        self.assertEqual(len(assignments), 6)
        first_period = [
            a for a in assignments.values()
            if a.time_slot.start_time == time(9, 0)
        ]
        self.assertGreaterEqual(len(first_period), 4)

    def test_greedy_two_victim_repair_places_scarce_lecture(self):
        # Audit finding #3: the lecture only fits 09:00, both rooms hold
        # parallel labs there. Bounded two-victim displacement moves both
        # labs to 11:00 - single-victim repair could not.
        sunday = [TimePreference(Day.SUNDAY, time(9, 0), time(13, 0))]
        labs, halls = make_rooms(sunday)
        scarce_lecturer = lecturer(
            50, [TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))]
        )
        the_lecture = lecture_block("L1", "IT202", 1, scarce_lecturer)

        engine = self._engine(labs, halls)
        assignments = engine.schedule_blocks([], {}, prebuilt_blocks=[the_lecture])
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments["L1"].time_slot.start_time, time(9, 0))


if __name__ == "__main__":
    unittest.main()
