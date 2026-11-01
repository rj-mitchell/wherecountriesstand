#!/usr/bin/env bash
# Install or update Where Countries Stand on the charity's shared server (whatever has been merged into `main` on
# GitHub). Run on the server, as root:
#   bash /opt/wherecountriesstand/deploy/update.sh
# It fetches the new code, rebuilds and restarts the site (a few seconds of downtime), refreshes its timers and clears
# out old build files. Where MPs Stand's web server is the front door (docs/DEPLOY.md).
set -euo pipefail
DIR="${DIR:-/opt/wherecountriesstand}"

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

echo "==> Refreshing the update and backup timers"
cp wcs-update.service wcs-update.timer wcs-backup.service wcs-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wcs-update.timer wcs-backup.timer >/dev/null

echo "==> Rebuilding the site"
mkdir -p backups
docker network create sites >/dev/null 2>&1 || true  # shared with the charity's other sites on this server
docker compose build
# The site runs as its own user (10001), not root: anything in its data or backups not owned by it is handed over
if docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
     'find /data /backups ! -user 10001 -print -quit | grep -q .'; then
  echo "==> Handing the site's data to its own user"
  docker compose stop app
  docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
    'find /data /backups ! -user 10001 -print0 | xargs -0r chown -h 10001:10001'
fi

echo "==> Restarting the site"
docker compose up -d --remove-orphans

echo "==> Clearing out old build files"
docker image prune -f >/dev/null
docker builder prune -f --filter until=72h >/dev/null || true

echo "==> Checking it answers"
for _ in $(seq 1 30); do
  if docker compose exec -T app python -c \
       "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then
    echo "✓ Where Countries Stand is running."
    echo "  Next update: $(systemctl show wcs-update.timer -p NextElapseUSecRealtime --value)"
    exit 0
  fi
  sleep 3
done
echo "✗ The site did not answer within a minute and a half: see docker compose -f $DIR/deploy/docker-compose.yml logs app"
exit 1
