# utils/api_departments.py


import logging
from typing import List

from models.department import Department


def convert_api_department(department_data: dict) -> Department:
    """
    Convert API department data to Department enum.

    Maps the English name from the API to your Department enum.
    Falls back to GENERAL if no match is found.
    """
    # Get the English name from API and standardize it
    name_en = department_data.get("nameEn", "").upper().replace(" ", "_")

    logging.debug(
        f"Converting department: '{name_en}' (ID: {department_data.get('id')})"
    )

    # Try to find a matching enum value
    for dept in Department:
        # Compare with standardized enum name
        if dept.name == name_en:
            logging.debug(f"Found direct match: {dept.name}")
            return dept

    # Second pass with more flexible matching
    name_en_lower = name_en.lower()
    for dept in Department:
        # Compare with value string (which might have spaces)
        if dept.value.upper().replace(" ", "_") == name_en:
            logging.debug(f"Found match by value: {dept.name}")
            return dept

    # Special cases mapping
    special_cases = {
        "GENERAL": Department.GENERAL,
        "COMPUTER_SCIENCE": Department.COMPUTER_SCIENCE,
        "INFORMATION_TECHNOLOGY": Department.INFORMATION_TECHNOLOGY,
        "INFORMATION_SYSTEMS": Department.INFORMATION_SYSTEMS,
        "ARTIFICIAL_INTELLIGENCE": Department.ARTIFICIAL_INTELLIGENCE,
        "CYBERSECURITY": Department.CYBERSECURITY,
    }

    if name_en in special_cases:
        logging.debug(f"Found match in special cases: {special_cases[name_en].name}")
        return special_cases[name_en]

    # Handle cases that might not match exactly
    if "COMPUTER" in name_en:
        logging.debug(f"Defaulting to COMPUTER_SCIENCE for {name_en}")
        return Department.COMPUTER_SCIENCE
    elif "TECHNOLOGY" in name_en or "IT" == name_en:
        logging.debug(f"Defaulting to INFORMATION_TECHNOLOGY for {name_en}")
        return Department.INFORMATION_TECHNOLOGY
    elif "SYSTEM" in name_en or "IS" == name_en:
        logging.debug(f"Defaulting to INFORMATION_SYSTEMS for {name_en}")
        return Department.INFORMATION_SYSTEMS
    elif "AI" in name_en or "ARTIFICIAL" in name_en or "INTELLIGENCE" in name_en:
        logging.debug(f"Defaulting to ARTIFICIAL_INTELLIGENCE for {name_en}")
        return Department.ARTIFICIAL_INTELLIGENCE
    elif "CYBER" in name_en or "SECURITY" in name_en:
        logging.debug(f"Defaulting to CYBERSECURITY for {name_en}")
        return Department.CYBERSECURITY
    else:
        logging.warning(f"No match found for '{name_en}', defaulting to GENERAL")
        return Department.GENERAL
