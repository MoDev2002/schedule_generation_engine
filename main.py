# main.py - Schedule Generation Engine Entry Point with Enhanced Logging

import json
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from backend.get_halls import get_halls
from backend.get_labs import get_labs
from backend.get_schedule import get_schedule_generation_context
from backend.get_schedule_settings import get_schedule_settings
from backend.get_staff_members import reset_staff_cache
from backend.get_study_plans import get_study_plans_by_ids
from backend.post_schedule import post_schedule_with_retry, validate_schedule_data
from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from schedule_format import (
    build_conflict_summary,
    generate_conflict_report,
    generate_schedule_json,
    generate_schedule_report,
    print_schedule_statistics,
)
from schedule_validator import ScheduleValidator
from scheduler import SchedulingEngine
from services.change_validator import ScheduleStateCache, apply_change, run_validation
from utils.api_schedule import convert_assignments_to_api_format
from utils.common_course_processor import ParentReferenceProcessor
from utils.feasibility import assess_feasibility
from utils.insights import (
    Insight,
    InsightCategory,
    InsightSeverity,
    entity,
)
from utils.room_selection import apply_room_selection
from utils.room_utils import get_room_key
from utils.run_snapshot import write_run_snapshot
from utils.schedule_continuation import reconcile_schedule


class GenerateRequest(BaseModel):
    study_plans: List[int]
    name_en: str
    name_ar: str
    base_schedule_id: Optional[int] = Field(default=None, gt=0)
    lectures_only: bool = False
    # Room pool for this run. None/omitted = every hall/lab in the system.
    hall_ids: Optional[List[int]] = None
    lab_ids: Optional[List[int]] = None
    # Dev/emergency override: post a schedule that still has unscheduled
    # blocks. The default refuses to post anything incomplete.
    allow_partial: bool = False

    @model_validator(mode="after")
    def validate_room_selection(self):
        if self.hall_ids is not None and not self.hall_ids:
            raise ValueError(
                "hall_ids must not be empty - omit it to allow every hall"
            )
        if self.lab_ids is not None and not self.lab_ids and not self.lectures_only:
            raise ValueError(
                "lab_ids must not be empty unless lectures_only is set - "
                "omit it to allow every lab"
            )
        return self


class RoomSpec(BaseModel):
    type: str  # 'hall' | 'lab'
    id: int


class SlotSpec(BaseModel):
    day: str
    start_time: str
    end_time: str


class ChangeSpec(BaseModel):
    type: str  # move_targets | move | swap_candidates | swap
    entry_id: int
    target: Optional[SlotSpec] = None
    room: Optional[RoomSpec] = None
    target_entry_id: Optional[int] = None


class ValidateChangeRequest(BaseModel):
    schedule_id: int
    state_version: Optional[str] = None
    refresh: bool = False
    apply: bool = False
    new_version: Optional[str] = None
    change: ChangeSpec


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://schedu-eight.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProgressTracker:
    """Track and report progress during schedule generation"""

    def __init__(self):
        self.current_step = 0
        self.total_steps = 7
        self.step_descriptions = {
            0: "Initializing",
            1: "Fetching study plans",
            2: "Fetching rooms and labs",
            3: "Processing common courses",
            4: "Fetching staff members",
            5: "Validating input data",
            6: "Generating schedule",
            7: "Saving results",
        }
        self.progress_file = "schedule_progress.json"
        self.start_time = datetime.now()

        # Detailed scheduling progress
        self.scheduling_details = {
            "current_study_plan": 0,
            "total_study_plans": 0,
            "current_study_plan_name": "",
            "current_blocks_scheduled": 0,
            "total_blocks_in_current_plan": 0,
            "total_blocks_scheduled": 0,
            "total_blocks_overall": 0,
            "current_attempt": 0,
            "scheduling_phase": "initializing",  # initializing, scheduling, optimizing, completed
        }

    def _write_progress(self, progress_data: Dict):
        """Atomically publish progress so polling never reads a partial JSON file."""
        logger = logging.getLogger("ProgressTracker")
        temporary_file = f"{self.progress_file}.{threading.get_ident()}.tmp"

        try:
            with open(temporary_file, "w", encoding="utf-8") as file:
                json.dump(progress_data, file, indent=2)
            os.replace(temporary_file, self.progress_file)
        except Exception as error:
            logger.warning(f"Failed to save progress: {error}")
            try:
                if os.path.exists(temporary_file):
                    os.remove(temporary_file)
            except OSError:
                pass

    def update_progress(self, step: int, message: str = "", details: Dict = None):
        """Update progress and save to file for frontend to read"""
        self.current_step = step
        logger = logging.getLogger("ProgressTracker")
        logger.info(
            f"PROGRESS UPDATE: Step {step}/{self.total_steps} - {self.step_descriptions.get(step, 'Unknown')}"
        )
        logger.info(f"PROGRESS MESSAGE: {message}")

        # Calculate percentage based on step and scheduling details
        if step == 5 and self.scheduling_details["total_blocks_overall"] > 0:
            # During scheduling, calculate more precise percentage
            base_percentage = (step / self.total_steps) * 100
            next_step_percentage = ((step + 1) / self.total_steps) * 100

            # Calculate scheduling progress within step 5
            scheduling_progress = (
                self.scheduling_details["total_blocks_scheduled"]
                / self.scheduling_details["total_blocks_overall"]
            )
            current_percentage = base_percentage + (
                scheduling_progress * (next_step_percentage - base_percentage)
            )
        else:
            current_percentage = (step / self.total_steps) * 100

        progress_data = {
            "status": "running",
            "current_step": step,
            "total_steps": self.total_steps,
            "percentage": current_percentage,
            "step_description": self.step_descriptions.get(step, "Unknown"),
            "message": message,
            "success": None,
            "details": details or {},
            "scheduling_details": self.scheduling_details.copy(),
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": (datetime.now() - self.start_time).total_seconds(),
        }

        self._write_progress(progress_data)

    def update_scheduling_progress(
        self,
        current_study_plan: int = None,
        total_study_plans: int = None,
        current_study_plan_name: str = None,
        current_blocks_scheduled: int = None,
        total_blocks_in_current_plan: int = None,
        total_blocks_scheduled: int = None,
        total_blocks_overall: int = None,
        current_attempt: int = None,
        scheduling_phase: str = None,
    ):
        """Update detailed scheduling progress"""
        logger = logging.getLogger("ProgressTracker")
        logger.debug("SCHEDULING PROGRESS UPDATE:")

        if current_study_plan is not None:
            self.scheduling_details["current_study_plan"] = current_study_plan
            logger.debug(f"  current_study_plan: {current_study_plan}")
        if total_study_plans is not None:
            self.scheduling_details["total_study_plans"] = total_study_plans
            logger.debug(f"  total_study_plans: {total_study_plans}")
        if current_study_plan_name is not None:
            self.scheduling_details["current_study_plan_name"] = current_study_plan_name
            logger.debug(f"  current_study_plan_name: {current_study_plan_name}")
        if current_blocks_scheduled is not None:
            self.scheduling_details["current_blocks_scheduled"] = (
                current_blocks_scheduled
            )
            logger.debug(f"  current_blocks_scheduled: {current_blocks_scheduled}")
        if total_blocks_in_current_plan is not None:
            self.scheduling_details["total_blocks_in_current_plan"] = (
                total_blocks_in_current_plan
            )
            logger.debug(
                f"  total_blocks_in_current_plan: {total_blocks_in_current_plan}"
            )
        if total_blocks_scheduled is not None:
            self.scheduling_details["total_blocks_scheduled"] = total_blocks_scheduled
            logger.debug(f"  total_blocks_scheduled: {total_blocks_scheduled}")
        if total_blocks_overall is not None:
            self.scheduling_details["total_blocks_overall"] = total_blocks_overall
            logger.debug(f"  total_blocks_overall: {total_blocks_overall}")
        if current_attempt is not None:
            self.scheduling_details["current_attempt"] = current_attempt
            logger.debug(f"  current_attempt: {current_attempt}")
        if scheduling_phase is not None:
            self.scheduling_details["scheduling_phase"] = scheduling_phase
            logger.debug(f"  scheduling_phase: {scheduling_phase}")

    def complete(
        self,
        success: bool,
        output_files: List[str] = None,
        conflict_summary: Dict = None,
        message: str = "",
        error: Optional[Dict] = None,
    ):
        """Mark the process as complete"""
        logger = logging.getLogger("ProgressTracker")
        logger.info(f"PROCESS COMPLETE: Success={success}, Files={output_files}")

        progress_data = {
            "status": "completed" if success else "failed",
            "current_step": self.total_steps if success else self.current_step,
            "total_steps": self.total_steps,
            "percentage": 100.0 if success else (self.current_step / self.total_steps) * 100,
            "step_description": "Complete" if success else "Failed",
            "success": success,
            "message": message,
            "error": error,
            "output_files": output_files or [],
            # The frontend reads this to show what needs a human's attention.
            "conflict_summary": conflict_summary,
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": (datetime.now() - self.start_time).total_seconds(),
        }

        self._write_progress(progress_data)


class ScheduleGenerationEngine:
    """Main engine for generating university schedules"""

    def __init__(
        self,
        study_plan_ids: List[int],
        schedule_name_en: str,
        schedule_name_ar: str,
        base_schedule_id: Optional[int] = None,
        lectures_only: bool = False,
        hall_ids: Optional[List[int]] = None,
        lab_ids: Optional[List[int]] = None,
        allow_partial: bool = False,
    ):
        self.study_plan_ids = study_plan_ids
        self.schedule_name_en = schedule_name_en
        self.schedule_name_ar = schedule_name_ar
        self.base_schedule_id = base_schedule_id
        self.lectures_only = lectures_only
        # Room pool for this run; None means every hall/lab is allowed.
        self.selected_hall_ids = hall_ids
        self.selected_lab_ids = lab_ids
        # Completeness gate: a schedule with unscheduled blocks is not posted
        # unless the request set this override explicitly.
        self.allow_partial = allow_partial
        self.progress = ProgressTracker()
        self.logger = logging.getLogger("ScheduleGenerationEngine")

        # Data containers
        self.study_plans = []
        self.halls = []
        self.labs = []
        self.reserved_period = None
        self.constraint_manager = None
        self.resource_manager = None
        self.scheduling_engine = None

        # Results
        self.assignments = {}
        self.output_files = []
        self.total_blocks = 0
        self.conflict_summary = None
        self.failure: Optional[Dict] = None
        self.run_insights = []
        self.run_snapshot_path = None
        self.coverage_report = {
            "generated_at": None,
            "requested_study_plan_ids": self.study_plan_ids,
            "loaded_study_plan_ids": [],
            "invalid_assignment": [],
            "unresolved_common_reference": [],
            "study_plan_not_requested": [],
        }
        self.coverage_report_file: Optional[str] = None
        self.reconciliation_summary = {
            "retained_entries": 0,
            "stale_entries_omitted": 0,
            "out_of_pool_entries_omitted": 0,
            "missing_blocks_generated": 0,
        }

        self.logger.info(f"INITIALIZATION: study_plan_ids={study_plan_ids}")
        self.logger.info(f"INITIALIZATION: schedule_name_en={schedule_name_en}")
        self.logger.info(f"INITIALIZATION: schedule_name_ar={schedule_name_ar}")
        self.logger.info(f"INITIALIZATION: base_schedule_id={base_schedule_id}")
        self.logger.info(f"INITIALIZATION: lectures_only={lectures_only}")
        self.logger.info(f"INITIALIZATION: hall_ids={hall_ids}")
        self.logger.info(f"INITIALIZATION: lab_ids={lab_ids}")

    def _record_failure(
        self, code: str, message: str, details: Optional[Dict] = None
    ):
        """Keep the safe, actionable failure information for the progress API."""
        self.failure = {
            "code": code,
            "message": message,
            "details": details or {},
        }

    def _fail_run(self, fallback_message: str) -> bool:
        """Publish a terminal failure state before ending a failed run."""
        failure = self.failure or {
            "code": "GENERATION_FAILED",
            "message": fallback_message,
            "details": {},
        }
        self.progress.complete(False, message=failure["message"], error=failure)
        return False

    def _write_coverage_report(self) -> Optional[str]:
        """Persist the input audit for both successful and failed runs."""
        self.coverage_report["generated_at"] = datetime.now().isoformat()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"reports/pre_schedule_coverage_{timestamp}.json"

        try:
            os.makedirs("reports", exist_ok=True)
            with open(filename, "w", encoding="utf-8") as file:
                json.dump(self.coverage_report, file, indent=2, ensure_ascii=False)
            self.coverage_report_file = filename
            self.logger.info("Pre-schedule coverage report written to %s", filename)
            return filename
        except Exception as error:
            self.logger.error(
                "Failed to write pre-schedule coverage report: %s",
                error,
                exc_info=True,
            )
            return None

    def run(self) -> bool:
        """Main execution method"""
        try:
            setup_logging()
            self.logger.info("=== STARTING SCHEDULE GENERATION ENGINE ===")
            self.progress.update_progress(0, "Starting schedule generation engine")

            # The staff cache lives for the whole process, and one process serves
            # many runs. Drop it so availability edited since the previous run
            # is what this run actually schedules against.
            reset_staff_cache()

            if not self._fetch_schedule_settings():
                return self._fail_run("Failed to fetch schedule settings")

            # Step 1: Fetch study plans
            if not self._fetch_study_plans():
                return self._fail_run("Failed to fetch study plans")

            # Step 2: Fetch rooms and labs
            if not self._fetch_facilities():
                return self._fail_run("Failed to fetch rooms and laboratories")

            # Step 3: Process common courses
            if not self._process_common_courses():
                return self._fail_run("Failed to process common courses")

            # Step 4: Staff members are already included in study plans
            self.progress.update_progress(
                3, "Staff member data loaded from study plans"
            )

            # Step 5: Validate input data
            if not self._validate_input_data():
                return self._fail_run("Input validation failed")

            # Step 6: Generate schedule
            if not self._generate_schedule():
                return self._fail_run("Failed to generate the schedule")

            # Step 7: Save results
            if not self._save_results():
                return self._fail_run("Failed to save schedule results")

            self.progress.complete(True, self.output_files, self.conflict_summary)
            self.logger.info("=== SCHEDULE GENERATION COMPLETED SUCCESSFULLY ===")
            return True

        except Exception as e:
            self.logger.error(f"Schedule generation failed: {str(e)}", exc_info=True)
            self._record_failure("UNEXPECTED_ERROR", "Schedule generation failed")
            return self._fail_run("Schedule generation failed")

    def _fetch_schedule_settings(self) -> bool:
        """Load the global blocked period before generating any assignments."""
        try:
            self.logger.info("=== FETCHING SCHEDULE SETTINGS ===")
            self.reserved_period = get_schedule_settings()
            if self.reserved_period is None:
                self.logger.info("No reserved period is configured")
            else:
                self.logger.info(
                    "Reserved period loaded: %s %s-%s",
                    self.reserved_period.day.name,
                    self.reserved_period.start_time,
                    self.reserved_period.end_time,
                )
            return True
        except Exception as error:
            self.logger.error(
                "Failed to fetch schedule settings; generation will not continue: %s",
                error,
                exc_info=True,
            )
            self._record_failure(
                "SCHEDULE_SETTINGS_FETCH_FAILED",
                "Unable to load schedule settings",
            )
            return False

    def _fetch_study_plans(self) -> bool:
        """Fetch study plans from backend"""
        try:
            self.logger.info("=== FETCHING STUDY PLANS ===")
            self.progress.update_progress(
                1, f"Fetching {len(self.study_plan_ids)} study plans"
            )

            self.logger.info(f"Requesting study plans with IDs: {self.study_plan_ids}")
            load_result = get_study_plans_by_ids(
                self.study_plan_ids, resolve_refs=True
            )
            self.study_plans = load_result.study_plans
            self.coverage_report["loaded_study_plan_ids"] = [
                study_plan.id for study_plan in self.study_plans
            ]
            self.coverage_report["invalid_assignment"] = load_result.errors

            if load_result.errors:
                coverage_report = self._write_coverage_report()
                self._record_failure(
                    "STUDY_PLAN_INPUT_INVALID",
                    "One or more study-plan assignments are invalid.",
                    {
                        "error_count": len(load_result.errors),
                        "errors": load_result.errors,
                        "coverage_report": coverage_report,
                    },
                )
                return False

            self.logger.info(
                f"Received {len(self.study_plans)} study plans from backend"
            )

            if len(self.study_plans) != len(self.study_plan_ids):
                coverage_report = self._write_coverage_report()
                self._record_failure(
                    "STUDY_PLAN_COVERAGE_INCOMPLETE",
                    "Not every requested study plan was loaded.",
                    {
                        "requested_count": len(self.study_plan_ids),
                        "loaded_count": len(self.study_plans),
                        "requested_study_plan_ids": self.study_plan_ids,
                        "loaded_study_plan_ids": self.coverage_report[
                            "loaded_study_plan_ids"
                        ],
                        "coverage_report": coverage_report,
                    },
                )
                return False

            if not self.study_plans:
                self.logger.error("No study plans found with the provided IDs")
                self._record_failure(
                    "STUDY_PLANS_NOT_FOUND",
                    "No study plans were loaded for this generation",
                    {"requested_study_plan_ids": self.study_plan_ids},
                )
                return False

            # Log detailed study plan information
            for i, sp in enumerate(self.study_plans):
                self.logger.info(f"STUDY PLAN {i+1}:")
                self.logger.info(f"  Academic List: {sp.academic_list.name}")
                self.logger.info(f"  Academic Level: {sp.academic_level}")
                self.logger.info(f"  Expected Students: {sp.expected_students}")
                self.logger.info(f"  Course Assignments: {len(sp.course_assignments)}")

                for j, ca in enumerate(sp.course_assignments):
                    self.logger.info(f"    COURSE {j+1}: {ca.course_code}")
                    self.logger.info(f"      Lecture Groups: {ca.lecture_groups}")
                    self.logger.info(f"      Lab Groups: {ca.lab_groups}")
                    self.logger.info(f"      Lecturers: {len(ca.lecturers)}")
                    self.logger.info(
                        f"      Teaching Assistants: {len(ca.teaching_assistants) if ca.teaching_assistants else 0}"
                    )

                    # Detailed lecturer info
                    for k, lecturer_assignment in enumerate(ca.lecturers):
                        lecturer_info = lecturer_assignment.get("lecturer", {})
                        lecturer_name = getattr(
                            lecturer_info,
                            "name",
                            lecturer_assignment.get("lecturer_id", "UNKNOWN"),
                        )
                        self.logger.info(
                            f"        LECTURER {k+1}: {lecturer_name} ({lecturer_assignment['num_of_groups']} groups)"
                        )

                    # Detailed TA info
                    if ca.teaching_assistants:
                        for k, ta_assignment in enumerate(ca.teaching_assistants):
                            ta_info = ta_assignment.get("teaching_assistant", {})
                            ta_name = getattr(
                                ta_info,
                                "name",
                                ta_assignment.get("teaching_assistant_id", "UNKNOWN"),
                            )
                            self.logger.info(
                                f"        TA {k+1}: {ta_name} ({ta_assignment['num_of_groups']} groups)"
                            )
                            self.logger.info(
                                f"        TA {k+1} Keys: {list(ta_assignment.keys())}"
                            )

            # Log what we fetched
            total_courses = sum(len(sp.course_assignments) for sp in self.study_plans)
            details = {
                "study_plans_count": len(self.study_plans),
                "total_courses": total_courses,
                "study_plan_names": [sp.academic_list.name for sp in self.study_plans],
            }

            self.progress.update_progress(
                1,
                f"Loaded {len(self.study_plans)} study plans with {total_courses} total courses",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(f"Failed to fetch study plans: {str(e)}", exc_info=True)
            self._record_failure(
                "STUDY_PLANS_FETCH_FAILED", "Unable to load study plans"
            )
            return False

    def _fetch_facilities(self) -> bool:
        """Fetch halls and labs from backend, scoped to the requested room pool"""
        try:
            self.logger.info("=== FETCHING FACILITIES ===")
            self.progress.update_progress(2, "Fetching halls and laboratories")

            # Fetch halls
            self.logger.info("Fetching halls...")
            halls = get_halls()
            self.logger.info(f"Received {len(halls)} halls")

            # Fetch labs
            self.logger.info("Fetching labs...")
            labs = get_labs()
            self.logger.info(f"Received {len(labs)} labs")

            if self.selected_hall_ids is not None:
                wanted = set(self.selected_hall_ids)
                missing = sorted(wanted - {hall.id for hall in halls})
                if missing:
                    self.logger.error(
                        f"Selected halls not found in the system: {missing}"
                    )
                    self._record_failure(
                        "ROOM_POOL_EMPTY",
                        "Some selected halls no longer exist",
                        {"missing_hall_ids": missing},
                    )
                    return False
                halls = [hall for hall in halls if hall.id in wanted]

            if self.selected_lab_ids is not None:
                wanted = set(self.selected_lab_ids)
                missing = sorted(wanted - {lab.id for lab in labs})
                if missing:
                    self.logger.error(
                        f"Selected labs not found in the system: {missing}"
                    )
                    self._record_failure(
                        "ROOM_POOL_EMPTY",
                        "Some selected labs no longer exist",
                        {"missing_lab_ids": missing},
                    )
                    return False
                labs = [lab for lab in labs if lab.id in wanted]

            self.halls = halls
            self.labs = labs

            if not self.halls:
                # Lecture blocks always exist, so an empty hall pool can never
                # satisfy a run. An empty lab pool is caught later by the
                # per-block pre-flight, which knows whether labs are needed.
                self.logger.error("No halls available for this run")
                self._record_failure(
                    "ROOM_POOL_EMPTY",
                    "No halls were selected or found - at least one hall is required",
                    {"selected_hall_ids": self.selected_hall_ids},
                )
                return False

            # Log facility details
            for i, hall in enumerate(self.halls):
                self.logger.info(f"HALL {i+1}: {hall.name} (Capacity: {hall.capacity})")

            for i, lab in enumerate(self.labs):
                self.logger.info(
                    f"LAB {i+1}: {lab.name} (Capacity: {lab.capacity}, Type: {lab.lab_type.value}, Used in non-specialist: {lab.used_in_non_specialist_courses})"
                )

            details = {
                "halls_count": len(self.halls),
                "labs_count": len(self.labs),
                "total_facilities": len(self.halls) + len(self.labs),
            }

            self.progress.update_progress(
                2, f"Loaded {len(self.halls)} halls and {len(self.labs)} labs", details
            )

            return True

        except Exception as e:
            self.logger.error(f"Failed to fetch facilities: {str(e)}", exc_info=True)
            self._record_failure(
                "FACILITIES_FETCH_FAILED", "Unable to load halls and laboratories"
            )
            return False

    def _process_common_courses(self) -> bool:
        """Process common courses to merge them with their parents"""
        try:
            self.logger.info("=== PROCESSING COMMON COURSES ===")
            self.progress.update_progress(
                3, "Processing common courses and merging duplicates"
            )

            processor = ParentReferenceProcessor()
            original_count = len(self.study_plans)

            reference_coverage = processor.build_reference_coverage(
                self.study_plans, self.study_plan_ids
            )
            for category, issues in reference_coverage.items():
                self.coverage_report[category].extend(issues)

            coverage_report = self._write_coverage_report()
            coverage_errors = [
                issue
                for category in (
                    "invalid_assignment",
                    "unresolved_common_reference",
                    "study_plan_not_requested",
                )
                for issue in self.coverage_report[category]
            ]
            if coverage_errors:
                self._record_failure(
                    "PRE_SCHEDULE_COVERAGE_FAILED",
                    "Study-plan coverage failed. Resolve the listed common-course references.",
                    {
                        "error_count": len(coverage_errors),
                        "errors": coverage_errors,
                        "coverage_report": coverage_report,
                    },
                )
                return False

            # Process the study plans to merge common courses
            self.study_plans = processor.process_common_courses(self.study_plans)

            # Log results
            total_courses_before = sum(
                len(sp.course_assignments) for sp in self.study_plans
            )

            self.logger.info(f"Common course processing completed")
            self.logger.info(f"Study plans: {original_count} (unchanged)")
            self.logger.info(
                f"Total course assignments after processing: {total_courses_before}"
            )

            # Log details about merged courses
            merged_courses = []
            for study_plan in self.study_plans:
                for assignment in study_plan.course_assignments:
                    if (
                        assignment.academic_lists is not None
                        and len(assignment.academic_lists) > 1
                    ):
                        merged_courses.append(assignment.course_code)

            if merged_courses:
                self.logger.info(
                    f"Found {len(merged_courses)} merged common courses: {merged_courses}"
                )
            else:
                self.logger.info("No common courses found to merge")

            details = {
                "merged_courses_count": len(merged_courses),
                "merged_course_codes": merged_courses,
            }

            self.progress.update_progress(
                3,
                f"Common course processing completed - {len(merged_courses)} courses merged",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(
                f"Common course processing failed: {str(e)}", exc_info=True
            )
            self._record_failure(
                "COMMON_COURSE_PROCESSING_FAILED", "Unable to process common courses"
            )
            return False

    def _validate_input_data(self) -> bool:
        """Validate all input data"""
        try:
            self.logger.info("=== VALIDATING INPUT DATA ===")
            self.progress.update_progress(4, "Validating input data")

            validator = ScheduleValidator()
            validation_messages = validator.validate_input_data(self.study_plans)

            # Check for errors
            errors = [msg for msg in validation_messages if msg.level.value == "ERROR"]
            warnings = [
                msg for msg in validation_messages if msg.level.value == "WARNING"
            ]

            self.logger.info(
                f"Validation completed: {len(errors)} errors, {len(warnings)} warnings"
            )

            for error in errors:
                self.logger.error(
                    f"VALIDATION ERROR: {error.message} - Context: {error.context}"
                )

            for warning in warnings:
                self.logger.warning(
                    f"VALIDATION WARNING: {warning.message} - Context: {warning.context}"
                )

            if errors:
                self._record_failure(
                    "INPUT_VALIDATION_FAILED",
                    "Input validation failed. Fix the listed assignments and try again.",
                    {
                        "error_count": len(errors),
                        "errors": [
                            {"message": error.message, "context": error.context}
                            for error in errors
                        ],
                    },
                )
                return False

            details = {
                "errors_count": len(errors),
                "warnings_count": len(warnings),
                "total_validations": len(validation_messages),
            }

            self.progress.update_progress(
                5,
                f"Validation completed: {len(errors)} errors, {len(warnings)} warnings",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(f"Validation failed: {str(e)}", exc_info=True)
            self._record_failure("INPUT_VALIDATION_FAILED", "Input validation failed")
            return False

    def _generate_schedule(self) -> bool:
        """Generate the actual schedule"""
        try:
            self.logger.info("=== GENERATING SCHEDULE ===")
            self.progress.update_progress(5, "Initializing scheduling engine")

            # Initialize managers
            self.constraint_manager = ConstraintManager()
            self.resource_manager = ResourceManager(
                self.halls,
                self.labs,
                reserved_period=self.reserved_period,
            )
            self.scheduling_engine = SchedulingEngine(
                self.constraint_manager, self.resource_manager
            )

            # Collect ALL course assignments from ALL study plans
            self.logger.info("=== COLLECTING ALL COURSE ASSIGNMENTS ===")
            all_course_assignments = []
            study_plan_mapping = {}
            study_plan_info = []  # For tracking and progress

            for i, study_plan in enumerate(self.study_plans):
                study_plan_name = study_plan.name
                self.logger.info(f"Study Plan {i+1}: {study_plan_name}")

                # Add all course assignments from this study plan
                plan_course_count = len(study_plan.course_assignments)
                for course_assignment in study_plan.course_assignments:
                    course_index = len(all_course_assignments)
                    all_course_assignments.append(course_assignment)
                    study_plan_mapping[course_index] = study_plan

                study_plan_info.append(
                    {
                        "name": study_plan_name,
                        "course_count": plan_course_count,
                    }
                )

            expected_blocks = self.scheduling_engine.build_blocks(
                all_course_assignments,
                study_plan_mapping,
                include_labs=not self.lectures_only,
            )
            apply_room_selection(expected_blocks, self.selected_lab_ids)
            self.total_blocks = len(expected_blocks)
            blocks_to_generate = expected_blocks
            fixed_assignments = {}

            if self.base_schedule_id is not None:
                self.progress.update_progress(5, "Reconciling source schedule")
                source_context = get_schedule_generation_context(self.base_schedule_id)
                reconciliation = reconcile_schedule(
                    expected_blocks,
                    source_context.get("entries", []),
                    self.halls,
                    self.labs,
                )
                fixed_assignments = reconciliation.fixed_assignments
                blocks_to_generate = reconciliation.missing_blocks
                self.reconciliation_summary.update(
                    {
                        "retained_entries": reconciliation.retained_entries,
                        "stale_entries_omitted": reconciliation.stale_entries_omitted,
                        "out_of_pool_entries_omitted": reconciliation.out_of_pool_entries_omitted,
                    }
                )
                if reconciliation.out_of_pool_entries_omitted:
                    self.logger.warning(
                        f"{reconciliation.out_of_pool_entries_omitted} source session(s) "
                        "reference rooms outside this run's room pool and will be "
                        "regenerated"
                    )

            self.logger.info(f"=== UNIFIED SCHEDULING SUMMARY ===")
            self.logger.info(f"Total study plans: {len(self.study_plans)}")
            self.logger.info(f"Total course assignments: {len(all_course_assignments)}")
            self.logger.info(f"Total expected blocks: {self.total_blocks}")
            self.logger.info(f"Retained source entries: {len(fixed_assignments)}")
            self.logger.info(f"Blocks to generate: {len(blocks_to_generate)}")

            # Phase 0 insights: snapshot the inputs, then prove aggregate
            # feasibility before any search. A DATA blocker fails the run in
            # seconds with the numbers and the levers, instead of burning
            # attempts on an input no search could ever fit.
            self.run_snapshot_path = write_run_snapshot(
                blocks=blocks_to_generate,
                halls=self.halls,
                labs=self.labs,
                reserved_period=self.reserved_period,
                meta={
                    "study_plan_ids": self.study_plan_ids,
                    "schedule_name_en": self.schedule_name_en,
                    "schedule_name_ar": self.schedule_name_ar,
                    "lectures_only": self.lectures_only,
                    "allow_partial": self.allow_partial,
                    "base_schedule_id": self.base_schedule_id,
                    "selected_hall_ids": self.selected_hall_ids,
                    "selected_lab_ids": self.selected_lab_ids,
                    "retained_source_entries": len(fixed_assignments),
                    "seed": self.scheduling_engine.seed,
                    "placement_core": (
                        "cpsat" if self.scheduling_engine.use_cpsat else "greedy"
                    ),
                },
            )

            feasibility_report = assess_feasibility(
                blocks=blocks_to_generate,
                halls=self.halls,
                labs=self.labs,
                reserved_period=self.reserved_period,
                strict_cohort_mode=self.constraint_manager.strict_cohort_mode,
            )
            for insight in feasibility_report.insights:
                insight.snapshot_ref = self.run_snapshot_path
            self.run_insights = feasibility_report.insights
            for warning in feasibility_report.warnings:
                self.logger.warning(f"INSIGHT: {warning.render()}")

            # On a continuation run the retained entries already consume
            # periods, so the fresh-run bound can overstate what is left to
            # place; the completeness gate at posting time still catches any
            # real shortfall, so blockers are logged loudly but not enforced.
            blockers = feasibility_report.blockers
            if blockers and not fixed_assignments:
                for blocker in blockers:
                    self.logger.error(f"INSIGHT: {blocker.render()}")
                top = blockers[0]
                extra = (
                    f" (+{len(blockers) - 1} more insight(s) in details)"
                    if len(blockers) > 1
                    else ""
                )
                self.progress.update_scheduling_progress(scheduling_phase="failed")
                self._record_failure(
                    top.code,
                    f"Run refused before scheduling: {top.verdict}{extra}",
                    {
                        "insights": [
                            insight.to_dict() for insight in feasibility_report.insights
                        ],
                        "feasibility_summary": feasibility_report.summary,
                        "run_snapshot": self.run_snapshot_path,
                    },
                )
                return False
            for blocker in blockers:
                self.logger.error(
                    f"INSIGHT (continuation run, not enforced): {blocker.render()}"
                )

            # Pre-flight: refuse the run when the selected room pool cannot
            # host a block at all, so the user gets one loud, specific failure
            # instead of an empty or quietly partial schedule. Runs after the
            # pre-flight still allow partial schedules via the conflict report.
            unsatisfiable_blocks = [
                block
                for block in blocks_to_generate
                if not self.resource_manager.get_suitable_rooms(block)
            ]
            if unsatisfiable_blocks:
                largest_hall = max((hall.capacity for hall in self.halls), default=0)
                largest_lab = max((lab.capacity for lab in self.labs), default=0)
                self.logger.error(
                    f"{len(unsatisfiable_blocks)} block(s) cannot be hosted by the "
                    f"selected rooms (largest hall: {largest_hall}, largest lab: {largest_lab})"
                )
                self.progress.update_scheduling_progress(scheduling_phase="failed")
                self._record_failure(
                    "ROOM_CAPACITY_INSUFFICIENT",
                    "No selected room can host "
                    f"{len(unsatisfiable_blocks)} of the requested session(s) - "
                    "widen the hall/lab selection or check room capacities",
                    {
                        "largest_selected_hall_capacity": largest_hall,
                        "largest_selected_lab_capacity": largest_lab,
                        "total_affected_blocks": len(unsatisfiable_blocks),
                        "blocks": [
                            {
                                "courses": block.course_codes,
                                "student_count": block.student_count,
                                "required_room_type": block.required_room_type,
                            }
                            for block in unsatisfiable_blocks[:20]
                        ],
                    },
                )
                return False

            # Initialize scheduling progress
            self.progress.update_scheduling_progress(
                current_study_plan=1,
                total_study_plans=len(self.study_plans),
                total_blocks_overall=self.total_blocks,
                total_blocks_scheduled=len(fixed_assignments),
                scheduling_phase="initialized",
            )

            self.progress.update_progress(
                6,
                f"Unified scheduling: {self.total_blocks} blocks from {len(self.study_plans)} study plans",
            )

            # Create enhanced progress callback that tracks across all study plans
            def unified_progress_callback(
                scheduled_count, total_count, phase="scheduling", attempt=1
            ):
                self.logger.debug(
                    f"Unified progress: {scheduled_count}/{total_count}, phase={phase}"
                )

                displayed_scheduled = len(fixed_assignments) + scheduled_count
                displayed_total = self.total_blocks

                # Calculate which study plan we're conceptually in based on progress
                current_plan = min(
                    len(self.study_plans),
                    max(
                        1,
                        (
                            (scheduled_count * len(self.study_plans)) // total_count + 1
                            if total_count
                            else 1
                        ),
                    ),
                )
                current_plan_name = f"Study Plans 1-{len(self.study_plans)} (Unified)"

                self.progress.update_scheduling_progress(
                    current_study_plan=current_plan,
                    current_study_plan_name=current_plan_name,
                    current_blocks_scheduled=displayed_scheduled,
                    total_blocks_in_current_plan=displayed_total,
                    total_blocks_scheduled=displayed_scheduled,
                    scheduling_phase=phase,
                    current_attempt=attempt,
                )

                phase_msg = {
                    "scheduling": "Placing blocks (unified)",
                    "optimizing": "Optimizing placement (unified)",
                    "local_search": "Local optimization (unified)",
                    "completed": "Completed (unified)",
                }.get(phase, phase)

                self.progress.update_progress(
                    6,
                    f"{phase_msg} - All study plans ({displayed_scheduled}/{displayed_total})",
                )

            # Schedule ALL blocks together as one unified problem
            self.logger.info("=== STARTING UNIFIED SCHEDULING ===")
            self.logger.info("All course assignments will be scheduled together")

            assignments = self.scheduling_engine.schedule_blocks(
                all_course_assignments,
                study_plan_mapping,
                progress_callback=unified_progress_callback,
                prebuilt_blocks=blocks_to_generate,
                fixed_assignments=fixed_assignments,
            )

            self.logger.info(f"=== UNIFIED SCHEDULING COMPLETED ===")
            self.logger.info(f"Total assignments created: {len(assignments)}")

            # Log detailed results by study plan
            for i, study_plan in enumerate(self.study_plans):
                study_plan_name = (
                    f"{study_plan.academic_list.name}_L{study_plan.academic_level}"
                )
                plan_assignments = [
                    (block_id, assignment)
                    for block_id, assignment in assignments.items()
                    if study_plan.academic_list.name in assignment.block.academic_lists
                    and study_plan.academic_level in assignment.block.academic_levels
                ]

                self.logger.info(
                    f"Study Plan {i+1} ({study_plan_name}): {len(plan_assignments)} assignments"
                )

            self.assignments = assignments
            self.reconciliation_summary["missing_blocks_generated"] = max(
                0, len(self.assignments) - len(fixed_assignments)
            )

            if not self.assignments:
                self.logger.error("No schedule assignments were generated")
                self._record_failure(
                    "NO_ASSIGNMENTS_GENERATED", "No schedule sessions could be generated"
                )
                return False

            # Final scheduling progress update
            self.progress.update_scheduling_progress(
                scheduling_phase="completed",
                total_blocks_scheduled=len(self.assignments),
            )

            details = {
                "total_assignments": len(self.assignments),
                "total_sessions": len(self.assignments),
                "study_plans_scheduled": len(self.study_plans),
                "retained_entries": self.reconciliation_summary["retained_entries"],
                "stale_entries_omitted": self.reconciliation_summary[
                    "stale_entries_omitted"
                ],
                "missing_blocks_generated": self.reconciliation_summary[
                    "missing_blocks_generated"
                ],
                "success_rate": f"{(len(self.assignments)/self.total_blocks)*100:.1f}%",
            }

            self.logger.info(
                f"UNIFIED SCHEDULING RESULT: {len(self.assignments)}/{self.total_blocks} blocks scheduled ({details['success_rate']})"
            )

            self.progress.update_progress(
                6,
                f"Unified schedule completed! {len(self.assignments)}/{self.total_blocks} blocks scheduled ({details['success_rate']})",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(f"Schedule generation failed: {str(e)}", exc_info=True)
            self.progress.update_scheduling_progress(scheduling_phase="failed")
            self._record_failure(
                "SCHEDULING_FAILED", "Schedule generation stopped unexpectedly"
            )
            return False

    def _save_results(self) -> bool:
        """Save schedule results to files"""
        try:
            self.logger.info("=== SAVING RESULTS ===")
            self.progress.update_progress(6, "Saving schedule results")

            # Generate timestamp for file names
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Generate JSON file using existing function
            json_filename = f"reports/schedule_{timestamp}.json"
            generate_schedule_json(self.assignments, json_filename)
            self.output_files.append(json_filename)

            # Generate text report using existing function
            txt_filename = f"reports/schedule_report_{timestamp}.txt"
            generate_schedule_report(self.assignments, txt_filename)
            self.output_files.append(txt_filename)

            # Print statistics to console using existing function
            print_schedule_statistics(self.assignments)

            # Check the finished schedule before it goes anywhere. Room, staff and
            # student double-bookings block publication; capacity overruns and
            # out-of-hours TA placements are reported but allowed through.
            self.logger.info("=== VALIDATING GENERATED SCHEDULE ===")
            validator = ScheduleValidator()
            _, conflicts = validator.validate_schedule_comprehensive(self.assignments)
            blocking, advisory = validator.split_conflicts(conflicts)

            for conflict in blocking:
                self.logger.error(f"BLOCKING CONFLICT: {conflict.description}")
            for conflict in advisory:
                self.logger.warning(f"ADVISORY: {conflict.description}")

            engine = self.scheduling_engine
            unscheduled_failures = list(engine.failed_blocks) if engine else []
            incomplete = bool(unscheduled_failures) and not self.allow_partial

            # Overflow placements (seats < students) are allowed by policy but
            # must be loud: they become DATA insights, land in the conflict
            # summary, and are rendered into the advisory report below.
            overflow_assignments = [
                assignment
                for assignment in self.assignments.values()
                if assignment.room.capacity < assignment.block.student_count
            ]
            overflow_insights = [
                Insight(
                    code="ROOM_CAPACITY_SHORTFALL",
                    category=InsightCategory.DATA,
                    severity=InsightSeverity.WARNING,
                    phase="finalize",
                    verdict=(
                        f"{assignment.block.course_codes[0]} group "
                        f"{assignment.block.group_number} placed in "
                        f"{assignment.room.name} ({assignment.room.capacity} seats) "
                        f"for {assignment.block.student_count} students."
                    ),
                    entities=[
                        entity(
                            "course",
                            assignment.block.course_codes[0],
                            assignment.block.course_codes[0],
                        ),
                        entity("room", assignment.room.id, assignment.room.name),
                    ],
                    evidence={
                        "student_count": assignment.block.student_count,
                        "room_capacity": assignment.room.capacity,
                    },
                    recommended_action=(
                        "Split the group or provide a larger room before the "
                        "schedule is used for attendance planning."
                    ),
                )
                for assignment in overflow_assignments
            ]
            self.run_insights.extend(overflow_insights)
            if engine is not None:
                self.run_insights.extend(
                    getattr(engine, "solve_insights", [])
                )
            for insight in overflow_insights:
                self.logger.warning(f"INSIGHT: {insight.render()}")

            posted = False
            if blocking:
                self.logger.error(
                    f"Refusing to post: {len(blocking)} unresolved double-booking(s). "
                    f"See the conflict report."
                )
            elif incomplete:
                self.logger.error(
                    f"Refusing to post: {len(unscheduled_failures)} block(s) could "
                    f"not be scheduled. Resolve the data insight(s) or pass "
                    f"allow_partial to override explicitly."
                )
            else:
                # Send schedule to backend
                try:
                    # Convert assignments to API format. The persisted room
                    # pool is the resolved post-filter list, so the schedule
                    # records exactly which rooms its generation could use
                    # (explicit-all for unscoped runs).
                    api_data = convert_assignments_to_api_format(
                        assignments=self.assignments,
                        schedule_name_en=self.schedule_name_en,
                        schedule_name_ar=self.schedule_name_ar,
                        source_schedule_id=self.base_schedule_id,
                        reserved_period=self.reserved_period,
                        available_hall_ids=[hall.id for hall in self.halls],
                        available_lab_ids=[lab.id for lab in self.labs],
                    )

                    # Validate with backend-specific validation
                    if not validate_schedule_data(api_data):
                        self.logger.error("Backend validation failed")
                    else:
                        # Post to backend
                        if post_schedule_with_retry(api_data, max_retries=3):
                            self.logger.info("Successfully posted schedule to backend")
                            posted = True
                        else:
                            self.logger.error("Failed to post schedule to backend")

                except Exception as e:
                    self.logger.error(f"Error posting to backend: {str(e)}")

            # Always write the conflict report, including the clean case, so there
            # is one place to look after every run.
            self.conflict_summary = build_conflict_summary(
                assignments=self.assignments,
                blocking_conflicts=blocking,
                advisory_conflicts=advisory,
                failed_blocks=engine.failed_blocks if engine else [],
                advisory_warnings=(engine.advisory_warnings if engine else [])
                + [insight.render() for insight in overflow_insights],
                total_blocks=self.total_blocks,
                posted=posted,
                reconciliation_summary=self.reconciliation_summary,
            )
            self.conflict_summary["insights"] = [
                insight.to_dict() for insight in self.run_insights
            ]

            conflict_filename = f"reports/conflict_report_{timestamp}.txt"
            generate_conflict_report(self.conflict_summary, conflict_filename)
            self.output_files.append(conflict_filename)

            totals = self.conflict_summary["totals"]
            if self.conflict_summary["clean"]:
                message = "Schedule generation completed - no conflicts"
            else:
                message = (
                    f"Schedule generation completed with "
                    f"{totals['blocking_conflicts']} blocking conflict(s), "
                    f"{totals['blocks_unscheduled']} unscheduled block(s)"
                    + ("" if posted else " - not posted to backend")
                )

            self.progress.update_progress(7, message, {"conflicts": totals})

            if incomplete:
                self._record_failure(
                    "INCOMPLETE_SCHEDULE",
                    f"{totals['blocks_scheduled']}/{totals['blocks_expected']} "
                    "blocks scheduled - incomplete schedules are not posted "
                    "(allow_partial overrides this explicitly).",
                    {
                        "insights": [
                            insight.to_dict() for insight in self.run_insights
                        ],
                        "conflict_summary": self.conflict_summary,
                        "run_snapshot": self.run_snapshot_path,
                    },
                )
                self.logger.info(f"Results saved to: {self.output_files}")
                return False

            self.logger.info(f"Results saved to: {self.output_files}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save results: {str(e)}", exc_info=True)
            self._record_failure(
                "RESULTS_SAVE_FAILED", "Unable to save the generated schedule"
            )
            return False

    def _generate_enhanced_json(self, filename: str):
        """Generate enhanced JSON with schedule metadata"""
        # Get basic schedule data
        schedule_data = generate_schedule_json(self.assignments, filename)

        # Calculate facilities used with composite keys
        room_keys_used = set(get_room_key(a.room) for a in self.assignments.values())
        halls_used = len([rk for rk in room_keys_used if rk[0] == "hall"])
        labs_used = len([rk for rk in room_keys_used if rk[0] == "lab"])

        # Add our custom metadata
        enhanced_metadata = {
            **schedule_data["metadata"],
            "schedule_name_en": self.schedule_name_en,
            "schedule_name_ar": self.schedule_name_ar,
            "study_plan_ids": self.study_plan_ids,
            "study_plans": [
                {
                    "academic_list": sp.academic_list.name,
                    "academic_level": sp.academic_level,
                    "expected_students": sp.expected_students,
                    "courses_count": len(sp.course_assignments),
                }
                for sp in self.study_plans
            ],
            "facilities_used": {
                "halls": halls_used,
                "labs": labs_used,
                "total_unique_rooms": len(room_keys_used),
            },
        }

        # Update the file with enhanced metadata
        schedule_data["metadata"] = enhanced_metadata

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(schedule_data, f, indent=2, ensure_ascii=False)


def setup_logging():
    """Setup comprehensive logging configuration - everything goes to debug.log"""

    # Remove any existing handlers to prevent duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create detailed formatter
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
    )

    # File handler for ALL logging - this is what you'll send to me
    file_handler = logging.FileHandler(
        "scheduler_debug_full.log", mode="w", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)

    # Console handler (minimal output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    # Configure root logger to capture EVERYTHING
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Force all loggers to DEBUG level
    loggers = [
        "ScheduleGenerationEngine",
        "ProgressTracker",
        "backend.get_study_plans",
        "backend.get_halls",
        "backend.get_labs",
        "backend.get_staff_members",
        "utils.api_study_plans",
        "utils.api_staff",
        "utils.api_academics",
        "utils.api_departments",
        "utils.api_halls",
        "utils.api_labs",
        "scheduler",
        "constraint_manager",
        "resource_manager",
        "requests.packages.urllib3",
        "requests",
    ]

    for logger_name in loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True  # Ensure logs propagate to root logger

    # Log the setup
    setup_logger = logging.getLogger("LogSetup")
    setup_logger.info("=== COMPREHENSIVE LOGGING SETUP COMPLETE ===")
    setup_logger.info("All logs will be saved to: scheduler_debug_full.log")
    setup_logger.info("This file will contain ALL debugging information")
    setup_logger.info("=" * 60)


# One generation at a time. The progress file and the debug log are single,
# process-wide files, so two concurrent runs would interleave their progress and
# truncate each other's log.
_generation_lock = threading.Lock()
_generation_running = False


@app.post("/generate")
def generate_schedule(req: GenerateRequest):
    global _generation_running

    with _generation_lock:
        if _generation_running:
            raise HTTPException(
                status_code=409,
                detail="A schedule generation is already running. "
                "Wait for it to finish or poll /progress.",
            )
        _generation_running = True

    # Replace any terminal result from a previous run before responding, so the
    # frontend begins polling a known running state instead of stale progress.
    queued_progress = ProgressTracker()
    queued_progress.update_progress(0, "Schedule generation queued")

    def run_generation():
        global _generation_running
        engine = None
        try:
            engine = ScheduleGenerationEngine(
                study_plan_ids=req.study_plans,
                schedule_name_en=req.name_en,
                schedule_name_ar=req.name_ar,
                base_schedule_id=req.base_schedule_id,
                lectures_only=req.lectures_only,
                hall_ids=req.hall_ids,
                lab_ids=req.lab_ids,
                allow_partial=req.allow_partial,
            )
            if not engine.run():
                logging.getLogger("ScheduleGenerationEngine").error(
                    "Schedule generation ended with a failure state"
                )
        except Exception as error:
            logging.getLogger("ScheduleGenerationEngine").error(
                "Unhandled generation worker error: %s", error, exc_info=True
            )
            progress = engine.progress if engine else ProgressTracker()
            progress.complete(
                False,
                message="Schedule generation stopped unexpectedly",
                error={
                    "code": "GENERATION_WORKER_FAILED",
                    "message": "Schedule generation stopped unexpectedly",
                    "details": {},
                },
            )
        finally:
            with _generation_lock:
                _generation_running = False

    # Start generation in background thread
    thread = threading.Thread(target=run_generation)
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": "Schedule generation started in background"}


@app.get("/progress")
def get_progress():
    progress_file = "schedule_progress.json"
    if not os.path.exists(progress_file):
        raise HTTPException(status_code=404, detail="Progress file not found")

    with open(progress_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# Rebuilt constraint state for saved schedules, keyed (schedule_id, version).
# The editor validates every hypothetical move/swap here so the constraint
# rules stay owned by the engine alone.
_state_cache = ScheduleStateCache()


@app.post("/validate-change")
def validate_change(req: ValidateChangeRequest):
    # apply=true comes from Laravel after a successful database write: commit
    # the same change to the cached state instead of validating it.
    if req.apply:
        applied = apply_change(
            _state_cache,
            req.schedule_id,
            req.state_version,
            req.change.model_dump(),
            req.new_version or "",
        )
        return {"ok": applied, "applied": applied}

    try:
        cached = _state_cache.get(req.schedule_id, req.state_version, req.refresh)
    except Exception as error:
        logging.getLogger("change_validator").error(
            "Failed to load state for schedule %s: %s", req.schedule_id, error
        )
        raise HTTPException(
            status_code=503,
            detail=f"Could not load schedule state for validation: {error}",
        ) from error

    try:
        return run_validation(cached, req.change.model_dump())
    except KeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9000)
