# models/room.py
from typing import Union

from models.halls import Hall
from models.labs import Lab

Room = Union[Hall, Lab]
