import logging
import unittest
from datetime import time
from typing import Dict, List

from academic_list import Course, ai_academic_list
from constraint_manager import ConstraintManager
from halls import Hall, halls
from labs import Lab, Labs, LabType
from models import Department
from resource_manager import ResourceManager
from schedule_format import (
    format_schedule,
    generate_schedule_json,
    generate_schedule_report,
    print_schedule_statistics,
)
from schedule_validator import ScheduleValidator, ValidationLevel
from scheduler import Assignment, Block, BlockType, SchedulingEngine
from staff_members import (
    Lecturer,
    StaffMember,
    TeachingAssistant,
    assistants,
    lecturers,
)
from study_plan import StudyPlan, ai_level1_study_plan
from time_preferences import BaseAvailability, Day, TimePreference


class RealDataTestCase(unittest.TestCase):
    """Base test case class with real data setup"""

    def setUp(self):
        self.halls = halls  # Real halls from halls.py
        self.labs = Labs  # Real labs from labs.py
        self.lecturers = lecturers  # Real lecturers from staff_members.py
        self.assistants = assistants  # Real TAs from staff_members.py
        self.courses = ai_academic_list.courses  # Real courses
        self.study_plan = ai_level1_study_plan  # Real study plan


class TestConstraintManager(RealDataTestCase):
    """Test ConstraintManager with real university data"""

    def setUp(self):
        super().setUp()
        self.constraint_manager = ConstraintManager()

    def test_real_room_booking(self):
        """Test room booking with real halls"""
        # Test with real lecture hall B101
        hall = next(h for h in self.halls if h.name == "101B")
        lecturer = next(l for l in self.lecturers if l.name == "Dr. Tamer Emara")
        time_slot = TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))

        # Create test block using real course
        block = Block(
            id="TEST_CS101",
            course_code="CS101",
            block_type=BlockType.LECTURE,
            staff_member=lecturer,
            student_count=40,  # From study plan expected students
            required_room_type="hall",
            group_number=1,
            total_groups=1,
            is_single_group_course=True,
            academic_list=self.study_plan.academic_list.name,
            academic_level=1,
        )

        # First assignment should be valid
        is_valid, message = self.constraint_manager.check_all_constraints(
            block, time_slot, hall, {}
        )
        self.assertTrue(is_valid, f"Initial assignment failed: {message}")

        # Second assignment at same time should fail
        assignments = {"TEST_CS101": Assignment(block, time_slot, hall)}
        is_valid, message = self.constraint_manager.check_all_constraints(
            block, time_slot, hall, assignments
        )
        self.assertFalse(is_valid, "Double booking was not detected")

    def test_real_lab_requirements(self):
        """Test lab requirements with real labs"""
        # Test with real lab L401 (specialist lab)
        specialist_lab = next(l for l in self.labs if l.name == "401")
        ta = next(a for a in self.assistants if a.name == "Eng. Ibrahim El Gazar")
        time_slot = TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))

        # Create test block for lab session
        block = Block(
            id="TEST_BS102_LAB",
            course_code="BS102",  # Discrete Structures lab
            block_type=BlockType.LAB,
            staff_member=ta,
            student_count=20,
            required_room_type="lab",
            group_number=1,
            total_groups=2,
            is_single_group_course=False,
            academic_list=self.study_plan.academic_list.name,
            academic_level=1,
            preferred_rooms=[specialist_lab],
        )

        # Should be valid for preferred lab
        is_valid, message = self.constraint_manager.check_all_constraints(
            block, time_slot, specialist_lab, {}
        )
        self.assertTrue(is_valid, f"Lab assignment to preferred lab failed: {message}")

        # Try with non-preferred lab
        general_lab = next(l for l in self.labs if l.lab_type == LabType.GENERAL)
        is_valid, message = self.constraint_manager.check_all_constraints(
            block, time_slot, general_lab, {}
        )
        self.assertFalse(is_valid, "Assignment to non-preferred lab was allowed")

    def test_real_staff_conflicts(self):
        """Test staff scheduling conflicts with real staff members"""
        lecturer = next(l for l in self.lecturers if l.name == "Dr. Mona El Bedwehy")
        time_slot = TimePreference(Day.SUNDAY, time(9, 0), time(11, 0))
        hall = self.halls[0]

        # Create two blocks for same lecturer
        block1 = Block(
            id="TEST_BLOCK1",
            course_code="BS104",  # Stats course from real data
            block_type=BlockType.LECTURE,
            staff_member=lecturer,
            student_count=40,
            required_room_type="hall",
            group_number=1,
            total_groups=1,
            is_single_group_course=True,
            academic_list=self.study_plan.academic_list.name,
            academic_level=1,
        )

        # First assignment should work
        is_valid, message = self.constraint_manager.check_all_constraints(
            block1, time_slot, hall, {}
        )
        self.assertTrue(is_valid, f"Initial lecturer assignment failed: {message}")

        # Second assignment at same time should fail
        assignments = {"TEST_BLOCK1": Assignment(block1, time_slot, hall)}
        is_valid, message = self.constraint_manager.check_all_constraints(
            block1, time_slot, self.halls[1], assignments
        )
        self.assertFalse(is_valid, "Lecturer double booking was not detected")


class TestResourceManager(RealDataTestCase):
    """Test ResourceManager with real university data"""

    def setUp(self):
        super().setUp()
        self.resource_manager = ResourceManager(self.halls, self.labs)

    def test_real_lab_allocation(self):
        """Test lab allocation with real labs and courses"""
        # Test allocation for BS102 (Discrete Structures) lab sessions
        course_assignment = next(
            ca for ca in self.study_plan.course_assignments if ca.course_code == "BS102"
        )
        block = Block(
            id="TEST_BS102_LAB",
            course_code="BS102",
            block_type=BlockType.LAB,
            staff_member=course_assignment.teaching_assistants[0]["teaching_assistant"],
            student_count=self.study_plan.expected_students
            // course_assignment.lab_groups,
            required_room_type="lab",
            group_number=1,
            total_groups=course_assignment.lab_groups,
            is_single_group_course=False,
            academic_list=self.study_plan.academic_list.name,
            academic_level=1,
        )

        suitable_rooms = self.resource_manager.get_suitable_rooms(block)
        self.assertTrue(len(suitable_rooms) > 0, "No suitable labs found")
        self.assertTrue(
            all(isinstance(room, Lab) for room in suitable_rooms),
            "Non-lab rooms suggested for lab session",
        )
        self.assertTrue(
            all(
                room.capacity >= (block.student_count * 0.8) for room in suitable_rooms
            ),
            "Suggested labs have insufficient capacity",
        )

    def test_real_time_slot_availability(self):
        """Test time slot generation with real staff preferences"""
        lecturer = next(l for l in self.lecturers if l.name == "Dr. Tamer Emara")
        hall = self.halls[0]

        # Get available slots for a lecture
        block = Block(
            id="TEST_SLOT",
            course_code="CS101",
            block_type=BlockType.LECTURE,
            staff_member=lecturer,
            student_count=40,
            required_room_type="hall",
            group_number=1,
            total_groups=1,
            is_single_group_course=True,
            academic_list=self.study_plan.academic_list.name,
            academic_level=1,
        )

        available_slots = self.resource_manager.get_available_slots(block, hall, {})

        # Verify slots match lecturer's preferences
        self.assertTrue(len(available_slots) > 0, "No available time slots found")
        for slot in available_slots:
            self.assertTrue(
                any(
                    pref.day == slot.day
                    and pref.start_time <= slot.start_time
                    and pref.end_time >= slot.end_time
                    for pref in lecturer.timing_preferences
                ),
                f"Suggested slot {slot} not in lecturer preferences",
            )


class TestSchedulingEngine(RealDataTestCase):
    """Test SchedulingEngine with real university data"""

    def setUp(self):
        super().setUp()
        self.constraint_manager = ConstraintManager()
        self.resource_manager = ResourceManager(self.halls, self.labs)
        self.scheduling_engine = SchedulingEngine(
            self.constraint_manager, self.resource_manager
        )

    def test_real_study_plan_scheduling(self):
        """Test scheduling of real study plan"""
        try:
            # Generate schedule
            assignments = self.scheduling_engine.schedule_blocks(
                self.study_plan.course_assignments, self.study_plan
            )

            # Print formatted schedule
            print("\n" + "=" * 100)
            print("GENERATED SCHEDULE")
            print("=" * 100)
            print(format_schedule(assignments))

            # Print statistics
            print_schedule_statistics(assignments)

            # Generate detailed report
            generate_schedule_report(assignments, "test_schedule_report.txt")

            # Generate JSON report
            generate_schedule_json(assignments, "test_schedule.json")

            # Verify all courses are scheduled
            scheduled_courses = set(
                assignment.block.course_code for assignment in assignments.values()
            )
            required_courses = set(
                ca.course_code for ca in self.study_plan.course_assignments
            )
            self.assertEqual(
                scheduled_courses, required_courses, "Not all courses were scheduled"
            )

            # Verify room capacities
            for assignment in assignments.values():
                self.assertGreaterEqual(
                    assignment.room.capacity,
                    assignment.block.student_count * 0.8,
                    f"Room {assignment.room.name} too small for {assignment.block.course_code}",
                )

            # Verify no staff conflicts
            staff_bookings = {}  # (staff_id, day, time) -> course_code
            for assignment in assignments.values():
                key = (
                    assignment.block.staff_member.id,
                    assignment.time_slot.day,
                    assignment.time_slot.start_time,
                )
                self.assertNotIn(
                    key,
                    staff_bookings,
                    f"Staff conflict found: {assignment.block.staff_member.name} at {assignment.time_slot}",
                )
                staff_bookings[key] = assignment.block.course_code

        except ValueError as e:
            self.fail(f"Schedule generation failed: {e}")


class TestScheduleValidator(RealDataTestCase):
    """Test ScheduleValidator with real university data"""

    def setUp(self):
        super().setUp()
        self.validator = ScheduleValidator()

    def test_real_study_plan_validation(self):
        """Test validation of real study plan"""
        messages = self.validator.validate_input_data([self.study_plan])

        # There should be no errors in our real data
        errors = [m for m in messages if m.level == ValidationLevel.ERROR]
        self.assertEqual(len(errors), 0, f"Validation errors found: {errors}")


def run_real_data_tests():
    """Run all tests with real university data"""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        filename="scheduler_real_tests.log",
    )

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestConstraintManager))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceManager))
    suite.addTests(loader.loadTestsFromTestCase(TestSchedulingEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestScheduleValidator))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    logging.info(f"Tests Run: {result.testsRun}")
    logging.info(f"Failures: {len(result.failures)}")
    logging.info(f"Errors: {len(result.errors)}")

    return result


if __name__ == "__main__":
    run_real_data_tests()
