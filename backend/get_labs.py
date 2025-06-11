# backend/get_labs.py


import logging
import os
from typing import List, Optional

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token, login
from models.labs import Lab, LabType
from utils.api_labs import convert_api_lab

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def get_labs(lab_type: Optional[str] = None) -> List[Lab]:
    """
    Fetch labs from the backend API.

    Args:
        lab_type: Optional filter for lab type ("specialist" or "general")
                 If None, all labs are returned

    Returns:
        List of Lab objects
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    # Prepare query parameters if filtering is requested
    params = {}
    if lab_type:
        params["type"] = lab_type

    response = requests.get(f"{url}/laps", headers=headers, params=params)
    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        labs_data = data["data"]
    else:
        # Handle the case where the response structure is different
        labs_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(f"Raw API response contained {len(labs_data)} labs")

    # Convert API data to Lab objects
    labs = [convert_api_lab(lab_data) for lab_data in labs_data]

    # Local filtering if API doesn't support it
    filtered_labs = labs
    if lab_type:
        if lab_type.lower() == "specialist":
            logging.debug("Filtering for SPECIALIST labs")
            filtered_labs = [lab for lab in labs if lab.lab_type == LabType.SPECIALIST]
            logging.debug(f"Found {len(filtered_labs)} SPECIALIST labs after filtering")
        elif lab_type.lower() == "general":
            logging.debug("Filtering for GENERAL labs")
            filtered_labs = [lab for lab in labs if lab.lab_type == LabType.GENERAL]
            logging.debug(f"Found {len(filtered_labs)} GENERAL labs after filtering")

    # Log each lab's details for verification
    for lab in filtered_labs:
        logging.debug(
            f"Lab in result: {lab.name} (ID: {lab.id}) - Type: {lab.lab_type.name}, "
            f"Non-specialist usable: {lab.used_in_non_specialist_courses}"
        )

    return filtered_labs


def get_specialist_labs() -> List[Lab]:
    """
    Fetch only specialist labs from the backend API.

    Returns:
        List of Lab objects with lab_type == LabType.SPECIALIST
    """
    return get_labs(lab_type="specialist")


def get_general_labs() -> List[Lab]:
    """
    Fetch only general labs from the backend API.

    Returns:
        List of Lab objects with lab_type == LabType.GENERAL
    """
    return get_labs(lab_type="general")


def get_labs_for_non_specialist_courses() -> List[Lab]:
    """
    Fetch labs that can be used for non-specialist courses based on business rules:
    1. All general labs regardless of usedInNonSpecialist flag
    2. Only specialist labs where usedInNonSpecialist is true

    Returns:
        List of Lab objects that can be used for non-specialist courses
    """
    all_labs = get_labs()

    # Apply business rules with detailed logging
    usable_labs = []
    for lab in all_labs:
        if lab.lab_type == LabType.GENERAL:
            logging.debug(f"Including general lab: {lab.name} (ID: {lab.id})")
            usable_labs.append(lab)
        elif lab.lab_type == LabType.SPECIALIST and lab.used_in_non_specialist_courses:
            logging.debug(
                f"Including specialist lab with non-specialist usage: {lab.name} (ID: {lab.id})"
            )
            usable_labs.append(lab)
        else:
            logging.debug(
                f"Excluding specialist lab without non-specialist usage: {lab.name} (ID: {lab.id})"
            )

    logging.debug(f"Found {len(usable_labs)} labs for non-specialist courses")
    return usable_labs


def get_labs_for_specialist_courses() -> List[Lab]:
    """
    Fetch all labs that can be used for specialist courses.
    For specialist courses, all labs (both general and specialist) can be used.

    Returns:
        List of all Lab objects
    """
    # For specialist courses, all labs can be used
    return get_labs()


if __name__ == "__main__":
    # Set to DEBUG for detailed output
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Test the general function
    all_labs = get_labs()
    print(f"Retrieved {len(all_labs)} total labs")

    # Print detailed info about all labs to verify conversion
    print("\nDETAILED LAB INFORMATION:")
    for lab in all_labs:
        print(f"Lab: {lab.name} (ID: {lab.id})")
        print(f"  Type: {lab.lab_type.name}")
        print(f"  Used in non-specialist courses: {lab.used_in_non_specialist_courses}")

    # Test the specialist labs function
    specialist_labs = get_specialist_labs()
    print(f"\nRetrieved {len(specialist_labs)} specialist labs")
    for lab in specialist_labs:
        print(f"  - {lab.name} (ID: {lab.id})")

    # Test the general labs function
    general_labs = get_general_labs()
    print(f"\nRetrieved {len(general_labs)} general labs")
    for lab in general_labs:
        print(f"  - {lab.name} (ID: {lab.id})")

    # Test the non-specialist courses function with updated business logic
    non_specialist_labs = get_labs_for_non_specialist_courses()
    print(
        f"\nRetrieved {len(non_specialist_labs)} labs usable for non-specialist courses"
    )
    for lab in non_specialist_labs:
        print(f"  - {lab.name} (ID: {lab.id}), Type: {lab.lab_type.name}")

    # Test the specialist courses function
    specialist_course_labs = get_labs_for_specialist_courses()
    print(
        f"\nRetrieved {len(specialist_course_labs)} labs usable for specialist courses"
    )
