"""GitHub PR creation for deprecation campaign reminder pull-requests.

Uses PyGithub to open a branch + PR on the target client repo.
Idempotency: if an open PR for ``guardian/deprecate-<campaign_id>``
already exists on the repo the function returns its number without
creating a duplicate.

The codemod patch body is derived from the campaign's ``ContractDiff``
guide when one exists, falling back to a generic deprecation notice.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_BODY = """\
## Deprecation reminder

This pull request was opened automatically by the
[Living API Contract Guardian](https://github.com/your-org/living-api-contract-guardian).

The endpoint / field referenced by this campaign has been deprecated.
Please migrate your usage before the sunset date.

### Suggested changes

{patch_suggestion}

---
*Campaign id: `{campaign_id}`*
"""


def _get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN environment variable is not set; cannot open reminder PRs."
        )
    return token


def open_reminder_pr(
    *,
    campaign_id: str,
    client_repo: str,
    patch_suggestion: str = "",
    pr_title: str | None = None,
    github_token: str | None = None,
) -> dict[str, Any]:
    """Open a reminder PR on *client_repo* for *campaign_id*.

    Returns a dict with keys:
    ``pr_number``  – PR number opened (or existing)
    ``created``    – True if a new PR was opened; False if one already existed
    ``branch``     – branch name

    Raises ``RuntimeError`` if GITHUB_TOKEN is absent and *github_token* is
    not provided.
    """
    import github as gh_module

    token = github_token or _get_github_token()
    branch = f"guardian/deprecate-{campaign_id}"
    title = pr_title or f"chore: deprecate endpoint/field (campaign {campaign_id[:8]})"

    g = gh_module.Github(token)
    repo = g.get_repo(client_repo)

    # Idempotency: check for an open PR with the same head branch.
    open_prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
    for pr in open_prs:
        log.info(
            "campaign.reminder_pr.exists",
            campaign_id=campaign_id,
            repo=client_repo,
            pr_number=pr.number,
            branch=branch,
        )
        return {"pr_number": pr.number, "created": False, "branch": branch}

    # Create the branch off the default branch.
    default_branch = repo.default_branch
    base_ref = repo.get_branch(default_branch)
    sha = base_ref.commit.sha

    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=sha)
        log.debug(
            "campaign.reminder_pr.branch_created",
            campaign_id=campaign_id,
            repo=client_repo,
            branch=branch,
        )
    except gh_module.GithubException as exc:
        # 422 = branch already exists (race or leftover from aborted run).
        if exc.status != 422:
            raise
        log.debug(
            "campaign.reminder_pr.branch_exists",
            campaign_id=campaign_id,
            repo=client_repo,
            branch=branch,
        )

    body = _DEFAULT_BODY.format(
        patch_suggestion=patch_suggestion or "_No automated patch available._",
        campaign_id=campaign_id,
    )
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch,
        base=default_branch,
    )
    log.info(
        "campaign.reminder_pr.opened",
        campaign_id=campaign_id,
        repo=client_repo,
        pr_number=pr.number,
        branch=branch,
    )
    return {"pr_number": pr.number, "created": True, "branch": branch}
