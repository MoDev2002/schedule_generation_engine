# backend/get_departments.py


import logging
import os
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token, login
from models.department import Department
from utils.api_departments import convert_api_department

# Set up logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def get_departments() -> List[Dict]:
    """
    Fetch departments from the backend API.

    Returns:
        List of dictionaries with department information including:
        - id: The department ID from the API
        - name: The localized name (Arabic in this case)
        - name_en: The English name
        - name_ar: The Arabic name
    """
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    response = requests.get(f"{url}/departments", headers=headers)
    data = response.json()

    # Check if response has the expected structure
    if "data" in data:
        departments_data = data["data"]
    else:
        # Handle the case where the response structure is different
        departments_data = data if isinstance(data, list) else []

    # Log the raw data for debugging
    logging.debug(f"Raw API response contained {len(departments_data)} departments")

    # Process departments with detailed information
    departments = []
    for dept_data in departments_data:
        # Convert to standard format
        department_info = {
            "id": dept_data["id"],
            "name": dept_data["name"],
            "name_en": dept_data["nameEn"],
            "name_ar": dept_data["nameAr"],
        }
        departments.append(department_info)

        # Log for verification
        logging.debug(f"Processed department: {department_info['name_en']}")

    return departments


def get_department_by_id(department_id: int) -> Optional[Dict]:
    """
    Get department by its ID.

    Args:
        department_id: The department ID to find

    Returns:
        Dictionary with department information or None if not found
    """
    departments = get_departments()
    for dept in departments:
        if dept["id"] == department_id:
            return dept
    return None


def get_department_by_name(name: str, language: str = "en") -> Optional[Dict]:
    """
    Get department by its name.

    Args:
        name: The department name to find
        language: The language of the name ('en' or 'ar')

    Returns:
        Dictionary with department information or None if not found
    """
    departments = get_departments()

    # Determine which field to search based on language
    field_name = "name_en" if language == "en" else "name_ar"

    # Case-insensitive search
    name_lower = name.lower()
    for dept in departments:
        if dept[field_name].lower() == name_lower:
            return dept
    return None


def get_department_map() -> Dict[int, Department]:
    """
    Get a mapping of department IDs to Department enum values.

    Returns:
        Dictionary mapping department IDs to Department enum values
    """
    departments = get_departments()
    return {dept["id"]: dept for dept in departments}


if __name__ == "__main__":
    # Test the main function
    departments = get_departments()
    print(f"Retrieved {len(departments)} departments")

    # Print detailed information
    print("\nDEPARTMENTS LIST:")
    for dept in departments:
        print(f"ID: {dept['id']} - {dept['name']}")

    # Test ID lookup
    if departments:
        test_id = departments[0]["id"]
        dept_by_id = get_department_by_id(test_id)
        print(
            f"\nLookup by ID {test_id}: {dept_by_id['name_en'] if dept_by_id else 'Not found'}"
        )

    # Test name lookup
    if departments:
        test_name = departments[0]["name_en"]
        dept_by_name = get_department_by_name(test_name)
        print(
            f"Lookup by name '{test_name}': {dept_by_name['id'] if dept_by_name else 'Not found'}"
        )
