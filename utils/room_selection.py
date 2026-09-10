# utils/room_selection.py


import logging
from typing import List, Optional, Set

logger = logging.getLogger("utils.room_selection")


def external_labs_outside_pool(
    external_lab_blocks: List,
    selected_lab_ids: Optional[List[int]],
) -> Set[int]:
    """
    External course ids whose declared eligible labs are ALL excluded by the
    run's lab selection.

    Unlike the soft-preference fallback below, an external course's declared
    labs are a hard requirement: scheduling its groups in an unrequested lab
    would be a silent reassignment. Callers refuse the run for any returned
    course; None selected_lab_ids (no pool restriction) never conflicts.
    """
    if selected_lab_ids is None:
        return set()

    allowed: Set[int] = set(selected_lab_ids)
    refused: Set[int] = set()
    for block in external_lab_blocks:
        preferred = getattr(block, "preferred_rooms", None)
        if not preferred:
            continue
        if not any(room.id in allowed for room in preferred):
            refused.add(block.external_course_id)
    return refused


def apply_room_selection(blocks: List, selected_lab_ids: Optional[List[int]]) -> None:
    """
    Restrict every block's preferred specialist labs to the schedule's
    selected lab pool, in place.

    A generation run may declare which labs are available (hall_ids/lab_ids on
    the request). Specialist labs arrive on blocks via course assignments and
    bypass the engine's filtered hall/lab lists, so the selection has to be
    applied here for every consumer of ``block.preferred_rooms``
    (ResourceManager.get_suitable_rooms, the constraint manager's
    lab-specialization rule, and the change validator's room pool).

    A block whose preferred labs are all excluded falls back to the
    general-purpose lab pool - preferred rooms are a soft preference, matching
    how ResourceManager already treats them when present vs absent.
    """
    if selected_lab_ids is None:
        return

    allowed = set(selected_lab_ids)
    for block in blocks:
        preferred = getattr(block, "preferred_rooms", None)
        if not preferred:
            continue

        filtered = [room for room in preferred if room.id in allowed]
        if len(filtered) != len(preferred):
            logger.debug(
                "Block %s: preferred labs narrowed by room selection (%d -> %d)",
                block.id,
                len(preferred),
                len(filtered),
            )
        block.preferred_rooms = filtered or None
