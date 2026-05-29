"""Fixture: a small `requests` client used by the miner test corpus."""

from __future__ import annotations

import requests

BASE_URL = "https://api.example.com"


def list_users() -> object:
    return requests.get(f"{BASE_URL}/users", params={"limit": 10, "cursor": ""})


def get_user(user_id: str) -> object:
    return requests.get(f"{BASE_URL}/users/{user_id}")


def create_user(email: str, name: str) -> object:
    return requests.post(
        f"{BASE_URL}/users",
        json={"email": email, "name": name},
    )


def update_user(user_id: str, name: str) -> object:
    return requests.patch(
        f"{BASE_URL}/users/{user_id}",
        json={"name": name},
    )


def delete_user(user_id: str) -> object:
    return requests.delete(f"{BASE_URL}/users/{user_id}")


def search(query: str) -> object:
    return requests.request(
        "GET",
        f"{BASE_URL}/search",
        params={"q": query},
    )


def health() -> object:
    return requests.get("https://api.example.com/healthz")
