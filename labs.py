from dataclasses import dataclass
from enum import Enum
from typing import List

from models import TimePreference
from time_prefrences import BaseAvailability


class LabType(Enum):
    GENERAL = "general"
    SPECIALIST = "specialist"


@dataclass
class Lab:
    id: int
    name: str
    capacity: int
    availability: List[TimePreference]
    lab_type: LabType
    used_in_non_specialist_courses: bool = True

    def __post_init__(self):
        if self.capacity <= 0:
            raise ValueError("Lab capacity must be positive")
        if not self.availability:
            raise ValueError("Lab must have at least one availability slot")


# Labs Seeding

L401 = Lab(
    1, "401", 25, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L402 = Lab(
    2, "402", 25, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L403 = Lab(3, "403", 23, BaseAvailability.generate_base_availability(), LabType.GENERAL)
L407 = Lab(
    4, "407", 36, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L408 = Lab(
    5, "408", 23, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L409 = Lab(
    6, "409", 22, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L410 = Lab(
    7,
    "410",
    15,
    BaseAvailability.generate_base_availability(),
    LabType.SPECIALIST,
    False,
)
L411 = Lab(
    8, "411", 25, BaseAvailability.generate_base_availability(), LabType.SPECIALIST
)
L412 = Lab(
    9,
    "412",
    12,
    BaseAvailability.generate_base_availability(),
    LabType.SPECIALIST,
    False,
)
L413 = Lab(
    10,
    "413",
    25,
    BaseAvailability.generate_base_availability(),
    LabType.SPECIALIST,
    False,
)

L201 = Lab(
    10, "201", 33, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L202 = Lab(
    11, "202", 24, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L203 = Lab(
    12, "203", 25, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L204 = Lab(
    13, "204", 25, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L205 = Lab(
    14, "205", 28, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L206 = Lab(
    15,
    "206",
    10,
    BaseAvailability.generate_base_availability(),
    LabType.SPECIALIST,
    False,
)
L207 = Lab(
    16, "207", 38, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L208 = Lab(
    17, "208", 25, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L209 = Lab(
    18, "209", 24, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L210 = Lab(
    19, "210", 25, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L211 = Lab(
    20, "211", 24, BaseAvailability.generate_base_availability(), LabType.GENERAL
)
L213 = Lab(
    20,
    "213",
    25,
    BaseAvailability.generate_base_availability(),
    LabType.SPECIALIST,
    False,
)
L214 = Lab(
    21, "214", 22, BaseAvailability.generate_base_availability(), LabType.GENERAL
)

Labs = [
    L401,
    L402,
    L403,
    L407,
    L408,
    L409,
    L410,
    L411,
    L412,
    L413,
    L201,
    L202,
    L203,
    L204,
    L205,
    L206,
    L207,
    L208,
    L209,
    L210,
    L211,
    L213,
    L214,
]

if __name__ == "__main__":
    print("Labs:")
    for lab in Labs:
        print(lab)
