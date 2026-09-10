from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from models.block import Assignment, Block
from models.halls import Hall
from models.labs import Lab
from models.time_preferences import Day, TimePreference
from utils.time_utils import preference_covers_slot, slot_set_permits


@dataclass
class ReconciliationResult:
    fixed_assignments: Dict[str, Assignment]
    missing_blocks: List[Block]
    retained_entries: int
    stale_entries_omitted: int
    out_of_pool_entries_omitted: int = 0
    out_of_window_entries_regenerated: int = 0
    out_of_allowed_slot_entries_regenerated: int = 0


def _cohort_identity(academic_ids: List[int], academic_levels: List[int]) -> tuple:
    return tuple(sorted(zip(map(int, academic_ids), map(int, academic_levels))))


def entry_span_hours(entry: Dict[str, Any]) -> int:
    """Session span in hours from the entry's start/end times - the entry
    side of the session_hours identity member."""
    try:
        start = datetime.strptime(str(entry["start_time"]), "%H:%M")
        end = datetime.strptime(str(entry["end_time"]), "%H:%M")
    except (KeyError, TypeError, ValueError):
        return 2  # malformed rows are handled downstream; assume the default
    return max(1, int((end - start).total_seconds() // 3600))


def build_block_identity(block: Block) -> tuple:
    # External identity: the external course plus the session shape. Room
    # presence is part of the identity, so flipping a lecture's venue
    # (ours <-> external) or adding/removing its internal lecturer makes old
    # entries stale instead of pinning a stale room onto a roomless block.
    # session_hours is part of it too: a 2h entry must never be pinned onto
    # a 1h block (or vice versa) after a duration edit.
    if getattr(block, "source_kind", "course") == "external":
        return (
            "external",
            block.external_course_id,
            block.block_type.value,
            block.group_number,
            block.total_groups,
            block.staff_member.id if block.staff_member else None,
            bool(block.requires_room),
            getattr(block, "session_hours", 2),
        )
    return (
        tuple(sorted(block.get_all_course_ids())),
        block.block_type.value,
        _cohort_identity(
            [academic_list.id for academic_list in block.academic_list_objects],
            block.academic_levels,
        ),
        block.group_number,
        block.total_groups,
        block.staff_member.id,
    )


def build_entry_identity(entry: Dict[str, Any]) -> tuple:
    if entry.get("entry_kind") == "external":
        return (
            "external",
            int(entry["external_course_id"]),
            entry["session_type"],
            int(entry["group_number"]),
            int(entry["total_groups"]),
            (
                int(entry["lecturer_id"])
                if entry.get("lecturer_id") is not None
                else None
            ),
            bool(
                entry.get("hall_id") is not None or entry.get("lab_id") is not None
            ),
            entry_span_hours(entry),
        )
    return (
        tuple(sorted(map(int, entry["course_ids"]))),
        entry["session_type"],
        _cohort_identity(entry["academic_ids"], entry["academic_levels"]),
        int(entry["group_number"]),
        int(entry["total_groups"]),
        int(entry["lecturer_id"]),
    )


def _source_room(entry: Dict[str, Any], halls: Dict[int, Hall], labs: Dict[int, Lab]):
    """Room for a source entry, or None when it lies outside the run's pool.

    Room-pool selections make out-of-pool source rooms a normal case (the
    source schedule was built with a wider pool), so this is a skip-and-count
    situation upstream, not an error.
    """
    hall_id = entry.get("hall_id")
    lab_id = entry.get("lab_id")

    if hall_id is not None:
        return halls.get(int(hall_id))
    if lab_id is not None:
        return labs.get(int(lab_id))

    return None


def _source_time_slot(entry: Dict[str, Any]) -> TimePreference:
    try:
        return TimePreference(
            day=Day[entry["day"].upper()],
            start_time=datetime.strptime(entry["start_time"], "%H:%M").time(),
            end_time=datetime.strptime(entry["end_time"], "%H:%M").time(),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Source entry {entry['id']} has an invalid time slot") from error


def reconcile_schedule(
    expected_blocks: List[Block],
    source_entries: List[Dict[str, Any]],
    halls: List[Hall],
    labs: List[Lab],
) -> ReconciliationResult:
    """Keep matching source placements and return current blocks that still need scheduling."""
    expected_by_identity: Dict[tuple, List[Block]] = defaultdict(list)
    for block in expected_blocks:
        expected_by_identity[build_block_identity(block)].append(block)

    hall_by_id = {hall.id: hall for hall in halls}
    lab_by_id = {lab.id: lab for lab in labs}
    fixed_assignments = {}
    retained_entries = 0
    stale_entries_omitted = 0
    out_of_pool_entries_omitted = 0
    out_of_window_entries_regenerated = 0
    out_of_allowed_slot_entries_regenerated = 0

    for entry in source_entries:
        is_external = entry.get("entry_kind") == "external"

        if entry.get("lecturer_id") is None and not is_external:
            # A forced lecturer deletion leaves course entries without staff;
            # they can never match a current block, so they are regenerated.
            # External entries are exempt: staffless labs and external-lecturer
            # lectures legitimately have no staff, and their identity match
            # (which carries the staff id) decides staleness instead.
            stale_entries_omitted += 1
            continue

        matching_blocks = expected_by_identity.get(build_entry_identity(entry), [])
        if not matching_blocks:
            stale_entries_omitted += 1
            continue

        slot = _source_time_slot(entry)

        # The declaring faculty may have changed (or newly declared) the
        # course's fixed slots since the source schedule was generated. An
        # entry outside the CURRENT set is regenerated, exactly like the
        # out-of-window rule below keeps placements consistent with live
        # room hours.
        if not slot_set_permits(matching_blocks[0].allowed_slots, slot):
            out_of_allowed_slot_entries_regenerated += 1
            continue

        room = _source_room(entry, hall_by_id, lab_by_id)
        if room is None:
            if is_external and entry.get("hall_id") is None and entry.get("lab_id") is None:
                # A roomless external entry (external-venue lecture) is pinned
                # WITHOUT a room - it is not out of any pool.
                block = matching_blocks.pop(0)
                fixed_assignments[f"source_entry_{entry['id']}"] = Assignment(
                    block=block,
                    time_slot=slot,
                    room=None,
                )
                retained_entries += 1
                continue

            # The block stays pending so it is scheduled inside the run's pool.
            out_of_pool_entries_omitted += 1
            continue

        if not any(
            preference_covers_slot(window, slot) for window in room.availability
        ):
            # The source placement no longer fits the room's CURRENT hours -
            # availability was edited since the schedule was generated.
            # Regenerating keeps the new schedule consistent with live room
            # data instead of silently carrying a stale placement (the same
            # spirit as the out-of-pool rule above).
            out_of_window_entries_regenerated += 1
            continue

        block = matching_blocks.pop(0)
        fixed_assignments[f"source_entry_{entry['id']}"] = Assignment(
            block=block,
            time_slot=slot,
            room=room,
        )
        retained_entries += 1

    missing_blocks = [
        block for matching_blocks in expected_by_identity.values() for block in matching_blocks
    ]
    return ReconciliationResult(
        fixed_assignments=fixed_assignments,
        missing_blocks=missing_blocks,
        retained_entries=retained_entries,
        stale_entries_omitted=stale_entries_omitted,
        out_of_pool_entries_omitted=out_of_pool_entries_omitted,
        out_of_window_entries_regenerated=out_of_window_entries_regenerated,
        out_of_allowed_slot_entries_regenerated=out_of_allowed_slot_entries_regenerated,
    )
