#!/usr/bin/env bash
# Install or update Where Countries Stand on the charity's shared server from `main`. Run on the server, as root:
#   bash /opt/wherecountriesstand/deploy/update.sh
# It fetches and builds the new code, restarts the site (a few seconds of downtime), checks that it answers, and
# only then installs the timers and prunes old build files. Where MPs Stand's web server is the front door
# (docs/DEPLOY.md).
#
# The first process to open the state database on new code applies any one-off migration, and that must be the
# site itself as it restarts. So no deploy starts while one of the site's jobs is running, the timers are held
# until the new site is up, and their units are installed only once it answers (installed before a failed build,
# they would run commands the old image lacks). If anything fails, the held timers are restarted as they were
# (docs/DEPLOY.md, "New code").
set -euo pipefail
DIR="${DIR:-/opt/wherecountriesstand}"
UNITS_DIR="${UNITS_DIR:-/etc/systemd/system}"
UNITS="wcs-update wcs-backup wcs-weekly-report"

echo "==> Fetching the latest version"
self_before=$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)
git -C "$DIR" pull --ff-only
# bash goes on reading the copy of this script it started with, so a new version only takes effect if run afresh
if [[ -z "${UPDATE_RESTARTED:-}" && "$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)" != "$self_before" ]]; then
  echo "==> This update script has changed: running the new version"
  UPDATE_RESTARTED=1 exec bash "$DIR/deploy/update.sh"
fi

cd "$DIR/deploy"
[[ -f .env ]] || { echo "No settings yet: cp .env.example .env, fill it in, and run this again (docs/DEPLOY.md)."; exit 1; }

for unit in $UNITS; do
  state=$(systemctl is-active "$unit.service" 2>/dev/null || true)
  if [[ "$state" == "active" || "$state" == "activating" ]]; then
    echo "✗ $unit.service is running: wait for it to finish (journalctl -u $unit.service -f), then run this again."
    exit 1
  fi
done
echo "==> Holding the site's timers until the new site is up"
held=""
for unit in $UNITS; do
  if systemctl is-active --quiet "$unit.timer" 2>/dev/null; then
    systemctl stop "$unit.timer"
    held="$held $unit.timer"
  fi
done
release() {
  # restart the held timers on the units already installed
  if [[ -n "$held" ]]; then
    # shellcheck disable=SC2086
    systemctl start $held || true
  fi
}
trap release EXIT

echo "==> Rebuilding the site"
mkdir -p backups
docker network create sites >/dev/null 2>&1 || true  # shared with the charity's other sites on this server
# the day each page last changed, from git, for the sitemap's lastmod (the image has no history of its own)
page_dates=""
for page in "$DIR"/web/site/*.html; do
  page_dates+="$(basename "$page" .html):$(git -C "$DIR" log -1 --format=%cs -- "web/site/$(basename "$page")") "
done
docker compose build --build-arg PAGE_DATES="$page_dates"
# The site runs as user 10001: hand over anything in its data or backups owned by someone else
if docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
     'find /data /backups ! -user 10001 -print -quit | grep -q .'; then
  echo "==> Handing the site's data to its own user"
  docker compose stop app
  docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
    'find /data /backups ! -user 10001 -print0 | xargs -0r chown -h 10001:10001'
fi

echo "==> Restarting the site"
docker compose up -d --remove-orphans

echo "==> Checking it answers"
answers=""
for _ in $(seq 1 30); do
  if docker compose exec -T app python -c \
       "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then
    answers=yes
    break
  fi
  sleep 3
done
if [[ -z "$answers" ]]; then
  echo "✗ The site did not answer within a minute and a half: see docker compose -f $DIR/deploy/docker-compose.yml logs app"
  echo "  Its timers are as they were. If the log says a row of the state database could not be brought across,"
  echo "  see docs/OPERATIONS.md, \"The state database\"."
  exit 1
fi

# The new site is up: install this version's timer units
echo "==> Installing or refreshing the update, backup and weekly report timers"
for unit in $UNITS; do
  cp "$unit.service" "$unit.timer" "$UNITS_DIR/"
done
systemctl daemon-reload
for unit in $UNITS; do
  systemctl enable --now "$unit.timer" >/dev/null
done
held=""  # all of them are running now

echo "==> Clearing out old build files"
docker image prune -f >/dev/null
docker builder prune -f --filter until=72h >/dev/null || true

echo "✓ Where Countries Stand is running."
echo "  Next update: $(systemctl show wcs-update.timer -p NextElapseUSecRealtime --value)"
