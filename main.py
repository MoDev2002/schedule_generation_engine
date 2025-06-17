# main.py - Schedule Generation Engine Entry Point with Enhanced Logging

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Dict, List, Optional

from backend.get_halls import get_halls
from backend.get_labs import get_labs
from backend.get_study_plans import get_study_plans_by_ids
from backend.post_schedule import post_schedule_with_retry, validate_schedule_data
from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.block import BlockType
from schedule_format import (
    generate_schedule_json,
    generate_schedule_report,
    print_schedule_statistics,
)
from schedule_validator import ScheduleValidator
from scheduler import SchedulingEngine
from utils.api_schedule import convert_assignments_to_api_format
from utils.room_utils import get_room_key


class ProgressTracker:
    """Track and report progress during schedule generation"""

    def __init__(self):
        self.current_step = 0
        self.total_steps = 6
        self.step_descriptions = {
            0: "Initializing",
            1: "Fetching study plans",
            2: "Fetching rooms and labs",
            3: "Fetching staff members",
            4: "Validating input data",
            5: "Generating schedule",
            6: "Saving results",
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
            "current_step": step,
            "total_steps": self.total_steps,
            "percentage": current_percentage,
            "step_description": self.step_descriptions.get(step, "Unknown"),
            "message": message,
            "details": details or {},
            "scheduling_details": self.scheduling_details.copy(),
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": (datetime.now() - self.start_time).total_seconds(),
        }

        # Save to file for frontend
        try:
            with open(self.progress_file, "w") as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save progress: {e}")

        # Also log to console
        percentage = progress_data["percentage"]
        print(
            f"[{percentage:5.1f}%] Step {step}/{self.total_steps}: {progress_data['step_description']}"
        )
        if message:
            print(f"         {message}")

        # Show detailed scheduling progress
        if step == 5 and self.scheduling_details["total_blocks_overall"] > 0:
            sched = self.scheduling_details
            print(
                f"         Study Plan {sched['current_study_plan']}/{sched['total_study_plans']}: {sched['current_study_plan_name']}"
            )
            print(
                f"         Blocks: {sched['total_blocks_scheduled']}/{sched['total_blocks_overall']} total ({sched['current_blocks_scheduled']}/{sched['total_blocks_in_current_plan']} in current)"
            )
            print(f"         Phase: {sched['scheduling_phase']}")

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

    def complete(self, success: bool, output_files: List[str] = None):
        """Mark the process as complete"""
        logger = logging.getLogger("ProgressTracker")
        logger.info(f"PROCESS COMPLETE: Success={success}, Files={output_files}")

        progress_data = {
            "current_step": self.total_steps,
            "total_steps": self.total_steps,
            "percentage": 100.0,
            "step_description": "Complete" if success else "Failed",
            "success": success,
            "output_files": output_files or [],
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": (datetime.now() - self.start_time).total_seconds(),
        }

        try:
            with open(self.progress_file, "w") as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save final progress: {e}")


class ScheduleGenerationEngine:
    """Main engine for generating university schedules"""

    def __init__(
        self, study_plan_ids: List[int], schedule_name_en: str, schedule_name_ar: str
    ):
        self.study_plan_ids = study_plan_ids
        self.schedule_name_en = schedule_name_en
        self.schedule_name_ar = schedule_name_ar
        self.progress = ProgressTracker()
        self.logger = logging.getLogger("ScheduleGenerationEngine")

        # Data containers
        self.study_plans = []
        self.halls = []
        self.labs = []
        self.constraint_manager = None
        self.resource_manager = None
        self.scheduling_engine = None

        # Results
        self.assignments = {}
        self.output_files = []

        self.logger.info(f"INITIALIZATION: study_plan_ids={study_plan_ids}")
        self.logger.info(f"INITIALIZATION: schedule_name_en={schedule_name_en}")
        self.logger.info(f"INITIALIZATION: schedule_name_ar={schedule_name_ar}")

    def run(self) -> bool:
        """Main execution method"""
        try:
            self.logger.info("=== STARTING SCHEDULE GENERATION ENGINE ===")
            self.progress.update_progress(0, "Starting schedule generation engine")

            # Step 1: Fetch study plans
            if not self._fetch_study_plans():
                return False

            # Step 2: Fetch rooms and labs
            if not self._fetch_facilities():
                return False

            # Step 3: Staff members are already included in study plans
            self.progress.update_progress(
                3, "Staff member data loaded from study plans"
            )

            # Step 4: Validate input data
            if not self._validate_input_data():
                return False

            # Step 5: Generate schedule
            if not self._generate_schedule():
                return False

            # Step 6: Save results
            if not self._save_results():
                return False

            self.progress.complete(True, self.output_files)
            print(f"\nSUCCESS: Schedule generation completed successfully!")
            print(f"Output files: {', '.join(self.output_files)}")
            self.logger.info("=== SCHEDULE GENERATION COMPLETED SUCCESSFULLY ===")
            return True

        except Exception as e:
            self.logger.error(f"Schedule generation failed: {str(e)}", exc_info=True)
            print(f"\nFAILED: Schedule generation failed: {str(e)}")
            self.progress.complete(False)
            return False

    def _fetch_study_plans(self) -> bool:
        """Fetch study plans from backend"""
        try:
            self.logger.info("=== FETCHING STUDY PLANS ===")
            self.progress.update_progress(
                1, f"Fetching {len(self.study_plan_ids)} study plans"
            )

            self.logger.info(f"Requesting study plans with IDs: {self.study_plan_ids}")
            self.study_plans = get_study_plans_by_ids(
                self.study_plan_ids, resolve_refs=True
            )

            self.logger.info(
                f"Received {len(self.study_plans)} study plans from backend"
            )

            if not self.study_plans:
                self.logger.error("No study plans found with the provided IDs")
                print("ERROR: No study plans found with the provided IDs")
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
            print(f"Failed to fetch study plans: {str(e)}")
            return False

    def _fetch_facilities(self) -> bool:
        """Fetch halls and labs from backend"""
        try:
            self.logger.info("=== FETCHING FACILITIES ===")
            self.progress.update_progress(2, "Fetching halls and laboratories")

            # Fetch halls
            self.logger.info("Fetching halls...")
            self.halls = get_halls()
            self.logger.info(f"Received {len(self.halls)} halls")

            # Fetch labs
            self.logger.info("Fetching labs...")
            self.labs = get_labs()
            self.logger.info(f"Received {len(self.labs)} labs")

            if not self.halls and not self.labs:
                self.logger.error("No facilities (halls or labs) found")
                print("No facilities (halls or labs) found")
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
            print(f"Failed to fetch facilities: {str(e)}")
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
                print(f"Validation failed with {len(errors)} errors:")
                for error in errors:
                    print(f"   - {error.message}")
                return False

            if warnings:
                print(f"Validation completed with {len(warnings)} warnings:")
                for warning in warnings:
                    print(f"   - {warning.message}")

            details = {
                "errors_count": len(errors),
                "warnings_count": len(warnings),
                "total_validations": len(validation_messages),
            }

            self.progress.update_progress(
                4,
                f"Validation completed: {len(errors)} errors, {len(warnings)} warnings",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(f"Validation failed: {str(e)}", exc_info=True)
            print(f"Validation failed: {str(e)}")
            return False

    def _generate_schedule(self) -> bool:
        """Generate the actual schedule"""
        try:
            self.logger.info("=== GENERATING SCHEDULE ===")
            self.progress.update_progress(5, "Initializing scheduling engine")

            # Initialize managers
            self.constraint_manager = ConstraintManager()
            self.resource_manager = ResourceManager(self.halls, self.labs)
            self.scheduling_engine = SchedulingEngine(
                self.constraint_manager, self.resource_manager
            )

            # Collect ALL course assignments from ALL study plans
            self.logger.info("=== COLLECTING ALL COURSE ASSIGNMENTS ===")
            all_course_assignments = []
            study_plan_mapping = {}
            study_plan_info = []  # For tracking and progress
            total_blocks = 0

            for i, study_plan in enumerate(self.study_plans):
                study_plan_name = study_plan.name
                self.logger.info(f"Study Plan {i+1}: {study_plan_name}")

                # Add all course assignments from this study plan
                plan_course_count = len(study_plan.course_assignments)
                for course_assignment in study_plan.course_assignments:
                    course_index = len(all_course_assignments)
                    all_course_assignments.append(course_assignment)
                    study_plan_mapping[course_index] = study_plan

                # Calculate expected blocks for this study plan
                plan_blocks = 0
                for course in study_plan.course_assignments:
                    course_blocks = course.lecture_groups + (course.lab_groups or 0)
                    plan_blocks += course_blocks
                    self.logger.info(
                        f"  Course {course.course_code}: {course_blocks} blocks"
                    )

                study_plan_info.append(
                    {
                        "name": study_plan_name,
                        "course_count": plan_course_count,
                        "expected_blocks": plan_blocks,
                    }
                )
                total_blocks += plan_blocks

            self.logger.info(f"=== UNIFIED SCHEDULING SUMMARY ===")
            self.logger.info(f"Total study plans: {len(self.study_plans)}")
            self.logger.info(f"Total course assignments: {len(all_course_assignments)}")
            self.logger.info(f"Total expected blocks: {total_blocks}")

            # Initialize scheduling progress
            self.progress.update_scheduling_progress(
                current_study_plan=1,
                total_study_plans=len(self.study_plans),
                total_blocks_overall=total_blocks,
                total_blocks_scheduled=0,
                scheduling_phase="initialized",
            )

            self.progress.update_progress(
                5,
                f"Unified scheduling: {total_blocks} blocks from {len(self.study_plans)} study plans",
            )

            # Create a representative study plan for scheduling context
            # Use the first study plan as a template, but this is mainly for metadata
            representative_study_plan = self.study_plans[0]

            # Create enhanced progress callback that tracks across all study plans
            def unified_progress_callback(
                scheduled_count, total_count, phase="scheduling", attempt=1
            ):
                self.logger.debug(
                    f"Unified progress: {scheduled_count}/{total_count}, phase={phase}"
                )

                # Calculate which study plan we're conceptually in based on progress
                current_plan = min(
                    len(self.study_plans),
                    max(
                        1, (scheduled_count * len(self.study_plans)) // total_count + 1
                    ),
                )
                current_plan_name = f"Study Plans 1-{len(self.study_plans)} (Unified)"

                self.progress.update_scheduling_progress(
                    current_study_plan=current_plan,
                    current_study_plan_name=current_plan_name,
                    current_blocks_scheduled=scheduled_count,
                    total_blocks_in_current_plan=total_blocks,  # All blocks are in "one plan" now
                    total_blocks_scheduled=scheduled_count,
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
                    5,
                    f"{phase_msg} - All study plans ({scheduled_count}/{total_blocks})",
                )

            # Schedule ALL blocks together as one unified problem
            self.logger.info("=== STARTING UNIFIED SCHEDULING ===")
            self.logger.info("All course assignments will be scheduled together")

            assignments = self.scheduling_engine.schedule_blocks(
                all_course_assignments,
                study_plan_mapping,
                progress_callback=unified_progress_callback,
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
                    if assignment.block.academic_list == study_plan.academic_list.name
                    and assignment.block.academic_level == study_plan.academic_level
                ]

                self.logger.info(
                    f"Study Plan {i+1} ({study_plan_name}): {len(plan_assignments)} assignments"
                )

            self.assignments = assignments

            if not self.assignments:
                self.logger.error("No schedule assignments were generated")
                print("No schedule assignments were generated")
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
                "success_rate": f"{(len(self.assignments)/total_blocks)*100:.1f}%",
            }

            self.logger.info(
                f"UNIFIED SCHEDULING RESULT: {len(self.assignments)}/{total_blocks} blocks scheduled ({details['success_rate']})"
            )

            self.progress.update_progress(
                5,
                f"Unified schedule completed! {len(self.assignments)}/{total_blocks} blocks scheduled ({details['success_rate']})",
                details,
            )

            return True

        except Exception as e:
            self.logger.error(f"Schedule generation failed: {str(e)}", exc_info=True)
            print(f"Schedule generation failed: {str(e)}")

            self.progress.update_scheduling_progress(scheduling_phase="failed")
            return False

    def _save_results(self) -> bool:
        """Save schedule results to files"""
        try:
            self.logger.info("=== SAVING RESULTS ===")
            self.progress.update_progress(6, "Saving schedule results")

            # Generate timestamp for file names
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Generate JSON file using existing function
            json_filename = f"schedule_{timestamp}.json"
            generate_schedule_json(self.assignments, json_filename)
            self.output_files.append(json_filename)

            # Generate text report using existing function
            txt_filename = f"schedule_report_{timestamp}.txt"
            generate_schedule_report(self.assignments, txt_filename)
            self.output_files.append(txt_filename)

            # Print statistics to console using existing function
            print(f"\nSchedule Statistics:")
            print_schedule_statistics(self.assignments)

            # Send schedule to backend
            try:
                # Convert assignments to API format
                api_data = convert_assignments_to_api_format(
                    assignments=self.assignments,
                    schedule_name_en=self.schedule_name_en,
                    schedule_name_ar=self.schedule_name_ar,
                )

                # Validate with backend-specific validation
                if not validate_schedule_data(api_data):
                    self.logger.error("Backend validation failed")
                    print(
                        "WARNING: Backend validation failed - schedule not posted to backend"
                    )
                else:
                    # Post to backend
                    if post_schedule_with_retry(api_data, max_retries=3):
                        self.logger.info("Successfully posted schedule to backend")
                        print("Schedule successfully posted to backend")
                    else:
                        self.logger.error("Failed to post schedule to backend")
                        print(
                            "WARNING: Failed to post schedule to backend (check logs for details)"
                        )

            except Exception as e:
                self.logger.error(f"Error posting to backend: {str(e)}")
                print(f"WARNING: Failed to post schedule to backend: {str(e)}")

            self.progress.update_progress(
                6,
                f"ٍSchedule generation completed successfully!",
            )

            self.logger.info(f"Results saved to: {self.output_files}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save results: {str(e)}", exc_info=True)
            print(f"Failed to save results: {str(e)}")
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


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="University Schedule Generation Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --study-plans 1 2 3 --name-en "Fall 2024 Schedule" --name-ar "جدول خريف 2024"
  python main.py -sp 1 -ne "Spring Schedule" -na "جدول الربيع"
        """,
    )

    parser.add_argument(
        "--study-plans",
        "-sp",
        nargs="+",
        type=int,
        required=True,
        help="List of study plan IDs to generate schedule for",
    )

    parser.add_argument(
        "--name-en", "-ne", type=str, required=True, help="Schedule name in English"
    )

    parser.add_argument(
        "--name-ar", "-na", type=str, required=True, help="Schedule name in Arabic"
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    return parser.parse_args()


def setup_logging(verbose: bool = False):
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


def main():
    """Main entry point"""
    print("University Schedule Generation Engine")
    print("=" * 50)

    # Parse arguments
    try:
        args = parse_arguments()
    except SystemExit:
        return 1

    # Setup logging
    setup_logging(args.verbose)

    # Log the request
    logger = logging.getLogger("main")
    logger.info("=== SCHEDULE GENERATION ENGINE STARTING ===")
    logger.info(f"Study plans: {args.study_plans}")
    logger.info(f"Schedule name (EN): {args.name_en}")
    logger.info(f"Schedule name (AR): {args.name_ar}")
    logger.info(f"Verbose: {args.verbose}")

    # Create and run engine
    engine = ScheduleGenerationEngine(
        study_plan_ids=args.study_plans,
        schedule_name_en=args.name_en,
        schedule_name_ar=args.name_ar,
    )

    success = engine.run()

    logger.info(f"=== SCHEDULE GENERATION ENGINE FINISHED: SUCCESS={success} ===")
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
