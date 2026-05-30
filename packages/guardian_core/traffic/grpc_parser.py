"""Parse gRPC call logs into normalized records.

The expected format is JSON-lines (one record per line), each shaped:

    {"method": "POST",
     "path":   "/pkg.Service/Method",
     "client": "billing-worker",
     "request":  {...},
     "response": {...},
     "status":   0,
     "timestamp": "2026-05-29T12:00:00Z"}

This keeps the gRPC ingestion symmetric with HAR ingestion: both produce
``(method, path, request_body, response_body, client, status, timestamp)``
tuples that flow into the same schema-inference + telemetry pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from guardian_core.logging import get_logger

log = get_logger(__name__)


class GrpcCallRecord(BaseModel):
    """One gRPC call extracted from a structured log line."""

    model_config = ConfigDict(extra="forbid")

    method: str = "POST"
    path: str
    client: str | None = None
    request_body: Any | None = None
    response_body: Any | None = None
    status: int = 0
    timestamp: str | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)


def parse_grpc_log(raw: bytes) -> list[GrpcCallRecord]:
    """Parse a JSONL gRPC call log payload into normalized records.

    Lines that fail to decode are skipped (and logged) so a single bad
    line doesn't sink the whole ingest. Empty payloads return an empty list.
    """
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    out: list[GrpcCallRecord] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            log.warning("grpc.parse.skip", lineno=lineno, reason="invalid json")
            continue
        if not isinstance(data, dict):
            log.warning("grpc.parse.skip", lineno=lineno, reason="not an object")
            continue
        path = data.get("path") or data.get("method_path")
        if not isinstance(path, str):
            log.warning("grpc.parse.skip", lineno=lineno, reason="missing path")
            continue
        record = GrpcCallRecord(
            method=str(data.get("method") or "POST").upper(),
            path=path,
            client=data.get("client") if isinstance(data.get("client"), str) else None,
            request_body=(
                data.get("request") if data.get("request") is not None else data.get("request_body")
            ),
            response_body=(
                data.get("response")
                if data.get("response") is not None
                else data.get("response_body")
            ),
            status=data["status"] if isinstance(data.get("status"), int) else 0,
            timestamp=data.get("timestamp") if isinstance(data.get("timestamp"), str) else None,
        )
        out.append(record)
    log.debug("grpc.parsed", record_count=len(out))
    return out
