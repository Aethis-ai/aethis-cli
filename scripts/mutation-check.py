#!/usr/bin/env python3
"""Measure whether the contract tests can actually fail.

A safety property is only as good as the test that would notice it breaking.
This harness breaks the contract on purpose, one mutation at a time, and runs
the suite against each: a mutation the suite still passes is a hole in the
oracle, not a passing build.

It exists because the P8 review ran exactly this and found 5 of 13 mutations
survived — including deleting a scrub site and redefining the blocking exit
code to 0 — while the suite reported 427 passed. Three causes, all now fixed:
scrub sites the fixtures never poisoned, exit assertions written against the
constant (a tautology that survives redefining it), and a test whose
assertions sat behind `if contract.is_blocked(...)`, so breaking that
predicate made it vacuous rather than red.

Each mutation is a literal source substitution applied to a temporary copy of
the tree, so nothing here can modify the working checkout.

    uv run python scripts/mutation-check.py
    uv run python scripts/mutation-check.py --list
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, NamedTuple

REPO = Path(__file__).resolve().parent.parent
SUITE_TIMEOUT = 900


class Mutation(NamedTuple):
    mutation_id: str
    path: str
    before: str
    after: str
    kills: str  # what SHOULD notice, in prose
    #: The test(s) that must be among the failures for this to count as a kill.
    #: Without them a "kill" is only "the suite went red", which an unrelated
    #: intermittent failure satisfies just as well as the defect does. Rows
    #: that predate this field are scored the old way and reported as
    #: `killed (unattributed)` so the weaker signal is visible rather than
    #: silently equivalent.
    detects: tuple = ()


MUTATIONS: List[Mutation] = [
    # -- the blocking predicate ------------------------------------------
    Mutation(
        "blocked-always-false",
        "aethis_cli/contract.py",
        "def is_blocked(response: Mapping[str, Any]) -> bool:\n"
        '    """True when the response carries at least one blocking input error."""\n'
        "    return bool(blocking_field_errors(response))",
        "def is_blocked(response: Mapping[str, Any]) -> bool:\n"
        '    """True when the response carries at least one blocking input error."""\n'
        "    return False",
        "nothing blocks; every blocking test must fail",
    ),
    Mutation(
        "field-errors-mapping-only",
        "aethis_cli/contract.py",
        '    if isinstance(raw, list):\n        return {f"[{i}]": str(item) for i, item in enumerate(raw)}\n'
        '    return {"__field_errors__": str(raw)}',
        "    return {}",
        "list/scalar field_errors shapes stop blocking",
    ),
    Mutation(
        "field-errors-list-shape",
        "aethis_cli/contract.py",
        '    if isinstance(raw, list):\n        return {f"[{i}]": str(item) for i, item in enumerate(raw)}',
        "    if isinstance(raw, list):\n        return {}",
        "a list-shaped field_errors stops blocking",
    ),
    Mutation(
        "presented-decision-echoes-server",
        "aethis_cli/contract.py",
        '    decision = response.get("decision")\n    if is_blocked(response):\n        return "undetermined"',
        '    decision = response.get("decision")\n    if False:\n        return "undetermined"',
        "presented_decision stops consulting the error channel",
    ),
    # -- the exit contract -------------------------------------------------
    Mutation(
        "exit-code-zero",
        "aethis_cli/contract.py",
        "EXIT_BLOCKING_INPUT = 3",
        "EXIT_BLOCKING_INPUT = 0",
        "a blocked evaluation starts passing shell gates",
    ),
    # -- the five scrub sites ---------------------------------------------
    Mutation(
        "scrub-top-level-decision",
        "aethis_cli/contract.py",
        '        if guarded.get("decision") in TERMINAL_DECISIONS:\n            guarded["decision"] = "undetermined"',
        "        pass",
        "a terminal verdict survives into JSON",
    ),
    Mutation(
        "scrub-explanation-decision",
        "aethis_cli/contract.py",
        '            if explanation.get("decision") in TERMINAL_DECISIONS:\n'
        '                explanation["decision"] = "undetermined"',
        "            pass",
        "embedded explanation.decision survives",
    ),
    Mutation(
        "scrub-explanation-decision-path",
        "aethis_cli/contract.py",
        '            explanation.pop("decision_path", None)',
        "            pass",
        "a satisfying path survives under a blocked result",
    ),
    Mutation(
        "scrub-trace-status",
        "aethis_cli/contract.py",
        '            if trace.get("status") in TERMINAL_DECISIONS:\n                trace["status"] = "undetermined"',
        "            pass",
        "embedded trace.status survives",
    ),
    Mutation(
        "scrub-trace-path",
        "aethis_cli/contract.py",
        '            trace.pop("path", None)',
        "            pass",
        "trace.path survives under a blocked result",
    ),
    # -- the guard's reporting --------------------------------------------
    Mutation(
        "no-contract-note",
        "aethis_cli/contract.py",
        "    guarded[CONTRACT_NOTE_KEY] = note",
        "    pass",
        "the enforcement record disappears from JSON",
    ),
    Mutation(
        "violations-never-reported",
        "aethis_cli/contract.py",
        '    if violations:\n        note["violations"] = violations',
        "    pass",
        "overrides stop being reported",
    ),
    # -- identity honesty --------------------------------------------------
    Mutation(
        "unknown-version-accepted",
        "aethis_cli/contract.py",
        '        if version is None or version == UNRESOLVED_VERSION:\n            unresolved.append("ruleset_version")\n'
        '        if digest is None:\n            unresolved.append("content_digest")\n'
        "    elif not rulebook_id and _looks_like_a_decision(response):",
        '        if False:\n            unresolved.append("ruleset_version")\n'
        '        if digest is None:\n            unresolved.append("content_digest")\n'
        "    elif not rulebook_id and _looks_like_a_decision(response):",
        "an unreproducible 'unknown' version prints as identity",
    ),
    # -- the exit is actually taken ---------------------------------------
    Mutation(
        "decide-never-exits-three",
        "aethis_cli/commands/decide_cmd.py",
        "    if blocked:\n        raise typer.Exit(code=contract.EXIT_BLOCKING_INPUT)\n",
        "    if False:\n        raise typer.Exit(code=contract.EXIT_BLOCKING_INPUT)\n",
        "`aethis decide` stops exiting non-zero when blocked",
    ),
    Mutation(
        "rulebook-decide-unguarded",
        "aethis_cli/commands/rulebooks_cmd.py",
        "    blocked = contract.is_blocked(result)\n    result = contract.guard_response(result)",
        "    blocked = False\n    result = result",
        "`aethis rulebooks decide` reverts to the unguarded surface",
    ),
    # -- the field projection ---------------------------------------------
    Mutation(
        "projection-drops-contract-note",
        "aethis_cli/render.py",
        "    keep = list(fields) + [f for f in PINNED_JSON_FIELDS if f in record and f not in fields]",
        "    keep = list(fields)",
        "--json <fields> loses the enforcement record",
        detects=(
            "tests/test_json_projection_pinning.py::test_the_enforcement_record_survives_a_projection_that_omits_it",
            # The `--json <fields>` route itself, not just the helper it calls:
            # the rule and its wiring to the flag are separate claims.
            "tests/test_json_projection_pinning.py::test_the_json_flag_projection_keeps_the_enforcement_record",
        ),
    ),
    # -- the test-case upload is idempotent, or says it is not -------------
    Mutation(
        "tests-upload-appends-again",
        "aethis_cli/commands/generate_cmd.py",
        "        result = client.add_tests(pid, normalised, replace=True) or {}",
        "        result = client.add_tests(pid, normalised) or {}",
        "every generate run duplicates the whole test suite again",
    ),
    Mutation(
        "capability-gate-bypassed",
        "aethis_cli/commands/generate_cmd.py",
        "    supported = client.supports_test_replace()",
        "    supported = True",
        "the flag is sent to an engine that would silently ignore it",
    ),
    Mutation(
        "append-warning-silenced",
        "aethis_cli/commands/generate_cmd.py",
        '    warn(\n        f"Test cases were APPENDED, not replaced',
        '    _ = (\n        f"Test cases were APPENDED, not replaced',
        "an appending upload goes back to being silent",
    ),
    Mutation(
        "replaced-count-hidden",
        "aethis_cli/commands/generate_cmd.py",
        'info(f"Uploaded {added} test case(s) from {tests_path.name} — {replaced} replaced")',
        'info(f"Uploaded {added} test case(s) from {tests_path.name}")',
        "a destructive overwrite stops being visible",
    ),
    Mutation(
        "unknown-schema-reads-as-supported",
        "aethis_cli/client.py",
        "            answer = None\n        self._test_replace_support = answer",
        "            answer = True\n        self._test_replace_support = answer",
        "an unreadable schema is treated as a capability the engine may not have",
    ),
    Mutation(
        "capability-probe-matches-substring",
        "aethis_cli/client.py",
        'answer = "replace" in properties',
        'answer = "replace" in str(properties)',
        "a member merely containing the word reads as the member",
    ),
    Mutation(
        "add-tests-always-replaces",
        "aethis_cli/client.py",
        '        if replace:\n            body["replace"] = True',
        '        if True:\n            body["replace"] = True',
        "the client sends a destructive flag nobody asked for",
    ),
    # -- authored display metadata on the field pin ------------------------
    Mutation(
        "metadata-dropped-from-payload",
        "aethis_cli/commands/generate_cmd.py",
        "        for prop in _ENGINE_GATED_FIELD_KEYS:\n"
        "            if prop in field:\n"
        "                value = field[prop]\n"
        "                spec[prop] = dict(value) if isinstance(value, dict) else value\n",
        "",
        "authored wording and the storage-key pairing never reach the engine",
        detects=("tests/test_field_display_metadata_transport.py::test_upload_transmits_labels_and_canonical_field",),
    ),
    Mutation(
        "metadata-presence-collapsed-to-truthiness",
        "aethis_cli/commands/generate_cmd.py",
        "            if prop in field:",
        "            if field.get(prop):",
        "a declared empty map is dropped, and skips the capability probe with it",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_an_empty_enum_labels_map_is_transmitted_as_declared",
        ),
    ),
    Mutation(
        "metadata-emitted-unconditionally",
        "aethis_cli/commands/generate_cmd.py",
        "            if prop in field:\n                value = field[prop]",
        "            if True:\n                value = field.get(prop)",
        "a project declaring nothing stops producing the payload it used to",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_payload_is_unchanged_for_a_project_that_declares_neither",
        ),
    ),
    Mutation(
        "validation-presence-collapsed-to-truthiness",
        "aethis_cli/commands/generate_cmd.py",
        '    if "enum_labels" in f:\n        labels = f["enum_labels"]',
        '    if f.get("enum_labels"):\n        labels = f["enum_labels"]',
        "a declared empty map stops being validated at all",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_an_empty_map_on_a_non_enum_field_is_still_rejected",
        ),
    ),
    Mutation(
        "write-back-drops-a-declared-empty-map",
        "aethis_cli/commands/generate_cmd.py",
        "        if k in _ENGINE_GATED_FIELD_KEYS:\n            if k in field:\n                out[k] = field[k]\n            continue\n",
        "",
        "a pull un-authors a declared empty map by calling it empty",
        detects=("tests/test_field_display_metadata_transport.py::test_write_back_preserves_an_explicit_empty_map",),
    ),
    Mutation(
        "empty-canonical-field-message-hides-the-stricture",
        "aethis_cli/commands/generate_cmd.py",
        'f"Field {key!r} declares an empty canonical_field. The engine would accept it — the value is "',
        'f"Field {key!r} declares an empty canonical_field. "',
        "the refusal stops disclosing that the CLI is stricter than the engine",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_the_empty_canonical_field_message_discloses_the_stricture",
        ),
    ),
    Mutation(
        "non-text-label-keys-unreported",
        "aethis_cli/commands/generate_cmd.py",
        "            non_text = sorted((repr(m) for m in labels if not isinstance(m, str)), key=str)",
        "            non_text = []",
        "a non-text member key goes unreported (and used to crash the formatter)",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_non_text_label_keys_are_a_validation_message_not_a_crash",
        ),
    ),
    Mutation(
        "metadata-capability-guard-skipped",
        "aethis_cli/commands/generate_cmd.py",
        "    check_display_metadata_support(client, expected_fields)\n",
        "",
        "an engine that discards the metadata is written to anyway",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_upload_aborts_when_the_engine_does_not_model_the_properties",
        ),
    ),
    Mutation(
        "unreadable-field-spec-schema-reads-as-unsupported",
        "aethis_cli/commands/generate_cmd.py",
        "    if advertised is None:",
        "    if advertised is None:\n        advertised = set()\n    if False:",
        "an unreachable schema blocks an upload that would have worked",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_upload_proceeds_but_says_so_when_the_engine_schema_is_unreadable",
        ),
    ),
    Mutation(
        "metadata-guard-probes-every-project",
        "aethis_cli/commands/generate_cmd.py",
        "    if not declared:\n        return\n",
        "",
        "a project declaring nothing is gated on a capability it does not use",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_an_old_engine_is_not_probed_for_a_project_that_declares_nothing",
        ),
    ),
    Mutation(
        "metadata-guard-names-every-gated-key",
        "aethis_cli/commands/generate_cmd.py",
        "    missing = [k for k in declared if k not in advertised]",
        "    missing = list(declared)",
        "the refusal stops naming which property is actually absent",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_the_probe_names_only_the_property_the_engine_is_missing",
        ),
    ),
    Mutation(
        "enum-labels-member-check-dropped",
        "aethis_cli/commands/generate_cmd.py",
        "                unknown = sorted(m for m in labels if isinstance(m, str) and m not in members)",
        "                unknown = []",
        "a label for a member the field does not have ships and is never rendered",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_validate_rejects_a_label_for_an_undeclared_member",
        ),
    ),
    Mutation(
        "enum-labels-non-enum-check-dropped",
        "aethis_cli/commands/generate_cmd.py",
        '            if ftype != "enum":',
        "            if False:",
        "member wording is accepted on a field that has no members",
        detects=("tests/test_field_display_metadata_transport.py::test_validate_rejects_labels_on_a_non_enum_field",),
    ),
    Mutation(
        "canonical-field-emptiness-check-dropped",
        "aethis_cli/commands/generate_cmd.py",
        "        elif isinstance(canonical, str) and not canonical.strip():",
        "        elif False:",
        "an empty storage-key pairing ships as though it were authored",
        detects=("tests/test_field_display_metadata_transport.py::test_validate_rejects_an_empty_canonical_field",),
    ),
    Mutation(
        "metadata-missing-from-canonical-key-order",
        "aethis_cli/commands/generate_cmd.py",
        '    "enum_labels",\n    "canonical_field",\n    "hints",',
        '    "hints",',
        "a pull rewrites the metadata out of its modelled place in fields.yaml",
        detects=("tests/test_field_display_metadata_transport.py::test_fields_yaml_write_back_preserves_the_metadata",),
    ),
    Mutation(
        "field-spec-properties-probe-answers-a-fixed-set",
        "aethis_cli/client.py",
        '                answer = set(schemas[model]["properties"])',
        '                answer = {"key", "sort"}',
        "the probe stops reporting what the engine actually advertises",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_probe_reports_the_properties_the_engine_advertises",
        ),
    ),
    # -- the same guard on the second upload path (rulebooks set-fields) ----
    Mutation(
        "set-fields-guard-removed",
        "aethis_cli/commands/rulebooks_cmd.py",
        "    check_display_metadata_support(client, fields, rulebook=True)\n",
        "",
        "the rulebook path goes back to letting an engine discard the metadata",
        detects=("tests/test_field_display_metadata_transport.py::test_set_fields_calls_the_guard_before_it_posts",),
    ),
    Mutation(
        "set-fields-guard-asks-the-wrong-model",
        "aethis_cli/commands/rulebooks_cmd.py",
        "    check_display_metadata_support(client, fields, rulebook=True)",
        "    check_display_metadata_support(client, fields, rulebook=False)",
        "the rulebook push is cleared by a model the engine does not post to",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_set_fields_refuses_for_real_against_an_engine_missing_the_rulebook_model",
        ),
    ),
    Mutation(
        "guard-model-selection-collapsed",
        "aethis_cli/commands/generate_cmd.py",
        "    advertised = client.rulebook_field_spec_properties() if rulebook else client.expected_field_spec_properties()",
        "    advertised = client.expected_field_spec_properties()",
        "both paths ask about the project pin, whatever they actually post",
        detects=(
            "tests/test_field_display_metadata_transport.py::test_set_fields_asks_about_the_rulebook_model_not_the_project_one",
        ),
    ),
]


def _apply(tree: Path, mutation: Mutation) -> bool:
    target = tree / mutation.path
    text = target.read_text()
    if mutation.before not in text:
        return False
    target.write_text(text.replace(mutation.before, mutation.after, 1))
    return True


# The mutated tree is a copy with `.git` deliberately excluded, so a test that
# resolves the source commit from repository metadata cannot pass in it. That
# is a property of this harness's sandbox, not of the code under test — the
# ordinary `test` job still asserts it against the real checkout — so it is
# excluded here rather than allowed to make every run red. A rename makes the
# baseline fail loudly, which is the right way for this to break.
_SANDBOX_DESELECT = ["tests/test_release_tooling.py::test_integrity_record_binds_artefact_to_source"]


def _validate_deselect(tree: Path) -> list:
    """Confirm each sandbox deselect names exactly one real test.

    `--deselect` matches by node-id PREFIX and silently ignores a selector
    that matches nothing. Both halves are traps: a suffix rename leaves the
    selector unmatched and the harness quietly stops excluding anything (so
    the baseline goes red for a reason nobody attributes), while a future test
    whose id extends this one would be excluded without anybody choosing that.

    So the selector is checked against the collected ids by EXACT equality,
    and the prefix set is required to be exactly that one test. Returns a list
    of complaints; empty means the exclusion is sound.
    """
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(tree),
            "pytest",
            "tests/",
            "--collect-only",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=SUITE_TIMEOUT,
        check=False,
    )
    collected = {ln.strip() for ln in completed.stdout.splitlines() if "::" in ln and not ln.startswith("FAILED")}
    problems = []
    for selector in _SANDBOX_DESELECT:
        exact = [n for n in collected if n == selector]
        prefixed = [n for n in collected if n.startswith(selector)]
        if not exact:
            problems.append(
                f"{selector} matches no collected test — it was renamed or removed. "
                f"Update _SANDBOX_DESELECT; pytest ignores an unmatched selector silently."
            )
        if len(prefixed) > 1:
            problems.append(
                f"{selector} prefix-matches {len(prefixed)} tests ({', '.join(sorted(prefixed))}) — "
                f"--deselect would exclude all of them, which nobody chose."
            )
    return problems


def _failed_tests(output: str) -> set:
    """Test ids from pytest's short summary (`FAILED path::test - reason`)."""
    return {m.group(1) for m in re.finditer(r"^FAILED (\S+?)(?: - |\s*$)", output, re.MULTILINE)}


def _run_suite(tree: Path) -> subprocess.CompletedProcess:
    deselect = [arg for test in _SANDBOX_DESELECT for arg in ("--deselect", test)]
    # Deliberately NOT `-x`: attributing a kill to a named test requires that
    # test to have run, and stopping at the first failure routinely means it
    # did not.
    return subprocess.run(
        ["uv", "run", "--project", str(tree), "pytest", "tests/", "-q", "-rf", "--no-cov", "-p", "no:cacheprovider"]
        + deselect,
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=SUITE_TIMEOUT,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list the mutation set and exit")
    parser.add_argument("--only", help="run a single mutation by id")
    parser.add_argument("--output", help="write the result record here")
    args = parser.parse_args()

    if args.list:
        for mutation in MUTATIONS:
            print(f"{mutation.mutation_id:34} {mutation.path:38} {mutation.kills}")
        return 0

    selected = [m for m in MUTATIONS if not args.only or m.mutation_id == args.only]
    if not selected:
        sys.exit(f"no mutation named {args.only!r}")

    # A kill here is "the suite failed" — which says nothing unless the suite
    # passes UNMUTATED first. Against an already-red tree every mutation is
    # scored as killed, including the ones nothing tests, and the run reports a
    # clean sweep. That is the same vacuous-green shape the mutations exist to
    # find, so the baseline is a precondition rather than a nicety.
    with tempfile.TemporaryDirectory(prefix="aethis-mutation-baseline-") as tmp:
        baseline_tree = Path(tmp) / "tree"
        shutil.copytree(
            REPO,
            baseline_tree,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache"),
        )
        deselect_problems = _validate_deselect(baseline_tree)
        baseline = _run_suite(baseline_tree)
    if deselect_problems:
        print("SANDBOX DESELECT INVALID — the exclusion is not doing what it claims:")
        for problem in deselect_problems:
            print(f"  {problem}")
        return 1
    if baseline.returncode != 0:
        print("BASELINE RED — the suite fails before any mutation is applied.")
        print("Every mutation would score as 'killed' for that reason, so no result here would mean anything.")
        print(baseline.stdout[-3000:] or baseline.stderr[-3000:])
        return 1
    print("baseline green — the suite passes unmutated\n")

    results = []
    for mutation in selected:
        with tempfile.TemporaryDirectory(prefix="aethis-mutation-") as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(
                REPO,
                tree,
                ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache"),
            )
            if not _apply(tree, mutation):
                results.append({"id": mutation.mutation_id, "status": "STALE", "detail": "source text not found"})
                print(f"STALE   {mutation.mutation_id} — mutation text no longer matches the source")
                continue
            completed = _run_suite(tree)
            went_red = completed.returncode != 0
            failures = _failed_tests(completed.stdout)
            if mutation.detects:
                # A kill must be the NAMED test failing. The suite going red
                # for some other reason is not evidence that anything detected
                # this mutation.
                missing = [t for t in mutation.detects if not any(f.endswith(t) or f == t for f in failures)]
                killed = went_red and not missing
                detail = "" if killed else f" (expected {', '.join(missing)} to fail)"
                label = "killed " if killed else "SURVIVED"
            else:
                killed = went_red
                detail = ""
                label = "killed*" if killed else "SURVIVED"
            results.append(
                {
                    "id": mutation.mutation_id,
                    "status": "killed" if killed else "SURVIVED",
                    "kills": mutation.kills,
                    "attributed": bool(mutation.detects),
                    "failures": sorted(failures)[:10],
                }
            )
            print(f"{label} {mutation.mutation_id} — {mutation.kills}{detail}")

    killed = sum(1 for r in results if r["status"] == "killed")
    attributed = sum(1 for r in results if r["status"] == "killed" and r.get("attributed"))
    unattributed = killed - attributed
    survived = [r for r in results if r["status"] == "SURVIVED"]
    stale = [r for r in results if r["status"] == "STALE"]
    record = {
        "total": len(results),
        "killed": killed,
        "killed_attributed": attributed,
        "killed_unattributed": unattributed,
        "survived": [r["id"] for r in survived],
        "stale": [r["id"] for r in stale],
        "results": results,
    }
    print(f"\nkilled {killed}/{len(results)} — {attributed} attributed, {unattributed} killed* (unattributed)")
    if unattributed:
        print(
            "  killed* = the suite went red, but no test was named as the one that must notice, "
            "so an unrelated failure would score the same. Weaker evidence than an attributed kill."
        )
    if args.output:
        Path(args.output).write_text(json.dumps(record, indent=2) + "\n")
    if survived or stale:
        for r in survived:
            print(f"oracle hole: {r['id']} survived — {r['kills']}", file=sys.stderr)
        for r in stale:
            print(f"mutation {r['id']} no longer applies; update it", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
