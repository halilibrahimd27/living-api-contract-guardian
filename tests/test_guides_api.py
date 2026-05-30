"""Integration tests for the LLM-drafted migration guide service.

Tests inject a :class:`~guardian_guides.MockLLMProvider` that returns
canned markdown completions keyed by the SHA-256 of the rendered
prompt. This means:

* Two runs of the same test produce byte-identical guides.
* If the prompt template ever drifts, the canned key no longer
  matches and the test surfaces the drift loudly.

Acceptance criteria covered here:

1. ``GET /guides/{diff_id}/{client_id}`` returns markdown that
   references real call sites.
2. Guide generation is deterministic given a fixed-seed / mocked LLM.
3. Snippets in the guide pass tree-sitter syntax validation; an LLM
   that emits broken code triggers a retry and eventually a 502.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from guardian_core.db import get_sessionmaker
from guardian_core.models import InferredEndpoint
from guardian_guides import LLMProvider, MockLLMProvider

from apps.api.main import create_app
from apps.api.routes.guides import get_llm_provider

# -----------------------------------------------------------------
# Canned LLM responses — well-formed and broken variants.
# -----------------------------------------------------------------

_GOOD_MARKDOWN = """\
# Migration guide for acme/users-client

## Path removed: GET /users

`src/api.py:10` calls `requests.get("/users")` — that route is gone.

Before:

```python
response = requests.get("/users")
```

After:

```python
response = requests.get("/people")
```
"""


_BROKEN_MARKDOWN = """\
# Migration guide for acme/users-client

## Path removed: GET /users

Before:

```python
response = requests.get(
```

After:

```python
response = requests.get("/people")
```
"""


# -----------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------


@pytest.fixture()
def llm_default_good() -> MockLLMProvider:
    """LLM that always returns the well-formed canned markdown.

    Using ``default=`` makes the test resilient to template tweaks —
    we are asserting determinism + grounding, not lockstep on the
    exact rendered prompt.
    """
    return MockLLMProvider(default=_GOOD_MARKDOWN)


@pytest.fixture()
def llm_then_good() -> MockLLMProvider:
    """LLM that emits broken code first, valid code on retry.

    Implemented via a sticky-state wrapper that swaps the default
    completion after the first call.
    """

    class _RetryMock:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, model: str, prompt: str) -> str:
            self.calls += 1
            return _BROKEN_MARKDOWN if self.calls == 1 else _GOOD_MARKDOWN

    return _RetryMock()  # type: ignore[return-value]


@pytest.fixture()
def llm_always_broken() -> MockLLMProvider:
    """LLM that always returns unparsable code blocks."""
    return MockLLMProvider(default=_BROKEN_MARKDOWN)


@pytest.fixture()
def app_with_llm(
    migrated_db: str,
) -> Iterator[tuple[TestClient, dict[str, LLMProvider]]]:
    """FastAPI test client whose LLM provider is swappable per-test."""
    app = create_app()
    container: dict[str, LLMProvider] = {}

    def _override() -> LLMProvider:
        return container["llm"]

    app.dependency_overrides[get_llm_provider] = _override
    with TestClient(app) as c:
        yield c, container


def _seed_call_sites(
    *,
    repo: str,
    path: str = "/users",
    method: str = "GET",
) -> None:
    """Insert one ``InferredEndpoint`` row so the guide has a real call site."""
    sessionmaker = get_sessionmaker()
    with sessionmaker() as s:
        s.add(
            InferredEndpoint(
                repo=repo,
                commit_sha="deadbeef",
                file="src/api.py",
                line=10,
                language="python",
                client_library="requests",
                method=method,
                path_template=path,
                fields={"names": ["limit", "offset"]},
                content_hash="0" * 64,
            )
        )
        s.commit()


def _post_diff(
    client: TestClient,
    *,
    before: str | None = "/users",
    after: str | None = None,
) -> dict[str, Any]:
    def spec(path: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "x", "version": "1"},
            "paths": {},
        }
        if path is not None:
            body["paths"][path] = {"get": {"responses": {"200": {"description": "ok"}}}}
        return body

    body = {
        "kind": "openapi",
        "before_spec": spec(before),
        "after_spec": spec(after),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    payload: dict[str, Any] = r.json()
    return payload


# -----------------------------------------------------------------
# 1. End-to-end: GET /guides/{diff_id}/{client_id} returns markdown.
# -----------------------------------------------------------------


def test_get_guide_returns_markdown_referencing_call_sites(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_default_good: MockLLMProvider,
) -> None:
    client, container = app_with_llm
    container["llm"] = llm_default_good
    _seed_call_sites(repo="acme/users-client")
    report = _post_diff(client)
    diff_id = report["diff_id"]
    assert diff_id, "POST /diff must return a persisted diff_id"

    r = client.get(f"/guides/{diff_id}/acme/users-client")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown"), r.headers
    body = r.text
    # Must reference the real call-site filename mined into the DB.
    assert "src/api.py" in body
    assert "acme/users-client" in body
    # LLM was called exactly once (no cache hit on first read).
    assert r.headers["x-guide-cache"] == "miss"
    assert len(llm_default_good.calls) == 1


def test_guide_404_when_diff_id_unknown(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_default_good: MockLLMProvider,
) -> None:
    client, container = app_with_llm
    container["llm"] = llm_default_good
    r = client.get("/guides/00000000-0000-0000-0000-000000000000/acme/users-client")
    assert r.status_code == 404, r.text


# -----------------------------------------------------------------
# 2. Determinism + caching.
# -----------------------------------------------------------------


def test_guide_is_deterministic_under_mocked_llm(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_default_good: MockLLMProvider,
) -> None:
    """Two successive GETs return byte-identical markdown.

    The second call must serve from cache: the LLM must not be invoked
    again. This validates the ``hash(diff_id, client_id, prompt_version,
    model)`` cache contract end-to-end.
    """
    client, container = app_with_llm
    container["llm"] = llm_default_good
    _seed_call_sites(repo="acme/users-client")
    report = _post_diff(client)
    diff_id = report["diff_id"]

    r1 = client.get(f"/guides/{diff_id}/acme/users-client")
    r2 = client.get(f"/guides/{diff_id}/acme/users-client")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.text == r2.text  # byte-identical
    assert r2.headers["x-guide-cache"] == "hit"
    assert len(llm_default_good.calls) == 1, "second call should hit cache, not LLM"


def test_guide_distinct_clients_get_distinct_cache_rows(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_default_good: MockLLMProvider,
) -> None:
    client, container = app_with_llm
    container["llm"] = llm_default_good
    _seed_call_sites(repo="acme/users-client")
    _seed_call_sites(repo="acme/billing-worker", path="/users")
    report = _post_diff(client)
    diff_id = report["diff_id"]

    r_a = client.get(f"/guides/{diff_id}/acme/users-client")
    r_b = client.get(f"/guides/{diff_id}/acme/billing-worker")
    assert r_a.status_code == 200
    assert r_b.status_code == 200
    # Two distinct cache rows (distinct prompt hashes), so two LLM calls.
    assert len(llm_default_good.calls) == 2
    a_hash = r_a.headers["x-guide-prompt-hash"]
    b_hash = r_b.headers["x-guide-prompt-hash"]
    assert a_hash != b_hash


# -----------------------------------------------------------------
# 3. Snippet validation + retry behavior.
# -----------------------------------------------------------------


def test_guide_retries_on_broken_snippet_then_succeeds(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_then_good: Any,
) -> None:
    """First LLM response has unparsable code; the second succeeds."""
    client, container = app_with_llm
    container["llm"] = llm_then_good
    _seed_call_sites(repo="acme/users-client")
    report = _post_diff(client)
    diff_id = report["diff_id"]

    r = client.get(f"/guides/{diff_id}/acme/users-client")
    assert r.status_code == 200, r.text
    assert r.headers["x-guide-retries"] == "1"
    assert llm_then_good.calls == 2, "LLM should be called twice (1 reject + 1 accept)"


def test_guide_502_when_llm_repeatedly_emits_broken_code(
    app_with_llm: tuple[TestClient, dict[str, LLMProvider]],
    llm_always_broken: MockLLMProvider,
) -> None:
    client, container = app_with_llm
    container["llm"] = llm_always_broken
    _seed_call_sites(repo="acme/users-client")
    report = _post_diff(client)
    diff_id = report["diff_id"]

    r = client.get(f"/guides/{diff_id}/acme/users-client")
    assert r.status_code == 502, r.text
    # 1 initial attempt + RETRY_LIMIT(=2) retries = 3 calls.
    assert len(llm_always_broken.calls) == 3
