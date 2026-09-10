# utils/run_snapshot.py
"""Reproducible run snapshots.

Every generation run writes its complete input picture - study-plan ids,
room pool calendars, reserved period, expected block inventory, engine
revision - to reports/run_snapshots/ before any scheduling starts. The
path is referenced from failure records and insights, so a bad run can be
replayed and diagnosed offline instead of reverse-engineered from logs.

The snapshot only ever contains fields constructed here; engine
credentials (.env EMAIL/PASSWORD and tokens) are never read or written.
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from models.time_preferences import ReservedPeriod

logger = logging.getLogger("run_snapshot")

SNAPSHOT_DIR = os.path.join("reports", "run_snapshots")


def _engine_revision() -> Optional[str]:
    """Short git revision of the engine repo, or None outside a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as error:  # noqa: BLE001 - snapshot must never fail a run
        logger.debug("Could not read engine revision: %s", error)
    return None


def write_run_snapshot(
    blocks: List,
    halls: List,
    labs: List,
    reserved_period: Optional[ReservedPeriod],
    meta: Dict,
) -> Optional[str]:
    """Persist the run's inputs; returns the path, or None when unwritable."""
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(SNAPSHOT_DIR, f"run_{timestamp}.json")

        payload = {
            "generated_at": datetime.now().isoformat(),
            "engine_revision": _engine_revision(),
            "run": meta,
            "reserved_period": (
                {
                    "day": reserved_period.day.name,
                    "start_time": reserved_period.start_time.strftime("%H:%M"),
                    "end_time": reserved_period.end_time.strftime("%H:%M"),
                }
                if reserved_period
                else None
            ),
            "halls": [
                {
                    "id": hall.id,
                    "name": hall.name,
                    "capacity": hall.capacity,
                    "availability": [
                        {
                            "day": window.day.name,
                            "start": window.start_time.strftime("%H:%M"),
                            "end": window.end_time.strftime("%H:%M"),
                        }
                        for window in hall.availability
                    ],
                }
                for hall in halls
            ],
            "labs": [
                {
                    "id": lab.id,
                    "name": lab.name,
                    "capacity": lab.capacity,
                    "lab_type": lab.lab_type.value,
                    "used_in_non_specialist_courses": lab.used_in_non_specialist_courses,
                    "availability": [
                        {
                            "day": window.day.name,
                            "start": window.start_time.strftime("%H:%M"),
                            "end": window.end_time.strftime("%H:%M"),
                        }
                        for window in lab.availability
                    ],
                }
                for lab in labs
            ],
            "expected_blocks": [
                {
                    "id": block.id,
                    "source_kind": getattr(block, "source_kind", "course"),
                    "external_course_id": getattr(
                        block, "external_course_id", None
                    ),
                    "course_codes": list(block.course_codes),
                    "block_type": block.block_type.value,
                    # Staff and room are optional on external blocks; the
                    # snapshot records their absence explicitly.
                    "staff": (
                        {
                            "id": block.staff_member.id,
                            "name": block.staff_member.name,
                            "is_lecturer": block.staff_member.__class__.__name__
                            == "Lecturer",
                        }
                        if block.staff_member is not None
                        else None
                    ),
                    "requires_room": getattr(block, "requires_room", True),
                    "student_count": block.student_count,
                    "required_room_type": block.required_room_type,
                    "group_number": block.group_number,
                    "total_groups": block.total_groups,
                    "academic_lists": list(block.academic_lists),
                    "academic_levels": list(block.academic_levels),
                    "is_merged_common_course": block.is_merged_common_course,
                }
                for block in blocks
            ],
        }

        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
        logger.info("Run snapshot written to %s", path)
        return path
    except Exception as error:  # noqa: BLE001 - snapshot must never fail a run
        logger.warning("Failed to write run snapshot: %s", error)
        return None
