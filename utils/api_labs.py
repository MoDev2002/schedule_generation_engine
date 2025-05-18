# utils/api_labs.py
import logging
from typing import List

from models.labs import Lab, LabType
from utils.time_utils import convert_api_time_preference


def convert_api_lab_type(type_str: str) -> LabType:
    """Convert lab type string from API to LabType enum"""
    logging.debug(f"Converting lab type: '{type_str}'")

    if not type_str:
        logging.warning(f"Empty lab type provided, defaulting to GENERAL")
        return LabType.GENERAL

    type_str = str(type_str).lower()  # Force string and lowercase

    if type_str == "specialist":
        logging.debug(f"Converted to SPECIALIST")
        return LabType.SPECIALIST
    elif type_str == "general":
        logging.debug(f"Converted to GENERAL")
        return LabType.GENERAL
    else:
        logging.warning(f"Unknown lab type '{type_str}', defaulting to GENERAL")
        return LabType.GENERAL


def convert_api_lab(lab_data: dict) -> Lab:
    """Convert API lab data to Lab object"""
    lab_id = lab_data["id"]
    name = lab_data["name"]
    capacity = lab_data["capacity"]

    # Extract and log the lab type for debugging
    raw_lab_type = lab_data.get("labType", "general")
    logging.debug(f"Lab {name} (ID: {lab_id}) has raw labType: '{raw_lab_type}'")

    lab_type = convert_api_lab_type(raw_lab_type)

    # Convert 0/1 to boolean - Laravel uses 0/1 for boolean values
    used_in_non_specialist = bool(lab_data.get("usedInNonSpecialistCourses", 1))
    logging.debug(
        f"Lab {name} (ID: {lab_id}) used_in_non_specialist: {used_in_non_specialist}"
    )

    availability = []
    for time_pref_data in lab_data["availability"]:
        availability.append(convert_api_time_preference(time_pref_data))

    lab = Lab(lab_id, name, capacity, availability, lab_type, used_in_non_specialist)
    logging.debug(
        f"Converted lab: {lab} with type {lab.lab_type} and used_in_non_specialist={lab.used_in_non_specialist_courses}"
    )
    return lab
