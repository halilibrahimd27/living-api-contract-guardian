"""LLM provider surface — a single litellm-backed completion call.

Production points the FastAPI dependency at :class:`LiteLLMProvider`,
which calls ``litellm.completion`` with the configured model. Tests
inject :class:`MockLLMProvider` instead, keyed by a content-hash over
the prompt so canned completions are deterministic across runs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol


def prompt_hash(prompt: str) -> str:
    """Stable SHA-256 over the rendered prompt text.

    Used both as the cache key for :class:`MockLLMProvider` and as the
    test-side handle for asserting determinism (two structurally
    identical prompts hash identically).
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class LLMProvider(Protocol):
    """A minimal LLM completion surface.

    Implementations MUST be side-effect free with respect to the
    workspace: production calls litellm; tests return canned strings.
    """

    def complete(self, *, model: str, prompt: str) -> str: ...


class LiteLLMProvider:
    """Production provider: routes through ``litellm.completion``.

    ``litellm`` itself dispatches to whichever backend the model id
    resolves to (OpenAI, Anthropic, Bedrock, etc.) and honors
    ``LITELLM_*`` env vars. Deterministic decoding is requested with
    ``temperature=0`` and ``seed=0`` — the actual seed support varies by
    backend, but the parameters are passed through unconditionally so
    the surface is consistent.
    """

    def __init__(self, *, temperature: float = 0.0, seed: int = 0) -> None:
        self._temperature = temperature
        self._seed = seed

    def complete(self, *, model: str, prompt: str) -> str:
        # Import lazily so tests that never call out to a real backend
        # don't need to pay the import cost / network warnings.
        import litellm

        result: Any = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            seed=self._seed,
        )
        try:
            content: Any = result.choices[0].message.content
        except (AttributeError, IndexError) as exc:
            raise RuntimeError("litellm returned an unexpected response shape") from exc
        if not isinstance(content, str):
            raise RuntimeError("litellm completion content was not a string")
        return content


class MockLLMProvider:
    """Test provider: returns canned completions keyed by ``prompt_hash``.

    Two construction modes:

    * ``responses={hash: "markdown"}`` — exact match on prompt hash.
    * ``default="..."`` — fallback for any unknown prompt.

    A call into ``complete`` with a hash that is neither in
    ``responses`` nor served by ``default`` raises :class:`KeyError`,
    which surfaces as a fast test failure (the canned table is
    incomplete) instead of an opaque LLM call.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        default: str | None = None,
    ) -> None:
        self._responses: dict[str, str] = dict(responses or {})
        self._default = default
        self.calls: list[tuple[str, str, str]] = []  # (model, prompt_hash, returned)

    def add(self, prompt: str, completion: str) -> str:
        """Register a canned completion for ``prompt``.

        Returns the prompt hash so callers can assert the expected key
        was registered (handy for diagnosing test-mocks vs prompt
        drift).
        """
        h = prompt_hash(prompt)
        self._responses[h] = completion
        return h

    def add_by_hash(self, hash_key: str, completion: str) -> None:
        """Register a canned completion directly by prompt hash."""
        self._responses[hash_key] = completion

    def complete(self, *, model: str, prompt: str) -> str:
        h = prompt_hash(prompt)
        if h in self._responses:
            answer = self._responses[h]
        elif self._default is not None:
            answer = self._default
        else:
            raise KeyError(
                f"MockLLMProvider has no canned completion for prompt hash {h!r} "
                "and no default was configured"
            )
        self.calls.append((model, h, answer))
        return answer
