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
