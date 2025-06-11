# backend/get_halls.py


import os

import requests
from dotenv import load_dotenv

from backend.login import get_auth_token
from utils.api_halls import convert_api_hall


def get_halls():
    load_dotenv()
    token = get_auth_token()

    url = os.getenv("BACKEND_URL")
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Authorization": f"Bearer {token}",
    }

    response = requests.get(f"{url}/halls", headers=headers)
    data = response.json()
    halls_data = data["data"]

    halls = [convert_api_hall(hall_data) for hall_data in halls_data]
    return halls


if __name__ == "__main__":
    get_halls()
