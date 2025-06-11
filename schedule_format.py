# schedule_format.py


import json
from collections import defaultdict
from datetime import datetime
from typing import Dict

from models.block import Assignment, BlockType
from models.labs import Lab
from utils.room_utils import get_room_key


def format_schedule(assignments: Dict[str, Assignment]) -> str:
    """Format schedule into readable output"""
    # Group assignments by day and time
    schedule_by_day = defaultdict(lambda: defaultdict(list))

    for block_id, assignment in assignments.items():
        day = assignment.time_slot.day
        start_time = assignment.time_slot.start_time
        schedule_by_day[day][start_time].append(assignment)

    # Format output
    output = []
    output.append("=" * 100)
    output.append("UNIVERSITY SCHEDULE")
    output.append("=" * 100)

    # Sort days and times
    for day in sorted(schedule_by_day.keys(), key=lambda x: x.value):
        output.append(f"\n{day.name}")
        output.append("-" * 100)

        for start_time in sorted(schedule_by_day[day].keys()):
            output.append(f"\n{start_time.strftime('%I:%M %p')}:")

            # Sort assignments by type (lectures first, then labs)
            assignments = sorted(
                schedule_by_day[day][start_time],
                key=lambda x: (x.block.block_type.value, x.block.course_code),
            )

            for assignment in assignments:
                block = assignment.block
                room = assignment.room

                # Format session type and group info
                session_type = (
                    "Lecture" if block.block_type == BlockType.LECTURE else "Lab"
                )
                group_info = f"Group {block.group_number}/{block.total_groups}"

                # Format basic info
                info = [
                    f"Course: {block.course_code}",
                    f"Type: {session_type}",
                    f"Group: {group_info}",
                    f"Room: {room.name} (Capacity: {room.capacity})",
                    f"Staff: {block.staff_member.name}",
                ]

                output.append("    " + " | ".join(info))

                # Add staff details
                staff_details = [
                    f"      Staff Department: {block.staff_member.department.value}",
                    f"      Academic Degree: {block.staff_member.academic_degree.value}",
                ]
                output.extend(staff_details)

                # Add room type for labs
                if isinstance(room, Lab):
                    output.append(f"      Lab Type: {room.lab_type.value}")

                output.append("    " + "-" * 80)  # Separator between assignments

    return "\n".join(output)


def print_schedule_statistics(assignments: Dict[str, Assignment]):
    """Print statistics about the schedule"""
    # Initialize counters
    total_sessions = len(assignments)
    lectures = 0
    labs = 0
    rooms_used = set()
    staff_assigned = set()
    courses = set()

    for assignment in assignments.values():
        # Count session types
        if assignment.block.block_type == BlockType.LECTURE:
            lectures += 1
        else:
            labs += 1

        # Track unique resources
        rooms_used.add(assignment.room.name)
        staff_assigned.add(assignment.block.staff_member.name)
        courses.add(assignment.block.course_code)

    # Print statistics
    print("\n" + "=" * 50)
    print("SCHEDULE STATISTICS")
    print("=" * 50)
    print(f"Total Sessions: {total_sessions}")
    print(f"Total Lectures: {lectures}")
    print(f"Total Labs: {labs}")
    print(f"Unique Rooms Used: {len(rooms_used)}")
    print(f"Staff Members Involved: {len(staff_assigned)}")
    print(f"Courses Scheduled: {len(courses)}")
    print("=" * 50)


def generate_schedule_report(
    assignments: Dict[str, Assignment], output_file: str = "schedule_report.txt"
):
    """Generate a complete schedule report"""
    with open(output_file, "w", encoding="utf-8") as f:
        # Write formatted schedule
        f.write(format_schedule(assignments))

        # Add statistics
        f.write("\n\n")

        # Collect statistics
        total_sessions = len(assignments)
        lectures = sum(
            1 for a in assignments.values() if a.block.block_type == BlockType.LECTURE
        )
        labs = total_sessions - lectures

        rooms_used = len({a.room.name for a in assignments.values()})
        staff_assigned = len({a.block.staff_member.name for a in assignments.values()})
        courses = len({a.block.course_code for a in assignments.values()})

        # Write statistics
        f.write("=" * 50 + "\n")
        f.write("SCHEDULE STATISTICS\n")
        f.write("=" * 50 + "\n")
        f.write(f"Total Sessions: {total_sessions}\n")
        f.write(f"Total Lectures: {lectures}\n")
        f.write(f"Total Labs: {labs}\n")
        f.write(f"Unique Rooms Used: {rooms_used}\n")
        f.write(f"Staff Members Involved: {staff_assigned}\n")
        f.write(f"Courses Scheduled: {courses}\n")
        f.write("=" * 50 + "\n")

    print(f"Schedule report generated: {output_file}")


def generate_schedule_json(
    assignments: Dict[str, Assignment], output_file: str = "schedule.json"
):
    """Generate a JSON representation of the schedule"""
    # Convert the schedule to a serializable format
    serialized_schedule = []

    for block_id, assignment in assignments.items():
        block = assignment.block
        room = assignment.room
        time_slot = assignment.time_slot

        # Use composite key for room identification
        room_type, room_id = get_room_key(room)
        lab_type = room.lab_type.value if room_type == "lab" else None

        # Create serializable object for this assignment
        serialized_assignment = {
            "block_id": block_id,
            "course_code": block.course_code,
            "session_type": block.block_type.value,
            "group_info": {
                "group_number": block.group_number,
                "total_groups": block.total_groups,
            },
            "room": {
                "composite_id": f"{room_type}_{room_id}",  # Add composite identifier
                "id": room_id,
                "name": room.name,
                "capacity": room.capacity,
                "type": room_type,
            },
            "staff": {
                "id": block.staff_member.id,
                "name": block.staff_member.name,
                "department": block.staff_member.department.value,
                "academic_degree": block.staff_member.academic_degree.value,
                "is_permanent": block.staff_member.is_permanent,
            },
            "time_slot": {
                "day": time_slot.day.name,
                "day_index": time_slot.day.value,
                "start_time": time_slot.start_time.strftime("%H:%M"),
                "end_time": time_slot.end_time.strftime("%H:%M"),
            },
            "student_count": block.student_count,
            "academic_list": block.academic_list,
            "academic_level": block.academic_level,
        }

        # Add lab-specific info if applicable
        if room_type == "lab":
            serialized_assignment["room"]["lab_type"] = lab_type
            serialized_assignment["room"][
                "used_in_non_specialist_courses"
            ] = room.used_in_non_specialist_courses

        serialized_schedule.append(serialized_assignment)

    # Sort by day and start time for consistency
    serialized_schedule.sort(
        key=lambda x: (x["time_slot"]["day_index"], x["time_slot"]["start_time"])
    )

    # Create metadata with composite key stats
    room_keys = set(
        get_room_key(assignment.room) for assignment in assignments.values()
    )
    metadata = {
        "total_sessions": len(assignments),
        "total_courses": len(set(a["course_code"] for a in serialized_schedule)),
        "total_rooms": len(room_keys),  # Use composite keys for accurate count
        "total_staff": len(set(a["staff"]["name"] for a in serialized_schedule)),
        "generation_timestamp": datetime.now().isoformat(),
    }

    # Create final output object
    output = {"metadata": metadata, "schedule": serialized_schedule}

    # Write to file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Schedule JSON file generated: {output_file}")
    return output


# Usage example:
if __name__ == "__main__":
    # Assuming we have assignments from our scheduler
    # print(format_schedule(assignments))
    # print_schedule_statistics(assignments))
    # generate_schedule_report(assignments)
    # generate_schedule_json(assignments)
    pass
