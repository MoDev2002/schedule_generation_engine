# schedule_format.py


import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

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
                key=lambda x: (
                    x.block.block_type.value,
                    x.block.display_label(),
                ),
            )

            for assignment in assignments:
                block = assignment.block
                room = assignment.room

                # Format session type and group info
                session_type = (
                    "Lecture" if block.block_type == BlockType.LECTURE else "Lab"
                )
                group_info = f"Group {block.group_number}/{block.total_groups}"

                # External blocks render from their own identity; local blocks
                # from the course codes.
                if block.is_external:
                    course_display = block.display_label()
                else:
                    primary_course_code = block.course_codes[
                        0
                    ]  # Use first course code as primary
                    if len(block.course_codes) > 1:
                        course_display = (
                            f"{primary_course_code} (+{len(block.course_codes)-1} others)"
                        )
                    else:
                        course_display = primary_course_code

                # Handle multiple academic lists (always a list now); external
                # blocks have none. Merged groups carry only the lists that
                # actually attend the group, so a single-list merged group
                # keeps the merged indicator.
                if not block.academic_lists:
                    academic_info = "Academic Lists: none (external course)"
                    merged_indicator = ""
                elif getattr(block, "is_merged_common_course", False):
                    # For merged courses, show all academic lists with their levels
                    academic_details = []
                    for i, academic_list in enumerate(block.academic_lists):
                        level = block.academic_levels[i]
                        academic_details.append(f"{academic_list} L{level}")
                    academic_info = f"Academic Lists: {', '.join(academic_details)}"
                    merged_indicator = " [MERGED COMMON COURSE]"
                elif len(block.academic_lists) > 1:
                    academic_details = []
                    for i, academic_list in enumerate(block.academic_lists):
                        level = block.academic_levels[i]
                        academic_details.append(f"{academic_list} L{level}")
                    academic_info = f"Academic Lists: {', '.join(academic_details)}"
                    merged_indicator = ""
                else:
                    # Single academic list
                    academic_info = f"Academic List: {block.academic_lists[0]} L{block.academic_levels[0]}"
                    merged_indicator = ""

                # Format basic info; roomless sessions render their venue
                # explicitly, staffless ones their staffing.
                info = [
                    f"Course: {course_display}{merged_indicator}",
                    f"Type: {session_type}",
                    f"Group: {group_info}",
                    (
                        f"Room: {room.name} (Capacity: {room.capacity})"
                        if room is not None
                        else "Room: External venue"
                    ),
                    (
                        f"Staff: {block.staff_member.name}"
                        if block.staff_member is not None
                        else "Staff: No internal staff assigned"
                    ),
                    f"Students: {block.student_count}",
                    academic_info,
                ]

                output.append("    " + " | ".join(info))

                # Add staff details
                if block.staff_member is not None:
                    staff_details = [
                        f"      Staff Department: {block.staff_member.department.name}",
                        f"      Academic Degree: {block.staff_member.academic_degree.name}",
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

        # Track unique resources; roomless/staffless external sessions and
        # external courses (no course_objects) render explicitly.
        if assignment.room is not None:
            rooms_used.add(assignment.room.name)
        if assignment.block.staff_member is not None:
            staff_assigned.add(assignment.block.staff_member.name)
        if assignment.block.course_objects:
            courses.add(assignment.block.course_objects[0].course_code)
        else:
            courses.add(assignment.block.display_label())

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
    assignments: Dict[str, Assignment], output_file: str = "reports/schedule_report.txt"
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

        rooms_used = len(
            {a.room.name for a in assignments.values() if a.room is not None}
        )
        staff_assigned = len(
            {
                a.block.staff_member.name
                for a in assignments.values()
                if a.block.staff_member is not None
            }
        )
        courses = set()
        for assignment in assignments.values():
            courses.update(assignment.block.all_participating_course_ids())

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


def build_conflict_summary(
    assignments: Dict[str, Assignment],
    blocking_conflicts: List,
    advisory_conflicts: List,
    failed_blocks: List,
    advisory_warnings: List[str],
    total_blocks: int,
    posted: bool,
    reconciliation_summary: Dict | None = None,
) -> Dict:
    """
    Collect everything that went wrong in one run into a single structure.

    Covers both halves of the question a timetabler asks after a run: what is
    wrong with the schedule that exists, and what could not be placed at all.
    """
    by_reason = defaultdict(list)
    for failure in failed_blocks:
        by_reason[failure.reason.value].append(
            {
                "block_id": failure.block_id,
                "block": failure.block_info,
                "details": failure.details,
                "attempted_rooms": failure.attempted_rooms,
                "attempted_slots": failure.attempted_slots,
            }
        )

    def as_dict(conflict):
        return {
            "type": conflict.conflict_type,
            "description": conflict.description,
            "affected_assignments": conflict.affected_assignments,
            "details": {k: str(v) for k, v in conflict.details.items()},
        }

    scheduled = len(assignments)

    return {
        "generated_at": datetime.now().isoformat(),
        "clean": not blocking_conflicts and not failed_blocks,
        "posted_to_backend": posted,
        "totals": {
            "blocks_expected": total_blocks,
            "blocks_scheduled": scheduled,
            "blocks_unscheduled": max(0, total_blocks - scheduled),
            "blocking_conflicts": len(blocking_conflicts),
            "advisory_conflicts": len(advisory_conflicts),
            "advisory_placements": len(advisory_warnings),
        },
        "blocking_conflicts": [as_dict(c) for c in blocking_conflicts],
        "advisory_conflicts": [as_dict(c) for c in advisory_conflicts],
        "unscheduled_blocks": dict(by_reason),
        "advisory_placements": list(advisory_warnings),
        "reconciliation": reconciliation_summary or {},
    }


def generate_conflict_report(
    summary: Dict, output_file: str = "reports/conflict_report.txt"
) -> str:
    """Write the conflict summary as a readable report and return its text"""
    totals = summary["totals"]
    lines = []

    lines.append("=" * 62)
    lines.append("SCHEDULE CONFLICT REPORT")
    lines.append("=" * 62)
    lines.append(f"Generated:            {summary['generated_at']}")
    lines.append(
        f"Blocks scheduled:     {totals['blocks_scheduled']}/"
        f"{totals['blocks_expected']}"
    )
    lines.append(f"Unscheduled blocks:   {totals['blocks_unscheduled']}")
    lines.append(f"Blocking conflicts:   {totals['blocking_conflicts']}")
    lines.append(f"Advisory conflicts:   {totals['advisory_conflicts']}")
    lines.append(f"Advisory placements:  {totals['advisory_placements']}")
    lines.append(
        f"Posted to backend:    {'yes' if summary['posted_to_backend'] else 'no'}"
    )
    reconciliation = summary.get("reconciliation", {})
    if reconciliation:
        lines.append(f"Retained entries:     {reconciliation['retained_entries']}")
        lines.append(
            f"Stale entries omitted: {reconciliation['stale_entries_omitted']}"
        )
        lines.append(
            f"Out-of-pool entries omitted: "
            f"{reconciliation.get('out_of_pool_entries_omitted', 0)}"
        )
        lines.append(
            f"Out-of-window entries regenerated: "
            f"{reconciliation.get('out_of_window_entries_regenerated', 0)}"
        )
        lines.append(
            f"Missing blocks generated: {reconciliation['missing_blocks_generated']}"
        )
    lines.append("")

    has_advisories = bool(
        summary["advisory_conflicts"] or summary["advisory_placements"]
    )
    if summary["clean"] and not has_advisories:
        lines.append("No conflicts and no unscheduled blocks.")
        lines.append("=" * 62)
        text = "\n".join(lines) + "\n"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Conflict report generated: {output_file}")
        return text

    if summary["blocking_conflicts"]:
        lines.append("-" * 62)
        lines.append(
            f"BLOCKING CONFLICTS ({len(summary['blocking_conflicts'])}) "
            "- schedule was not published"
        )
        lines.append("-" * 62)
        grouped = defaultdict(list)
        for conflict in summary["blocking_conflicts"]:
            grouped[conflict["type"]].append(conflict)
        for conflict_type, items in grouped.items():
            lines.append(f"\n{conflict_type} ({len(items)}):")
            for conflict in items:
                lines.append(f"  - {conflict['description']}")
                lines.append(
                    f"    blocks: {', '.join(conflict['affected_assignments'])}"
                )
        lines.append("")

    if summary["unscheduled_blocks"]:
        total = sum(len(v) for v in summary["unscheduled_blocks"].values())
        lines.append("-" * 62)
        lines.append(f"COULD NOT BE SCHEDULED ({total})")
        lines.append("-" * 62)
        for reason, failures in summary["unscheduled_blocks"].items():
            lines.append(
                f"\n{reason.upper().replace('_', ' ')} ({len(failures)}):"
            )
            for failure in failures:
                lines.append(f"  - {failure['block']}")
                lines.append(f"    {failure['details']}")
        lines.append("")

    if summary["advisory_conflicts"]:
        lines.append("-" * 62)
        lines.append(
            f"ADVISORY - REVIEW BUT NOT BLOCKING "
            f"({len(summary['advisory_conflicts'])})"
        )
        lines.append("-" * 62)
        for conflict in summary["advisory_conflicts"]:
            lines.append(f"  - {conflict['description']}")
        lines.append("")

    if summary["advisory_placements"]:
        lines.append("-" * 62)
        lines.append(
            f"OUTSIDE RECORDED TA HOURS ({len(summary['advisory_placements'])})"
        )
        lines.append("-" * 62)
        for placement in summary["advisory_placements"]:
            lines.append(f"  - {placement}")
        lines.append("")

    lines.append("=" * 62)
    text = "\n".join(lines) + "\n"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Conflict report generated: {output_file}")
    return text


def generate_schedule_json(
    assignments: Dict[str, Assignment], output_file: str = "reports/schedule.json"
):
    """Generate a JSON representation of the schedule"""
    # Convert the schedule to a serializable format
    serialized_schedule = []

    for block_id, assignment in assignments.items():
        block = assignment.block
        room = assignment.room
        time_slot = assignment.time_slot

        # Use composite key for room identification; roomless sessions have
        # no room of ours at all.
        room_type, room_id = get_room_key(room) if room is not None else (None, None)
        lab_type = room.lab_type.value if room_type == "lab" else None

        academic_lists_info = {
            "academic_lists": block.academic_lists,
            "academic_levels": block.academic_levels,
            # Merged groups carry only their attending lists, so the flag -
            # not the list count - is the source of truth.
            "is_merged_common_course": bool(
                getattr(block, "is_merged_common_course", False)
            ),
        }

        # Create serializable object for this assignment
        serialized_assignment = {
            "block_id": block_id,
            "entry_kind": block.source_kind,
            "external_course_id": block.external_course_id,
            "course_codes": (
                [course.course_code for course in block.course_objects]
                if block.course_objects
                else []
            ),
            "display_label": block.display_label(),
            "session_type": block.block_type.value,
            "group_info": {
                "group_number": block.group_number,
                "total_groups": block.total_groups,
            },
            "room": (
                {
                    "composite_id": f"{room_type}_{room_id}",  # Add composite identifier
                    "id": room_id,
                    "name": room.name,
                    "capacity": room.capacity,
                    "type": room_type,
                }
                if room is not None
                else None
            ),
            "staff": (
                {
                    "id": block.staff_member.id,
                    "name": block.staff_member.name,
                    "department": block.staff_member.department.name,
                    "academic_degree": block.staff_member.academic_degree.name,
                    "is_permanent": block.staff_member.is_permanent,
                }
                if block.staff_member is not None
                else None
            ),
            "time_slot": {
                "day": time_slot.day.name,
                "day_index": time_slot.day.value,
                "start_time": time_slot.start_time.strftime("%H:%M"),
                "end_time": time_slot.end_time.strftime("%H:%M"),
            },
            "student_count": block.student_count,
            "academic_lists": academic_lists_info["academic_lists"],
            "academic_levels": academic_lists_info["academic_levels"],
            "is_merged_common_course": academic_lists_info[
                "is_merged_common_course"
            ],
        }

        # Add lab-specific info if applicable
        if room is not None and room_type == "lab":
            serialized_assignment["room"]["lab_type"] = lab_type
            serialized_assignment["room"][
                "used_in_non_specialist_courses"
            ] = room.used_in_non_specialist_courses

        serialized_schedule.append(serialized_assignment)

    # Sort by day and start time for consistency
    serialized_schedule.sort(
        key=lambda x: (x["time_slot"]["day_index"], x["time_slot"]["start_time"])
    )

    # Create metadata with composite key stats; staffless sessions count for
    # no staff, and external courses count as courses.
    room_keys = set(
        get_room_key(assignment.room)
        for assignment in assignments.values()
        if assignment.room is not None
    )
    staff_names = {
        a["staff"]["name"] for a in serialized_schedule if a["staff"] is not None
    }
    metadata = {
        "total_sessions": len(assignments),
        "total_courses": len(
            set(
                course_code
                for a in serialized_schedule
                for course_code in a["course_codes"]
            )
            | {
                a["display_label"]
                for a in serialized_schedule
                if a["entry_kind"] == "external"
            }
        ),
        "total_rooms": len(room_keys),  # Use composite keys for accurate count
        "total_staff": len(staff_names),
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
