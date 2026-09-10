# services/change_validator.py

"""
Validation of hypothetical schedule changes (move / swap) for the web editor.

The generator is the only component that owns the hard/soft constraint rules,
so edit operations coming from the frontend are validated here instead of being
re-implemented in TypeScript or PHP. Schedule state is rebuilt from the saved
schedule through the same load path the continuation feature uses
(generation-context -> study plans -> blocks -> reconcile -> seed), cached per
(schedule_id, state_version), and every check runs against a disposable copy of
the indexes so validation can never corrupt the cached master state.

Entry identity: DB schedule_entries.id -> state block id, either
"source_entry_{id}" (matched against a rebuilt block by reconcile_schedule) or
"stale_entry_{id}" (a minimal Block rebuilt from the entry row itself, used
when the study plan drifted since the schedule was generated).
"""

import logging
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Union

from backend.get_academics import get_academic_list_by_id
from backend.get_external_courses import get_external_courses_by_ids
from backend.get_halls import get_halls
from backend.get_labs import get_labs
from backend.get_schedule import get_schedule_generation_context
from backend.get_schedule_settings import get_schedule_settings
from backend.get_staff_members import get_all_staff_members, reset_staff_cache
from backend.get_study_plans import get_study_plans_by_ids, get_study_plans_summary
from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList
from models.block import Assignment, Block, BlockType
from models.halls import Hall
from models.labs import Lab
from models.staff_members import StaffMember
from models.study_plan import CourseAssignment
from models.time_preferences import Day, ReservedPeriod, TimePreference
from scheduler import SchedulingEngine
from utils.common_course_processor import ParentReferenceProcessor
from utils.room_selection import apply_room_selection
from utils.room_utils import get_room_key
from utils.schedule_continuation import entry_span_hours, reconcile_schedule
from utils.time_utils import intervals_overlap, slot_set_permits

logger = logging.getLogger("change_validator")

# can_assign() only reports constraint descriptions; map them to the stable
# codes the API contract (and therefore the frontend messages) rely on.
HARD_CONFLICT_CODES = {
    "No double room booking": "ROOM_CONFLICT",
    "No double staff booking": "STAFF_CONFLICT",
    "Room must be available in the given time slot": "ROOM_AVAILABILITY_CONFLICT",
    "Students cannot be in two sessions at once": "STUDENT_CONFLICT",
    "Lab specialization and preferences must be met": "LAB_REQUIREMENT_CONFLICT",
}

SOFT_CONSTRAINT_CODES = {
    "Lecturer timing preferences": "LECTURER_PREFERENCE",
    "Teaching Assistant timing preferences": "TA_PREFERENCE",
    "Minimize schedule gaps": "COHORT_GAP",
    "Room capacity utilization": "ROOM_CAPACITY",
}

STATE_CACHE_TTL = timedelta(minutes=10)
STATE_CACHE_MAX = 8


@dataclass
class LoadedSchedule:
    """Everything needed to simulate changes against one saved schedule."""

    schedule_id: int
    state_version: str
    constraint_manager: ConstraintManager
    resource_manager: ResourceManager
    halls: Dict[int, Hall]
    labs: Dict[int, Lab]
    entry_block_ids: Dict[int, str]
    entry_room_types: Dict[int, str]
    loaded_at: datetime
    warnings: List[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ScheduleStateCache:
    """
    Thread-safe cache of rebuilt schedule state: one entry per schedule.

    The version is checked against the caller's `state_version` (the Laravel
    `updated_at`): a mismatch reloads, because someone changed the schedule
    behind our back. An applied editor change updates both the cached state
    and the version in place (see `apply_change`), so an editing session never
    pays the slow reload while it is the only one touching the schedule.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: Dict[int, LoadedSchedule] = {}

    def get(
        self, schedule_id: int, state_version: Optional[str], refresh: bool = False
    ) -> LoadedSchedule:
        with self._lock:
            cached = self._entries.get(schedule_id)
            if (
                cached is not None
                and not refresh
                and state_version in (None, cached.state_version)
                and datetime.now() - cached.loaded_at < STATE_CACHE_TTL
            ):
                # Sliding expiry: an active editing session keeps its state.
                cached.loaded_at = datetime.now()
                return cached

        # Load outside the lock: the fetches hit the Laravel API and are slow.
        loaded = load_schedule_state(schedule_id, state_version or "")

        with self._lock:
            self._entries[schedule_id] = loaded
            while len(self._entries) > STATE_CACHE_MAX:
                oldest_key = min(self._entries, key=lambda k: self._entries[k].loaded_at)
                del self._entries[oldest_key]
        return loaded


def _parse_hm(value: str) -> time:
    hours, minutes = str(value).split(":")
    return time(int(hours), int(minutes))


def _slot_from_parts(day: str, start: str, end: str) -> TimePreference:
    return TimePreference(
        day=Day[str(day).upper()],
        start_time=_parse_hm(start),
        end_time=_parse_hm(end),
    )


def _study_plan_ids_for_entries(entries: List[Dict[str, Any]]) -> List[int]:
    """The study plans that cover the schedule's (academic list, level) cohorts."""
    cohorts = set()
    for entry in entries:
        for academic_id, level in zip(
            entry.get("academic_ids") or [], entry.get("academic_levels") or [],
            strict=False,
        ):
            cohorts.add((int(academic_id), int(level)))

    matching_ids = []
    for summary in get_study_plans_summary():
        cohort = (int(summary["academic_list_id"]), int(summary["academic_level"]))
        if cohort in cohorts:
            matching_ids.append(int(summary["id"]))
    return sorted(matching_ids)


def _entry_room(
    entry: Dict[str, Any], halls: Dict[int, Hall], labs: Dict[int, Lab]
) -> Optional[Union[Hall, Lab]]:
    hall_id = entry.get("hall_id")
    if hall_id is not None:
        return halls.get(int(hall_id))
    lab_id = entry.get("lab_id")
    if lab_id is not None:
        return labs.get(int(lab_id))
    return None


def _build_entry_block(
    entry: Dict[str, Any],
    staff_by_id: Dict[int, StaffMember],
    academics_by_id: Dict[int, AcademicList],
    external_course=None,
) -> Optional[Block]:
    """
    Rebuild a minimal schedulable Block from a raw schedule_entries row.

    Used for entries whose identity no longer matches a current study-plan
    block ("stale" entries). Staff timing preferences come from the staff
    fetch; preferred labs cannot be known without a matching course assignment,
    so the lab-specialization rule degrades to "any general-purpose lab".

    External entries rebuild from the external identity instead: no local
    course or academic data is consulted, and staff is genuinely optional
    (staffless labs, external-lecturer lectures).
    """
    if entry.get("entry_kind") == "external":
        lecturer_id = entry.get("lecturer_id")
        staff = (
            staff_by_id.get(int(lecturer_id)) if lecturer_id is not None else None
        )
        if lecturer_id is not None and staff is None:
            logger.warning(
                "Entry %s: staff %s not resolvable",
                entry.get("id"),
                entry.get("lecturer_id"),
            )
            return None

        is_lab = entry["session_type"] == "lab"
        total_groups = int(entry["total_groups"])
        roomless = entry.get("hall_id") is None and entry.get("lab_id") is None

        allowed_slots = None
        if external_course is not None:
            declared = (
                external_course.lab_time_slots
                if is_lab
                else external_course.lecture_time_slots
            )
            allowed_slots = list(declared) if declared else None

        return Block(
            id=f"stale_entry_{entry['id']}",
            course_codes=[],
            course_objects=[],
            block_type=BlockType.LAB if is_lab else BlockType.LECTURE,
            staff_member=staff,
            student_count=int(entry["student_count"]),
            required_room_type="lab" if is_lab else "hall",
            group_number=int(entry["group_number"]),
            total_groups=total_groups,
            is_single_group_course=total_groups <= 1,
            academic_lists=[],
            academic_list_objects=[],
            academic_levels=[],
            source_kind="external",
            external_course_id=(
                int(entry["external_course_id"])
                if entry.get("external_course_id") is not None
                else None
            ),
            requires_room=not roomless,
            allowed_slots=allowed_slots,
            session_hours=entry_span_hours(entry),
        )

    lecturer_id = entry.get("lecturer_id")
    # A forced lecturer deletion leaves entries without staff; they cannot be
    # rebuilt and fall back to the excluded-with-warning path.
    staff = staff_by_id.get(int(lecturer_id)) if lecturer_id is not None else None
    if staff is None:
        logger.warning(
            "Entry %s: staff %s not resolvable", entry.get("id"), entry.get("lecturer_id")
        )
        return None

    list_objects: List[AcademicList] = []
    list_names: List[str] = []
    levels: List[int] = []
    for academic_id, level in zip(
        entry.get("academic_ids") or [], entry.get("academic_levels") or [],
        strict=False,
    ):
        academic = academics_by_id.get(int(academic_id))
        if academic is None:
            logger.warning(
                "Entry %s: academic list %s not resolvable", entry.get("id"), academic_id
            )
            return None
        list_objects.append(academic)
        list_names.append(academic.name)
        levels.append(int(level))

    course_ids = [int(course_id) for course_id in entry.get("course_ids") or []]
    if not course_ids:
        logger.warning("Entry %s: no course ids", entry.get("id"))
        return None

    is_lab = entry["session_type"] == "lab"
    total_groups = int(entry["total_groups"])

    # Synthetic assignment that satisfies CourseAssignment validation while
    # carrying exactly the fields the constraint checks read.
    synthetic = CourseAssignment(
        id=-1,
        course_id=course_ids[0],
        course_code=str(course_ids[0]),
        lecture_groups=1,
        lecturers=[{"lecturer": staff, "num_of_groups": 1}],
        course_ids=course_ids,
        course_codes=[str(course_id) for course_id in course_ids],
        total_students=int(entry["student_count"]),
        academic_lists=list_names,
        academic_levels=levels,
    )

    return Block(
        id=f"stale_entry_{entry['id']}",
        course_codes=[str(course_id) for course_id in course_ids],
        course_objects=[synthetic],
        block_type=BlockType.LAB if is_lab else BlockType.LECTURE,
        staff_member=staff,
        student_count=int(entry["student_count"]),
        required_room_type="lab" if is_lab else "hall",
        group_number=int(entry["group_number"]),
        total_groups=total_groups,
        is_single_group_course=total_groups <= 1,
        academic_lists=list_names,
        academic_list_objects=list_objects,
        academic_levels=levels,
        # Stale rows predate the block model, so the merged flag is inferred
        # the same way the report renderer does: multiple attending lists on
        # one entry mean a merged common-course group.
        is_merged_common_course=len(list_names) > 1,
    )


def load_schedule_state(schedule_id: int, state_version: str) -> LoadedSchedule:
    """Rebuild and seed full constraint state for one saved schedule."""
    # Study-plan rebuild resolves staff through the shared process-lifetime
    # cache; drop it so a reload after an availability edit validates against
    # current data rather than the first fetch of this process.
    reset_staff_cache()
    context = get_schedule_generation_context(schedule_id)
    entries: List[Dict[str, Any]] = context.get("entries", [])
    if not entries:
        raise ValueError(f"Schedule {schedule_id} has no entries to validate")

    reserved_period: Optional[ReservedPeriod] = get_schedule_settings()

    # Scope the room pool to the schedule's recorded selection (None = every
    # room, for schedules generated before selections existed) so edit and
    # replace candidates never offer rooms the schedule may not use.
    available_hall_ids = context.get("available_hall_ids")
    available_lab_ids = context.get("available_lab_ids")

    halls_list = get_halls()
    labs_list = get_labs()
    if available_hall_ids is not None:
        wanted_halls = set(available_hall_ids)
        halls_list = [hall for hall in halls_list if hall.id in wanted_halls]
    if available_lab_ids is not None:
        wanted_labs = set(available_lab_ids)
        labs_list = [lab for lab in labs_list if lab.id in wanted_labs]
    halls = {hall.id: hall for hall in halls_list}
    labs = {lab.id: lab for lab in labs_list}

    warnings: List[str] = []
    entry_block_ids: Dict[int, str] = {}
    entry_room_types: Dict[int, str] = {}

    # Study-plan rebuild first, so entries reconcile onto real blocks with full
    # fidelity (staff objects, preferred labs, merged common courses).
    resource_manager = ResourceManager(halls_list, labs_list, reserved_period)
    engine = SchedulingEngine(ConstraintManager(), resource_manager)

    fixed_assignments: Dict[str, Assignment] = {}
    plan_ids = _study_plan_ids_for_entries(entries)
    expected_blocks: List[Block] = []
    if plan_ids:
        load_result = get_study_plans_by_ids(plan_ids, resolve_refs=True)
        # Unlike generation, validation must not refuse on drifted inputs: a
        # schedule whose study plan changed afterwards still has to be
        # editable. Plans that fail to load are skipped and their entries fall
        # back to being rebuilt from the saved rows.
        if load_result.errors:
            warnings.append(
                f"{len(load_result.errors)} study-plan assignment(s) no longer load "
                "cleanly; some entries were rebuilt from the saved rows"
            )
        processor = ParentReferenceProcessor()
        study_plans = processor.process_common_courses(load_result.study_plans)

        assignments_list: List[CourseAssignment] = []
        study_plan_mapping: Dict[int, Any] = {}
        for study_plan in study_plans:
            for course_assignment in study_plan.course_assignments:
                study_plan_mapping[len(assignments_list)] = study_plan
                assignments_list.append(course_assignment)

        expected_blocks.extend(
            engine.build_blocks(
                assignments_list, study_plan_mapping, include_labs=True
            )
        )

    # External entries reconcile against externally-built blocks, so staffless
    # labs and roomless lectures match with full fidelity instead of falling
    # to the stale-row rebuild.
    external_ids = sorted(
        {
            int(entry["external_course_id"])
            for entry in entries
            if entry.get("entry_kind") == "external"
            and entry.get("external_course_id") is not None
        }
    )
    if external_ids:
        ext_result = get_external_courses_by_ids(external_ids)
        if ext_result.errors:
            warnings.append(
                f"{len(ext_result.errors)} external course(s) no longer load "
                "cleanly; some entries were rebuilt from the saved rows"
            )
        expected_blocks.extend(
            engine.build_external_blocks(ext_result.external_courses)
        )
        external_courses_by_id = {
            course.id: course for course in ext_result.external_courses
        }
    else:
        external_courses_by_id = {}

    if plan_ids or external_ids:
        apply_room_selection(expected_blocks, available_lab_ids)
        reconciliation = reconcile_schedule(
            expected_blocks, entries, halls_list, labs_list
        )
        fixed_assignments = reconciliation.fixed_assignments
        if reconciliation.stale_entries_omitted:
            warnings.append(
                f"{reconciliation.stale_entries_omitted} entr(ies) no longer match "
                "current study plans and were rebuilt from the saved rows"
            )
    else:
        warnings.append("No matching study plans found; entries rebuilt from saved rows")

    constraint_manager = ConstraintManager()

    stale_staff: Optional[Dict[int, StaffMember]] = None
    stale_academics: Dict[int, AcademicList] = {}

    for entry in entries:
        entry_id = int(entry["id"])
        block_id = f"source_entry_{entry_id}"
        assignment = fixed_assignments.get(block_id)

        if assignment is None:
            if stale_staff is None:
                stale_staff = {member.id: member for member in get_all_staff_members()}

            for academic_id in entry.get("academic_ids") or []:
                aid = int(academic_id)
                if aid not in stale_academics:
                    academic = get_academic_list_by_id(aid)
                    if academic is not None:
                        stale_academics[aid] = academic

            block = _build_entry_block(
                entry,
                stale_staff,
                stale_academics,
                external_course=external_courses_by_id.get(
                    entry.get("external_course_id")
                ),
            )
            room = _entry_room(entry, halls, labs)
            # A roomless external entry legitimately has no room; anything
            # else without a resolvable room (or block) is excluded.
            roomless_external = (
                entry.get("entry_kind") == "external"
                and entry.get("hall_id") is None
                and entry.get("lab_id") is None
            )
            if block is None or (room is None and not roomless_external):
                warnings.append(
                    f"Entry {entry_id} could not be rebuilt and is excluded from validation"
                )
                continue
            assignment = Assignment(
                block=block,
                time_slot=_slot_from_parts(
                    entry["day"], entry["start_time"], entry["end_time"]
                ),
                room=room,
            )
            block_id = block.id

        if not constraint_manager.seed_fixed_assignment(block_id, assignment):
            warnings.append(f"Entry {entry_id} could not be seeded into state")
            continue

        entry_block_ids[entry_id] = block_id
        entry_room_types[entry_id] = (
            "lab" if entry["session_type"] == "lab" else "hall"
        )

    logger.info(
        "Loaded state for schedule %s (version %s): %s entries seeded",
        schedule_id,
        state_version or "none",
        len(entry_block_ids),
    )
    return LoadedSchedule(
        schedule_id=schedule_id,
        state_version=state_version,
        constraint_manager=constraint_manager,
        resource_manager=resource_manager,
        halls=halls,
        labs=labs,
        entry_block_ids=entry_block_ids,
        entry_room_types=entry_room_types,
        loaded_at=datetime.now(),
        warnings=warnings,
    )


def _clone_constraint_manager(cached: LoadedSchedule) -> ConstraintManager:
    """
    A disposable copy of the indexes; objects inside are treated as read-only.

    Cloning takes the schedule's lock so a concurrent `apply_change` can never
    be copied half-way.
    """
    with cached.lock:
        clone = ConstraintManager()
        clone.state = deepcopy(cached.constraint_manager.state)
        clone.current_assignments = dict(cached.constraint_manager.current_assignments)
    return clone


def _conflict(code: str, message: str, context: Optional[Dict[str, Any]] = None) -> Dict:
    payload = {"code": code, "message": message}
    if context:
        payload["context"] = context
    return payload


def _find_counterpart(
    cm: ConstraintManager, block: Block, slot: TimePreference, room
) -> Optional[str]:
    """Best-effort human hint at what the candidate placement collides with."""
    room_day = (
        cm.state.room_bookings.get(get_room_key(room), {}).get(slot.day, [])
        if room is not None
        else []
    )
    staff_day = (
        cm.state.staff_bookings.get(block.staff_member.id, {}).get(slot.day, [])
        if block.staff_member is not None
        else []
    )
    cohort_days: List[tuple] = []
    for cohort_key in cm._cohort_keys(block):
        cohort_days.extend(cm.state.cohort_bookings.get(cohort_key, {}).get(slot.day, []))

    for bookings in (room_day, staff_day, cohort_days):
        for start, end, blocker_id in bookings:
            if not intervals_overlap(slot.start_time, slot.end_time, start, end):
                continue
            other = cm.current_assignments.get(blocker_id)
            if other is None:
                continue
            codes = ", ".join(other.block.course_codes)
            window = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
            return f"{codes} ({slot.day.name.title()} {window})"
    return None


def _hard_conflicts(
    cm: ConstraintManager,
    block: Block,
    slot: TimePreference,
    room: Union[Hall, Lab],
    resource_manager: ResourceManager,
) -> List[Dict]:
    conflicts: List[Dict] = []

    if resource_manager._overlaps_reserved_period(slot):
        conflicts.append(
            _conflict(
                "RESERVED_PERIOD_CONFLICT",
                "The target period overlaps the reserved period",
                {
                    "day": slot.day.name.lower(),
                    "start_time": slot.start_time.strftime("%H:%M"),
                    "end_time": slot.end_time.strftime("%H:%M"),
                },
            )
        )

    # A course whose requesting faculty declared fixed time slots can only
    # ever sit inside them - moves and swaps alike.
    allowed_slots = getattr(block, "allowed_slots", None)
    if allowed_slots and not slot_set_permits(allowed_slots, slot):
        conflicts.append(
            _conflict(
                "FIXED_SLOT_CONFLICT",
                "The requesting faculty fixed this course to specific time "
                "slots; the target falls outside them",
                {
                    "day": slot.day.name.lower(),
                    "start_time": slot.start_time.strftime("%H:%M"),
                    "end_time": slot.end_time.strftime("%H:%M"),
                },
            )
        )

    for constraint in cm.hard_constraints:
        try:
            if constraint["func"](block, slot, room):
                continue
            code = HARD_CONFLICT_CODES.get(constraint["description"], "HARD_CONFLICT")
            context: Dict[str, Any] = {
                "room": room.name if room is not None else None,
                "day": slot.day.name.lower(),
                "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"),
            }
            counterpart = _find_counterpart(cm, block, slot, room)
            if counterpart:
                context["counterpart"] = counterpart
                message = f"{constraint['description']}: conflicts with {counterpart}"
            else:
                message = constraint["description"]
            conflicts.append(_conflict(code, message, context))
        except Exception as error:
            logger.error("Constraint %s raised: %s", constraint["description"], error)
            conflicts.append(
                _conflict("VALIDATION_ERROR", f"{constraint['description']}: {error}")
            )
    return conflicts


def _soft_scores(
    cm: ConstraintManager, block: Block, slot: TimePreference, room
) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for constraint in cm.soft_constraints:
        try:
            score = constraint["func"](block, slot, room)
        except Exception as error:
            logger.error("Soft constraint %s raised: %s", constraint["description"], error)
            score = 0.0
        code = SOFT_CONSTRAINT_CODES.get(constraint["description"], constraint["description"])
        scores[code] = score
    return scores


def _soft_weight_map(cm: ConstraintManager) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for constraint in cm.soft_constraints:
        code = SOFT_CONSTRAINT_CODES.get(constraint["description"], constraint["description"])
        weights[code] = constraint["weight"]
    return weights


def _weighted_total(weights: Dict[str, float], scores: Dict[str, float]) -> float:
    return sum(score * weights[code] for code, score in scores.items() if code in weights)


def _soft_context(block: Block, room) -> Dict[str, Any]:
    return {
        "staff_name": (
            block.staff_member.name if block.staff_member is not None else None
        ),
        "room_name": room.name if room is not None else None,
        "capacity": room.capacity if room is not None else None,
        "student_count": block.student_count,
    }


def _soft_regression_messages(
    code: str, before: float, after: float, context: Dict[str, Any]
) -> str:
    if code in ("LECTURER_PREFERENCE", "TA_PREFERENCE"):
        return f"{context['staff_name']} has no timing preference covering this slot"
    if code == "COHORT_GAP":
        return "Moving this session widens a gap between sessions for a cohort"
    if code == "ROOM_CAPACITY":
        return (
            f"{context['room_name']} seats {context['capacity']} but this session "
            f"has {context['student_count']} students"
        )
    return f"{code} regressed ({before:.2f} -> {after:.2f})"


def _soft_deltas(
    weights: Dict[str, float],
    block: Block,
    room,
    before_scores: Dict[str, float],
    after_scores: Dict[str, float],
) -> List[Dict]:
    deltas: List[Dict] = []
    context = _soft_context(block, room)
    for code, after in after_scores.items():
        before = before_scores.get(code, 0.0)
        if after < before - 1e-9:
            deltas.append(
                {
                    "code": code,
                    "message": _soft_regression_messages(code, before, after, context),
                    "before": round(before, 3),
                    "after": round(after, 3),
                    "context": context,
                }
            )
    return deltas


def _require_entry(cached: LoadedSchedule, entry_id: int) -> str:
    block_id = cached.entry_block_ids.get(int(entry_id))
    if block_id is None:
        raise KeyError(f"Entry {entry_id} is not part of schedule {cached.schedule_id}")
    return block_id


def _require_grid_slot(
    resource_manager: ResourceManager, slot: TimePreference, session_hours: int = 2
) -> None:
    # A 1h session targets one half of a grid slot; anything else targets a
    # whole 2h slot.
    candidates: List[TimePreference] = []
    for grid_slot in resource_manager.time_slots:
        candidates.append(grid_slot)
        if session_hours == 1:
            candidates.extend(ResourceManager._half_slots(grid_slot))

    for candidate in candidates:
        if (
            candidate.day == slot.day
            and candidate.start_time == slot.start_time
            and candidate.end_time == slot.end_time
        ):
            return
    if session_hours == 1:
        raise ValueError(
            "Target must be a 1-hour session inside the working grid "
            "(Sun-Thu 09:00-19:00, excluding the reserved period)"
        )
    raise ValueError(
        "Target must be a whole 2-hour session inside the working grid "
        "(Sun-Thu 09:00-19:00, excluding the reserved period)"
    )


def _room_for_spec(cached: LoadedSchedule, room_type: str, room_id: int):
    room = cached.labs.get(int(room_id)) if room_type == "lab" else cached.halls.get(int(room_id))
    if room is None:
        raise ValueError(f"Unknown {room_type} with id {room_id}")
    return room


def simulate_move(
    cached: LoadedSchedule,
    entry_id: int,
    slot: TimePreference,
    room: Optional[Union[Hall, Lab]] = None,
) -> Dict[str, Any]:
    """Validate moving one entry to (slot, room); room None keeps the current room."""
    block_id = _require_entry(cached, entry_id)
    original = cached.constraint_manager.current_assignments[block_id]
    target_room = room if room is not None else original.room
    _require_grid_slot(
        cached.resource_manager,
        slot,
        getattr(original.block, "session_hours", 2),
    )

    clone = _clone_constraint_manager(cached)
    clone.undo_assignment(block_id)

    hard = _hard_conflicts(
        clone, original.block, slot, target_room, cached.resource_manager
    )
    weights = _soft_weight_map(clone)
    before_scores = _soft_scores(clone, original.block, original.time_slot, original.room)

    if hard:
        return {
            "ok": False,
            "hard_conflicts": hard,
            "soft_deltas": [],
            "score_before": round(_weighted_total(weights, before_scores), 3),
            "score_after": None,
        }

    after_scores = _soft_scores(clone, original.block, slot, target_room)
    return {
        "ok": True,
        "hard_conflicts": [],
        "soft_deltas": _soft_deltas(
            weights, original.block, target_room, before_scores, after_scores
        ),
        "score_before": round(_weighted_total(weights, before_scores), 3),
        "score_after": round(_weighted_total(weights, after_scores), 3),
    }


def simulate_swap(cached: LoadedSchedule, entry_a_id: int, entry_b_id: int) -> Dict[str, Any]:
    """
    Validate exchanging day, time, and room between two entries.

    Both entries are unseeded first (each must not collide with itself), then A
    is placed against the remaining state and committed into the clone so B is
    checked against A's new placement with the real constraint code.
    """
    if int(entry_a_id) == int(entry_b_id):
        raise ValueError("An entry cannot be swapped with itself")
    block_a_id = _require_entry(cached, entry_a_id)
    block_b_id = _require_entry(cached, entry_b_id)

    assignment_a = cached.constraint_manager.current_assignments[block_a_id]
    assignment_b = cached.constraint_manager.current_assignments[block_b_id]

    # A roomless external-venue session cannot trade places with a roomed
    # one: the swap would fake a room booking onto one and tear one off the
    # other.
    if (assignment_a.room is None) != (assignment_b.room is None):
        return {
            "ok": False,
            "hard_conflicts": [
                _conflict(
                    "ROOM_SHAPE_MISMATCH",
                    "A roomless external-venue session cannot swap with a roomed session",
                )
            ],
            "soft_deltas": [],
            "score_before": None,
            "score_after": None,
        }

    clone = _clone_constraint_manager(cached)
    clone.undo_assignment(block_a_id)
    clone.undo_assignment(block_b_id)

    hard = _hard_conflicts(
        clone, assignment_a.block, assignment_b.time_slot, assignment_b.room,
        cached.resource_manager,
    )
    if not hard:
        try:
            clone._add_to_state(
                f"swap_preview_{block_a_id}",
                Assignment(assignment_a.block, assignment_b.time_slot, assignment_b.room),
            )
        except Exception as error:
            hard = [_conflict("HARD_CONFLICT", str(error))]
    if not hard:
        hard = _hard_conflicts(
            clone, assignment_b.block, assignment_a.time_slot, assignment_a.room,
            cached.resource_manager,
        )

    # Soft comparison on a state that contains neither entry, so both sides of
    # the swap are scored against the same baseline.
    weights = _soft_weight_map(clone)
    soft_clone = _clone_constraint_manager(cached)
    soft_clone.undo_assignment(block_a_id)
    soft_clone.undo_assignment(block_b_id)

    soft_deltas: List[Dict] = []
    for assignment, new_slot, new_room in (
        (assignment_a, assignment_b.time_slot, assignment_b.room),
        (assignment_b, assignment_a.time_slot, assignment_a.room),
    ):
        before_scores = _soft_scores(
            soft_clone, assignment.block, assignment.time_slot, assignment.room
        )
        after_scores = _soft_scores(soft_clone, assignment.block, new_slot, new_room)
        soft_deltas.extend(
            _soft_deltas(weights, assignment.block, new_room, before_scores, after_scores)
        )

    return {
        "ok": not hard,
        "hard_conflicts": hard,
        "soft_deltas": soft_deltas,
        "score_before": None,
        "score_after": None,
    }


def _room_pool(cached: LoadedSchedule, block: Block) -> List[Union[Hall, Lab]]:
    """Candidate rooms for a block, mirroring ResourceManager.get_suitable_rooms' pool."""
    if block.required_room_type == "lab":
        if block.preferred_rooms:
            return list(block.preferred_rooms)
        return [lab for lab in cached.labs.values() if lab.used_in_non_specialist_courses]
    return list(cached.halls.values())


def evaluate_move_targets(cached: LoadedSchedule, entry_id: int) -> List[Dict]:
    """
    Viability of every grid slot x candidate room for one entry, in one pass.

    The entry is unseeded once; neither can_assign nor the soft evaluators
    mutate state, so every (slot, room) pair is checked against the same
    state-minus-self without further copying.
    """
    block_id = _require_entry(cached, entry_id)
    original = cached.constraint_manager.current_assignments[block_id]

    clone = _clone_constraint_manager(cached)
    clone.undo_assignment(block_id)

    weights = _soft_weight_map(clone)
    before_total = _weighted_total(
        weights,
        _soft_scores(clone, original.block, original.time_slot, original.room),
    )

    targets: List[Dict] = []
    # A 1h block targets either half of every grid slot; 2h blocks the slots.
    session_hours = getattr(original.block, "session_hours", 2)
    target_slots: List[TimePreference] = []
    for grid_slot in cached.resource_manager.time_slots:
        if session_hours == 1:
            target_slots.extend(ResourceManager._half_slots(grid_slot))
        else:
            target_slots.append(grid_slot)

    for slot in target_slots:
        rooms_payload: List[Dict] = []
        slot_viable = False

        # Slots outside the course's declared fixed set are reported, but
        # never viable - the faculty requirement is a hard restriction.
        slot_permitted = slot_set_permits(
            getattr(original.block, "allowed_slots", None), slot
        )
        slot_conflicts = [] if slot_permitted else ["FIXED_SLOT_CONFLICT"]

        # Roomless entries move on periods alone: no room dimension exists.
        if original.room is None:
            hard = _hard_conflicts(
                clone, original.block, slot, None, cached.resource_manager
            )
            viable = not hard and slot_permitted
            targets.append(
                {
                    "day": slot.day.name.lower(),
                    "start_time": slot.start_time.strftime("%H:%M"),
                    "end_time": slot.end_time.strftime("%H:%M"),
                    "viable": viable,
                    "rooms": [],
                    "hard_conflicts": (
                        []
                        if viable
                        else [c["code"] for c in hard] + slot_conflicts
                    ),
                }
            )
            continue

        for room in _room_pool(cached, original.block):
            hard = _hard_conflicts(
                clone, original.block, slot, room, cached.resource_manager
            )
            viable = not hard and slot_permitted
            soft_delta = None
            if viable:
                after_total = _weighted_total(
                    weights, _soft_scores(clone, original.block, slot, room)
                )
                soft_delta = round(after_total - before_total, 3)

            rooms_payload.append(
                {
                    "id": room.id,
                    "type": "lab" if isinstance(room, Lab) else "hall",
                    "name": room.name,
                    "viable": viable,
                    "soft_delta": soft_delta,
                    "hard_conflicts": (
                        []
                        if viable
                        else [c["code"] for c in hard] + slot_conflicts
                    ),
                }
            )
            slot_viable = slot_viable or viable

        targets.append(
            {
                "day": slot.day.name.lower(),
                "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"),
                "viable": slot_viable,
                "rooms": rooms_payload,
            }
        )

    return targets


def evaluate_swap_candidates(cached: LoadedSchedule, entry_id: int) -> List[Dict]:
    """Viability of swapping the entry with every other same-room-type entry."""
    _require_entry(cached, entry_id)
    target_room_type = cached.entry_room_types[int(entry_id)]
    target_block_id = cached.entry_block_ids[int(entry_id)]
    target_is_roomless = (
        cached.constraint_manager.current_assignments[target_block_id].room is None
    )

    candidates: List[Dict] = []
    for other_id, room_type in cached.entry_room_types.items():
        if other_id == int(entry_id) or room_type != target_room_type:
            continue
        other_block_id = cached.entry_block_ids.get(other_id)
        if other_block_id is None:
            continue
        other_assignment = cached.constraint_manager.current_assignments.get(
            other_block_id
        )
        if other_assignment is None:
            continue
        # Roomless entries only ever swap with roomless ones (mirrors the
        # hard guard in simulate_swap, without paying for the simulation).
        if target_is_roomless != (other_assignment.room is None):
            continue
        try:
            outcome = simulate_swap(cached, int(entry_id), other_id)
        except KeyError:
            continue
        candidates.append(
            {
                "entry_id": other_id,
                "viable": outcome["ok"],
                "hard_conflicts": [
                    {"code": conflict["code"], "message": conflict["message"]}
                    for conflict in outcome["hard_conflicts"]
                ],
            }
        )
    return candidates


def apply_change(
    cache: "ScheduleStateCache",
    schedule_id: int,
    state_version: Optional[str],
    change: Dict[str, Any],
    new_version: str,
) -> bool:
    """
    Commit an already-validated change to the cached master state.

    Called by the Laravel API after the database write succeeded, so the next
    validation sees the schedule as it now exists without a full reload.
    Re-applies the same move/swap through seed_fixed_assignment. Returns False
    (leaving the cache to self-heal via a version-mismatch reload) when there
    is no matching cache entry.
    """
    with cache._lock:
        cached = cache._entries.get(schedule_id)
        if cached is None or (state_version and cached.state_version != state_version):
            return False

    change_type = change.get("type")

    with cached.lock:
        cm = cached.constraint_manager

        if change_type == "move":
            block_id = cached.entry_block_ids.get(int(change["entry_id"]))
            if block_id is None:
                return False
            original = cm.current_assignments.get(block_id)
            if original is None:
                return False
            target = change.get("target") or {}
            slot = _slot_from_parts(
                target.get("day"), target.get("start_time"), target.get("end_time")
            )
            room = original.room
            room_spec = change.get("room")
            if room_spec:
                room = _room_for_spec(cached, room_spec["type"], room_spec["id"])
            cm.undo_assignment(block_id)
            if not cm.seed_fixed_assignment(
                block_id, Assignment(original.block, slot, room)
            ):
                return False

        elif change_type == "swap":
            block_a_id = cached.entry_block_ids.get(int(change["entry_id"]))
            block_b_id = cached.entry_block_ids.get(int(change.get("target_entry_id")))
            if block_a_id is None or block_b_id is None:
                return False
            assignment_a = cm.current_assignments.get(block_a_id)
            assignment_b = cm.current_assignments.get(block_b_id)
            if assignment_a is None or assignment_b is None:
                return False
            cm.undo_assignment(block_a_id)
            cm.undo_assignment(block_b_id)
            if not cm.seed_fixed_assignment(
                block_a_id,
                Assignment(assignment_a.block, assignment_b.time_slot, assignment_b.room),
            ):
                return False
            if not cm.seed_fixed_assignment(
                block_b_id,
                Assignment(assignment_b.block, assignment_a.time_slot, assignment_a.room),
            ):
                return False

        else:
            return False

        cached.state_version = new_version
        cached.loaded_at = datetime.now()
    return True


def run_validation(cached: LoadedSchedule, change: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatch one editor change to its validation mode.

    Modes: move_targets (batch: every grid slot x room for the picker),
    move (one slot + optional room), swap_candidates (batch: every
    compatible entry), swap (one pair). Raises ValueError for malformed
    requests and KeyError for unknown entries.
    """
    change_type = change.get("type")
    entry_id = change.get("entry_id")
    if entry_id is None:
        raise ValueError("change.entry_id is required")

    if change_type == "move_targets":
        return {
            "ok": True,
            "targets": evaluate_move_targets(cached, int(entry_id)),
            "warnings": cached.warnings,
        }

    if change_type == "swap_candidates":
        return {
            "ok": True,
            "candidates": evaluate_swap_candidates(cached, int(entry_id)),
            "warnings": cached.warnings,
        }

    if change_type == "move":
        target = change.get("target")
        if not target:
            raise ValueError("move requires change.target {day, start_time, end_time}")
        slot = _slot_from_parts(target["day"], target["start_time"], target["end_time"])
        room = None
        room_spec = change.get("room")
        if room_spec:
            if not room_spec.get("type") or room_spec.get("id") is None:
                raise ValueError("change.room requires {type, id}")
            room = _room_for_spec(cached, room_spec["type"], room_spec["id"])
        result = simulate_move(cached, int(entry_id), slot, room)
        result["warnings"] = cached.warnings
        return result

    if change_type == "swap":
        target_entry_id = change.get("target_entry_id")
        if target_entry_id is None:
            raise ValueError("swap requires change.target_entry_id")
        result = simulate_swap(cached, int(entry_id), int(target_entry_id))
        result["warnings"] = cached.warnings
        return result

    raise ValueError(f"Unknown change type: {change_type}")
