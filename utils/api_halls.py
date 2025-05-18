from models.halls import Hall
from utils.time_utils import convert_api_time_preference


def convert_api_hall(hall_data: dict) -> Hall:
    """Convert API hall data to Hall object"""
    hall_id = hall_data["id"]
    name = hall_data["name"]
    capacity = hall_data["capacity"]

    availability = []
    for time_pref_data in hall_data["availability"]:
        availability.append(convert_api_time_preference(time_pref_data))

    return Hall(hall_id, name, capacity, availability)
