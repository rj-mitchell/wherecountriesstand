"""The server's files (deploy/): the order `update.sh` works in and the nightly unit's time limit; and the move
from a state database the old code made.

The independent reviews of the adoptions found three faults in how a deploy would have gone. `update.sh`
installed the new timers before it built the new image, so after a failed build they would have run commands the
old image lacks. The one-off import of the old rows runs on whichever command first opens the database on new
code, so a timer's command in the gap before the site restarted would have imported while the old site was still
writing, and what it wrote afterwards would have been left behind. And the housekeeping after the nightly backup
(``ExecStopPost``) had systemd's 90-second stop timeout, not the unit's 30 minutes.

Nothing here can run a real deploy: `update.sh` is run in a folder of its own with stand-ins for git, docker and
systemctl that note each call, so its order is what it does, not what its text says.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess

import pytest
import shared
from wts_core.testing import FixedClock
from wts_core.usage import BriefingArchive, token_id

from wcs.config import REPO_ROOT
from wcs.replies import open_replies
from wcs.state import open_state

DEPLOY = REPO_ROOT / "deploy"
UNITS = ("wcs-update", "wcs-backup", "wcs-weekly-report", "wcs-topics")


STAND_IN = r"""#!/usr/bin/env bash
# A stand-in for git, docker and systemctl: notes what it was asked, and answers as the test said it should.
name=$(basename "$0")
echo "$name $*" >> "$FAKE/calls"
case "$name $*" in
  "systemctl is-active --quiet "*) [[ -e "$FAKE/timers/$3" ]] ;;
  "systemctl is-active "*) if [[ -e "$FAKE/running/$2" ]]; then echo activating; exit 3; else echo inactive; exit 3; fi ;;
  "systemctl stop "*) shift 1; for t in "$@"; do rm -f "$FAKE/timers/$t"; done ;;
  "systemctl start "*) shift 1; for t in "$@"; do touch "$FAKE/timers/$t"; done ;;
  "systemctl enable --now "*) shift 2; for t in "$@"; do touch "$FAKE/timers/$t"; done ;;
  "systemctl show "*) echo "Sat 2026-11-14 17:41:00 UTC" ;;
  "git -C "*" log -1 --format=%cs -- web/site/"*) echo 2026-11-19 ;;
  "docker compose build"*) [[ ! -e "$FAKE/build-fails" ]] ;;
  "docker compose run "*) exit 1 ;;   # the look for files not owned by the site's user: none
  "docker compose exec "*) [[ ! -e "$FAKE/not-answering" ]] ;;
  "sleep "*) ;;
esac
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A stand-in for the server: a copy of deploy/ in a folder of its own, a folder for systemd's units, and git,
    docker, systemctl and sleep replaced by a script that notes each call. The site's three timers are running,
    as they are on the server before a deploy."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash here")
    fake, site, bin_ = tmp_path / "fake", tmp_path / "site", tmp_path / "bin"
    for folder in (fake / "timers", fake / "running", tmp_path / "units", bin_):
        folder.mkdir(parents=True)
    shutil.copytree(DEPLOY, site / "deploy")
    (site / "web" / "site").mkdir(parents=True)
    for page in ("about", "index"):
        (site / "web" / "site" / f"{page}.html").touch()
    (site / "deploy" / ".env").write_text("", encoding="utf-8")
    for name in ("git", "docker", "systemctl", "sleep"):
        (bin_ / name).write_text(STAND_IN, encoding="utf-8")
        (bin_ / name).chmod(0o755)
    for unit in UNITS:
        (fake / "timers" / f"{unit}.timer").touch()
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE", str(fake))
    monkeypatch.setenv("DIR", str(site))
    monkeypatch.setenv("UNITS_DIR", str(tmp_path / "units"))

    def deploy():
        done = subprocess.run([bash, str(site / "deploy" / "update.sh")], capture_output=True, text=True)
        calls = (fake / "calls").read_text(encoding="utf-8").splitlines() if (fake / "calls").exists() else []
        return done, calls

    deploy.fake, deploy.units = fake, tmp_path / "units"
    deploy.timers = lambda: sorted(p.name for p in (fake / "timers").iterdir())
    return deploy


def position(calls: list[str], wanted: str) -> int:
    found = [n for n, call in enumerate(calls) if call.startswith(wanted)]
    assert found, f"update.sh never ran {wanted!r}: {calls}"
    return found[0]


def no_wcs_command(calls: list[str]) -> bool:
    """No call is a `wcs` command in the site's image: the only `docker compose run` is a shell's."""
    return all("--entrypoint sh" in call for call in calls if call.startswith("docker compose run"))


def test_update_sh_holds_the_timers_builds_and_brings_the_new_site_up_and_only_then_installs_the_units(server):
    done, calls = server()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "✓ Where Countries Stand is running." in done.stdout
    held = position(calls, "systemctl stop wcs-update.timer")
    build, up = position(calls, "docker compose build"), position(calls, "docker compose up -d")
    answers = position(calls, "docker compose exec")
    reload_, enable = position(calls, "systemctl daemon-reload"), position(calls, "systemctl enable --now")
    assert position(calls, "git -C") < held < build < up < answers < reload_ < enable
    # the image is told the day each page last changed, from git, for the sitemap (it has no history of its own)
    assert calls[build] == "docker compose build --build-arg PAGE_DATES=about:2026-11-19 index:2026-11-19 "
    # every timer was held before the build, and each is running again on the new units at the end
    for unit in UNITS:
        assert position(calls, f"systemctl stop {unit}.timer") < build
        assert position(calls, f"systemctl enable --now {unit}.timer") > answers
    assert server.timers() == sorted(f"{unit}.timer" for unit in UNITS)
    # the units installed are this version's, every one of them, and no others
    assert sorted(p.name for p in server.units.iterdir()) == sorted(p.name for p in DEPLOY.iterdir()
                                                                    if p.suffix in (".service", ".timer"))
    assert sorted(p.stem for p in DEPLOY.glob("*.timer")) == sorted(p.stem for p in DEPLOY.glob("*.service")) \
        == sorted(UNITS)
    # no `wcs` command was run before the site restarted (nor after): whatever opens the state database first on
    # new code makes the one-off move, and must be the site
    assert no_wcs_command(calls) and not any(" wcs " in call for call in calls)


def test_update_sh_leaves_the_units_alone_when_the_build_fails_and_starts_the_timers_again(server):
    (server.fake / "build-fails").touch()
    done, calls = server()
    assert done.returncode != 0
    assert list(server.units.iterdir()) == []  # not one new unit: the old image has no such commands
    assert not any(call.startswith(("docker compose up", "systemctl enable", "systemctl daemon-reload"))
                   for call in calls)
    assert server.timers() == sorted(f"{unit}.timer" for unit in UNITS)  # as they were
    assert position(calls, "systemctl start") > position(calls, "docker compose build") and no_wcs_command(calls)
    # they were held while it built: none could have started a command on a half-made image
    assert position(calls, "systemctl stop wcs-backup.timer") < position(calls, "docker compose build")


def test_update_sh_leaves_the_units_alone_when_the_new_site_does_not_answer(server):
    (server.fake / "not-answering").touch()
    done, calls = server()
    assert done.returncode == 1 and "did not answer" in done.stdout and "Its timers are as they were" in done.stdout
    assert list(server.units.iterdir()) == [] and not any(call.startswith("systemctl enable") for call in calls)
    assert server.timers() == sorted(f"{unit}.timer" for unit in UNITS)


@pytest.mark.parametrize("job", UNITS)
def test_update_sh_does_not_start_while_one_of_the_sites_jobs_is_running(server, job):
    (server.fake / "running" / f"{job}.service").touch()
    done, calls = server()
    assert done.returncode == 1 and f"{job}.service is running" in done.stdout
    assert not any(call.startswith(("docker", "systemctl stop")) for call in calls)  # nothing built, nothing held
    assert server.timers() == sorted(f"{unit}.timer" for unit in UNITS)


def test_update_sh_on_a_first_install_has_no_timers_to_hold_and_installs_them_at_the_end(server):
    for timer in (server.fake / "timers").iterdir():
        timer.unlink()
    done, calls = server()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any(call.startswith(("systemctl stop", "systemctl start")) for call in calls)
    assert server.timers() == sorted(f"{unit}.timer" for unit in UNITS)


def test_update_sh_is_valid_bash():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash here")
    assert subprocess.run([bash, "-n", str(DEPLOY / "update.sh")], capture_output=True).returncode == 0


def test_the_housekeeping_after_the_backup_has_its_own_time_limit():
    unit = (DEPLOY / "wcs-backup.service").read_text(encoding="utf-8")
    service = dict(line.split("=", 1) for line in unit.split("[Service]")[1].splitlines()
                   if "=" in line and not line.startswith("#"))
    assert service["ExecStopPost"].endswith("wcs tidy")
    # ExecStopPost is timed by TimeoutStopSec, whose default is 90 seconds
    assert service["TimeoutStopSec"] == "30min" == service["TimeoutStartSec"]


# The move from a state database the old code made


@pytest.fixture
def old(settings):
    """A data folder holding a state database the old code made (tests/golden/old-state.sql)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.state_db)
    con.executescript((shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8"))
    con.close()
    return settings


def dump(path) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def test_what_has_waited_three_months_lapses_at_the_first_listing_after_the_move(old):
    """The first write or listing after the move lapses whatever has already waited three months: a statement is
    declined, and an open report loses its contact. The package's own ``would_lapse`` says which beforehand, and
    that is what then happens."""
    clock = FixedClock(shared.START)
    db = open_state(old, clock)
    desk = open_replies(db, BriefingArchive(db, new_id=lambda: token_id(8), clock=clock), clock,
                        record_exists=lambda _id: False)
    would = desk.would_lapse()
    assert [s.id for s in would.statements] == [7] and [r.id for r in would.reports] == [1]
    # the first listing does it: exactly those, and no others
    waiting = {s["id"] for s in desk.statements(status="pending")}
    assert 7 not in waiting and desk.statement(7)["status"] == "declined" and not desk.statement(7)["email"]
    open_reports = {r["id"]: r for r in desk.reports()}
    assert open_reports[1]["contact"] is None and open_reports[2]["contact"] == "reader@example.org"
    assert not desk.would_lapse().statements and not desk.would_lapse().reports


@pytest.mark.parametrize("log", ["not json", '["received"]', '{"at": "2026-11-12T09:00:00+00:00"}', '"received"'])
def test_a_row_that_would_stop_the_move_stops_it_until_it_is_mended(old, log):
    """The move copies the old rows in one transaction when the new code first opens the database. The one thing
    the old tables could not check is a statement's log, kept as JSON in one column: one the move cannot read
    stops it, the site does not start, and nothing is half copied. Mended, the move goes through."""
    clock = FixedClock(shared.START)
    con = sqlite3.connect(old.state_db)
    con.execute("UPDATE statements SET log = ? WHERE id = 2", (log,))
    con.commit()
    con.close()
    before = dump(old.state_db)

    # the site does not start, and the database is left as it was but for the package's empty tables
    with pytest.raises(sqlite3.Error):
        open_state(old, clock)
    con = sqlite3.connect(old.state_db)
    assert con.execute("SELECT count(*) FROM wts_schema WHERE name LIKE 'import:%'").fetchone()[0] == 0
    assert [line for line in dump(old.state_db) if "wts_" not in line and "sqlite_sequence" not in line] == \
        [line for line in before if "sqlite_sequence" not in line]
    for table in ("wts_spending", "wts_briefings", "wts_statements", "wts_reports"):
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table  # nothing half copied
    # mended as docs/OPERATIONS.md says, the move goes through
    con.execute("UPDATE statements SET log = '[]' WHERE id = 2")
    con.commit()
    con.close()
    db = open_state(old, clock)
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM wts_statements").fetchone()[0] == 7
        assert con.execute("SELECT count(*) FROM wts_statement_log WHERE statement = 2").fetchone()[0] == 0
