"""Evolution rule engine: classify contract changes as additive / behavioral / breaking.

The package is layered:

* :mod:`guardian_diff.openapi` and :mod:`guardian_diff.proto` produce a stream
  of :class:`~guardian_diff.models.RawChange` objects from two contract
  versions.
* :mod:`guardian_diff.ruleset` loads a YAML ruleset (defaults shipped under
  ``packages/guardian_diff/rules/default.yml``) keyed by raw-change ``kind``.
* :mod:`guardian_diff.engine` joins the two, attaching a stable
  ``change_id``, verdict, rule id and rationale to each change, and
  produces a :class:`~guardian_diff.models.ChangeReport`.
* :mod:`guardian_diff.clients` annotates each change with the set of
  client repos (mined ``InferredEndpoint`` rows) whose call sites land
  on the changed location.
* :mod:`guardian_diff.spectral` shells out to a vendored Spectral CLI if
  one is present under ``vendor/bin/spectral``; absent that, the engine
  still runs end-to-end and Spectral findings are simply omitted.
"""

from __future__ import annotations

from guardian_diff.engine import classify_changes, diff_contracts
from guardian_diff.models import ChangeRecord, ChangeReport, RawChange, Verdict
from guardian_diff.ruleset import Rule, RuleSet, load_default_rules, load_rules_from_yaml

__all__ = [
    "ChangeRecord",
    "ChangeReport",
    "RawChange",
    "Rule",
    "RuleSet",
    "Verdict",
    "classify_changes",
    "diff_contracts",
    "load_default_rules",
    "load_rules_from_yaml",
]
