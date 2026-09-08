# solver/cpsat_scheduler.py
"""CP-SAT placement core.

Replaces the greedy attempt loop (room-first placement, single-victim
repair, 100 identical retries - audit findings #2/#3/#4) with one exact
constraint solve over blocks x rooms x periods:

- Coverage is a hard constraint: every block is placed, or the solver
  PROVES the input cannot be fully placed and names a minimal conflicting
  set of blocks (via assumptions), then falls back to a maximum-coverage
  partial. A partial always fails the posting gate downstream.
- Quality is a weighted objective, scaled lexicographically:
  overflow avoidance (dominant) > staff timing preferences (matching the
  constraint manager's soft weights) > day compaction (earliness).
- Deterministic: fixed seed, single search worker, deterministic time
  cap - same inputs produce the same schedule.

The clash rules come from utils/sections.py through the constraint
manager's mode, and every hard rule mirrors what ConstraintManager
enforces, so a solved schedule always commits cleanly.
"""

import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional, Set, Tuple

from models.block import Assignment, Block
from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer
from models.time_preferences import ReservedPeriod, TimePreference
from utils.room_utils import get_room_key
from utils.sections import blocks_clash, cohort_keys as sections_cohort_keys
from utils.time_utils import intervals_overlap

from ortools.sat.python import cp_model

logger = logging.getLogger("cpsat_scheduler")

SLOT_DURATION_MINUTES = 120
# Objective scale: keep the tiers lexicographic in practice.
PREFERENCE_UNIT = 100  # x soft weight (5 lecturer / 3 TA)
OVERFLOW_UNIT = 100_000  # per missing seat
EARLINESS_UNIT = 1  # per period index within the day


@dataclass
class SolveResult:
    status: str  # OPTIMAL | FEASIBLE | PARTIAL | INFEASIBLE | UNKNOWN
    assignments: Dict[str, Assignment] = field(default_factory=dict)
    skipped_block_ids: List[str] = field(default_factory=list)
    conflicting_block_ids: List[str] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)


def _periods_from_windows(
    windows: List[TimePreference],
    reserved_period: Optional[ReservedPeriod],
) -> List[Tuple]:
    """Whole 2h sessions inside each window, minus reserved overlaps."""
    periods: Set[Tuple] = set()
    for window in windows:
        cursor = window.start_time
        while cursor is not None:
            end_minute = cursor.hour * 60 + cursor.minute + SLOT_DURATION_MINUTES
            if end_minute > 24 * 60:
                break
            slot_end = time(end_minute // 60, end_minute % 60)
            if slot_end > window.end_time:
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
            cursor = _advance(cursor, SLOT_DURATION_MINUTES)
    return sorted(periods, key=lambda p: (p[0].value, p[1], p[2]))


def _advance(t: time, minutes: int) -> Optional[time]:
    total = t.hour * 60 + t.minute + minutes
    if total > 24 * 60:
        return None
    return time(total // 60, total % 60)


def _atomic_intervals(periods: List[Tuple]) -> List[Tuple]:
    """Split a day's periods at every boundary into atomic intervals."""
    by_day: Dict = {}
    for day, start, end in periods:
        by_day.setdefault(day, set()).update({start, end})
    atomics: List[Tuple] = []
    for day, boundaries in by_day.items():
        ordered = sorted(boundaries)
        for i in range(len(ordered) - 1):
            atomics.append((day, ordered[i], ordered[i + 1]))
    return atomics


def _covers(period: Tuple, atomic: Tuple) -> bool:
    p_day, p_start, p_end = period
    a_day, a_start, a_end = atomic
    return p_day == a_day and p_start <= a_start and a_end <= p_end


def _overlaps(p1: Tuple, p2: Tuple) -> bool:
    return p1[0] == p2[0] and intervals_overlap(
        p1[1], p1[2], p2[1], p2[2]
    )


def _room_pool(block: Block, halls: List[Hall], labs: List[Lab]) -> List:
    """Mirrors ResourceManager.get_suitable_rooms, overflow-inclusive."""
    if block.required_room_type == "lab":
        pool = (
            list(block.preferred_rooms)
            if block.preferred_rooms
            else [lab for lab in labs if lab.used_in_non_specialist_courses]
        )
    else:
        pool = list(halls)

    fitting = [room for room in pool if room.capacity >= block.student_count]
    if fitting:
        return fitting
    # Overflow is a policy-allowed fallback; the objective punishes it hard.
    return [
        room
        for room in pool
        if room.capacity >= block.student_count * 0.8
    ]


def _staff_covered(staff_member, period: Tuple) -> bool:
    slot = TimePreference(day=period[0], start_time=period[1], end_time=period[2])
    return any(
        pref.day == slot.day
        and pref.start_time <= slot.start_time
        and slot.end_time <= pref.end_time
        for pref in staff_member.timing_preferences
    )


def solve_schedule(
    blocks: List[Block],
    halls: List[Hall],
    labs: List[Lab],
    reserved_period: Optional[ReservedPeriod],
    constraint_manager,
    fixed_assignments: Optional[Dict[str, Assignment]] = None,
    seed: int = 0,
    time_budget_s: float = 60.0,
) -> SolveResult:
    """Solve the placement problem exactly; see the module docstring."""
    fixed_assignments = fixed_assignments or {}
    started = time_module.monotonic()

    room_periods: Dict[Tuple, List[Tuple]] = {}
    for room in [*halls, *labs]:
        room_periods[get_room_key(room)] = _periods_from_windows(
            room.availability, reserved_period
        )

    all_periods = sorted(
        {period for periods in room_periods.values() for period in periods},
        key=lambda p: (p[0].value, p[1], p[2]),
    )
    atomics = _atomic_intervals(all_periods)

    strict_mode = bool(getattr(constraint_manager, "strict_cohort_mode", False))

    # Candidate (room, period) pairs per block, minus anything a fixed
    # placement already forbids. Fixed blocks have no variables; excluding
    # conflicting candidates up front encodes their room/staff/cohort
    # occupation exactly.
    fixed_entries = list(fixed_assignments.values())
    candidates: Dict[str, List[Tuple[Tuple, Tuple]]] = {}
    for block in blocks:
        # A block that is itself pinned (defensive: callers normally keep
        # fixed entries out of the block list) gets exactly its pinned option.
        fixed_own = fixed_assignments.get(block.id)
        if fixed_own is not None:
            candidates[block.id] = [
                (
                    get_room_key(fixed_own.room),
                    (
                        fixed_own.time_slot.day,
                        fixed_own.time_slot.start_time,
                        fixed_own.time_slot.end_time,
                    ),
                )
            ]
            continue

        options: List[Tuple[Tuple, Tuple]] = []
        lecturer_binding = isinstance(block.staff_member, Lecturer) and bool(
            block.staff_member.timing_preferences
        )
        for room in _room_pool(block, halls, labs):
            for period in room_periods[get_room_key(room)]:
                if lecturer_binding and not _staff_covered(block.staff_member, period):
                    continue
                if any(
                    fixed.block.id != block.id
                    and _conflicts_with_fixed(block, room, period, fixed)
                    for fixed in fixed_entries
                ):
                    continue
                options.append((get_room_key(room), period))
        candidates[block.id] = sorted(options)

    model = cp_model.CpModel()
    x: Dict[str, Dict[Tuple, object]] = {}
    skips: Dict[str, object] = {}
    x_by_room_atomic: Dict[Tuple, List] = {}
    x_by_staff_atomic: Dict[Tuple, List] = {}
    pref_terms: List = []
    overflow_terms: List = []
    earliness_terms: List = []
    day_index: Dict = {}

    for day, _, _ in all_periods:
        day_index.setdefault(day, sorted(s for d, s, _ in all_periods if d == day))

    for block in blocks:
        block_id = block.id
        x[block_id] = {}
        skip = model.NewBoolVar(f"skip_{block_id}")
        skips[block_id] = skip

        preference_weight = (
            5 if isinstance(block.staff_member, Lecturer) else 3
        )

        for room_key, period in candidates[block_id]:
            var = model.NewBoolVar(
                f"x_{block_id}_{room_key[0]}_{room_key[1]}_"
                f"{period[0].value}_{period[1].strftime('%H%M')}"
            )
            x[block_id][(room_key, period)] = var

            if _staff_covered(block.staff_member, period):
                pref_terms.append(PREFERENCE_UNIT * preference_weight * var)
            room = _lookup_room(room_key, halls, labs)
            if room is not None and room.capacity < block.student_count:
                overflow_terms.append(
                    OVERFLOW_UNIT * (block.student_count - room.capacity) * var
                )
            index_in_day = day_index[period[0]].index(period[1])
            earliness_terms.append(EARLINESS_UNIT * index_in_day * var)

            for atomic in atomics:
                if _covers(period, atomic):
                    x_by_room_atomic.setdefault((room_key, atomic), []).append(var)
                    x_by_staff_atomic.setdefault(
                        (block.staff_member.id, atomic), []
                    ).append(var)

        model.Add(sum(x[block_id].values()) + skip == 1)

    # Exactly-one room / staff occupation per atomic interval.
    for occupancy in x_by_room_atomic.values():
        if len(occupancy) > 1:
            model.Add(sum(occupancy) <= 1)
    for occupancy in x_by_staff_atomic.values():
        if len(occupancy) > 1:
            model.Add(sum(occupancy) <= 1)

    # Student clashes, grouped the way utils/sections.py partitions them.
    cohort_blocks: Dict[Tuple, List[Block]] = {}
    for block in blocks:
        for cohort in set(sections_cohort_keys(block)):
            cohort_blocks.setdefault(cohort, []).append(block)

    for _cohort, cohort_block_list in cohort_blocks.items():
        sections: Dict[int, List[Block]] = {}
        whole: List[Block] = []
        for block in cohort_block_list:
            if block.is_merged_common_course or block.is_single_group_course:
                whole.append(block)
            else:
                sections.setdefault(block.group_number, []).append(block)

        # Same section = same students: one session per section at a time.
        for section_blocks in sections.values():
            section_vars_by_atomic: Dict[Tuple, List] = {}
            for block in section_blocks:
                for room_key, period in candidates[block.id]:
                    var = x[block.id][(room_key, period)]
                    for atomic in atomics:
                        if _covers(period, atomic):
                            section_vars_by_atomic.setdefault(atomic, []).append(var)
            for occupancy in section_vars_by_atomic.values():
                if len(occupancy) > 1:
                    model.Add(sum(occupancy) <= 1)

        # Whole-cohort blocks clash with everything of the cohort.
        for w_block in whole:
            for other in cohort_block_list:
                if other.id == w_block.id:
                    continue
                if not blocks_clash(w_block, other, strict_cohort_mode=strict_mode):
                    continue
                for w_key, w_period in candidates[w_block.id]:
                    w_var = x[w_block.id][(w_key, w_period)]
                    for o_key, o_period in candidates[other.id]:
                        if _overlaps(w_period, o_period):
                            model.Add(
                                w_var + x[other.id][(o_key, o_period)] <= 1
                            )

    # Fixed blocks clash with nothing here: conflicting candidates were
    # removed up front, and fixed-fixed clashes are pre-validated upstream.

    # Objective: overflow avoidance > preferences > day compaction.
    model.Maximize(
        sum(pref_terms) - sum(overflow_terms) - sum(earliness_terms)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget_s
    solver.parameters.max_deterministic_time = 30.0
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed

    # Pass 1: full coverage required. Each block gets a positive assumption
    # literal "must place me" (the documented pattern - sufficient-assumption
    # indices map straight back to these), implying its skip stays false.
    must_place: Dict[str, object] = {}
    for block in blocks:
        must = model.NewBoolVar(f"must_place_{block.id}")
        model.AddImplication(must, skips[block.id].Not())
        must_place[block.id] = must
    model.ClearAssumptions()
    for block in blocks:
        model.AddAssumption(must_place[block.id])
    status = solver.Solve(model)

    conflicting: List[str] = []
    if status == cp_model.INFEASIBLE:
        index_to_block = {
            must_place[block.id].Index(): block.id
            for block in blocks
        }
        conflicting = [
            index_to_block[index]
            for index in solver.SufficientAssumptionsForInfeasibility()
            if index in index_to_block
        ]
        logger.warning(
            "Full placement proved infeasible; minimal conflicting set: %s",
            conflicting,
        )
        # Pass 2: maximize coverage (skips dominated by the quality weights).
        model.ClearAssumptions()
        model.Minimize(
            sum(10_000_000 * skips[block.id] for block in blocks)
            + sum(pref_terms)
            - sum(overflow_terms)
            - sum(earliness_terms)
        )
        status = solver.Solve(model)

    elapsed = time_module.monotonic() - started

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(
            status="INFEASIBLE" if status == cp_model.INFEASIBLE else "UNKNOWN",
            conflicting_block_ids=conflicting,
            stats={
                "elapsed_s": round(elapsed, 2),
                "solver_status": solver.StatusName(status),
            },
        )

    assignments: Dict[str, Assignment] = {}
    skipped: List[str] = []
    rooms_by_key = {}
    for room in [*halls, *labs]:
        rooms_by_key[get_room_key(room)] = room

    for block in blocks:
        chosen = None
        for (room_key, period), var in x[block.id].items():
            if solver.BooleanValue(var):
                chosen = (room_key, period)
                break
        if chosen is None:
            skipped.append(block.id)
            continue
        room_key, period = chosen
        assignments[block.id] = Assignment(
            block=block,
            time_slot=TimePreference(day=period[0], start_time=period[1],
                                     end_time=period[2]),
            room=rooms_by_key[room_key],
        )

    final_status = "PARTIAL" if skipped else (
        "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
    )
    return SolveResult(
        status=final_status,
        assignments=assignments,
        skipped_block_ids=skipped,
        conflicting_block_ids=conflicting,
        stats={
            "elapsed_s": round(elapsed, 2),
            "solver_status": solver.StatusName(status),
            "branches": solver.NumBranches(),
            "conflicts": solver.NumConflicts(),
        },
    )


def _lookup_room(room_key: Tuple, halls: List[Hall], labs: List[Lab]):
    for room in [*halls, *labs]:
        if get_room_key(room) == room_key:
            return room
    return None


def _conflicts_with_fixed(
    block: Block, room, period: Tuple, fixed: Assignment
) -> bool:
    """True when a candidate (room, period) collides with a fixed placement."""
    fixed_block = fixed.block
    if get_room_key(fixed.room) == get_room_key(room) and _overlaps(
        period, (fixed.time_slot.day, fixed.time_slot.start_time,
                 fixed.time_slot.end_time)
    ):
        return True
    if fixed_block.staff_member.id == block.staff_member.id and _overlaps(
        period, (fixed.time_slot.day, fixed.time_slot.start_time,
                 fixed.time_slot.end_time)
    ):
        return True
    fixed_period = (
        fixed.time_slot.day,
        fixed.time_slot.start_time,
        fixed.time_slot.end_time,
    )
    return _overlaps(period, fixed_period) and blocks_clash(
        block, fixed_block
    )
