from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Set, Tuple, Union

from models.halls import Hall
from models.labs import Lab
from models.staff_members import StaffMember, TeachingAssistant
from models.time_preferences import Day, TimePreference


@dataclass
class ResourcePool:
    """Holds all available resources and their current state"""

    halls: Dict[int, Hall]
    labs: Dict[int, Lab]
    general_labs: Set[int]  # IDs of general-purpose labs
    specialist_labs: Dict[str, Set[int]]  # course_code -> lab_ids
    staff_members: Dict[int, StaffMember]
    time_slots: List[TimePreference]


class ResourceManager:
    def __init__(
        self,
        halls: List[Hall],
        labs: List[Lab],
        time_slot_duration: int = 2,
        break_duration: int = 0,
    ):
        self.halls = {hall.id: hall for hall in halls}
        self.labs = {lab.id: lab for lab in labs}
        self.time_slot_duration = time_slot_duration
        self.break_duration = break_duration

        # Initialize specialized collections
        self.general_labs = set()
        self.specialist_labs = defaultdict(set)
        self._categorize_labs()

        # Initialize time slots
        self.time_slots = self._generate_time_slots()

        # Track resource usage
        self.hall_usage = defaultdict(int)
        self.lab_usage = defaultdict(int)
        self.staff_workload = defaultdict(int)

    def _categorize_labs(self):
        """Categorize labs into general and specialist pools"""
        for lab_id, lab in self.labs.items():
            if lab.used_in_non_specialist_courses:
                self.general_labs.add(lab_id)
            else:
                # Add to specialist pool - will be assigned to specific courses later
                pass

    def _generate_time_slots(self) -> List[TimePreference]:
        """Generate all possible time slots with proper breaks"""
        time_slots = []
        working_days = [
            Day.SUNDAY,
            Day.MONDAY,
            Day.TUESDAY,
            Day.WEDNESDAY,
            Day.THURSDAY,
        ]

        day_start = time(9, 0)  # 9 AM
        day_end = time(19, 0)  # 7 PM

        for day in working_days:
            current_time = day_start
            while current_time.hour + self.time_slot_duration <= day_end.hour:
                # Skip Monday 1-3 PM slot (prayer time)
                if day == Day.MONDAY and current_time.hour in [13, 14]:
                    current_time = time(current_time.hour + 1, 0)
                    continue

                slot_end = time(current_time.hour + self.time_slot_duration, 0)
                time_slots.append(
                    TimePreference(day=day, start_time=current_time, end_time=slot_end)
                )

                # Add break time
                if self.break_duration > 0:
                    current_time = time(
                        current_time.hour
                        + self.time_slot_duration
                        + self.break_duration,
                        0,
                    )
                else:
                    current_time = time(current_time.hour + self.time_slot_duration, 0)

        return time_slots

    def get_suitable_rooms(
        self, block, preferred_capacity_ratio: float = 0.8
    ) -> List[Union[Hall, Lab]]:
        """Get suitable rooms for a block based on type and capacity requirements"""
        required_capacity = block.student_count
        suitable_rooms = []

        if block.required_room_type == "lab":
            # Handle lab requirements
            if block.preferred_rooms:
                # Only use preferred labs
                suitable_rooms = [
                    self.labs[lab.id]
                    for lab in block.preferred_rooms
                    if lab.capacity >= (required_capacity * preferred_capacity_ratio)
                ]
            else:
                # Use general labs
                suitable_rooms = [
                    lab
                    for lab in self.labs.values()
                    if (
                        lab.used_in_non_specialist_courses
                        and lab.capacity
                        >= (required_capacity * preferred_capacity_ratio)
                    )
                ]
        else:
            # Handle hall requirements
            suitable_rooms = [
                hall
                for hall in self.halls.values()
                if hall.capacity >= (required_capacity * preferred_capacity_ratio)
            ]

        # Sort rooms by optimal capacity utilization
        return sorted(suitable_rooms, key=lambda r: abs(r.capacity - required_capacity))

    def get_available_slots(
        self, block, room: Union[Hall, Lab], existing_assignments: Dict
    ) -> List[TimePreference]:
        """Get available time slots for a block in a specific room"""
        available_slots = []

        # Get room's base availability
        base_slots = set((slot.day, slot.start_time) for slot in room.availability)

        # Remove slots that are already assigned
        used_slots = set()
        for assignment in existing_assignments.values():
            if assignment.room == room:
                used_slots.add(
                    (assignment.time_slot.day, assignment.time_slot.start_time)
                )

        # Get staff member's availability
        staff_slots = set(
            (slot.day, slot.start_time)
            for slot in block.staff_member.timing_preferences
        )

        # Find available slots (intersection of all constraints)
        available_slot_tuples = base_slots - used_slots
        if isinstance(block.staff_member, TeachingAssistant):
            # For TAs, prefer their available slots but don't restrict to them
            available_slot_tuples = sorted(
                available_slot_tuples, key=lambda x: x in staff_slots, reverse=True
            )
        else:
            # For lecturers, strictly follow their preferences
            available_slot_tuples = available_slot_tuples.intersection(staff_slots)

        # Convert back to TimePreference objects
        for day, start_time in available_slot_tuples:
            end_time = time(start_time.hour + self.time_slot_duration, 0)
            available_slots.append(
                TimePreference(day=day, start_time=start_time, end_time=end_time)
            )

        return available_slots

    def update_resource_usage(self, assignment):
        """Update usage statistics after making an assignment"""
        if isinstance(assignment.room, Hall):
            self.hall_usage[assignment.room.id] += 1
        else:
            self.lab_usage[assignment.room.id] += 1

        self.staff_workload[assignment.block.staff_member.id] += 1

    def get_resource_usage_stats(self) -> Dict:
        """Get statistics about resource utilization"""
        return {
            "hall_usage": dict(self.hall_usage),
            "lab_usage": dict(self.lab_usage),
            "staff_workload": dict(self.staff_workload),
        }

    def get_least_used_room(
        self, suitable_rooms: List[Union[Hall, Lab]]
    ) -> Union[Hall, Lab]:
        """Get the least used room from a list of suitable rooms"""
        return min(
            suitable_rooms,
            key=lambda r: (
                self.hall_usage[r.id] if isinstance(r, Hall) else self.lab_usage[r.id]
            ),
        )

    def balance_staff_workload(
        self, block, possible_staff: List[StaffMember]
    ) -> StaffMember:
        """Choose staff member with lowest current workload"""
        return min(possible_staff, key=lambda s: self.staff_workload[s.id])
