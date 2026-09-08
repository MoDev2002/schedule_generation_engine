# utils/room_selection.py


import logging
from typing import List, Optional

logger = logging.getLogger("utils.room_selection")


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
