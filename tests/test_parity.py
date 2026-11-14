"""The site on wts-core gives what its own code gave: each golden output (tests/golden/, recorded by
tests/golden/record.py on the code as it was) compared with what the site gives now, on the same inputs.

A difference is either a fault to mend or one of the differences wts-core's docs/AS-BUILT.md and docs/MIGRATION.md
list for this site, and then it is asserted here, by name, beside the comparison it belongs to. Nothing is skipped.
"""

from __future__ import annotations

import shared

from wcs.config import REPO_ROOT, load_settings


def by_name(rows):
    return {row["name"]: row for row in rows}


# ---- settings ------------------------------------------------------------------------------------------------------


def test_settings_resolve_as_they_did(tmp_path, monkeypatch):
    """The real settings file and samples, with a local file, overrides and each environment variable: the whole
    of what was read, the data folder, and every accessor of the site's own."""
    monkeypatch.setattr("os.environ", dict(__import__("os").environ))  # the cases set and clear WCS_ variables
    before = by_name(shared.read("settings.json"))
    now = by_name(shared.settings_cases(tmp_path / "settings", REPO_ROOT,
                                        lambda path, **overrides: load_settings(path, **overrides)))
    assert now.keys() == before.keys() and len(before) == len(shared.SETTINGS_CASES)
    for name in before:
        assert now[name] == before[name], name


# ---- the checks on briefings -----------------------------------------------------------------------------------


def test_the_checks_report_what_they_reported():
    """Each test briefing through every check, as the pipeline runs them. One difference, and it is listed
    (wts-core docs/MIGRATION.md 1.4, docs/AS-BUILT.md "checks"): an answer now carries the version of the checking
    rules that wrote it, as a new key, ``rules``. Everything else is what it was: never a flag fewer."""
    from wts_core.checks import RULES_VERSION

    from wcs.rag.checks import checked
    from wcs.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    before = by_name(shared.read("checks.json")["written"])
    assert before.keys() == {case["name"] for case in shared.CHECK_CASES}
    for case in shared.CHECK_CASES:
        answer = dict(checked(case["blocks"], sources))
        assert answer.pop("rules") == RULES_VERSION, case["name"]
        assert answer == before[case["name"]]["answer"], case["name"]


def test_saved_briefings_are_shown_again_as_they_were():
    """Saved and cached briefings from before the move carry no stamp of the rules, so each is judged again when it
    is shown, with curly quotes: the same statuses, counts and text as the site's own recheck gave."""
    import json

    from wcs.rag.checks import rechecked

    before = by_name(shared.read("checks.json")["shown_again"])
    assert before.keys() == {case["name"] for case in shared.SAVED_BRIEFINGS}
    for case in shared.SAVED_BRIEFINGS:
        assert rechecked(json.loads(json.dumps(case["events"]))) == before[case["name"]]["events"], case["name"]


def test_the_evidence_note_rule_is_the_wide_one_it_was():
    """This site takes a sentence with "was supplied" or "were retrieved" in it for a note about the evidence, which
    is loose ("Gas was supplied to two million homes." is not marked). It is kept as it is here, so that nothing
    changes with the move; narrowing it is a separate change for the owner, with an evaluation run before and
    after (wts-core docs/ARCHITECTURE.md's table of findings; docs/AS-BUILT.md, "checks")."""
    from wts_core.checks import EVIDENCE_NOTES_STRICT, EVIDENCE_NOTES_WIDE, Verifier

    from wcs.rag.checks import VERIFIER
    from wcs.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    case = next(c for c in shared.CHECK_CASES if c["name"] == "notes about the evidence, wide and narrow")
    wide = Verifier(no_records="No records found.", evidence_notes=EVIDENCE_NOTES_WIDE)
    strict = Verifier(no_records="No records found.", evidence_notes=EVIDENCE_NOTES_STRICT)
    ours = VERIFIER.verify(case["blocks"], sources).to_dict()
    assert ours == wide.verify(case["blocks"], sources).to_dict()
    assert ours["unsupported_segments"] < strict.verify(case["blocks"], sources).to_dict()["unsupported_segments"]
