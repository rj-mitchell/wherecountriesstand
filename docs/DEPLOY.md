# Deploying the site

How the maintainer sets up and runs Where Countries Stand on the charity's shared server. The site has its own
folder (`/opt/wherecountriesstand`), container, data, settings and spending caps. The server, its front door (Caddy,
in Where MPs Stand's checkout at `/opt/hansardgpt`), Cloudflare, email alerts and the off-site backup bucket are
shared, and are described in Where MPs Stand's
[`docs/HOSTING.md`](https://github.com/rj-mitchell/wherempsstand/blob/main/docs/HOSTING.md). This page follows that
document's "Adding a site" steps and gives only this site's own. Day-to-day running is in
[OPERATIONS.md](OPERATIONS.md).

## What it needs

| | Minimum | Comfortable |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU (meaning search is built on the CPU: about 55 passages a second on 4) |
| Memory | 4 GB | 8 GB |
| Disk | 15 GB | 30 GB |

After the first load the data came to 2.7 GB (measured in October 2026): the downloaded corpora and UN records
(1.7 GB, kept so nothing need be downloaded again), the stored speeches (205 MB) and the keyword index (790 MB,
574,720 passages). Meaning search adds about 1 GB.

It also needs a domain name, Docker with the compose plugin, and an Anthropic API key for written briefings.
Without a key, the site shows the sources only.

## First setup

1. Cloudflare, for wherecountriesstand.com: an A record, proxied; `www` redirected; Full (strict) with an origin
   certificate ("Adding a site" in Where MPs Stand's `docs/HOSTING.md`).
2. Code and settings, on the server, as root:
   ```bash
   git clone https://github.com/rj-mitchell/wherecountriesstand /opt/wherecountriesstand
   cd /opt/wherecountriesstand/deploy
   cp .env.example .env && nano .env    # WCS_ANTHROPIC_API_KEY, WCS_GUESTBOOK_NAME and WCS_GUESTBOOK_EMAIL
   ```
   `WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL` fill in the form Harvard Dataverse asks for before the General
   Debate corpus can be downloaded ([DATA.md](DATA.md)).
3. Start the site:
   ```bash
   bash /opt/wherecountriesstand/deploy/update.sh
   ```
   It builds and starts the site, checks it answers, and installs the update, backup and weekly report timers. With
   no data yet, searches find nothing.
4. The front door: add `wherecountriesstand.com=wherecountriesstand` to `SITES`, and paste the origin certificate
   with `cloudflare.sh site wherecountriesstand.com` ("Adding a site", step 3, in Where MPs Stand's
   `docs/HOSTING.md`).
5. The first load, in the background, at a quiet time:
   ```bash
   cd /opt/wherecountriesstand/deploy
   nohup docker compose run --rm -T app wcs bootstrap > /var/log/wcs-bootstrap.log 2>&1 &
   tail -f /var/log/wcs-bootstrap.log     # Ctrl-C stops watching, not the load
   ```
   Rehearsed from an empty volume in October 2026 on 4 CPUs, it took about two and a half hours:

   | Step | Time |
   |---|---|
   | Voting data, Security Council corpus (510 MB) and General Debate corpus (71 MB): download and load | 1 minute |
   | The UN's own records since the corpora: 778 documents | 1 hour 35 minutes |
   | Rights of reply in past General Debates (1993 to 2023) | about 45 minutes |
   | Keyword index | 1 minute |

   If it stops (a closed connection, a restart), run it again: it skips what is done. Without the guestbook
   settings it carries on without the General Debate corpus; add them later and run `wcs fetch-gd` and
   `wcs ingest-gd`.

   Keyword search works as soon as the index is built. Meaning search is then built by the scheduled updates, an
   hour a run, so it covers everything within about a day and a half (`wcs bootstrap --embed-hours N` does N hours
   of it at the end of the first load instead).
6. Check: visit the site, ask a question, and run the shared `check.sh` ("Adding a site", step 4). The site takes
   about 20 seconds to start, and the first few searches after a start take a few seconds while the index is read
   from disk.

## Running it

Commands below are run in `/opt/wherecountriesstand/deploy`. Any `wcs` command runs in a one-off container:
`docker compose run --rm -T app wcs <command>`.

### Timers

| Timer | When (UTC) | What |
|---|---|---|
| `wcs-backup.timer` | 4.20am daily | The nightly backup, then the housekeeping (`wcs tidy`) |
| `wcs-update.timer` | 5.41am and 5.41pm, up to 15 minutes late | The scheduled update ([OPERATIONS.md](OPERATIONS.md#the-scheduled-update)); it can run for hours |
| `wcs-weekly-report.timer` | 7.07am on Mondays | The weekly report on briefings ([OPERATIONS.md](OPERATIONS.md#how-briefings-went)) |

`update.sh` installs all three. The housekeeping has half an hour (`TimeoutStopSec` in `wcs-backup.service`).

### New code

Merge it into `main` on GitHub, then run `bash /opt/wherecountriesstand/deploy/update.sh`. The script fetches the
code, holds the site's timers, builds, restarts the site on the new code and checks it answers. Only then does it
install or refresh the timers' units and start them again. If the build fails or the site does not answer, it
stops, leaves the units as they were and starts the held timers again.

* Deploy when no timer is due: mid-morning to mid-afternoon UTC is free every day. The script refuses to start while
  one of the site's jobs is running. A run that falls due while it works starts once the timers are released, on the
  new code.
* Run no `wcs` command between the build and the restart. Whatever first opens the state database on new code makes
  any one-off change that code brings, and that must be the site itself as it restarts. A `wcs` command run by hand
  on the new image while the old site is still running would make the change early, and what the old site wrote
  afterwards would be left behind.

After a deploy, check meaning search is on: `wcs search "climate finance"` begins `… candidates; meaning search on`.
The search index records the embedding model it was built with. If the settings name another, meaning search is
turned off, with a warning in the next update's report, until `wcs index --re-embed` has built the vectors again.

### Spending

Spending caps are in `config/settings.toml`, under `[usage]`: $25 a day (raised from $3 by the owner on 19 November
2026) and $100 a month (raised from $50 the same day), 8 new briefings
per visitor per day and 20 requests a minute. At most 8 briefings are written at once (2 per visitor), and each
counts against the caps at an estimated $0.25 until its real cost is known, so many questions arriving together
cannot overshoot a cap. Every briefing's cost is recorded in `/data/state.db` (table `wts_spending`), and
`wcs usage` shows it. When a cap is reached the site carries on showing sources, and says why.

### Prices and changing the model

Each model the site calls (`[answer] model`, which writes briefings, and `[answer] query_parse_model`, which reads
questions) must have its own prices in `config/settings.toml`, under `[usage.models."<its name>"]`. The site will not
start with a model that has none: it stops with "the briefing desk cannot be built … no price for <model>" and names
the settings files it read. A dearer model costed at another's prices would undercount spending, and the caps would
bite late. To try another model on the server, put both the model and its prices in `/data/settings.local.toml`:

```toml
[answer]
model = "<the model's name>"

[usage.models."<the model's name>"]
price_input_per_mtok = 0.0        # US dollars per million tokens, from the provider's price list
price_output_per_mtok = 0.0
price_cache_read_per_mtok = 0.0
```

A table there adds to the repository's tables and replaces none of them. A name matches a table if it is the
table's name or begins with it, so a dated name needs no table of its own. Where more than one table matches, the
one with the longest name is used. Give such a variant its table: a model whose name begins with one already there
(`claude-sonnet-5-5` with something added, say) and has no table of its own is costed at the shorter name's
prices, and the site starts all the same. Then restart the site (`docker compose restart app`) and check
`wcs usage`.

### The nightly family check

The charity's nightly check asks each site what is waiting, how old its backups are and the newest weekly report's
totals (numbers only). To let it, set `WCS_CHECK_TOKEN` in `deploy/.env` to the same random value as the charity
repository's `FAMILY_CHECK_TOKEN` secret. Left empty, `/api/check` is not there.

### Backups

Everything but the state database can be rebuilt. That database (spending, saved briefings, reports and
statements) is copied every night (`wcs-backup.timer`) to `/opt/wherecountriesstand/deploy/backups`, where the last
14 are kept, and to the charity's Backblaze B2 bucket, where the last 60 are kept (Where MPs Stand's
`docs/HOSTING.md`, "Backups"). Each update warns if the backups stop.

To set up the off-site copy, once, copy the bucket and key from Where MPs Stand's settings (`HANSARD_B2_*`) and check
them:

```bash
cd /opt/wherecountriesstand/deploy
for k in BUCKET KEY_ID APPLICATION_KEY; do v=$(grep "^HANSARD_B2_$k=" /opt/hansardgpt/deploy/.env | cut -d= -f2-); \
  sed -i "/^WCS_B2_$k=/d" .env; echo "WCS_B2_$k=$v" >> .env; done
docker compose run --rm -T app wcs offsite                  # "ok": true
docker compose run --rm -T app wcs backup --to /backups     # ends "Off-site copy: stored in ..."
```

To put a backup back, stop the site and restore it. The backup is checked first, and the database it replaces is
kept as `/data/state.db.before-restore`:

```bash
cd /opt/wherecountriesstand/deploy
docker compose stop app
docker compose run --rm app wcs restore /backups/wcs-state-2026-11-05.sqlite.gz    # asks first
docker compose start app
```

A second restore is refused while `/data/state.db.before-restore` is still there, so restoring the wrong day and
then the right one cannot lose the database the site was running on. Once it is clear which copy is wanted, move it
out of the way (`docker compose run --rm app mv /data/state.db.before-restore /data/state.db.before-restore-1`) and
restore again. For a copy from B2, download it into `/opt/wherecountriesstand/deploy/backups` first and give it to
the site's user (`chown 10001:10001 <file>`).

A backup holds no visitor codes and no visitor secret (wts-core 0.3.2), and times of day for its own day only: like
the housekeeping, it first removes those of earlier days. Run on new code before the site has started on it, a backup is still made, but it says on standard
error what the copy holds and ends with status 1. `update.sh` starts the site before it releases the timers, so this
cannot happen at a deploy; do not run `wcs backup` by hand between a build and the restart.

### Privacy

The privacy notice (`web/site/privacy.html`) says what the site keeps and for how long, and names Cloudflare, which
carries the site's visits. Keep it true when changing what is stored, for how long, or the hosting. No requests are
logged: `wcs serve` turns the access log off, and the front door keeps none.

### Search engines and link previews

`/robots.txt`, `/sitemap.xml` and the pages' link-preview tags use `https://` and `DOMAIN` from `.env` (or
`WCS_SITE_URL`, or `[site] url` in `config/settings.toml`). Crawlers are kept out of `/api/`, so one that runs the
page's script cannot ask for briefings.

### Caching

Pages are sent with `Cache-Control: no-cache`, so browsers and Cloudflare check them on every visit. Each page's
stylesheets and scripts, including the family kit's, carry a hash of their content in their address
(`/styles.css?v=…`, added as the page is served) and are kept for a year, so nothing needs purging after a deploy.
The site's own fonts are also kept for a year, so a changed font needs a new file name. The family kit's fonts keep
their names when the kit changes, so they are kept for a week; icons and the link card for a day.

### Pages and the family kit

The head, header and footer are written once, in `web/partials/`, and put into each page as the site starts
(`<!--#include header-->`). A page naming a partial that doesn't exist stops the site starting, so the mistake shows
at once.

`web/site/family/` is a copy of the Where They Stand family kit: the endorsement, the footer shelf, the favicon and
link card, the family stylesheet, and the Source Serif 4 and Source Sans 3 fonts. Don't edit it. It is copied from
the kit in the charity's repository (wheretheystand-charity, `family/kit/`):

```bash
node <charity repository>/family/kit/sync.mjs --site countries --out web/site/family --base /family/           # after the kit changes
node <charity repository>/family/kit/sync.mjs --site countries --out web/site/family --base /family/ --check   # is the copy up to date?
```

After a sync, raise the kit version in `.github/workflows/ci.yml` (the `kit` job) to match `web/site/family/VERSION`.
The kit has no scripts and loads nothing from other sites, so the front door's content security policy
(`default-src 'self'`) allows it.

### Visitors' addresses

The front door passes each visitor's address (from Cloudflare) in `X-Forwarded-For`, which
`wcs serve --behind-proxy` trusts. The container's port is reachable only over the server's Docker networks, so
nobody else can set it.

## If something goes wrong

* The build fails with "429 Too Many Requests" from Docker Hub (its limit on anonymous downloads). Fetch the same
  Python image from Google's mirror, then build again:
  `docker pull mirror.gcr.io/library/python:3.12.15-slim && docker tag mirror.gcr.io/library/python:3.12.15-slim python:3.12.15-slim`.
* The site does not answer after a deploy: `update.sh` says so and leaves the timers as they were.
  `docker compose logs app` shows why.

## Running it locally with Docker

```bash
docker compose up -d --build                  # http://localhost:8000
docker compose exec app wcs bootstrap         # the first load (a couple of hours; can be run again)
```

Running it without Docker is in the [README](../README.md#quick-start).
