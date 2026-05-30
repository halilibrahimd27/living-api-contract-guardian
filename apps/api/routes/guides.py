"""Migration-guide retrieval API: ``GET /guides/{diff_id}/{client_id}``.

The route is a thin adapter over :class:`guardian_guides.GuideService`.
The LLM provider is resolved via :func:`get_llm_provider`, which tests
override with a :class:`~guardian_guides.MockLLMProvider` through
``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import PlainTextResponse
from guardian_core.logging import get_logger
from guardian_guides import (
    GuideGenerationError,
    GuideRequest,
    GuideService,
    LiteLLMProvider,
    LLMProvider,
)
from sqlalchemy.orm import Session

from apps.api.deps import get_db

router = APIRouter(prefix="/guides", tags=["guides"])
log = get_logger(__name__)


def get_llm_provider() -> LLMProvider:
    """Default LLM provider: routes through litellm.

    Tests replace this dependency with a
    :class:`~guardian_guides.MockLLMProvider` via
    ``app.dependency_overrides[get_llm_provider] = ...``.
    """
    return LiteLLMProvider()


@router.get(
    "/{diff_id}/{client_id:path}",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Markdown migration guide.",
            "content": {"text/markdown": {"schema": {"type": "string"}}},
        },
        404: {"description": "diff_id not found."},
        502: {"description": "LLM returned unparsable code repeatedly."},
    },
)
def get_guide(
    diff_id: str = Path(..., min_length=8, max_length=36, description="contract_diffs.id"),
    client_id: str = Path(
        ...,
        min_length=1,
        max_length=512,
        description="The mined repo identifier (e.g. ``acme/users-client``).",
    ),
    model: str = "gpt-4o-mini",
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
) -> PlainTextResponse:
    """Return the LLM-drafted migration guide as ``text/markdown``.

    Cached by ``hash(diff_id, client_id, prompt_version, model)`` —
    repeat calls do not invoke the LLM. On generation failure (the
    LLM emitted unparsable code blocks past the retry limit), responds
    ``502`` and surfaces the underlying reason in the JSON detail.
    """
    service = GuideService(session=db, llm=llm)
    try:
        result = service.generate(
            GuideRequest(
                diff_id=diff_id,
                client_id=client_id,
                model=model,
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"diff not found: {diff_id!r}",
        ) from exc
    except GuideGenerationError as exc:
        log.error(
            "guides.failed",
            diff_id=diff_id,
            client_id=client_id,
            reason=exc.reason,
            retries=exc.retries,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"guide generation failed: {exc.reason}",
        ) from exc

    log.info(
        "guides.served",
        diff_id=diff_id,
        client_id=client_id,
        model=model,
        cache=result.served_from_cache,
        retries=result.retries,
    )
    return PlainTextResponse(
        content=result.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "X-Guide-Prompt-Version": result.prompt_version,
            "X-Guide-Prompt-Hash": result.prompt_hash,
            "X-Guide-Model": result.model,
            "X-Guide-Retries": str(result.retries),
            "X-Guide-Cache": "hit" if result.served_from_cache else "miss",
        },
    )
