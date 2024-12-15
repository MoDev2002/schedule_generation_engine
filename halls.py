from dataclasses import dataclass
from typing import List

from time_preferences import BaseAvailability, TimePreference


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


# Hall Seeding
B101 = Hall(1, "101B", 200, BaseAvailability.generate_base_availability())
B102 = Hall(2, "102B", 200, BaseAvailability.generate_base_availability())
B103 = Hall(3, "103B", 200, BaseAvailability.generate_base_availability())
B104 = Hall(4, "104B", 350, BaseAvailability.generate_base_availability())
B105 = Hall(5, "105B", 350, BaseAvailability.generate_base_availability())
H302 = Hall(6, "302H", 45, BaseAvailability.generate_base_availability())

halls = [B101, B102, B103, B104, B105, H302]

if __name__ == "__main__":
    print("Halls:")
    for hall in halls:
        print(hall)
