import random
import unittest
from datetime import time
from types import SimpleNamespace

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.halls import Hall
from models.staff_members import AcademicDegree, Lecturer
from models.time_preferences import Day, TimePreference
from scheduler import SchedulingEngine


def lecturer(staff_id: int) -> Lecturer:
    return Lecturer(
        id=staff_id,
        name=f"Lecturer {staff_id}",
        department=Department(1, "computer science"),
        timing_preferences=[],
        academic_degree=AcademicDegree(1, "professor", "ا"),
        is_permanent=True,
    )


def course(course_id: int) -> SimpleNamespace:
    # Attribute-compatible with CourseAssignment for the paths under test.
    return SimpleNamespace(id=course_id, course_id=course_id, course_ids=None)


def block(
    block_id: str,
    staff_id: int,
    cohorts: int = 1,
    list_offset: int = 0,
    student_count: int = 10,
    group_number: int = 1,
    single_group: bool = True,
) -> Block:
    lists = [f"List{i + list_offset}" for i in range(cohorts)]
    return Block(
        id=block_id,
        course_codes=[f"C{block_id}"],
        course_objects=[course(100 + cohorts + list_offset)],
        block_type=BlockType.LECTURE,
        staff_member=lecturer(staff_id),
        student_count=student_count,
        required_room_type="hall",
        group_number=group_number,
        total_groups=1,
        is_single_group_course=single_group,
        academic_lists=lists,
        academic_list_objects=[],
        academic_levels=[1] * cohorts,
    )


def hall(hall_id: int, slots) -> Hall:
    return Hall(
        id=hall_id,
        name=f"H{hall_id}",
        capacity=100,
        availability=list(slots),
    )


def slot(day: Day, start_hour: int) -> TimePreference:
    return TimePreference(day=day, start_time=time(start_hour, 0), end_time=time(start_hour + 2, 0))


def build_engine(halls):
    constraint_manager = ConstraintManager()
    resource_manager = ResourceManager(halls=halls, labs=[], reserved_period=None)
    return SchedulingEngine(constraint_manager, resource_manager)


def placed(engine, block_id):
    assignment = engine.constraint_manager.get_all_assignments()[block_id]
    return (
        assignment.time_slot.day,
        assignment.time_slot.start_time,
        get_room_name(assignment),
    )


def get_room_name(assignment):
    return assignment.room.name


class DisplacementRepairTests(unittest.TestCase):
    def test_repair_displaces_block_and_replaces_victim(self):
        # B is too big for H2, so its only candidate is H1@9-11, held by A.
        # A is small enough to step aside into H2, where its cohort is free.
        big_hall = hall(1, [slot(Day.SUNDAY, 9)])
        small_hall = Hall(id=2, name="H2", capacity=20, availability=[slot(Day.SUNDAY, 9)])
        engine = build_engine([big_hall, small_hall])
        block_a = block("A", staff_id=1, list_offset=0, student_count=10)
        block_b = block("B", staff_id=2, list_offset=1, student_count=50)

        self.assertTrue(
            engine.constraint_manager.make_assignment(
                "A", Assignment(block_a, slot(Day.SUNDAY, 9), big_hall)
            )
        )

        failure_assignment, failure = engine._schedule_single_block(block_b)
        self.assertIsNone(failure_assignment)
        self.assertIn("No available time slots", failure.details)
        engine.failed_blocks.append(failure)

        repaired = engine._repair_failed_blocks([block_a, block_b], {})

        self.assertEqual(repaired, 1)
        self.assertEqual(engine.failed_blocks, [])
        assignments = engine.constraint_manager.get_all_assignments()
        self.assertIn("A", assignments)
        self.assertIn("B", assignments)
        # B takes the contended big-hall slot; A steps aside into H2.
        self.assertEqual(placed(engine, "B"), (Day.SUNDAY, time(9, 0), "H1"))
        self.assertEqual(placed(engine, "A"), (Day.SUNDAY, time(9, 0), "H2"))

    def test_rollback_leaves_state_untouched_when_victim_cannot_move(self):
        sunday_slot = [slot(Day.SUNDAY, 9)]
        engine = build_engine([hall(1, sunday_slot)])
        block_a = block("A", staff_id=1)
        block_b = block("B", staff_id=2)

        original = Assignment(block_a, sunday_slot[0], engine.resource_manager.halls[1])
        self.assertTrue(engine.constraint_manager.make_assignment("A", original))

        failure_assignment, failure = engine._schedule_single_block(block_b)
        self.assertIsNone(failure_assignment)
        engine.failed_blocks.append(failure)

        snapshot = dict(engine.constraint_manager.get_all_assignments())
        repaired = engine._repair_failed_blocks([block_a, block_b], {})

        self.assertEqual(repaired, 0)
        self.assertEqual(len(engine.failed_blocks), 1)
        # The failed swap must restore the victim to its exact original slot.
        restored = placed(engine, "A")
        self.assertEqual(restored, (original.time_slot.day, original.time_slot.start_time, "H1"))
        self.assertEqual(set(engine.constraint_manager.get_all_assignments()), set(snapshot))

    def test_fixed_assignments_are_never_displaced(self):
        sunday_slot = [slot(Day.SUNDAY, 9)]
        engine = build_engine([hall(1, sunday_slot)])
        block_a = block("A", staff_id=1)
        block_b = block("B", staff_id=2)

        fixed = Assignment(block_a, sunday_slot[0], engine.resource_manager.halls[1])
        self.assertTrue(engine.constraint_manager.make_assignment("A", fixed))

        failure_assignment, failure = engine._schedule_single_block(block_b)
        self.assertIsNone(failure_assignment)
        engine.failed_blocks.append(failure)

        repaired = engine._repair_failed_blocks([block_a, block_b], {"A": fixed})

        self.assertEqual(repaired, 0)
        self.assertEqual(placed(engine, "A"), (Day.SUNDAY, time(9, 0), "H1"))


class CohortPriorityTests(unittest.TestCase):
    def test_more_cohorts_sorts_earlier(self):
        engine = build_engine([])
        multi = block("multi", staff_id=1, cohorts=7, single_group=False)
        single = block("single", staff_id=2, cohorts=1, single_group=False)
        constraints = {"multi": (3, 20), "single": (3, 20)}

        ordered = engine._sort_blocks_by_priority([single, multi], constraints, random.Random(1))

        self.assertEqual([b.id for b in ordered], ["multi", "single"])

    def test_single_group_still_dominates_cohort_count(self):
        engine = build_engine([])
        multi = block("multi", staff_id=1, cohorts=7, single_group=False)
        single = block("single", staff_id=2, cohorts=1, single_group=True)
        constraints = {"multi": (3, 20), "single": (3, 20)}

        ordered = engine._sort_blocks_by_priority([multi, single], constraints, random.Random(1))

        self.assertEqual([b.id for b in ordered], ["single", "multi"])

    def test_cohort_keys_deduplicate_lists(self):
        duplicated = block("dup", staff_id=1, cohorts=2)
        duplicated.academic_lists = ["List0", "List0"]
        self.assertEqual(len(SchedulingEngine._cohort_keys(duplicated)), 1)


if __name__ == "__main__":
    unittest.main()
