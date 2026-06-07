"""Fixture: a small `requests` client used by the miner test corpus."""

from __future__ import annotations

import requests

BASE_URL = "https://api.example.com"

# bandit B113 is moot here: this file is a *fixture* — it is never executed,
# only parsed by the static AST miner — but we still pass a timeout to keep
# the security scan green and model real-world client code.
_TIMEOUT = 10


def list_users() -> object:
    return requests.get(
        f"{BASE_URL}/users",
        params={"limit": 10, "cursor": ""},
        timeout=_TIMEOUT,
    )


def get_user(user_id: str) -> object:
    return requests.get(f"{BASE_URL}/users/{user_id}", timeout=_TIMEOUT)


def create_user(email: str, name: str) -> object:
    return requests.post(
        f"{BASE_URL}/users",
        json={"email": email, "name": name},
        timeout=_TIMEOUT,
    )


def update_user(user_id: str, name: str) -> object:
    return requests.patch(
        f"{BASE_URL}/users/{user_id}",
        json={"name": name},
        timeout=_TIMEOUT,
    )


def delete_user(user_id: str) -> object:
    return requests.delete(f"{BASE_URL}/users/{user_id}", timeout=_TIMEOUT)


def search(query: str) -> object:
    return requests.request(
        "GET",
        f"{BASE_URL}/search",
        params={"q": query},
        timeout=_TIMEOUT,
    )


def health() -> object:
    return requests.get("https://api.example.com/healthz", timeout=_TIMEOUT)
