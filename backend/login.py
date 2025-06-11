# backend/login.py

import os

import requests
from dotenv import load_dotenv


def login():
    load_dotenv()
    url = os.getenv("BACKEND_URL")
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")

    print(f"Logging in to {url} with email {email}")

    response = requests.post(
        f"{url}/login",
        json={"email": email, "password": password},
        headers={
            "Accept": "application/json",
            "Accept-Language": "en",
        },
    )

    data = response.json()
    token = data["data"]["token"]
    print(f"Login successful. Token: {token}")
    return token


_AUTH_TOKEN = None


def get_auth_token():
    """Get authentication token, login only once"""
    global _AUTH_TOKEN
    if not _AUTH_TOKEN:
        _AUTH_TOKEN = login()
    return _AUTH_TOKEN


if __name__ == "__main__":
    login()
