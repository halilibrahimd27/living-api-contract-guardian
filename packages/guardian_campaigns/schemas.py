"""Pydantic v2 schemas for the campaign orchestrator HTTP boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CampaignState = Literal["draft", "active", "decaying", "ready_to_remove", "completed", "aborted"]


class CampaignCreate(BaseModel):
    """Payload for ``POST /campaigns``."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: str | None = None
    endpoint_id: str | None = None
    field_path: str | None = None
    usage_threshold_pct: Annotated[float, Field(ge=0.0, le=100.0)] = 5.0
    decay_window_days: Annotated[int, Field(ge=1, le=365)] = 30
    github_repo: str | None = Field(
        default=None,
        description="owner/repo slug of the client repo to open reminder PRs against.",
    )


class MetricPoint(BaseModel):
    """One sampled point on the decay curve."""

    model_config = ConfigDict(from_attributes=True)

    sampled_at: datetime
    usage_count: int
    ewma_value: float
    remaining_client_count: int


class ReminderPRRead(BaseModel):
    """Summary of a reminder PR opened for a campaign."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    client_repo: str
    pr_number: int | None
    branch_name: str
    pr_state: str


class CampaignRead(BaseModel):
    """Full representation returned by ``GET /campaigns/{id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    endpoint_id: str | None
    field_path: str | None
    state: CampaignState
    usage_threshold_pct: float
    decay_window_days: int
    peak_usage: int
    github_repo: str | None
    created_at: datetime
    updated_at: datetime
    decay_curve: list[MetricPoint] = Field(default_factory=list)
    remaining_clients: list[str] = Field(
        default_factory=list,
        description="Repos of clients whose usage is still above zero.",
    )
    reminder_prs: list[ReminderPRRead] = Field(default_factory=list)


class CampaignUpdate(BaseModel):
    """Payload for ``PATCH /campaigns/{id}``."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    usage_threshold_pct: Annotated[float, Field(ge=0.0, le=100.0)] | None = None
    decay_window_days: Annotated[int, Field(ge=1, le=365)] | None = None
    github_repo: str | None = None


class CampaignTransitionRequest(BaseModel):
    """Payload for ``POST /campaigns/{id}/transition``."""

    model_config = ConfigDict(extra="forbid")

    trigger: Literal["activate", "start_decay", "mark_ready", "complete", "abort"]
    # Peak usage to record when activating.
    peak_usage: int | None = Field(default=None, ge=0)


class EvaluateResult(BaseModel):
    """Result returned by the in-process campaign evaluation helper."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    previous_state: CampaignState
    new_state: CampaignState
    transition_fired: str | None
    metric: MetricPoint | None
    extra: dict[str, Any] = Field(default_factory=dict)
