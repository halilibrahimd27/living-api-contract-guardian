"""Pydantic v2 models describing miner output."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["python", "javascript", "typescript"]
ClientLibrary = Literal[
    "requests",
    "httpx",
    "fetch",
    "axios",
    "grpc",
]


class InferredCallSite(BaseModel):
    """A single discovered HTTP or gRPC call site.

    ``path_template`` is normalized OpenAPI style: dynamic segments
    derived from f-string / template-literal placeholders or path
    parameters become ``{name}``. ``fields`` carries the parameter names
    observed in query strings, JSON / form bodies, and gRPC request
    messages (key names only — values are not captured).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: Annotated[str, Field(min_length=1, max_length=1024)]
    line: Annotated[int, Field(ge=1)]
    language: Language
    client_library: ClientLibrary
    method: Annotated[str, Field(min_length=1, max_length=32)]
    path_template: Annotated[str, Field(min_length=1, max_length=1024)]
    fields: list[str] = Field(default_factory=list)

    def content_hash(self) -> str:
        """Stable sha256 over identifying fields for idempotent upsert.

        ``line`` is intentionally part of the hash so two distinct call
        sites in the same file that share method/path collapse only when
        they are textually identical, not when they only happen to share
        a verb and template.
        """
        material = "|".join(
            [
                self.file,
                str(self.line),
                self.language,
                self.client_library,
                self.method.upper(),
                self.path_template,
                ",".join(sorted(self.fields)),
            ]
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()
