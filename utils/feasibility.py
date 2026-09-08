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
  disjoint student sections, so the bound is whole-cohort sessions (which
  exclude everything else of the cohort) plus the lab demand that staff
  serialization and lab-room concurrency force: each course whose groups
  sit on one TA needs at least that TA's group count of distinct periods,
  and all labs together need at least ceil(total lab groups / general-lab
  count). Sound as a lower bound; it may understate demand, never
  overstate it.
- strict_cohort_mode: courses of one cohort can never share a period, so
  the per-course bounds add up (the audited plan-116 bound).

Everything the run gets wrong on top of that is expressed as a structured
Insight (see utils/insights.py). This module never mutates state and is
safe to run offline on fixtures.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from models.block import Block, BlockType
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
from utils.time_utils import (
    add_minutes,
    intervals_overlap,
    preference_covers_slot,
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


def _block_cohorts(block: Block) -> List[CohortKey]:
    return list(zip(block.academic_lists, block.academic_levels, strict=False))


def _staff_available_period_count(
    staff_member, periods: Set[PeriodKey]
) -> Optional[int]:
    """Count grid periods fully inside one of the staff member's windows."""
    if not staff_member.timing_preferences:
        return None
    count = 0
    for day, start, end in periods:
        slot = TimePreference(day=day, start_time=start, end_time=end)
        if any(
            preference_covers_slot(pref, slot)
            for pref in staff_member.timing_preferences
        ):
            count += 1
    return count


def _lab_pool_for(block: Block, labs: List[Lab]) -> List[Lab]:
    """The rooms get_suitable_rooms would draw from (mirrors its pool rule)."""
    if block.required_room_type != "lab":
        return []
    if block.preferred_rooms:
        return list(block.preferred_rooms)
    return [lab for lab in labs if lab.used_in_non_specialist_courses]


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

    # (cohort, block_type, course codes) -> blocks of that course for that cohort
    course_groups: Dict[Tuple[CohortKey, str, Tuple[str, ...]], List[Block]] = (
        defaultdict(list)
    )
    for block in blocks:
        for cohort in _block_cohorts(block):
            course_groups[
                (cohort, block.block_type.value, tuple(sorted(block.course_codes)))
            ].append(block)

    per_cohort_summaries: List[Dict] = []
    general_lab_count = sum(
        1 for lab in labs if lab.used_in_non_specialist_courses
    )

    for cohort in sorted({key[0] for key in course_groups}):
        cohort_label = f"{cohort[0]} L{cohort[1]}"
        atomic_required = 0
        whole_cohort_sessions = 0
        lab_sessions = 0
        max_lab_bottleneck = 0
        course_rows: List[Dict] = []

        for (key_cohort, block_type, codes), course_blocks in sorted(
            course_groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
        ):
            if key_cohort != cohort:
                continue

            groups = len(course_blocks)
            per_staff: Dict[int, int] = defaultdict(int)
            for course_block in course_blocks:
                per_staff[course_block.staff_member.id] += 1
            distinct_staff = len(per_staff)
            bottleneck = max(per_staff.values())

            cohort_whole = [
                course_block
                for course_block in course_blocks
                if course_block.is_merged_common_course
                or course_block.is_single_group_course
            ]
            whole_cohort_sessions += len(cohort_whole)

            if block_type == BlockType.LAB.value:
                # Rotatable labs: sections may run them beside other courses'
                # sections; staff serialization and lab-room count bound how
                # fast they can be drained.
                rotatable = [
                    course_block for course_block in course_blocks
                    if course_block not in cohort_whole
                ]
                lab_sessions += len(rotatable)
                rot_per_staff: Dict[int, int] = defaultdict(int)
                for course_block in rotatable:
                    rot_per_staff[course_block.staff_member.id] += 1
                if rot_per_staff:
                    max_lab_bottleneck = max(
                        max_lab_bottleneck, max(rot_per_staff.values())
                    )

            atomic_required += bottleneck

            course_rows.append(
                {
                    "course_codes": list(codes),
                    "type": block_type,
                    "groups": groups,
                    "staff": [
                        {"id": staff_id, "groups": count}
                        for staff_id, count in sorted(per_staff.items())
                    ],
                    "distinct_staff": distinct_staff,
                    "atomic_bound": bottleneck,
                    "whole_cohort_sessions": len(cohort_whole),
                }
            )

        # Sections-mode lower bound: whole-cohort sessions exclude every other
        # session of the cohort, and the rotatable labs need at least
        # max(staff serialization, lab-room concurrency) further periods.
        lab_room_concurrency = math.ceil(lab_sessions / max(1, general_lab_count))
        sections_bound = whole_cohort_sessions + max(
            max_lab_bottleneck, lab_room_concurrency
        )

        cohort_summary = {
            "cohort": cohort_label,
            "required_periods_cohort_atomic": atomic_required,
            "required_periods_sections_bound": sections_bound,
            "whole_cohort_sessions": whole_cohort_sessions,
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
    # the same shortfall is a loud warning.
    demand_seen: Set[int] = set()
    for block in blocks:
        staff_member = block.staff_member
        if staff_member.id in demand_seen:
            continue
        demand_seen.add(staff_member.id)

        groups = sum(
            1
            for other in blocks
            if other.staff_member.id == staff_member.id
        )
        supply = _staff_available_period_count(staff_member, union_periods)
        if supply is None or groups <= supply:
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
                    f"(ID {staff_member.id}) holds {groups} session(s) but their "
                    f"recorded hours cover only {supply} period(s)."
                ),
                entities=[entity("staff", staff_member.id, staff_member.name)],
                evidence={"assigned_sessions": groups, "covered_periods": supply},
                recommended_action=(
                    "Widen the staff member's availability windows or move some of "
                    "their groups to other qualified staff."
                    if is_lecturer
                    else "Widen the TA's hours (or accept out-of-hours placements - "
                    "each one is reported), or move groups to other TAs."
                ),
            )
        )

    # Capacity: an overflow placement (seats < students) is allowed by policy
    # but must be loud. A block no room can host even at the fallback ratio is
    # left to the per-block pre-flight, which fails the run for it.
    for block in blocks:
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
                        f"{block.course_codes[0]} group {block.group_number} has "
                        f"{block.student_count} students but the largest suitable "
                        f"room seats {best} - it can only be placed as an overflow."
                    ),
                    entities=[
                        entity("course", block.course_codes[0], block.course_codes[0]),
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

    # Section-mapping assumption surface: positional sections (group k = the
    # same students in every course of the cohort) only aligns when courses
    # have matching group counts.
    for cohort in sorted({key[0] for key in course_groups}):
        counts = {
            codes[0]: groups
            for (key_cohort, block_type, codes), course_blocks in course_groups.items()
            if key_cohort == cohort
            and block_type == BlockType.LAB.value
            and (groups := len(course_blocks))
        }
        if len(set(counts.values())) > 1:
            cohort_label = f"{cohort[0]} L{cohort[1]}"
            report.insights.append(
                Insight(
                    code="SECTION_MAPPING_MISMATCH",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.WARNING,
                    phase="preflight",
                    verdict=(
                        f"Cohort {cohort_label} courses have unequal lab group "
                        f"counts ({counts}) - group numbers may not align into "
                        f"student sections across all courses."
                    ),
                    entities=[entity("cohort", f"{cohort[0]}:{cohort[1]}", cohort_label)],
                    evidence={"lab_group_counts": counts},
                    recommended_action=(
                        "Confirm the group-to-section mapping with the registrar, "
                        "or equalize group counts across the cohort's courses."
                    ),
                )
            )

    report.summary = {
        "periods_available_lab": len(lab_periods),
        "periods_available_hall": len(hall_periods),
        "periods_available_union": len(union_periods),
        "cohorts": per_cohort_summaries,
        "insights": summarize(report.insights),
    }
    return report
