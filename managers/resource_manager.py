# managers/resource_manager.py


import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import time
from typing import Dict, List, Optional, Set, Tuple, Union

from models.halls import Hall
from models.labs import Lab
from models.staff_members import StaffMember, TeachingAssistant
from models.time_preferences import (
    Day,
    ReservedPeriod,
    TimePreference,
)
from utils.room_utils import get_room_key
from utils.time_utils import (
    add_minutes,
    intervals_overlap,
    preference_covers_slot,
    slot_set_permits,
    window_covers,
)


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
        reserved_period: Optional[ReservedPeriod],
        time_slot_duration: int = 2,
        break_duration: int = 0,
    ):
        self.logger = logging.getLogger("resource_manager")
        self.halls = {hall.id: hall for hall in halls}
        self.labs = {lab.id: lab for lab in labs}
        self.time_slot_duration = time_slot_duration
        self.break_duration = break_duration
        self.reserved_period = reserved_period

        # Initialize specialized collections
        self.general_labs = set()
        self.specialist_labs = defaultdict(set)
        self._categorize_labs()

        # Initialize time slots
        self.time_slots = self._generate_time_slots()

        # Track resource usage with composite keys
        self.room_usage = defaultdict(int)  # Change: single dict with composite keys
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
        """
        The nominal working grid, for reporting and diagnostics.

        Actual candidate sessions come from each room's own availability windows
        via get_available_slots - this is not consulted when scheduling.
        """
        working_days = [
            Day.SUNDAY,
            Day.MONDAY,
            Day.TUESDAY,
            Day.WEDNESDAY,
            Day.THURSDAY,
        ]

        day_start = time(9, 0)  # 9 AM
        day_end = time(19, 0)  # 7 PM

        time_slots = []
        for day in working_days:
            for slot in self._slots_in_window(
                TimePreference(day=day, start_time=day_start, end_time=day_end)
            ):
                if self._overlaps_reserved_period(slot):
                    continue
                time_slots.append(slot)

        return time_slots

    def _overlaps_reserved_period(self, slot: TimePreference) -> bool:
        return (
            self.reserved_period is not None
            and slot.day == self.reserved_period.day
            and intervals_overlap(
                slot.start_time,
                slot.end_time,
                self.reserved_period.start_time,
                self.reserved_period.end_time,
            )
        )

    def get_suitable_rooms(
        self, block, preferred_capacity_ratio: float = 0.8
    ) -> List[Union[Hall, Lab]]:
        """Get suitable rooms for a block based on type and capacity requirements"""
        # Roomless blocks (external-venue lectures) book no room of ours;
        # their candidates are periods, not (room, period) pairs.
        if not getattr(block, "requires_room", True):
            return []

        required_capacity = block.student_count

        if block.required_room_type == "lab":
            if block.preferred_rooms:
                pool = list(block.preferred_rooms)
            else:
                pool = [
                    lab
                    for lab in self.labs.values()
                    if lab.used_in_non_specialist_courses
                ]
        else:
            pool = list(self.halls.values())

        # Rooms that genuinely seat the group come first. The ratio is only a
        # fallback for when nothing does, and it is reported rather than silent -
        # an over-subscribed room is a real problem, not a preference.
        suitable_rooms = [r for r in pool if r.capacity >= required_capacity]

        if not suitable_rooms:
            suitable_rooms = [
                r
                for r in pool
                if r.capacity >= (required_capacity * preferred_capacity_ratio)
            ]
            if suitable_rooms:
                self.logger.warning(
                    f"Block {block.id} needs {required_capacity} seats but no "
                    f"{block.required_room_type} is that large; falling back to rooms "
                    f"at >= {preferred_capacity_ratio:.0%} capacity "
                    f"(largest available: {max(r.capacity for r in suitable_rooms)})"
                )

        # Sort rooms by optimal capacity utilization
        return sorted(suitable_rooms, key=lambda r: abs(r.capacity - required_capacity))

    def _slots_in_window(self, window: TimePreference) -> List[TimePreference]:
        """
        Every whole session that fits inside one availability window.

        Windows are free-form {day, startTime, endTime} rows, so a 09:00-17:00 row
        yields four 2-hour sessions rather than one, and a 09:00-10:00 row yields
        none. Stepping through the window is what makes broad windows usable and
        keeps short ones from producing a session the room cannot host.
        """
        slots = []
        step = self.time_slot_duration * 60
        stride = step + self.break_duration * 60

        cursor = window.start_time
        while cursor is not None:
            slot_end = add_minutes(cursor, step)
            if slot_end is None or not window_covers(
                window.start_time, window.end_time, cursor, slot_end
            ):
                break
            slots.append(
                TimePreference(day=window.day, start_time=cursor, end_time=slot_end)
            )
            cursor = add_minutes(cursor, stride)

        return slots

    @staticmethod
    def _half_slots(slot: TimePreference) -> List[TimePreference]:
        """The two 1h halves of a 2h grid slot (the slot itself when it
        cannot be split)."""
        mid = add_minutes(slot.start_time, 60)
        if mid is None or mid >= slot.end_time:
            return [slot]
        return [
            TimePreference(day=slot.day, start_time=slot.start_time, end_time=mid),
            TimePreference(day=slot.day, start_time=mid, end_time=slot.end_time),
        ]

    def _candidates_for_block(
        self, block, raw_candidates: Dict[Tuple, TimePreference]
    ) -> Dict[Tuple, TimePreference]:
        """Candidate slots at the block's own span: whole 2h slots, or both
        halves of each for a 1h session (two may pack back-to-back)."""
        if getattr(block, "session_hours", 2) != 1:
            return raw_candidates
        expanded: Dict[Tuple, TimePreference] = {}
        for slot in raw_candidates.values():
            for half in self._half_slots(slot):
                expanded[(half.day, half.start_time, half.end_time)] = half
        return expanded

    def get_available_slots(
        self, block, room: Union[Hall, Lab], existing_assignments: Dict
    ) -> List[TimePreference]:
        """Get available time slots for a block in a specific room"""
        room_key = get_room_key(room)

        # Candidate sessions the room can actually host, de-duplicated across
        # overlapping availability windows.
        candidates = {}
        for window in room.availability:
            for slot in self._slots_in_window(window):
                candidates[(slot.day, slot.start_time, slot.end_time)] = slot
        candidates = self._candidates_for_block(block, candidates)

        # Drop anything overlapping a session already booked in this room, or one
        # this staff member is already teaching elsewhere. Staffless blocks
        # (external labs without internal staff) only contend for the room.
        staff_id = block.staff_member.id if block.staff_member is not None else None
        busy = []
        for assignment in existing_assignments.values():
            same_room = (
                assignment.room is not None
                and get_room_key(assignment.room) == room_key
            )
            same_staff = (
                staff_id is not None
                and assignment.block.staff_member is not None
                and assignment.block.staff_member.id == staff_id
            )
            if same_room or same_staff:
                busy.append(assignment.time_slot)

        free = [
            slot
            for slot in candidates.values()
            if slot_set_permits(block.allowed_slots, slot)
            and not self._overlaps_reserved_period(slot)
            and not any(
                taken.day == slot.day
                and intervals_overlap(
                    slot.start_time, slot.end_time, taken.start_time, taken.end_time
                )
                for taken in busy
            )
        ]

        if block.staff_member is None:
            # No internal staff: no hours to honour, no preference to score.
            ordered = free
        else:
            preferences = block.staff_member.timing_preferences
            preferred = [
                slot
                for slot in free
                if any(preference_covers_slot(pref, slot) for pref in preferences)
            ]

            if isinstance(block.staff_member, TeachingAssistant):
                # TA hours are advisory: prefer them, fall back to any free slot. The
                # scheduler warns about placements made outside them.
                remaining = [slot for slot in free if slot not in preferred]
                ordered = preferred + remaining
            elif not preferences:
                # A lecturer with no recorded preferences would otherwise intersect to
                # nothing and become unschedulable. Treat them as fully available.
                self.logger.warning(
                    f"Lecturer {block.staff_member.name} (ID: {staff_id}) has no timing "
                    f"preferences recorded - treating as available for all room hours"
                )
                ordered = free
            else:
                # Lecturer hours are binding.
                ordered = preferred

        # Stable order so a run is reproducible; the scheduler shuffles between
        # attempts when it wants variation.
        return sorted(ordered, key=lambda s: (s.day.value, s.start_time, s.end_time))

    def get_available_periods(
        self, block, existing_assignments: Dict
    ) -> List[TimePreference]:
        """
        Available periods for a roomless block (external-venue lecture).

        Candidates are the nominal working grid - the same grid every room's
        windows step through - rather than any single room's windows. Only
        the block's staff (when present) and the reserved period constrain
        them; a roomless lecture may legally share its period with any room's
        bookings.
        """
        staff_id = block.staff_member.id if block.staff_member is not None else None
        busy = []
        for assignment in existing_assignments.values():
            if (
                staff_id is not None
                and assignment.block.staff_member is not None
                and assignment.block.staff_member.id == staff_id
            ):
                busy.append(assignment.time_slot)

        raw_candidates = {
            (slot.day, slot.start_time, slot.end_time): slot
            for slot in self.time_slots
        }
        free = [
            slot
            for slot in self._candidates_for_block(block, raw_candidates).values()
            if slot_set_permits(block.allowed_slots, slot)
            and not self._overlaps_reserved_period(slot)
            and not any(
                taken.day == slot.day
                and intervals_overlap(
                    slot.start_time, slot.end_time, taken.start_time, taken.end_time
                )
                for taken in busy
            )
        ]

        if block.staff_member is None:
            ordered = free
        else:
            preferences = block.staff_member.timing_preferences
            preferred = [
                slot
                for slot in free
                if any(preference_covers_slot(pref, slot) for pref in preferences)
            ]
            if isinstance(block.staff_member, TeachingAssistant):
                remaining = [slot for slot in free if slot not in preferred]
                ordered = preferred + remaining
            elif not preferences:
                ordered = free
            else:
                # Lecturer hours are binding, room or no room: the reserved
                # lecturer time is ours even when the hall is not.
                ordered = preferred

        return sorted(ordered, key=lambda s: (s.day.value, s.start_time, s.end_time))

    def update_resource_usage(self, assignment):
        """Update usage statistics after making an assignment"""
        if assignment.room is not None:
            room_key = get_room_key(assignment.room)  # Use composite key
            self.room_usage[room_key] += 1
        if assignment.block.staff_member is not None:
            self.staff_workload[assignment.block.staff_member.id] += 1

    def get_resource_usage_stats(self) -> Dict:
        """Get statistics about resource utilization"""
        return {
            "room_usage": dict(self.room_usage),  # Now uses composite keys
            "staff_workload": dict(self.staff_workload),
        }

    def get_least_used_room(
        self, suitable_rooms: List[Union[Hall, Lab]]
    ) -> Union[Hall, Lab]:
        """Get the least used room from a list of suitable rooms"""
        return min(
            suitable_rooms,
            key=lambda r: self.room_usage[get_room_key(r)],  # Use composite key
        )

    def balance_staff_workload(
        self, block, possible_staff: List[StaffMember]
    ) -> StaffMember:
        """Choose staff member with lowest current workload"""
        return min(possible_staff, key=lambda s: self.staff_workload[s.id])
