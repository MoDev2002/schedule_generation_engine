"""Offline audit probes; no API calls and no production scheduling changes.

Run from the engine directory with Python 3.11+. These assertions describe
observed defects, not regression tests for the desired corrected behavior.
"""

import json
import logging
import sys
from collections import Counter
from datetime import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.block import Assignment, Block, BlockType
from models.department import Department
from models.halls import Hall
from models.labs import Lab, LabType
from models.staff_members import AcademicDegree, Lecturer, TeachingAssistant
from models.time_preferences import Day, TimePreference
from schedule_validator import ScheduleValidator
from scheduler import SchedulingEngine

logging.disable(logging.CRITICAL)


def slot(hour, day=Day.SUNDAY, end=None):
    return TimePreference(day, time(hour), time(end or hour + 2))


def staff(identifier, preferences=None, lecture=False):
    cls = Lecturer if lecture else TeachingAssistant
    return cls(
        identifier, f"Staff {identifier}", Department(1, "CS"),
        preferences or [], AcademicDegree(1 if lecture else 5, "staff", ""), True,
    )


def lab(identifier, windows, capacity=30):
    return Lab(identifier, f"Lab {identifier}", capacity, windows, LabType.GENERAL)


def block(identifier, course=1, group=1, total=2, teacher=None, cohorts=None,
          students=20, lecture=False):
    cohorts = cohorts or ["Cohort"]
    return Block(
        id=identifier, course_codes=[f"C{course}"],
        course_objects=[SimpleNamespace(id=course, course_id=course, course_ids=None)],
        block_type=BlockType.LECTURE if lecture else BlockType.LAB,
        staff_member=teacher or staff(group), student_count=students,
        required_room_type="hall" if lecture else "lab", group_number=group,
        total_groups=total, is_single_group_course=total == 1,
        academic_lists=cohorts, academic_list_objects=[],
        academic_levels=[1] * len(cohorts),
    )


def engine(labs=None, halls=None):
    return SchedulingEngine(ConstraintManager(), ResourceManager(halls or [], labs or [], None))


def placements(assignments):
    return {
        key: [a.room.name, a.time_slot.day.name, str(a.time_slot.start_time)]
        for key, a in sorted(assignments.items())
    }


def commit(cm, assignments):
    for key, a in assignments.items():
        assert cm.make_assignment(key, a), key
    valid, conflicts = ScheduleValidator().validate_schedule_comprehensive(assignments)
    assert valid, conflicts


def serial_labs_and_early_stop():
    rooms = [lab(1, [slot(9, end=13)]), lab(2, [slot(9, end=13)])]
    blocks = [block("A1", group=1), block("A2", group=2)]
    eng = engine(rooms)
    callbacks = []
    actual = eng.schedule_blocks([], {}, prebuilt_blocks=blocks, max_attempts=100,
                                 progress_callback=lambda *args: callbacks.append(args))
    assert len({a.time_slot.start_time for a in actual.values()}) == 2
    witness = {b.id: Assignment(b, slot(9), rooms[i]) for i, b in enumerate(blocks)}
    commit(ConstraintManager(), witness)
    assert callbacks[-1] == (2, 2, "completed", 1)
    return {"actual": placements(actual), "feasible_parallel": placements(witness),
            "raw_score": eng._evaluate_schedule(actual), "attempts_used": 1}


def ta_preference_discarded():
    room = lab(1, [slot(9), slot(11)])
    b = block("TA", teacher=staff(1, [slot(11)]))
    eng = engine([room])
    candidates = eng.resource_manager.get_available_slots(b, room, {})
    chosen, _ = eng._schedule_single_block(b)
    assert chosen.time_slot.start_time == time(9)
    assert eng.constraint_manager.can_assign(b, slot(11), room)[0]
    return {"preferred": "11:00", "chosen": str(chosen.time_slot.start_time),
            "candidate_order": [str(s.start_time) for s in candidates]}


def repair_cannot_move_parallel_bundle():
    rooms = [lab(1, [slot(9), slot(11)]), lab(2, [slot(9), slot(11)])]
    hall = Hall(1, "Lecture hall", 100, [slot(9)])
    eng = engine(rooms, [hall])
    a1, a2 = block("A1", group=1), block("A2", group=2)
    lecture = block("B", course=2, total=1, teacher=staff(3, lecture=True), lecture=True)
    original = {"A1": Assignment(a1, slot(9), rooms[0]),
                "A2": Assignment(a2, slot(9), rooms[1])}
    commit(eng.constraint_manager, original)
    assert eng._conflict_causes(lecture, slot(9), hall, original) == {"A1", "A2"}
    repaired = eng._try_displacement_repair(lecture, {})
    assert not repaired
    assert placements(eng.constraint_manager.get_all_assignments()) == placements(original)
    witness = {"A1": Assignment(a1, slot(11), rooms[0]),
               "A2": Assignment(a2, slot(11), rooms[1]),
               "B": Assignment(lecture, slot(9), hall)}
    commit(ConstraintManager(), witness)
    return {"repair_succeeded": repaired, "conflicting_blocks": 2,
            "feasible_complete_schedule": placements(witness)}


def retries_repeat_and_miss_feasible_schedule():
    # Four A groups serialize into two rooms over two times, occupying their
    # cohort twice at each time. Placing B now requires moving two A groups.
    rooms = [lab(i, [slot(9), slot(11)]) for i in range(1, 5)]
    blocks = [block(f"A{i}", course=1, group=i, total=4, students=25)
              for i in range(1, 5)]
    blocks += [block("B1", course=2, group=1, students=20, teacher=staff(5)),
               block("B2", course=2, group=2, students=20, teacher=staff(6))]
    eng = engine(rooms)
    orders = []
    original_sort = eng._sort_blocks_by_priority

    def capture_order(*args, **kwargs):
        result = original_sort(*args, **kwargs)
        orders.append(tuple(b.id for b in result))
        return result

    eng._sort_blocks_by_priority = capture_order
    actual = eng.schedule_blocks([], {}, prebuilt_blocks=blocks, max_attempts=100)
    witness = {b.id: Assignment(b, slot(9 if i < 4 else 11), rooms[i % 4])
               for i, b in enumerate(blocks)}
    commit(ConstraintManager(), witness)
    course_orders = {tuple(identifier[0] for identifier in order) for order in orders}
    assert len(actual) == 4 and len(orders) == 100 and len(course_orders) == 1
    return {"scheduled": len(actual), "expected": len(blocks),
            "attempts": len(orders), "distinct_orders": len(set(orders)),
            "distinct_course_orders": len(course_orders),
            "actual": placements(actual), "feasible": placements(witness)}


def gap_score_measures_span():
    room = lab(1, [slot(9, end=17)])
    cm = ConstraintManager()
    blocks = [block(f"G{i}", course=i, total=1, teacher=staff(i)) for i in range(1, 4)]
    assignments = {b.id: Assignment(b, slot(9 + 2 * i), room) for i, b in enumerate(blocks)}
    commit(cm, assignments)
    score = cm.evaluate_gaps(blocks[0], slot(9), room)
    assert score == 0.5
    return {"sessions": ["09-11", "11-13", "13-15"], "actual_idle_minutes": 0,
            "gap_score": score, "maximum_gap_score": 1.0}


def room_window_lattice_loses_valid_staff_slot():
    hall = Hall(1, "Hall", 100, [slot(9, end=17)])
    b = block("L", total=1, lecture=True, teacher=staff(1, [slot(10)], lecture=True))
    eng = engine(halls=[hall])
    candidates = eng.resource_manager.get_available_slots(b, hall, {})
    assert not candidates
    assert eng.constraint_manager.can_assign(b, slot(10), hall)[0]
    return {"room_window": "09-17", "lecturer_window": "10-12",
            "candidates": 0, "10-12_passes_hard_constraints": True}


def cross_course_section_model():
    rooms = [lab(1, [slot(9)]), lab(2, [slot(9)])]
    a = block("ProgrammingSection1", course=1, group=1)
    b = block("ElectronicsSection2", course=2, group=2)
    cm = ConstraintManager()
    assert cm.make_assignment(a.id, Assignment(a, slot(9), rooms[0]))
    allowed, reason = cm.can_assign(b, slot(9), rooms[1])
    assert not allowed
    return {"different_sections_different_courses_allowed": allowed, "reason": reason,
            "qualification": "Only a defect if sections have stable, disjoint membership across courses."}


def advisory_log_does_not_follow_final_assignment():
    room = lab(1, [slot(9), slot(11)])
    a = block("Victim", course=1, total=1, teacher=staff(1, [slot(11)]))
    b = block("Incoming", course=2, total=1, teacher=staff(2), cohorts=["Other"])
    eng = engine([room])
    original = Assignment(a, slot(9), room)
    assert eng.constraint_manager.make_assignment(a.id, original)
    eng._record_advisory_warnings(original)
    assert eng._try_displacement_repair(b, {})  # Free 11:00 is chosen directly.
    # Explicit displacement exercises the successful repair path used in real runs.
    eng.constraint_manager.undo_assignment(b.id)
    assert eng._swap_with_victim(b, slot(9), room, a.id,
                                 eng.constraint_manager.get_all_assignments())
    actual = eng.constraint_manager.get_all_assignments()
    assert actual[a.id].time_slot.start_time == time(11)
    assert len(eng.advisory_warnings) == 1  # Stale 09:00 warning remains.
    return {"final_victim_start": "11:00 (preferred)", "warnings": eng.advisory_warnings}


def reports_summary():
    output = []
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / "reports").glob("schedule_202609*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))["schedule"]
        types = Counter(row["session_type"] for row in rows)
        labs = [row for row in rows if row["session_type"] == "lab"]
        times = Counter((row["time_slot"]["day"], row["time_slot"]["start_time"]) for row in labs)
        output.append({"file": path.name, "sessions": dict(types),
                       "peak_concurrent_labs": max(times.values(), default=0)})
    return output


def current_input_lower_bounds():
    """Analyze read-only snapshots captured during this audit, without networking."""
    from utils.api_halls import convert_api_hall
    from utils.api_labs import convert_api_lab
    from utils.time_utils import convert_api_day_to_enum, convert_api_time_to_time_object
    from models.time_preferences import ReservedPeriod

    directory = Path(__file__).resolve().parent
    plans = json.loads((directory / "current_plan_allocations.json").read_text(encoding="utf-8"))
    data = json.loads((directory / "current_room_windows.json").read_text(encoding="utf-8"))
    period = data["schedule-settings"]["reserved_period"]
    reserved = ReservedPeriod(convert_api_day_to_enum(period["day"]),
                              convert_api_time_to_time_object(period["start_time"]),
                              convert_api_time_to_time_object(period["end_time"])) if period else None
    rooms = ([convert_api_hall(row) for row in data["halls"]]
             + [convert_api_lab(row) for row in data["laps"]])
    rm = ResourceManager([], [], reserved)
    candidates = {s for room in rooms for window in room.availability
                  for s in rm._slots_in_window(window)}
    usable = {s for s in candidates if not rm._overlaps_reserved_period(s)}
    # This count is an upper bound on usable non-overlapping periods because
    # the captured room windows all use the same 09/11/13/15 start-time grid.
    assert all(s.start_time.hour in (9, 11, 13, 15) and s.start_time.minute == 0
               and s.end_time.hour == s.start_time.hour + 2 for s in candidates)
    bounds = []
    for plan in plans:
        # All captured courses have exactly one lecture; do not assume that
        # multiple lecture groups would have to run serially in other inputs.
        assert all(c["lectures"] == 1 for c in plan["courses"])
        course_bounds = [{"course": c["code"], "lecture_periods": 1,
                          "lab_periods": max((t["groups"] for t in c["tas"]), default=0)}
                         for c in plan["courses"]]
        minimum = sum(c["lecture_periods"] + c["lab_periods"] for c in course_bounds)
        bounds.append({"plan_id": plan["id"], "minimum_required_periods": minimum,
                       "all_room_upper_bound": len(usable), "courses": course_bounds})
    assert next(b for b in bounds if b["plan_id"] == 116)["minimum_required_periods"] == 26
    assert len(candidates) == 24 and len(usable) == 23
    return {"rooms": len(rooms), "periods_before_reservation": len(candidates),
            "periods_after_reservation": len(usable), "plan_bounds": bounds}


if __name__ == "__main__":
    findings = {
        "serial_labs_and_early_stop": serial_labs_and_early_stop(),
        "ta_preference_discarded": ta_preference_discarded(),
        "repair_cannot_move_parallel_bundle": repair_cannot_move_parallel_bundle(),
        "retries_repeat": retries_repeat_and_miss_feasible_schedule(),
        "gap_score_measures_span": gap_score_measures_span(),
        "room_window_lattice": room_window_lattice_loses_valid_staff_slot(),
        "cross_course_section_model": cross_course_section_model(),
        "stale_advisory": advisory_log_does_not_follow_final_assignment(),
        "current_input_lower_bounds": current_input_lower_bounds(),
        "reports": reports_summary(),
    }
    print(json.dumps(findings, indent=2, ensure_ascii=True))
