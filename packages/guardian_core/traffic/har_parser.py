"""Parse HAR (HTTP Archive) uploads into a normalized stream of request/response records.

Uses ``haralyzer`` 2.x for HAR semantics and ``ijson`` for streaming-friendly
JSON body decoding. We do **not** load the entire HAR into a single Python
object when we can avoid it; ``ijson`` is used opportunistically for the
``content.text`` field of large response bodies.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import ijson
from haralyzer import HarParser
from pydantic import BaseModel, ConfigDict, Field

from guardian_core.logging import get_logger

log = get_logger(__name__)


class HarRequestRecord(BaseModel):
    """One request/response pair extracted from a HAR entry.

    Bodies are already decoded as JSON when possible; ``request_body`` and
    ``response_body`` are ``None`` if the body was missing or unparseable.
    """

    model_config = ConfigDict(extra="forbid")

    method: str
    url: str
    status: int
    request_body: Any | None = None
    response_body: Any | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    timestamp: str | None = None
    content_type_request: str | None = None
    content_type_response: str | None = None


@dataclass
class _ParsedHar:
    entries: list[dict[str, Any]] = field(default_factory=list)


def _stream_json_body(text: str) -> Any | None:
    """Decode a JSON body, preferring ``ijson`` for large payloads.

    Returns ``None`` for empty / non-JSON strings rather than raising.
    """
    if not text or not text.strip():
        return None
    # Cheap heuristic: ijson pays off above ~64KB; below that ``json`` is faster.
    if len(text) < 64_000:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
    try:
        buf = io.BytesIO(text.encode("utf-8"))
        # ``ijson.items(buf, '')`` yields the root value once.
        items_iter: Iterator[Any] = ijson.items(buf, "")
        for value in items_iter:
            return value
    except (ijson.JSONError, ValueError, UnicodeDecodeError):
        return None
    return None


def _headers_to_dict(headers: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in headers:
        name = h.get("name")
        value = h.get("value")
        if isinstance(name, str) and isinstance(value, str):
            out[name.lower()] = value
    return out


def _querystring_to_dict(qs: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for q in qs:
        name = q.get("name")
        value = q.get("value")
        if isinstance(name, str):
            out[name] = value if isinstance(value, str) else ""
    return out


def parse_har_bytes(raw: bytes) -> list[HarRequestRecord]:
    """Parse a HAR payload (bytes) and yield normalized request records.

    Bytes are passed to ``haralyzer.HarParser`` which validates structure;
    we then walk the entries and decode JSON bodies one at a time so a
    single oversized response cannot blow up the whole import.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid HAR payload: {exc}") from exc

    if not isinstance(data, dict) or "log" not in data:
        raise ValueError("invalid HAR payload: missing 'log'")

    parser = HarParser(har_data=data)
    out: list[HarRequestRecord] = []
    for page in parser.pages:
        for entry in page.entries:
            record = _entry_to_record(entry.raw_entry)
            if record is not None:
                out.append(record)
    # Some HARs have no pages — fall back to top-level entries when needed.
    if not parser.pages:
        for raw_entry in data["log"].get("entries", []):
            record = _entry_to_record(raw_entry)
            if record is not None:
                out.append(record)
    log.debug("har.parsed", entry_count=len(out))
    return out


def _entry_to_record(raw_entry: dict[str, Any]) -> HarRequestRecord | None:
    """Convert one HAR entry dict into a ``HarRequestRecord``."""
    request = raw_entry.get("request") or {}
    response = raw_entry.get("response") or {}
    method = request.get("method")
    url = request.get("url")
    status = response.get("status")
    if not isinstance(method, str) or not isinstance(url, str):
        return None
    if not isinstance(status, int):
        status = 0
    req_headers = _headers_to_dict(request.get("headers") or [])
    resp_headers = _headers_to_dict(response.get("headers") or [])
    query = _querystring_to_dict(request.get("queryString") or [])

    request_body: Any | None = None
    post = request.get("postData") or {}
    post_text = post.get("text")
    if isinstance(post_text, str):
        request_body = _stream_json_body(post_text)

    response_body: Any | None = None
    content = response.get("content") or {}
    resp_text = content.get("text")
    if isinstance(resp_text, str):
        response_body = _stream_json_body(resp_text)

    return HarRequestRecord(
        method=method.upper(),
        url=url,
        status=status,
        request_body=request_body,
        response_body=response_body,
        request_headers=req_headers,
        response_headers=resp_headers,
        query=query,
        timestamp=(
            raw_entry.get("startedDateTime")
            if isinstance(raw_entry.get("startedDateTime"), str)
            else None
        ),
        content_type_request=req_headers.get("content-type"),
        content_type_response=resp_headers.get("content-type"),
    )
