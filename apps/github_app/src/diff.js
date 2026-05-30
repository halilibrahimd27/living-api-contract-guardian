// @ts-check
/**
 * Thin wrapper around the `guardian` Python CLI.
 *
 * The Probot app shells out to the published CLI rather than re-
 * implementing the diff in Node so there is exactly one rule engine.
 * Specs are written to a per-invocation tmpdir and passed by path.
 */

import { spawn } from "node:child_process";
import { writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const GUARDIAN_BIN = process.env.GUARDIAN_BIN || "guardian";

/**
 * @typedef {{
 *   contract_kind: "openapi" | "proto";
 *   ruleset_id: string;
 *   summary: { breaking: number; behavioral: number; additive: number; total: number };
 *   changes: Array<{
 *     change_id: string;
 *     kind: string;
 *     location: string;
 *     verdict: "additive" | "behavioral" | "breaking";
 *     rule_id: string;
 *     rationale: string;
 *     affected_clients: string[];
 *   }>;
 * }} ChangeReport
 */

/**
 * Run `guardian diff --format json` and return the parsed ChangeReport.
 *
 * @param {string} baseText
 * @param {string} headText
 * @param {{ acceptBreaking?: boolean; kind?: "openapi" | "proto" }} opts
 * @returns {Promise<ChangeReport>}
 */
export async function runGuardianDiff(baseText, headText, opts = {}) {
  const { acceptBreaking = false, kind = "openapi" } = opts;
  const dir = await mkdtemp(join(tmpdir(), "guardian-"));
  const basePath = join(dir, "base");
  const headPath = join(dir, "head");
  try {
    await writeFile(basePath, baseText);
    await writeFile(headPath, headText);
    const args = [
      "diff",
      "--base",
      basePath,
      "--head",
      headPath,
      "--kind",
      kind,
      "--format",
      "json",
    ];
    if (acceptBreaking) args.push("--accept-breaking");

    const { stdout, code } = await runCommand(GUARDIAN_BIN, args);
    // `guardian diff` exits 2 on breaking unless --accept-breaking is set.
    // For wrapper purposes, anything but 0/2 indicates a crash.
    if (code !== 0 && code !== 2) {
      throw new Error(`guardian diff failed (code=${code}): ${stdout}`);
    }
    return /** @type {ChangeReport} */ (JSON.parse(stdout.trim()));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/**
 * @param {string} cmd
 * @param {string[]} args
 * @returns {Promise<{ stdout: string; stderr: string; code: number }>}
 */
function runCommand(cmd, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => (stdout += b.toString("utf-8")));
    child.stderr.on("data", (b) => (stderr += b.toString("utf-8")));
    child.on("error", reject);
    child.on("close", (code) => resolve({ stdout, stderr, code: code ?? 1 }));
  });
}

/**
 * Render a Markdown summary of a ChangeReport (mirrors
 * `guardian_diff.ci_format.render_markdown`). Kept in JS for the App
 * because the Checks API and PR comment body are JS-side.
 *
 * @param {ChangeReport} report
 * @param {{ bypassLabel: string | null }} opts
 * @returns {string}
 */
export function summarizeReport(report, opts) {
  const { summary, changes, contract_kind, ruleset_id } = report;
  const bypassLabel = opts.bypassLabel;
  const lines = [
    "## :shield: Guardian — API Contract Diff",
    "",
    `**Contract kind:** \`${contract_kind}\`  `,
    `**Ruleset:** \`${ruleset_id}\``,
    "",
    "| Verdict | Count |",
    "|---|---:|",
    `| :no_entry: breaking | ${summary.breaking} |`,
    `| :warning: behavioral | ${summary.behavioral} |`,
    `| :white_check_mark: additive | ${summary.additive} |`,
    `| **Total** | **${summary.total}** |`,
    "",
  ];
  if (summary.breaking > 0) {
    if (bypassLabel) {
      lines.push(
        `> :unlock: Breaking changes are being **bypassed** because the \`${bypassLabel}\` label is present on this PR.`,
        "",
      );
    } else {
      lines.push(
        "> :no_entry: This PR introduces **breaking** changes. Add the `guardian:accept-breaking` label to bypass this gate.",
        "",
      );
    }
  }
  for (const verdict of /** @type {const} */ ([
    "breaking",
    "behavioral",
    "additive",
  ])) {
    const bucket = changes.filter((c) => c.verdict === verdict);
    if (bucket.length === 0) continue;
    const heading =
      verdict === "breaking"
        ? "Breaking changes"
        : verdict === "behavioral"
          ? "Behavioral changes"
          : "Additive changes";
    lines.push(`### ${heading} (${bucket.length})`, "");
    for (const c of bucket) {
      const clients =
        c.affected_clients.length > 0
          ? ` — affects ${c.affected_clients.map((s) => `\`${s}\``).join(", ")}`
          : "";
      lines.push(
        `- **${c.kind}** at \`${c.location}\` [\`${c.rule_id}\`] — ${c.rationale}${clients}`,
      );
    }
    lines.push("");
  }
  lines.push("### Per-client impact", "");
  const tally = new Map();
  const breakingClients = new Set();
  for (const c of changes) {
    for (const client of c.affected_clients) {
      tally.set(client, (tally.get(client) ?? 0) + 1);
      if (c.verdict === "breaking") breakingClients.add(client);
    }
  }
  if (tally.size === 0) {
    lines.push(
      "_No downstream client repos are affected by these changes._",
      "",
    );
  } else {
    lines.push("| Client repo | Affected changes | Breaking? |", "|---|---:|:---:|");
    const rows = [...tally.entries()].sort(
      (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
    );
    for (const [client, n] of rows) {
      const flag = breakingClients.has(client) ? ":no_entry:" : ":white_check_mark:";
      lines.push(`| \`${client}\` | ${n} | ${flag} |`);
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd() + "\n";
}
