# models/halls.py

from dataclasses import dataclass
from typing import List

from models.time_preferences import BaseAvailability, TimePreference


@dataclass
class Hall:
    id: int
    name: str
    capacity: int
    availability: List[TimePreference]

    def __post_init__(self):
        if self.capacity <= 0:
            raise ValueError("Hall capacity must be positive")
        if not self.availability:
            raise ValueError("Hall must have at least one availability slot")


if __name__ == "__main__":
    pass
