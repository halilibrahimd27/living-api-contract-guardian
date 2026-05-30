// @ts-check
/**
 * Guardian — Probot 13.x entrypoint.
 *
 * Subscribes to PR open / synchronize / reopen / label events on repos
 * where the App is installed, runs `guardian diff` on the changed spec,
 * publishes a Check Run with annotations + a PR comment, and forwards
 * the resulting ChangeReport to the Guardian backend so it can be
 * reproduced from history.
 *
 * Webhook signature verification is handled by Probot's built-in
 * `@octokit/webhooks` integration (it reads `WEBHOOK_SECRET` and
 * rejects unsigned payloads before any handler runs), so we never see
 * a payload that didn't pass HMAC validation.
 *
 * The pull-request handler is exported as a stand-alone async function
 * (`handlePullRequest`) and accepts an `overrides` parameter so the
 * offline test suite can drive it with a stubbed octokit + a fake
 * `runDiff` / `postRun`, replaying webhook payloads from
 * `@octokit/fixtures` style canned JSON without spawning Python or
 * touching the network.
 */

import { runGuardianDiff, summarizeReport } from "./diff.js";
import { postRun } from "./backend.js";
import { buildAnnotations } from "./annotations.js";

export const BYPASS_LABEL = "guardian:accept-breaking";
const DEFAULT_SPEC_PATH = process.env.GUARDIAN_SPEC_PATH || "openapi.json";
const BACKEND_URL = process.env.GUARDIAN_API_URL || "http://localhost:8000";
export const CHECK_NAME = "Guardian / API Contract Diff";
export const ALLOWED_PR_ACTIONS = new Set([
  "opened",
  "reopened",
  "synchronize",
  "labeled",
  "unlabeled",
  "ready_for_review",
]);

/**
 * @typedef {{
 *   runDiff?: typeof runGuardianDiff,
 *   postRun?: typeof postRun,
 *   specPath?: string,
 *   backendUrl?: string,
 *   bypassLabel?: string,
 * }} HandlerOverrides
 */

/**
 * @param {import("probot").Context<"pull_request">} ctx
 * @param {string} specPath
 * @returns {Promise<{ baseText: string; headText: string } | null>}
 */
async function fetchSpecs(ctx, specPath) {
  const { owner, repo } = ctx.repo();
  const pr = ctx.payload.pull_request;
  const base = pr.base.sha;
  const head = pr.head.sha;
  /** @type {(ref: string) => Promise<string>} */
  const fetchAt = async (ref) => {
    const res = await ctx.octokit.repos.getContent({
      owner,
      repo,
      path: specPath,
      ref,
    });
    if (Array.isArray(res.data) || res.data.type !== "file") {
      throw new Error(`spec path ${specPath} is not a file at ${ref}`);
    }
    const b64 = /** @type {{ content: string }} */ (res.data).content;
    return Buffer.from(b64, "base64").toString("utf-8");
  };
  try {
    const baseText = await fetchAt(base);
    const headText = await fetchAt(head);
    return { baseText, headText };
  } catch (err) {
    ctx.log.warn({ err }, "guardian.fetch_spec_failed");
    return null;
  }
}

/**
 * Handle a pull_request webhook event. Exported for the offline test
 * harness; production wiring is done by the `guardianApp` factory.
 *
 * @param {import("probot").Context<"pull_request">} ctx
 * @param {HandlerOverrides} [overrides]
 */
export async function handlePullRequest(ctx, overrides = {}) {
  const action = ctx.payload.action;
  if (!ALLOWED_PR_ACTIONS.has(action)) return;

  const runDiff = overrides.runDiff ?? runGuardianDiff;
  const persistRun = overrides.postRun ?? postRun;
  const specPath = overrides.specPath ?? DEFAULT_SPEC_PATH;
  const backendUrl = overrides.backendUrl ?? BACKEND_URL;
  const bypassLabel = overrides.bypassLabel ?? BYPASS_LABEL;

  const pr = ctx.payload.pull_request;
  const { owner, repo } = ctx.repo();
  const headSha = pr.head.sha;
  const baseSha = pr.base.sha;
  const bypass = (pr.labels || []).some((l) => l.name === bypassLabel);

  const specs = await fetchSpecs(ctx, specPath);
  if (specs === null) {
    ctx.log.info("guardian.skip.no_spec");
    return;
  }

  // Create a check run in progress, then run the diff, then complete it.
  const created = await ctx.octokit.checks.create({
    owner,
    repo,
    name: CHECK_NAME,
    head_sha: headSha,
    status: "in_progress",
  });
  const checkRunId = created.data.id;

  let report;
  try {
    report = await runDiff(specs.baseText, specs.headText, {
      acceptBreaking: bypass,
    });
  } catch (err) {
    ctx.log.error({ err }, "guardian.diff_failed");
    await ctx.octokit.checks.update({
      owner,
      repo,
      check_run_id: checkRunId,
      status: "completed",
      conclusion: "action_required",
      output: {
        title: "Guardian diff crashed",
        summary: `Guardian failed to compute a diff: ${err instanceof Error ? err.message : String(err)}`,
      },
    });
    return;
  }

  const summary = summarizeReport(report, {
    bypassLabel: bypass ? bypassLabel : null,
  });
  const conclusion =
    report.summary.breaking > 0 && !bypass ? "failure" : "success";

  const annotations = buildAnnotations(report, specPath);

  await ctx.octokit.checks.update({
    owner,
    repo,
    check_run_id: checkRunId,
    status: "completed",
    conclusion,
    output: {
      title: `Guardian — ${report.summary.breaking} breaking / ${report.summary.behavioral} behavioral / ${report.summary.additive} additive`,
      summary,
      annotations,
    },
  });

  // PR comment with the same Markdown body so reviewers see it inline.
  await ctx.octokit.issues.createComment({
    owner,
    repo,
    issue_number: pr.number,
    body: summary,
  });

  // Persist the run via the Guardian backend.
  try {
    await persistRun(backendUrl, {
      repo: `${owner}/${repo}`,
      pr_number: pr.number,
      head_sha: headSha,
      base_sha: baseSha,
      conclusion,
      report_json: report,
      bypass_label_present: bypass,
      check_run_id: checkRunId,
    });
  } catch (err) {
    ctx.log.warn({ err }, "guardian.backend_post_failed");
  }
}

/**
 * Probot application factory.
 *
 * @param {import("probot").Probot} app
 */
export default function guardianApp(app) {
  app.on(
    [
      "pull_request.opened",
      "pull_request.reopened",
      "pull_request.synchronize",
      "pull_request.labeled",
      "pull_request.unlabeled",
      "pull_request.ready_for_review",
    ],
    (ctx) => handlePullRequest(ctx),
  );
}
