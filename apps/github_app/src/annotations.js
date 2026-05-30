// @ts-check
/**
 * Build GitHub Checks API annotations from a Guardian ChangeReport.
 *
 * The Checks API requires every annotation to point at a file path plus
 * a (start_line, end_line) range. The rule engine emits
 * JSON-Pointer-style locations against the spec object tree (e.g.
 * `paths./users.get`) — these are not source coordinates. We therefore
 * anchor each annotation to the spec file (`specPath`) at line 1 and
 * surface the structural location verbatim in the message body and
 * `raw_details` so a reviewer reading "Files Changed" gets the full
 * rule + rationale + structural pointer inline against the spec file.
 *
 * GitHub caps annotations at 50 per `checks.update` call, so we slice
 * defensively. Severities map to annotation levels per the Checks API
 * contract: `breaking → failure`, `behavioral → warning`, `additive →
 * notice`.
 */

/**
 * @typedef {import("./diff.js").ChangeReport} ChangeReport
 */

/** Maximum number of annotations accepted by Checks API per call. */
export const ANNOTATION_LIMIT = 50;

const LEVEL_BY_VERDICT = /** @type {const} */ ({
  breaking: "failure",
  behavioral: "warning",
  additive: "notice",
});

/**
 * Build the annotations array for `checks.update({ output: { annotations } })`.
 *
 * @param {ChangeReport} report
 * @param {string} specPath  Repo-relative path to the spec file.
 * @returns {Array<{
 *   path: string;
 *   start_line: number;
 *   end_line: number;
 *   annotation_level: "failure" | "warning" | "notice";
 *   title: string;
 *   message: string;
 *   raw_details: string;
 * }>}
 */
export function buildAnnotations(report, specPath) {
  if (!specPath || typeof specPath !== "string") {
    throw new TypeError("buildAnnotations: specPath must be a non-empty string");
  }
  const changes = report.changes ?? [];
  // Stable ordering: breaking first, then behavioral, then additive.
  /** @type {Record<string, number>} */
  const ORDER = { breaking: 0, behavioral: 1, additive: 2 };
  const sorted = [...changes].sort((a, b) => {
    const da = ORDER[a.verdict] ?? 99;
    const db = ORDER[b.verdict] ?? 99;
    return da - db;
  });

  const annotations = [];
  for (const c of sorted.slice(0, ANNOTATION_LIMIT)) {
    const level = LEVEL_BY_VERDICT[c.verdict] ?? "notice";
    const clientHint =
      c.affected_clients && c.affected_clients.length > 0
        ? ` (affects ${c.affected_clients.length} client repo${
            c.affected_clients.length === 1 ? "" : "s"
          })`
        : "";
    annotations.push({
      path: specPath,
      // The rule engine resolves spec-structure paths, not source
      // coordinates; line 1 keeps the annotation visible at the top of
      // the spec file's diff hunk while the message carries the real
      // structural pointer.
      start_line: 1,
      end_line: 1,
      annotation_level: level,
      title: `${c.kind} [${c.rule_id}]`,
      message: `${c.location}: ${c.rationale}${clientHint}`,
      raw_details: JSON.stringify(
        {
          change_id: c.change_id,
          kind: c.kind,
          location: c.location,
          verdict: c.verdict,
          rule_id: c.rule_id,
          affected_clients: c.affected_clients ?? [],
        },
        null,
        2,
      ),
    });
  }
  return annotations;
}
