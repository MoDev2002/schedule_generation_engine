# utils/room_utils.py

from typing import Tuple, Union

from models.halls import Hall
from models.labs import Lab


def get_room_key(room: Union[Hall, Lab]) -> Tuple[str, int]:
    """
    Get composite key for a room that distinguishes between halls and labs.

    Args:
        room: Hall or Lab object

    Returns:
        Tuple of (room_type, room_id) where room_type is 'hall' or 'lab'
    """
    room_type = "hall" if isinstance(room, Hall) else "lab"
    return (room_type, room.id)
