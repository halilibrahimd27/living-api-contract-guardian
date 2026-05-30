"""Renderers that turn a :class:`ChangeReport` into CI-friendly text.

Used by the ``guardian diff`` CLI and the GitHub App when posting a
PR comment / check-run summary. Two output families are exported:

* :func:`render_markdown` — a self-contained GitHub-flavoured Markdown
  summary suitable for ``GITHUB_STEP_SUMMARY``, a check-run ``summary``
  field, or the body of a PR comment. It groups changes by verdict,
  surfaces the rule id + rationale, and rolls up a **per-client impact**
  table so reviewers can see at a glance which downstream repos a
  breaking change touches.
* :func:`render_workflow_commands` — one ``::error`` /
  ``::warning`` workflow command per change, emitted to ``stdout`` by
  the CLI in ``--format github`` mode. These render as inline
  annotations on the PR's Files Changed view when the CLI runs inside
  ``actions/checkout``-ed source.
* :func:`render_text` — plain text for terminal use.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from guardian_diff.models import ChangeRecord, ChangeReport

_VERDICT_BADGE: dict[str, str] = {
    "breaking": ":no_entry: breaking",
    "behavioral": ":warning: behavioral",
    "additive": ":white_check_mark: additive",
}

_VERDICT_HEADER: dict[str, str] = {
    "breaking": "Breaking changes",
    "behavioral": "Behavioral changes",
    "additive": "Additive changes",
}


def _client_impact_table(records: Iterable[ChangeRecord]) -> list[str]:
    """Build the per-client impact rollup as Markdown lines."""
    counter: Counter[str] = Counter()
    breaking_clients: set[str] = set()
    for r in records:
        for client in r.affected_clients:
            counter[client] += 1
            if r.verdict == "breaking":
                breaking_clients.add(client)
    if not counter:
        return ["_No downstream client repos are affected by these changes._", ""]
    lines = [
        "| Client repo | Affected changes | Breaking? |",
        "|---|---:|:---:|",
    ]
    for client, total in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        flag = ":no_entry:" if client in breaking_clients else ":white_check_mark:"
        lines.append(f"| `{client}` | {total} | {flag} |")
    lines.append("")
    return lines


def _changes_section(records: list[ChangeRecord], verdict: str) -> list[str]:
    bucket = [r for r in records if r.verdict == verdict]
    if not bucket:
        return []
    lines = [f"### {_VERDICT_HEADER[verdict]} ({len(bucket)})", ""]
    for r in bucket:
        clients = (
            f" — affects {', '.join(f'`{c}`' for c in r.affected_clients)}"
            if r.affected_clients
            else ""
        )
        lines.append(
            f"- **{r.kind}** at `{r.location}` " f"[`{r.rule_id}`] — {r.rationale}{clients}"
        )
    lines.append("")
    return lines


def render_markdown(report: ChangeReport, *, bypass_label: str | None = None) -> str:
    """Render the report as a self-contained Markdown document."""
    s = report.summary
    head = [
        "## :shield: Guardian — API Contract Diff",
        "",
        f"**Contract kind:** `{report.contract_kind}`  ",
        f"**Ruleset:** `{report.ruleset_id}`",
        "",
        "| Verdict | Count |",
        "|---|---:|",
        f"| {_VERDICT_BADGE['breaking']} | {s.breaking} |",
        f"| {_VERDICT_BADGE['behavioral']} | {s.behavioral} |",
        f"| {_VERDICT_BADGE['additive']} | {s.additive} |",
        f"| **Total** | **{s.total}** |",
        "",
    ]
    if s.breaking > 0:
        if bypass_label:
            head.append(
                f"> :unlock: Breaking changes are being **bypassed** because the "
                f"`{bypass_label}` label is present on this PR."
            )
        else:
            head.append(
                "> :no_entry: This PR introduces **breaking** changes. Add the "
                "`guardian:accept-breaking` label to bypass this gate."
            )
        head.append("")

    body: list[str] = []
    for verdict in ("breaking", "behavioral", "additive"):
        body.extend(_changes_section(report.changes, verdict))

    impact = ["### Per-client impact", ""] + _client_impact_table(report.changes)

    return "\n".join(head + body + impact).rstrip() + "\n"


def render_workflow_commands(report: ChangeReport) -> str:
    """Emit ``::error`` / ``::warning`` workflow commands, one per change.

    These show up as PR file/line annotations when the CLI runs inside
    a GitHub Actions job. Locations are surfaced as ``file=`` even
    though they are JSON-pointer-style paths, so they appear in the
    job log rather than against a source file (the rule engine does
    not resolve spec locations to source files).
    """
    lines: list[str] = []
    for r in report.changes:
        if r.verdict == "breaking":
            cmd = "error"
        elif r.verdict == "behavioral":
            cmd = "warning"
        else:
            cmd = "notice"
        title = f"{r.kind} [{r.rule_id}]"
        msg = (
            r.rationale.replace("\n", " ")
            .replace("%", "%25")
            .replace("\r", "")
            .replace(":", "%3A")
            .replace(",", "%2C")
        )
        lines.append(f"::{cmd} title={title}::{r.location}: {msg}")
    return "\n".join(lines)


def render_text(report: ChangeReport) -> str:
    """Render a plain-text summary suitable for a terminal."""
    s = report.summary
    out = [
        f"Guardian diff [{report.contract_kind}] ruleset={report.ruleset_id}",
        f"  breaking={s.breaking} behavioral={s.behavioral} additive={s.additive} total={s.total}",
    ]
    for verdict in ("breaking", "behavioral", "additive"):
        bucket = [r for r in report.changes if r.verdict == verdict]
        if not bucket:
            continue
        out.append("")
        out.append(f"  {_VERDICT_HEADER[verdict]} ({len(bucket)}):")
        for r in bucket:
            extra = f" (affects: {', '.join(r.affected_clients)})" if r.affected_clients else ""
            out.append(f"    - {r.kind} @ {r.location} [{r.rule_id}] — {r.rationale}{extra}")
    return "\n".join(out) + "\n"
