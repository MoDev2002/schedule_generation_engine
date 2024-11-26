from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import Dict, List


class Day(Enum):
    SUNDAY = 0
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6


@dataclass
class TimePreference:
    day: Day
    start_time: time
    end_time: time

    def __str__(self):
        return (
            f"{self.day.name}: {self.start_time.strftime('%I:%M %p')} - "
            f"{self.end_time.strftime('%I:%M %p')}"
        )


class BaseAvailability:
    @staticmethod
    def generate_base_availability() -> List[TimePreference]:
        # Define start and end times for the day
        day_start = time(9, 0)  # 9 AM
        day_end = time(19, 0)  # 7 PM
        slot_duration = 2  # 2 hours per slot

        availability = []

        # Generate time slots for each day
        for day in [Day.SUNDAY, Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY, Day.THURSDAY]:
            current_time = day_start
            while current_time.hour + slot_duration <= day_end.hour:
                # Skip Monday 1-3 PM slot
                if (
                    day == Day.MONDAY
                    and current_time.hour == 13
                    or (day == Day.MONDAY and current_time.hour == 12)
                ):
                    current_time = time(current_time.hour + 1, 0)
                    continue

                slot_end = time(current_time.hour + slot_duration, 0)

                # Add the time slot
                availability.append(
                    TimePreference(day=day, start_time=current_time, end_time=slot_end)
                )

                # Move to next slot
                current_time = time(current_time.hour + slot_duration, 0)

        return availability

    @staticmethod
    def print_availability(availability: List[TimePreference]):
        # Group time slots by day
        day_slots: Dict[Day, List[TimePreference]] = {}
        for slot in availability:
            if slot.day not in day_slots:
                day_slots[slot.day] = []
            day_slots[slot.day].append(slot)

        # Print organized schedule
        print("\nBase Availability Schedule:")
        print("=" * 50)
        for day in sorted(day_slots.keys(), key=lambda x: x.value):
            print(f"\n{day.name}:")
            print("-" * 20)
            for slot in sorted(day_slots[day], key=lambda x: x.start_time):
                print(
                    f"  {slot.start_time.strftime('%I:%M %p')} - "
                    f"{slot.end_time.strftime('%I:%M %p')}"
                )


# Example usage
if __name__ == "__main__":
    # Generate base availability
    base_availability = BaseAvailability.generate_base_availability()

    # Print the schedule
    BaseAvailability.print_availability(base_availability)

    # Print total number of available slots
    print(f"\nTotal available time slots: {len(base_availability)}")
