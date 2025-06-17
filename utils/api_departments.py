# utils/api_departments.py


import logging
from typing import List

from models.department import Department


def convert_api_department(department_data: dict) -> Department:
    """Convert API department data to Department object."""
    return Department(id=department_data["id"], name=department_data["name"])
