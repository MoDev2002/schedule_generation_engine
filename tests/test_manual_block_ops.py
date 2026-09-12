# tests/test_manual_block_ops.py
"""Manual add/delete block flows on top of the change validator.

The scan (/pending-blocks) splits reconcile leftovers into pending blocks
("missing") and stale entry ids. Adding a pending block validates against the
cached state as-is (nothing is unseeded first); removing an entry unseeds it
and returns its block to the pending list, so a deleted block re-appears in
the scan. Add soft warnings are shape-scoped: only codes that apply to the
block warn, and only at their worst bucket.
"""

import unittest
from datetime import datetime, time

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList, Course
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import Day, TimePreference
from services.change_validator import (
    LoadedSchedule,
    ScheduleStateCache,
    block_identity_key,
    run_validation,
    apply_change,
    serialize_pending_block,
)

DEPARTMENT = Department(1, "Computer Science")
LECTURER_DEGREE = AcademicDegree(3, "Lecturer", "Dr.")
TA_DEGREE = AcademicDegree(4, "Teaching Assistant", "TA.")

SUNDAY = TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(17, 0))
SUNDAY_9 = TimePreference(day=Day.SUNDAY, start_time=time(9, 0), end_time=time(11, 0))
SUNDAY_11 = TimePreference(day=Day.SUNDAY, start_time=time(11, 0), end_time=time(13, 0))


def lecturer(staff_id, prefs=(SUNDAY,)):
    return Lecturer(staff_id, f"Dr. {staff_id}", DEPARTMENT, list(prefs), LECTURER_DEGREE, True)


def ta(staff_id, prefs=(SUNDAY,)):
    return TeachingAssistant(staff_id, f"TA {staff_id}", DEPARTMENT, list(prefs), TA_DEGREE, False)


def make_list():
    return AcademicList(
        5, "AHLIA", DEPARTMENT,
        [Course(1920, "IT202", "IT202", "IT202", 3, 2, 4)],
    )


def lab_block(block_id, group_number=1, staff_member=None, student_count=25,
              total_groups=5):
    return Block(
        id=block_id,
        course_codes=["IT202"],
        course_objects=[CourseAssignment(
            id=1, course_id=1920, course_code="IT202",
            lecture_groups=1,
            lecturers=[{"lecturer": lecturer(700), "num_of_groups": 1}],
            lab_groups=total_groups,
            teaching_assistants=[
                {"teaching_assistant": staff_member or ta(7), "num_of_groups": total_groups}
            ],
        )],
        block_type=BlockType.LAB,
        staff_member=staff_member or ta(7),
        student_count=student_count,
        required_room_type="lab",
        group_number=group_number,
        total_groups=total_groups,
        is_single_group_course=False,
        academic_lists=["AHLIA"],
        academic_list_objects=[make_list()],
        academic_levels=[2],
    )


def external_block(block_id, external_course_id=9, staff_member=None,
                   requires_room=True, session_hours=2, allowed_slots=None):
    return Block(
        id=block_id,
        course_codes=[],
        course_objects=[],
        block_type=BlockType.LECTURE,
        staff_member=staff_member,
        student_count=40,
        required_room_type="hall",
        group_number=1,
        total_groups=1,
        is_single_group_course=True,
        academic_lists=[],
        academic_list_objects=[],
        academic_levels=[],
        source_kind="external",
        external_course_id=external_course_id,
        external_course_name="External Course",
        external_course_code="EX9",
        requires_room=requires_room,
        allowed_slots=allowed_slots,
        session_hours=session_hours,
    )


def make_rooms():
    return (
        [Hall(1, "H1", 120, [SUNDAY]), Hall(2, "H2", 100, [SUNDAY])],
        [Lab(1, "Lab1", 30, [SUNDAY], LabType.GENERAL),
         Lab(2, "Lab2", 30, [SUNDAY], LabType.GENERAL)],
    )


def make_loaded(seeded=(), pending=(), extra_entry_ids=(), version="2026-01-01T00:00:00Z"):
    """A LoadedSchedule assembled by hand - no Laravel fetches."""
    halls_list, labs_list = make_rooms()
    rm = ResourceManager(halls_list, labs_list, None)
    cm = ConstraintManager()
    entry_block_ids, entry_room_types = {}, {}
    for block_id, block, slot, room in seeded:
        assert cm.seed_fixed_assignment(block_id, Assignment(block, slot, room))
        entry_id = int(block_id.rsplit("_", 1)[1])
        entry_block_ids[entry_id] = block_id
        entry_room_types[entry_id] = (
            "lab" if block.block_type == BlockType.LAB else "hall"
        )
    return LoadedSchedule(
        schedule_id=1,
        state_version=version,
        constraint_manager=cm,
        resource_manager=rm,
        halls={h.id: h for h in halls_list},
        labs={lab.id: lab for lab in labs_list},
        entry_block_ids=entry_block_ids,
        entry_room_types=entry_room_types,
        loaded_at=datetime.now(),
        pending_blocks={block_identity_key(b): b for b in pending},
        stale_entry_ids=[],
        all_entry_ids=set(entry_block_ids) | set(extra_entry_ids),
    )


def targets_for(cached, block_key):
    response = run_validation(cached, {"type": "add_targets", "block_key": block_key})
    assert response["ok"]
    return response["targets"]


def slot_target(targets, day, start):
    return next(t for t in targets if t["day"] == day and t["start_time"] == start)


class IdentityKeyAndSerializerTests(unittest.TestCase):
    def test_key_is_deterministic(self):
        block = lab_block("p1")
        self.assertEqual(block_identity_key(block), block_identity_key(block))

    def test_key_survives_rebuild(self):
        # Two structurally identical blocks (fresh objects, same fields)
        # produce the same key - the frontend can echo it across reloads.
        self.assertEqual(
            block_identity_key(lab_block("p1")), block_identity_key(lab_block("p1"))
        )

    def test_different_group_different_key(self):
        self.assertNotEqual(
            block_identity_key(lab_block("p1", group_number=1)),
            block_identity_key(lab_block("p2", group_number=2)),
        )

    def test_serialize_course_block(self):
        payload = serialize_pending_block(lab_block("p1", student_count=25))
        self.assertEqual(payload["entry_kind"], "course")
        self.assertEqual(payload["course_ids"], [1920])
        self.assertEqual(payload["session_type"], "lab")
        self.assertEqual(payload["group_number"], 1)
        self.assertEqual(payload["total_groups"], 5)
        self.assertEqual(payload["student_count"], 25)
        self.assertEqual(payload["academic_ids"], [5])
        self.assertEqual(payload["academic_levels"], [2])
        self.assertEqual(payload["department_ids"], [DEPARTMENT.id])
        self.assertEqual(payload["academic_list_names"], ["AHLIA"])
        self.assertTrue(payload["requires_room"])
        self.assertEqual(payload["session_hours"], 2)
        self.assertIn("key", payload)

    def test_serialize_external_block(self):
        payload = serialize_pending_block(
            external_block("e1", staff_member=lecturer(700))
        )
        self.assertEqual(payload["entry_kind"], "external")
        self.assertEqual(payload["external_course_id"], 9)
        self.assertEqual(payload["external_course_name"], "External Course")
        self.assertEqual(payload["course_ids"], [])
        self.assertEqual(payload["academic_ids"], [])
        self.assertEqual(payload["session_type"], "lecture")
        self.assertEqual(payload["lecturer_id"], 700)
        self.assertTrue(payload["requires_room"])


class AddTargetsTests(unittest.TestCase):
    def setUp(self):
        self.pending = lab_block("p1")
        self.cached = make_loaded(
            seeded=[("source_entry_11", lab_block("s1"), SUNDAY_9, make_rooms()[1][0])],
            pending=[self.pending],
        )
        self.key = block_identity_key(self.pending)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            run_validation(self.cached, {"type": "add_targets", "block_key": "nope"})

    def test_every_grid_slot_reported(self):
        targets = targets_for(self.cached, self.key)
        # 5 days x 5 2h slots for a 2h block.
        self.assertEqual(len(targets), 25)

    def test_occupied_room_slot_not_viable_free_slot_is(self):
        # The seeded entry is group 2 with its own TA - a different student
        # section and different staff, so the only clash with the pending
        # group-1 block is the shared room.
        cached = make_loaded(
            seeded=[("source_entry_11",
                     lab_block("s1", group_number=2, staff_member=ta(8)),
                     SUNDAY_9, make_rooms()[1][0])],
            pending=[self.pending],
        )
        targets = targets_for(cached, self.key)
        busy = slot_target(targets, "sunday", "09:00")
        lab1 = next(r for r in busy["rooms"] if r["id"] == 1)
        self.assertFalse(lab1["viable"])
        self.assertIn("ROOM_CONFLICT", lab1["hard_conflicts"])
        # Lab 2 is free in the same period.
        lab2 = next(r for r in busy["rooms"] if r["id"] == 2)
        self.assertTrue(lab2["viable"])
        self.assertTrue(busy["viable"])

        free = slot_target(targets, "sunday", "11:00")
        self.assertTrue(free["viable"])

    def test_staff_clash_marks_room_non_viable(self):
        # The pending block's TA is already teaching the seeded entry.
        busy_ta = self.cached.constraint_manager.current_assignments[
            "source_entry_11"
        ].block.staff_member
        pending = lab_block("p1", staff_member=busy_ta)
        cached = make_loaded(
            seeded=[("source_entry_11", lab_block("s1"), SUNDAY_9, make_rooms()[1][0])],
            pending=[pending],
        )
        targets = targets_for(cached, block_identity_key(pending))
        busy = slot_target(targets, "sunday", "09:00")
        for room in busy["rooms"]:
            self.assertFalse(room["viable"])
            self.assertIn("STAFF_CONFLICT", room["hard_conflicts"])

    def test_fixed_slots_outside_set_not_viable(self):
        pending = external_block(
            "e1", allowed_slots=[SUNDAY_11], requires_room=True
        )
        cached = make_loaded(pending=[pending])
        targets = targets_for(cached, block_identity_key(pending))
        outside = slot_target(targets, "sunday", "09:00")
        self.assertFalse(outside["viable"])
        for room in outside["rooms"]:
            self.assertIn("FIXED_SLOT_CONFLICT", room["hard_conflicts"])
        inside = slot_target(targets, "sunday", "11:00")
        self.assertTrue(inside["viable"])

    def test_roomless_external_has_no_room_dimension(self):
        pending = external_block("e1", requires_room=False)
        cached = make_loaded(pending=[pending])
        targets = targets_for(cached, block_identity_key(pending))
        self.assertEqual(len(targets), 25)
        for target in targets:
            self.assertEqual(target["rooms"], [])
        self.assertTrue(slot_target(targets, "monday", "09:00")["viable"])

    def test_one_hour_block_targets_half_slots(self):
        pending = external_block("e1", requires_room=False, session_hours=1)
        cached = make_loaded(pending=[pending])
        targets = targets_for(cached, block_identity_key(pending))
        starts = sorted(t["start_time"] for t in targets if t["day"] == "sunday")
        self.assertEqual(
            starts, ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00",
                     "15:00", "16:00", "17:00", "18:00"]
        )

    def test_lab_block_pool_labs_only(self):
        targets = targets_for(self.cached, self.key)
        every_room = {room["id"] for target in targets for room in target["rooms"]}
        self.assertEqual(every_room, {1, 2})


class AddValidationTests(unittest.TestCase):
    def setUp(self):
        self.pending = lab_block("p1")
        self.key = block_identity_key(self.pending)
        self.cached = make_loaded(pending=[self.pending])

    def add_change(self, day="sunday", start="11:00", end="13:00", lab=2, key=None):
        return {
            "type": "add",
            "block_key": key or self.key,
            "target": {"day": day, "start_time": start, "end_time": end},
            "room": {"type": "lab", "id": lab},
        }

    def test_clean_placement_passes_without_warnings(self):
        result = run_validation(self.cached, self.add_change())
        self.assertTrue(result["ok"])
        self.assertEqual(result["hard_conflicts"], [])
        self.assertEqual(result["soft_deltas"], [])

    def test_room_required(self):
        with self.assertRaises(ValueError):
            run_validation(
                self.cached,
                {
                    "type": "add",
                    "block_key": self.key,
                    "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
                },
            )

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            run_validation(self.cached, self.add_change(key="nope"))

    def test_off_grid_slot_rejected(self):
        with self.assertRaises(ValueError):
            run_validation(
                self.cached,
                self.add_change(start="12:00", end="14:00"),
            )

    def test_hard_conflict_on_occupied_room(self):
        hall_block = lab_block("s1")
        cached = make_loaded(
            seeded=[("source_entry_11", hall_block, SUNDAY_11, make_rooms()[1][1])]
        )
        # A different pending block, same room and period.
        other = lab_block("p9")
        cached.pending_blocks[block_identity_key(other)] = other
        result = run_validation(
            cached, self.add_change(lab=2, key=block_identity_key(other))
        )
        self.assertFalse(result["ok"])
        self.assertIn(
            "ROOM_CONFLICT", [c["code"] for c in result["hard_conflicts"]]
        )

    def test_capacity_overflow_warns_but_passes(self):
        big = lab_block("p1", student_count=150)
        cached = make_loaded(pending=[big])
        result = run_validation(
            cached,
            {
                "type": "add",
                "block_key": block_identity_key(big),
                "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
                "room": {"type": "lab", "id": 2},
            },
        )
        self.assertTrue(result["ok"])
        capacity = [
            d for d in result["soft_deltas"] if d["code"] == "ROOM_CAPACITY"
        ]
        self.assertEqual(len(capacity), 1)
        self.assertIn("seats 30", capacity[0]["message"])

    def test_preference_warnings_are_shape_scoped(self):
        # TA-staffed block with no timing preferences: the TA code warns,
        # the lecturer code is structural and must not.
        no_prefs = lab_block("p1", staff_member=ta(8, prefs=()))
        cached = make_loaded(pending=[no_prefs])
        result = run_validation(
            cached,
            {
                "type": "add",
                "block_key": block_identity_key(no_prefs),
                "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
                "room": {"type": "lab", "id": 2},
            },
        )
        codes = [d["code"] for d in result["soft_deltas"]]
        self.assertIn("TA_PREFERENCE", codes)
        self.assertNotIn("LECTURER_PREFERENCE", codes)

    def test_staff_blocker_conflict_is_hard(self):
        from models.time_preferences import StaffBlocker

        blocked_ta = ta(8, prefs=(SUNDAY,))
        blocked_ta.blockers = [
            StaffBlocker(
                day=Day.SUNDAY,
                start_time=time(11, 0),
                end_time=time(13, 0),
                label="Meeting",
            )
        ]
        blocked = lab_block("p1", staff_member=blocked_ta)
        cached = make_loaded(pending=[blocked])
        result = run_validation(
            cached,
            {
                "type": "add",
                "block_key": block_identity_key(blocked),
                "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
                "room": {"type": "lab", "id": 2},
            },
        )
        self.assertFalse(result["ok"])
        self.assertIn(
            "STAFF_BLOCKER_CONFLICT", [c["code"] for c in result["hard_conflicts"]]
        )


class RemoveValidationTests(unittest.TestCase):
    def test_seeded_entry_ok(self):
        cached = make_loaded(
            seeded=[("source_entry_11", lab_block("s1"), SUNDAY_9, make_rooms()[1][0])]
        )
        result = run_validation(cached, {"type": "remove", "entry_id": 11})
        self.assertTrue(result["ok"])

    def test_unrebuildable_entry_ok(self):
        # In the DB (all_entry_ids) but failed to rebuild - deletion is the
        # only repair, so it must validate.
        cached = make_loaded(
            seeded=[("source_entry_11", lab_block("s1"), SUNDAY_9, make_rooms()[1][0])],
            extra_entry_ids=[77],
        )
        result = run_validation(cached, {"type": "remove", "entry_id": 77})
        self.assertTrue(result["ok"])

    def test_unknown_entry_raises(self):
        cached = make_loaded()
        with self.assertRaises(KeyError):
            run_validation(cached, {"type": "remove", "entry_id": 999})

    def test_missing_entry_id_raises(self):
        cached = make_loaded()
        with self.assertRaises(ValueError):
            run_validation(cached, {"type": "remove"})


class ApplyChangeTests(unittest.TestCase):
    def setUp(self):
        self.pending = lab_block("p1")
        self.key = block_identity_key(self.pending)
        self.cached = make_loaded(pending=[self.pending])
        self.cache = ScheduleStateCache()
        self.cache._entries[1] = self.cached
        self.add = {
            "type": "add",
            "block_key": self.key,
            "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
            "room": {"type": "lab", "id": 2},
            "new_entry_id": 55,
        }

    def test_add_commit_seeds_and_depletes_pending(self):
        self.assertTrue(
            apply_change(self.cache, 1, self.cached.state_version, self.add, "v2")
        )
        self.assertNotIn(self.key, self.cached.pending_blocks)
        self.assertEqual(self.cached.entry_block_ids[55], "source_entry_55")
        self.assertEqual(self.cached.entry_room_types[55], "lab")
        self.assertIn(55, self.cached.all_entry_ids)
        seeded = self.cached.constraint_manager.current_assignments["source_entry_55"]
        self.assertEqual(seeded.time_slot.start_time, time(11, 0))
        self.assertEqual(seeded.room.id, 2)

    def test_add_unknown_key_fails(self):
        bad = dict(self.add, block_key="nope")
        self.assertFalse(
            apply_change(self.cache, 1, self.cached.state_version, bad, "v2")
        )

    def test_remove_commit_returns_block_to_pending(self):
        apply_change(self.cache, 1, self.cached.state_version, self.add, "v2")
        self.assertTrue(
            apply_change(
                self.cache,
                1,
                self.cached.state_version,
                {"type": "remove", "entry_id": 55},
                "v3",
            )
        )
        self.assertIn(self.key, self.cached.pending_blocks)
        self.assertNotIn(55, self.cached.entry_block_ids)
        self.assertNotIn(55, self.cached.all_entry_ids)
        # The freed placement validates clean again.
        result = run_validation(
            self.cached,
            {
                "type": "add",
                "block_key": self.key,
                "target": {"day": "sunday", "start_time": "11:00", "end_time": "13:00"},
                "room": {"type": "lab", "id": 2},
            },
        )
        self.assertTrue(result["ok"])

    def test_remove_unrebuildable_entry_is_noop_true(self):
        self.cached.all_entry_ids.add(77)
        self.assertTrue(
            apply_change(
                self.cache,
                1,
                self.cached.state_version,
                {"type": "remove", "entry_id": 77},
                "v2",
            )
        )
        self.assertNotIn(77, self.cached.all_entry_ids)

    def test_round_trip_scan_view(self):
        """Scan -> add -> remove -> scan sees the same pending block again."""
        before = run_validation(
            self.cached, {"type": "add_targets", "block_key": self.key}
        )
        apply_change(self.cache, 1, self.cached.state_version, self.add, "v2")
        with self.assertRaises(KeyError):
            run_validation(self.cached, {"type": "add_targets", "block_key": self.key})
        apply_change(
            self.cache,
            1,
            self.cached.state_version,
            {"type": "remove", "entry_id": 55},
            "v3",
        )
        after = run_validation(
            self.cached, {"type": "add_targets", "block_key": self.key}
        )
        self.assertEqual(len(before["targets"]), len(after["targets"]))


if __name__ == "__main__":
    unittest.main()
