import unittest
from types import SimpleNamespace

from scheduler import BlockType, SchedulingEngine


def course_assignment(assignment_id: int, course_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=assignment_id, course_id=course_id)


def academic_list(academic_list_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=academic_list_id)


class BlockIdentityTests(unittest.TestCase):
    def test_same_code_staff_and_group_are_distinct_for_different_curricula(self):
        df_cs205 = SchedulingEngine._build_block_id(
            BlockType.LECTURE,
            "CS205",
            [course_assignment(582, 1805)],
            [academic_list(104)],
            [2],
            1,
        )
        common_cs205 = SchedulingEngine._build_block_id(
            BlockType.LECTURE,
            "CS205",
            [
                course_assignment(600, 1650),
                course_assignment(600, 1651),
                course_assignment(600, 1652),
            ],
            [academic_list(105), academic_list(107), academic_list(109)],
            [2, 2, 2],
            1,
        )

        self.assertNotEqual(df_cs205, common_cs205)

    def test_identity_is_independent_of_curriculum_input_order(self):
        forward = SchedulingEngine._build_block_id(
            BlockType.LECTURE,
            "CS205",
            [course_assignment(600, 1650), course_assignment(600, 1651)],
            [academic_list(105), academic_list(107)],
            [2, 2],
            1,
        )
        reversed_order = SchedulingEngine._build_block_id(
            BlockType.LECTURE,
            "CS205",
            [course_assignment(600, 1651), course_assignment(600, 1650)],
            [academic_list(107), academic_list(105)],
            [2, 2],
            1,
        )

        self.assertEqual(forward, reversed_order)

    def test_different_session_types_have_distinct_ids(self):
        course = [course_assignment(600, 1650)]
        academic_lists = [academic_list(105)]
        lecture_id = SchedulingEngine._build_block_id(
            BlockType.LECTURE, "CS205", course, academic_lists, [2], 1
        )
        lab_id = SchedulingEngine._build_block_id(
            BlockType.LAB, "CS205", course, academic_lists, [2], 1
        )

        self.assertNotEqual(lecture_id, lab_id)

    def test_preflight_rejects_a_true_duplicate_session(self):
        duplicate = SimpleNamespace(
            id="L_CS205_duplicate_1",
            academic_lists=["[DF] Cybersecurity 2024"],
            academic_levels=[2],
            group_number=1,
            get_all_course_ids=lambda: {1805},
        )
        engine = SchedulingEngine(None, None)

        with self.assertRaisesRegex(
            ValueError, "Duplicate curriculum session blocks"
        ):
            engine._validate_unique_block_ids([duplicate, duplicate])


if __name__ == "__main__":
    unittest.main()
