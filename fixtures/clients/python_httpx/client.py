"""Fixture: an `httpx` client used by the miner test corpus."""

from __future__ import annotations

import httpx

API_ROOT = "https://api.example.com/v1"


def list_orders() -> httpx.Response:
    return httpx.get(f"{API_ROOT}/orders", params={"page": 1})


def get_order(order_id: str) -> httpx.Response:
    return httpx.get(f"{API_ROOT}/orders/{order_id}")


def place_order(item: str, qty: int) -> httpx.Response:
    return httpx.post(
        f"{API_ROOT}/orders",
        json={"item": item, "qty": qty},
    )


def cancel_order(order_id: str) -> httpx.Response:
    return httpx.delete(f"{API_ROOT}/orders/{order_id}")


async def fetch_order_items(order_id: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(f"{API_ROOT}/orders/{order_id}/items")
