# utils/feasibility.py
"""Aggregate preflight feasibility for one generation run.

Computes, per cohort, a lower bound on the number of distinct weekly
periods the run must occupy and compares it against the number of periods
the selected rooms can offer at all. Both ends of the comparison are
chosen so a shortfall is a *proof*:

- required: one staff member cannot teach two groups at once, so a course
  needs at least (largest number of groups given to any one of its staff)
  periods; different courses of one cohort can never share a period, so
  the course bounds add up. Room scarcity can only raise the real need.
- available: the union of 2-hour sessions across every selected room
  (minus the reserved period) is the most any cohort can ever reach.

If required > available, the run is infeasible no matter how good the
search is - it is reported as a DATA blocker with the numbers and the
named levers instead of being fed to a search that must fail.

The enforced bound depends on the clash mode the engine will run under:

- sections mode (default): courses of one cohort may share a period across
  disjoint student sections, so the bound is the single-group sessions
  (true whole-cohort attendance, which exclude everything else of the
  cohort) plus the rotatable demand - merged common-course groups and
  plain multi-group labs alike - bounded by four individually-proven
  terms: the largest per-staff group load over rotatable labs, the same
  over merged sessions (labs and lectures), the merged section demand
  (section s of the cohort attends one session per merged course that
  offers a section s), and lab-room concurrency ceil(lab sessions / lab
  pool). Each term counts periods the whole-cohort sessions cannot reuse:
  one staff member's sessions are pairwise distinct; a section cannot sit
  in two rooms at once or during a whole-cohort session; each lab session
  occupies one lab. The max of individually-sound additions is sound; it
  may understate demand, never overstate it.
- strict_cohort_mode: courses of one cohort can never share a period (and
  merged groups serialize again), so the per-course bounds add up (the
  audited plan-116 bound).

Everything the run gets wrong on top of that is expressed as a structured
Insight (see utils/insights.py). This module never mutates state and is
safe to run offline on fixtures.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from models.block import SOURCE_EXTERNAL, Block, BlockType
from models.labs import Lab
from models.staff_members import Lecturer
from models.time_preferences import ReservedPeriod, TimePreference
from utils.insights import (
    Insight,
    InsightCategory,
    InsightSeverity,
    entity,
    summarize,
)
from utils.room_utils import get_room_key
from utils.sections import cohort_keys
from utils.time_utils import (
    GRID_LAST_DAY_VALUE,
    GRID_SLOT_MINUTES,
    GRID_SLOT_START_MINUTES,
    add_minutes,
    blocker_overlaps_slot,
    intervals_overlap,
    preference_covers_slot,
    to_minutes,
    window_covers,
)

logger = logging.getLogger("feasibility")

CohortKey = Tuple[str, int]
PeriodKey = Tuple  # (Day, start_time, end_time)


@dataclass
class FeasibilityReport:
    insights: List[Insight] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)

    @property
    def blockers(self) -> List[Insight]:
        return [
            i for i in self.insights if i.severity is InsightSeverity.BLOCKER
        ]

    @property
    def warnings(self) -> List[Insight]:
        return [
            i for i in self.insights if i.severity is InsightSeverity.WARNING
        ]


def _room_periods(
    rooms: List,
    reserved_period: Optional[ReservedPeriod],
    slot_duration_minutes: int,
    stride_minutes: int,
) -> Set[PeriodKey]:
    """
    Every 2-hour session any of these rooms can host, minus reserved periods.

    Mirrors ResourceManager._slots_in_window (window-stepped candidates) so
    the preflight bound matches what the search could actually reach. This
    is the generous upper bound: scarcity of a specific room type is a
    separate, additional restriction handled by the per-block pre-flight.
    """
    periods: Set[PeriodKey] = set()
    for room in rooms:
        for window in room.availability:
            cursor = window.start_time
            while cursor is not None:
                slot_end = add_minutes(cursor, slot_duration_minutes)
                if slot_end is None or not window_covers(
                    window.start_time, window.end_time, cursor, slot_end
                ):
                    break
                overlaps_reserved = (
                    reserved_period is not None
                    and window.day == reserved_period.day
                    and intervals_overlap(
                        cursor,
                        slot_end,
                        reserved_period.start_time,
                        reserved_period.end_time,
                    )
                )
                if not overlaps_reserved:
                    periods.add((window.day, cursor, slot_end))
                cursor = add_minutes(cursor, stride_minutes)
    return periods


def _course_key(block: Block) -> Tuple:
    """
    Canonical identity of the course a block belongs to.

    Merged common-course blocks carry a per-group SUBSET of the participating
    courses, so their arrays alone would fragment one course into several
    rows; merged_course_ids keeps the full participation set. Plain blocks
    key on their course codes.
    """
    merged_ids = getattr(block, "merged_course_ids", None)
    return tuple(sorted(merged_ids)) if merged_ids else tuple(sorted(block.course_codes))


def _staff_available_period_count(
    staff_member, periods: Set[PeriodKey]
) -> Optional[int]:
    """
    Count grid periods the person could actually teach in: inside one of
    their availability windows (when they declared any) and overlapping no
    blocker. Someone with neither windows nor blockers is unconstrained
    (None - no capacity verdict is possible).
    """
    blockers = getattr(staff_member, "blockers", [])
    has_windows = bool(staff_member.timing_preferences)
    has_blockers = bool(blockers)
    if not has_windows and not has_blockers:
        return None
    count = 0
    for day, start, end in periods:
        slot = TimePreference(day=day, start_time=start, end_time=end)
        if has_windows and not any(
            preference_covers_slot(pref, slot)
            for pref in staff_member.timing_preferences
        ):
            continue
        if has_blockers and any(
            blocker_overlaps_slot(blocker, slot) for blocker in blockers
        ):
            continue
        count += 1
    return count


def _lab_pool_for(block: Block, labs: List[Lab]) -> List[Lab]:
    """The rooms get_suitable_rooms would draw from (mirrors its pool rule)."""
    if block.required_room_type != "lab":
        return []
    if block.preferred_rooms:
        return list(block.preferred_rooms)
    return [lab for lab in labs if lab.used_in_non_specialist_courses]


def _slot_grid_problem(
    slot: TimePreference,
    reserved_period: Optional[ReservedPeriod],
    allow_reserved: bool = False,
) -> Optional[str]:
    """Why a declared slot cannot exist on the teaching grid, or None."""
    start = to_minutes(slot.start_time)
    end = to_minutes(slot.end_time)
    if start not in GRID_SLOT_START_MINUTES or end - start != GRID_SLOT_MINUTES:
        return "it is not a whole 2h grid period (09:00-19:00)"
    if slot.day.value > GRID_LAST_DAY_VALUE:
        return "it falls outside the teaching week (Sun-Thu)"
    if (
        not allow_reserved
        and reserved_period is not None
        and slot.day == reserved_period.day
        and intervals_overlap(
            slot.start_time,
            slot.end_time,
            reserved_period.start_time,
            reserved_period.end_time,
        )
    ):
        return "it overlaps the reserved period"
    return None


def _slot_hosted_by_any_room(pool: List, slot: TimePreference) -> bool:
    """True when at least one room's current availability covers the slot."""
    return any(
        any(preference_covers_slot(window, slot) for window in room.availability)
        for room in pool
    )


def _staff_covers_any_slot(staff_member, slots: List[TimePreference]) -> bool:
    """True when staff hours cover at least one declared slot. No recorded
    hours means fully available (the engine's standing convention)."""
    if not staff_member.timing_preferences:
        return True
    return any(
        any(preference_covers_slot(pref, slot) for pref in staff_member.timing_preferences)
        for slot in slots
    )


def _declared_slot_insights(
    blocks: List[Block],
    halls: List,
    labs: List[Lab],
    reserved_period: Optional[ReservedPeriod],
) -> List[Insight]:
    """
    Hard-slot bounds for external courses that declare fixed time slots.

    A declared set is a hard candidate restriction, so anything provably
    infeasible about it must surface here as DATA - never as an unexplained
    ENGINE failure after the search. These bounds are strictly tighter than
    the run-level pool bounds above, which stay in place for unconstrained
    courses.
    """
    insights: List[Insight] = []

    # Group external blocks per course+component; every block of a component
    # carries the same allowed_slots set.
    components: Dict[Tuple[int, str], List[Block]] = {}
    for block in blocks:
        if block.source_kind == SOURCE_EXTERNAL and block.allowed_slots:
            components.setdefault(
                (block.external_course_id, block.block_type.value), []
            ).append(block)

    for (course_id, component), course_blocks in sorted(components.items()):
        slots = list(course_blocks[0].allowed_slots or [])
        slot_count = len(slots)
        if slot_count == 0:
            continue
        is_lab = component == "lab"
        component_word = "lab" if is_lab else "lecture"
        label = course_blocks[0].external_course_name or f"course {course_id}"
        entities = [
            entity(
                "external_course",
                course_id,
                course_blocks[0].external_course_name or str(course_id),
            )
        ]

        # One staff member covers at most this many groups per 2h slot
        # (1 for 2h sessions; Phase 3's 1h sessions may pack two).
        session_hours = max(
            getattr(block, "session_hours", 2) for block in course_blocks
        )
        sessions_per_slot = max(1, GRID_SLOT_MINUTES // (session_hours * 60))
        groups = len(course_blocks)
        held_by: Dict[int, int] = {}
        staff_by_id: Dict[int, object] = {}
        for block in course_blocks:
            if block.staff_member is not None:
                held_by[block.staff_member.id] = (
                    held_by.get(block.staff_member.id, 0) + 1
                )
                staff_by_id[block.staff_member.id] = block.staff_member

        # 1) The declared slots themselves must exist on the teaching grid.
        # A course with the reserved-period override may declare slots inside
        # the reserved window - the grid shape is checked, the reserved
        # overlap is not.
        allow_reserved = bool(
            getattr(course_blocks[0], "allow_reserved_period", False)
        )
        for slot in slots:
            problem = _slot_grid_problem(slot, reserved_period, allow_reserved)
            if problem is None:
                continue
            slot_label = (
                f"{slot.day.name} {slot.start_time.strftime('%H:%M')}-"
                f"{slot.end_time.strftime('%H:%M')}"
            )
            insights.append(
                Insight(
                    code=(
                        "EXTERNAL_LAB_SLOT_NOT_ON_GRID"
                        if is_lab
                        else "EXTERNAL_LECTURE_SLOT_NOT_ON_GRID"
                    ),
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.BLOCKER,
                    phase="preflight",
                    verdict=(
                        f"External course '{label}' declares the {component_word} "
                        f"slot {slot_label}, but {problem}."
                    ),
                    entities=entities,
                    evidence={"slot": slot_label, "reason": problem},
                    recommended_action=(
                        "Move the slot onto the teaching grid (Sun-Thu, whole "
                        "2h periods, clear of the reserved period) or drop it."
                    ),
                )
            )

        # 2) Room coverage: every slot needs at least one room of the pool
        #    that is actually available for it.
        if is_lab:
            pool = (
                list(course_blocks[0].preferred_rooms)
                if course_blocks[0].preferred_rooms
                else [
                    lab for lab in labs if lab.used_in_non_specialist_courses
                ]
            )
            no_room_code = "EXTERNAL_LAB_SLOT_NO_ROOM"
            room_word = "lab"
        elif course_blocks[0].requires_room:
            pool = list(halls)
            no_room_code = "EXTERNAL_LECTURE_SLOT_NO_HALL"
            room_word = "hall"
        else:
            pool = []  # external-venue lectures reserve no room
            no_room_code = "EXTERNAL_LECTURE_SLOT_NO_HALL"
            room_word = "hall"
        if pool:
            for slot in slots:
                if _slot_hosted_by_any_room(pool, slot):
                    continue
                slot_label = (
                    f"{slot.day.name} {slot.start_time.strftime('%H:%M')}-"
                    f"{slot.end_time.strftime('%H:%M')}"
                )
                insights.append(
                    Insight(
                        code=no_room_code,
                        category=InsightCategory.DATA,
                        severity=InsightSeverity.BLOCKER,
                        phase="preflight",
                        verdict=(
                            f"External course '{label}' declares the "
                            f"{component_word} slot {slot_label}, but no "
                            f"{room_word} in the pool is available for it."
                        ),
                        entities=entities,
                        evidence={
                            "slot": slot_label,
                            f"{room_word}_pool_size": len(pool),
                        },
                        recommended_action=(
                            f"Extend the {room_word}'s hours, include more "
                            f"{room_word}s in the selection, or move the slot."
                        ),
                    )
                )

        # 3) Capacity: groups × session hours against slots × rooms (or, for
        #    a roomless lecture, slots × lecturers).
        if is_lab:
            capacity = slot_count * sessions_per_slot * len(pool)
        elif course_blocks[0].requires_room:
            capacity = slot_count * sessions_per_slot * len(halls)
        else:
            capacity = slot_count * sessions_per_slot * max(1, len(held_by))
        if groups > capacity:
            insights.append(
                Insight(
                    code=(
                        "EXTERNAL_LAB_SLOT_CAPACITY_EXCEEDED"
                        if is_lab
                        else "EXTERNAL_LECTURE_SLOT_CAPACITY_EXCEEDED"
                    ),
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.BLOCKER,
                    phase="preflight",
                    verdict=(
                        f"External course '{label}': {groups} {component_word} "
                        f"group(s) of {session_hours}h cannot fit into "
                        f"{slot_count} declared slot(s) "
                        f"(capacity {capacity})."
                    ),
                    entities=entities,
                    evidence={
                        "declared_slots": slot_count,
                        "groups": groups,
                        "session_hours": session_hours,
                        "capacity": capacity,
                    },
                    recommended_action=(
                        "Add time slots, reduce the group count, drop to 1h "
                        "sessions, or widen the room/staff pool. This is a "
                        "data change, not a scheduling defect."
                    ),
                )
            )

        # 4) Staff reach and hours against the declared slots.
        for staff_id, held in sorted(held_by.items()):
            staff_member = staff_by_id[staff_id]
            if not is_lab:
                # Lecturer hours are binding: count the slots they can teach.
                if staff_member.timing_preferences:
                    covered = sum(
                        1
                        for slot in slots
                        if any(
                            preference_covers_slot(pref, slot)
                            for pref in staff_member.timing_preferences
                        )
                    )
                else:
                    covered = slot_count  # fully available
                if held > covered * sessions_per_slot:
                    insights.append(
                        Insight(
                            code="EXTERNAL_LECTURE_SLOT_LECTURER_UNAVAILABLE",
                            category=InsightCategory.DATA,
                            severity=InsightSeverity.BLOCKER,
                            phase="preflight",
                            verdict=(
                                f"Lecturer {staff_member.name} (ID {staff_id}) "
                                f"holds {held} group(s) of external course "
                                f"'{label}' but their hours cover only "
                                f"{covered} of the {slot_count} declared "
                                f"slot(s)."
                            ),
                            entities=[
                                entity("staff", staff_id, staff_member.name),
                                *entities,
                            ],
                            evidence={
                                "declared_slots": slot_count,
                                "covered_slots": covered,
                                "held_groups": held,
                            },
                            recommended_action=(
                                "Widen the lecturer's availability windows, "
                                "declare more slots, or move groups to other "
                                "lecturers."
                            ),
                        )
                    )
                continue

            # Lab TAs: one TA staffs at most one group per half-slot.
            if held > slot_count * sessions_per_slot:
                insights.append(
                    Insight(
                        code="EXTERNAL_LAB_TA_REACH_EXCEEDED",
                        category=InsightCategory.DATA,
                        severity=InsightSeverity.BLOCKER,
                        phase="preflight",
                        verdict=(
                            f"TA {staff_member.name} (ID {staff_id}) holds "
                            f"{held} group(s) of external course '{label}' "
                            f"but {slot_count} declared slot(s) give them at "
                            f"most {slot_count * sessions_per_slot}."
                        ),
                        entities=[entity("staff", staff_id, staff_member.name), *entities],
                        evidence={
                            "declared_slots": slot_count,
                            "held_groups": held,
                            "max_groups_per_ta": slot_count * sessions_per_slot,
                        },
                        recommended_action=(
                            "Assign more TAs, declare more slots, drop to 1h "
                            "sessions, or reduce the group count."
                        ),
                    )
                )
            elif not _staff_covers_any_slot(staff_member, slots):
                # Advisory, like every TA-hours shortfall.
                insights.append(
                    Insight(
                        code="EXTERNAL_LAB_SLOT_OUTSIDE_TA_HOURS",
                        category=InsightCategory.DATA,
                        severity=InsightSeverity.WARNING,
                        phase="preflight",
                        verdict=(
                            f"TA {staff_member.name} (ID {staff_id}) of "
                            f"external course '{label}' has no hours covering "
                            f"any of the {slot_count} declared slot(s) - any "
                            f"placement is out-of-hours and will be reported."
                        ),
                        entities=[entity("staff", staff_id, staff_member.name), *entities],
                        evidence={"declared_slots": slot_count},
                        recommended_action=(
                            "Widen the TA's hours or accept the reported "
                            "out-of-hours placements."
                        ),
                    )
                )

    return insights


def assess_feasibility(
    blocks: List[Block],
    halls: List,
    labs: List[Lab],
    reserved_period: Optional[ReservedPeriod],
    slot_duration_hours: int = 2,
    break_duration_minutes: int = 0,
    strict_cohort_mode: bool = False,
) -> FeasibilityReport:
    """Compute the aggregate feasibility report for the blocks to place."""
    slot_minutes = slot_duration_hours * 60
    stride = slot_minutes + break_duration_minutes

    lab_periods = _room_periods(labs, reserved_period, slot_minutes, stride)
    hall_periods = _room_periods(halls, reserved_period, slot_minutes, stride)
    union_periods = lab_periods | hall_periods

    report = FeasibilityReport()
    if not blocks:
        report.summary = {
            "periods_available_lab": len(lab_periods),
            "periods_available_hall": len(hall_periods),
            "periods_available_union": len(union_periods),
            "cohorts": [],
        }
        return report

    # (cohort, block_type, canonical course key) -> blocks of that course
    course_groups: Dict[Tuple[CohortKey, str, Tuple], List[Block]] = (
        defaultdict(list)
    )
    for block in blocks:
        for cohort in cohort_keys(block):
            course_groups[
                (cohort, block.block_type.value, _course_key(block))
            ].append(block)

    per_cohort_summaries: List[Dict] = []

    for cohort in sorted({key[0] for key in course_groups}):
        cohort_label = f"{cohort[0]} L{cohort[1]}"
        atomic_required = 0
        whole_cohort_sessions = 0
        lab_sessions = 0
        max_lab_bottleneck = 0
        max_merged_bottleneck = 0
        merged_per_staff: Dict[int, int] = defaultdict(int)
        merged_sections_by_course: List[int] = []
        usable_lab_rooms: Set[Tuple] = set()
        course_rows: List[Dict] = []

        for (key_cohort, block_type, _key), course_blocks in sorted(
            course_groups.items(),
            # Canonical keys are int tuples for merged courses and code
            # tuples for plain ones - never compare them directly.
            key=lambda item: (item[0][0], item[0][1], str(item[0][2])),
        ):
            if key_cohort != cohort:
                continue

            groups = len(course_blocks)
            per_staff: Dict[int, int] = defaultdict(int)
            for course_block in course_blocks:
                if course_block.staff_member is None:
                    # Staffless external blocks serialize on nothing.
                    continue
                per_staff[course_block.staff_member.id] += 1
            distinct_staff = len(per_staff)
            bottleneck = max(per_staff.values())

            # Whole-cohort attendance is exactly the single-group courses
            # now; merged multi-group blocks are rotatable sections.
            cohort_whole = [
                course_block
                for course_block in course_blocks
                if course_block.is_single_group_course
            ]
            whole_cohort_sessions += len(cohort_whole)

            is_merged_row = bool(
                getattr(course_blocks[0], "is_merged_common_course", False)
            )
            row_codes = sorted(
                {code for cb in course_blocks for code in cb.course_codes}
            )

            rotatable = [
                course_block for course_block in course_blocks
                if course_block not in cohort_whole
            ]

            if is_merged_row and rotatable:
                for course_block in rotatable:
                    if course_block.staff_member is not None:
                        merged_per_staff[course_block.staff_member.id] += 1
                merged_sections_by_course.append(
                    max(course_block.group_number for course_block in rotatable)
                )

            if block_type == BlockType.LAB.value:
                # Rotatable labs: sections may run them beside other courses'
                # sections; staff serialization and lab-room count bound how
                # fast they can be drained.
                lab_sessions += len(rotatable)
                rot_per_staff: Dict[int, int] = defaultdict(int)
                for course_block in rotatable:
                    if course_block.staff_member is None:
                        continue
                    rot_per_staff[course_block.staff_member.id] += 1
                if rot_per_staff:
                    max_lab_bottleneck = max(
                        max_lab_bottleneck, max(rot_per_staff.values())
                    )
                # Every room any of these courses may use counts toward the
                # concurrency bound - specialist/preferred labs included, so
                # a specialist-heavy pool cannot be wrongly refused.
                for course_block in course_blocks:
                    usable_lab_rooms.update(
                        get_room_key(room)
                        for room in _lab_pool_for(course_block, labs)
                    )

            atomic_required += bottleneck

            course_rows.append(
                {
                    "course_codes": row_codes,
                    "type": block_type,
                    "groups": groups,
                    "staff": [
                        {"id": staff_id, "groups": count}
                        for staff_id, count in sorted(per_staff.items())
                    ],
                    "distinct_staff": distinct_staff,
                    "atomic_bound": bottleneck,
                    "whole_cohort_sessions": len(cohort_whole),
                    "is_merged": is_merged_row,
                    "sections_for_cohort": (
                        max(
                            (
                                course_block.group_number
                                for course_block in rotatable
                            ),
                            default=0,
                        )
                        if rotatable
                        else 0
                    ),
                }
            )

        if merged_per_staff:
            max_merged_bottleneck = max(merged_per_staff.values())
        merged_section_demand = 0
        for section in range(1, max(merged_sections_by_course, default=0) + 1):
            applicable = sum(
                1 for sections in merged_sections_by_course if sections >= section
            )
            merged_section_demand = max(merged_section_demand, applicable)

        # Sections-mode lower bound: whole-cohort sessions exclude every
        # other session of the cohort; the rotatable sessions need at least
        # max(lab staff serialization, merged staff serialization, merged
        # section demand, lab-room concurrency) further periods. Each term
        # counts periods the whole-cohort sessions cannot reuse, and the
        # max of individually-sound additions is sound.
        lab_room_concurrency = (
            math.ceil(lab_sessions / len(usable_lab_rooms))
            if usable_lab_rooms
            else 0
        )
        sections_bound = whole_cohort_sessions + max(
            max_lab_bottleneck,
            max_merged_bottleneck,
            merged_section_demand,
            lab_room_concurrency,
        )

        cohort_summary = {
            "cohort": cohort_label,
            "required_periods_cohort_atomic": atomic_required,
            "required_periods_sections_bound": sections_bound,
            "whole_cohort_sessions": whole_cohort_sessions,
            "max_merged_bottleneck": max_merged_bottleneck,
            "merged_section_demand": merged_section_demand,
            "courses": course_rows,
        }
        per_cohort_summaries.append(cohort_summary)

        if not union_periods:
            continue

        if strict_cohort_mode:
            enforced = atomic_required
            enforced_name = "required_periods_cohort_atomic"
        else:
            enforced = sections_bound
            enforced_name = "required_periods_sections_bound"

        if enforced > len(union_periods):
            offenders = sorted(
                course_rows, key=lambda row: row["atomic_bound"], reverse=True
            )
            top = [
                f"{row['course_codes'][0]} needs {row['atomic_bound']} period(s) "
                f"({row['staff'][0]['groups']} group(s) on one staff member)"
                + (
                    f" (merged course, sections 1-{row['sections_for_cohort']})"
                    if row["is_merged"]
                    else ""
                )
                for row in offenders
                if row["atomic_bound"] > 1
            ][:3]
            if strict_cohort_mode:
                action = (
                    "Redistribute each single-staff course's groups across more "
                    "qualified staff, extend the teaching day, or free the "
                    "reserved period - or run without strict_cohort_mode: the "
                    "section model may make this input feasible."
                )
            else:
                action = (
                    "Redistribute single-staff courses' groups across more "
                    "qualified staff, extend the teaching day, add labs, or free "
                    "the reserved period. This is a data change, not a "
                    "scheduling defect."
                )
            report.insights.append(
                Insight(
                    code="COHORT_PERIOD_BUDGET_EXCEEDED",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.BLOCKER,
                    phase="preflight",
                    verdict=(
                        f"Cohort {cohort_label} needs at least {enforced} "
                        f"distinct weekly periods ({enforced_name}) but only "
                        f"{len(union_periods)} exist across all selected rooms "
                        f"- no search can fit it."
                    ),
                    entities=[entity("cohort", f"{cohort[0]}:{cohort[1]}", cohort_label)]
                    + [
                        entity("course", row["course_codes"][0], ", ".join(row["course_codes"]))
                        for row in offenders[:3]
                    ],
                    evidence={
                        "enforced_bound": enforced_name,
                        "required_periods_cohort_atomic": atomic_required,
                        "required_periods_sections_bound": sections_bound,
                        "whole_cohort_sessions": whole_cohort_sessions,
                        "max_merged_bottleneck": max_merged_bottleneck,
                        "merged_section_demand": merged_section_demand,
                        "available_periods_union": len(union_periods),
                        "available_periods_lab": len(lab_periods),
                        "available_periods_hall": len(hall_periods),
                        "per_course_bounds": course_rows,
                        "top_offenders": top,
                    },
                    recommended_action=action,
                )
            )

    # Staff demand vs recorded windows. Lecturer hours are binding in the
    # engine, so a shortfall there is a blocker; TA hours are advisory, so
    # the same shortfall is a loud warning. Staffed external sessions - v2/v3/v4
    # labs and v3/v4 lectures alike - join this check unchanged; staffless
    # blocks occupy no staff and are skipped.
    demand_seen: Set[int] = set()
    for block in blocks:
        staff_member = block.staff_member
        if staff_member is None:
            continue
        if staff_member.id in demand_seen:
            continue
        demand_seen.add(staff_member.id)

        matching = [
            other
            for other in blocks
            if other.staff_member is not None
            and other.staff_member.id == staff_member.id
        ]
        # Demand in period-units: a 1h session needs half a period, and two
        # of a staff member's 1h groups may share one (back-to-back halves).
        sessions = len(matching)
        demand_periods = math.ceil(
            sum(getattr(other, "session_hours", 2) for other in matching) / 2
        )
        supply = _staff_available_period_count(staff_member, union_periods)
        if supply is None or demand_periods <= supply:
            continue

        is_lecturer = isinstance(staff_member, Lecturer)
        report.insights.append(
            Insight(
                code="STAFF_DEMAND_EXCEEDS_AVAILABILITY",
                category=InsightCategory.DATA,
                severity=InsightSeverity.BLOCKER if is_lecturer else InsightSeverity.WARNING,
                phase="preflight",
                verdict=(
                    f"{'Lecturer' if is_lecturer else 'TA'} {staff_member.name} "
                    f"(ID {staff_member.id}) holds {sessions} session(s) "
                    f"(= {demand_periods} period(s)) but their recorded hours "
                    f"cover only {supply} period(s)."
                ),
                entities=[entity("staff", staff_member.id, staff_member.name)],
                evidence={
                    "assigned_sessions": sessions,
                    "demand_periods": demand_periods,
                    "covered_periods": supply,
                },
                recommended_action=(
                    "Widen the staff member's availability windows or move some of "
                    "their groups to other qualified staff."
                    if is_lecturer
                    else "Widen the TA's hours (or accept out-of-hours placements - "
                    "each one is reported), or move groups to other TAs."
                ),
            )
        )

    # Run-level room bounds for external demand. Per-cohort bounds never see
    # external blocks (they belong to no cohort), and lab pools overlap across
    # cohorts, so the aggregate demand is proven here against the run's own
    # pools: every lab session occupies one lab for one period, so
    # ceil(total lab sessions / lab-pool size) distinct periods are needed;
    # the same holds for roomed external lectures against the hall pool.
    external_lab_blocks = [
        block
        for block in blocks
        if block.source_kind == SOURCE_EXTERNAL
        and block.block_type == BlockType.LAB
    ]
    if external_lab_blocks and labs:
        cohort_lab_sessions = sum(
            1
            for block in blocks
            if block.source_kind != SOURCE_EXTERNAL
            and block.block_type == BlockType.LAB
        )
        # External demand in period-units: two 1h groups may share one lab
        # period back-to-back, so 1h groups count half a period each.
        external_lab_periods = math.ceil(
            sum(getattr(block, "session_hours", 2) for block in external_lab_blocks)
            / 2
        )
        total_lab_demand = cohort_lab_sessions + external_lab_periods
        lab_bound = math.ceil(total_lab_demand / len(labs))
        if lab_bound > len(lab_periods):
            course_names = sorted(
                {
                    block.external_course_name
                    or f"course {block.external_course_id}"
                    for block in external_lab_blocks
                }
            )
            report.insights.append(
                Insight(
                    code="EXTERNAL_LAB_DEMAND_EXCEEDS_POOL",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.BLOCKER,
                    phase="preflight",
                    verdict=(
                        f"{total_lab_demand} lab period(s) of demand "
                        f"({len(external_lab_blocks)} external group(s) from "
                        f"{course_names}) cannot fit: {len(labs)} eligible "
                        f"lab(s) need at least {lab_bound} distinct periods "
                        f"but only {len(lab_periods)} exist."
                    ),
                    entities=[
                        entity(
                            "external_course",
                            block.external_course_id,
                            block.external_course_name or str(block.external_course_id),
                        )
                        for block in external_lab_blocks[:6]
                    ],
                    evidence={
                        "external_lab_groups": len(external_lab_blocks),
                        "external_lab_periods": external_lab_periods,
                        "cohort_lab_sessions": cohort_lab_sessions,
                        "eligible_lab_count": len(labs),
                        "required_periods": lab_bound,
                        "available_lab_periods": len(lab_periods),
                    },
                    recommended_action=(
                        "Add labs to the selection, reduce external lab groups, "
                        "or extend lab hours. This is a data change, not a "
                        "scheduling defect."
                    ),
                )
            )

    external_roomed_lectures = [
        block
        for block in blocks
        if block.source_kind == SOURCE_EXTERNAL
        and block.block_type == BlockType.LECTURE
        and block.requires_room
    ]
    if external_roomed_lectures and halls:
        external_lecture_periods = math.ceil(
            sum(
                getattr(block, "session_hours", 2)
                for block in external_roomed_lectures
            )
            / 2
        )
        lecture_bound = math.ceil(external_lecture_periods / len(halls))
        if lecture_bound > len(hall_periods):
            course_names = sorted(
                {
                    block.external_course_name
                    or f"course {block.external_course_id}"
                    for block in external_roomed_lectures
                }
            )
            report.insights.append(
                Insight(
                    code="EXTERNAL_LECTURE_DEMAND_EXCEEDS_POOL",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.BLOCKER,
                    phase="preflight",
                    verdict=(
                        f"{len(external_roomed_lectures)} external lecture "
                        f"group(s) ({course_names}) cannot fit: "
                        f"{len(halls)} hall(s) need at least {lecture_bound} "
                        f"distinct periods but only {len(hall_periods)} exist."
                    ),
                    entities=[
                        entity(
                            "external_course",
                            block.external_course_id,
                            block.external_course_name or str(block.external_course_id),
                        )
                        for block in external_roomed_lectures[:6]
                    ],
                    evidence={
                        "external_lecture_groups": len(external_roomed_lectures),
                        "hall_count": len(halls),
                        "required_periods": lecture_bound,
                        "available_hall_periods": len(hall_periods),
                    },
                    recommended_action=(
                        "Add halls to the selection, reduce external lecture "
                        "groups, or extend hall hours. This is a data change, "
                        "not a scheduling defect."
                    ),
                )
            )

    # Hard fixed-slot bounds for external courses that declare time slots -
    # strictly tighter than the pool bounds above, per course.
    report.insights.extend(
        _declared_slot_insights(blocks, halls, labs, reserved_period)
    )

    # Capacity: an overflow placement (seats < students) is allowed by policy
    # but must be loud. A block no room can host even at the fallback ratio is
    # left to the per-block pre-flight, which fails the run for it.
    for block in blocks:
        if not block.requires_room:
            # Roomless sessions occupy no seats of ours; nothing to overflow.
            continue
        pool = (
            _lab_pool_for(block, labs)
            if block.required_room_type == "lab"
            else list(halls)
        )
        if not pool:
            continue
        best = max(room.capacity for room in pool)
        if block.student_count > best:
            report.insights.append(
                Insight(
                    code="ROOM_CAPACITY_SHORTFALL",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.WARNING,
                    phase="preflight",
                    verdict=(
                        f"{block.display_label()} has "
                        f"{block.student_count} students but the largest suitable "
                        f"room seats {best} - it can only be placed as an overflow."
                    ),
                    entities=[
                        entity("course", block.display_label(), block.display_label()),
                        entity("room", pool[0].id, max(pool, key=lambda r: r.capacity).name),
                    ],
                    evidence={
                        "student_count": block.student_count,
                        "largest_room_capacity": best,
                    },
                    recommended_action=(
                        "Split the group, or provide a larger room. Overflow "
                        "placements post with a warning by policy."
                    ),
                )
            )

    # (Under proportional merged-group ownership every participating list's
    # students land in some group by construction, so there is no
    # dropped-section failure mode to audit here.)

    report.summary = {
        "periods_available_lab": len(lab_periods),
        "periods_available_hall": len(hall_periods),
        "periods_available_union": len(union_periods),
        "cohorts": per_cohort_summaries,
        "insights": summarize(report.insights),
    }
    return report
