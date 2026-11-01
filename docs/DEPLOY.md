# Putting the site on a server

Where Countries Stand runs on the charity's shared server, beside Where MPs Stand (decision 9), in its own folder
(`/opt/wherecountriesstand`) with its own container, data, settings and spending caps. Where MPs Stand's web
server (Caddy) is the front door for every site on the server: it answers wherecountriesstand.com through
Cloudflare, with the same protections as its own site, and passes the visitors to this site's container over a
shared Docker network (`sites`). Updates and backups run on timers, each in a one-off container, as on Where MPs
Stand. See "Other sites on this server" in Where MPs Stand's `docs/HOSTING.md`.

## What it needs

| | Minimum | Comfortable |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU (meaning search is built on the CPU: about 55 passages a second on 4) |
| Memory | 4 GB | 8 GB |
| Disk | 15 GB | 30 GB |

The data comes to 2.7 GB after the first load (measured in a rehearsal, October 2026): the downloaded corpora and
UN records (1.7 GB, kept so nothing need be downloaded again), the stored speeches (205 MB) and the keyword index
(790 MB, 574,720 passages). Meaning search adds about 1 GB as it is built.

Also needed: a domain name, Docker with the compose plugin, and an Anthropic API key for written briefings (without
one, the site shows the sources only).

## Steps

1. **Cloudflare**, for wherecountriesstand.com (as for wherempsstand.com in Where MPs Stand's `docs/HOSTING.md`,
   "Cloudflare protection"):
   * the "coming soon" page is a Cloudflare Worker, `wherecountriesstand-holding` (`deploy/holding/`): remove its
     custom domain first (Workers & Pages > wherecountriesstand-holding > Settings > Domains & Routes);
   * an **A** record for `@` pointing at the server, **Proxied**, and `www` redirected to it by a redirect rule;
   * SSL/TLS **Full (strict)**, and an **origin certificate** for wherecountriesstand.com (leave its page open).
2. **Code and settings**, on the server, as root:
   ```bash
   git clone https://github.com/rj-mitchell/wherecountriesstand /opt/wherecountriesstand
   cd /opt/wherecountriesstand/deploy
   cp .env.example .env && nano .env    # WCS_ANTHROPIC_API_KEY, WCS_GUESTBOOK_NAME and WCS_GUESTBOOK_EMAIL
   ```
   Spending caps are in `config/settings.toml`, `[usage]`: $3 a day and $50 a month to start (decision 9), 40 new
   briefings per visitor per day, 20 requests a minute.
3. **Start it:**
   ```bash
   bash /opt/wherecountriesstand/deploy/update.sh
   ```
   It builds and starts the site, and installs its update and backup timers. With no data yet, it finds nothing.
4. **The front door:** make sure `wherecountriesstand.com=wherecountriesstand` is in `SITES` in
   `/opt/hansardgpt/deploy/.env`, then paste the origin certificate:
   ```bash
   bash /opt/hansardgpt/deploy/cloudflare.sh site wherecountriesstand.com
   ```
5. **The General Debate corpus** is downloaded by the first load (next step), which fills in the guestbook form
   Harvard Dataverse asks for with `WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL` from `.env`. Without them the first
   load carries on without the corpus; add them and run `wcs fetch-gd` and `wcs ingest-gd` later (as below), or
   download the two files by hand from https://doi.org/10.7910/DVN/0TJX8Y and `docker compose cp` them into
   `/data/raw/ungd/`.
6. **First load**, in the background, at a quiet time (overnight):
   ```bash
   cd /opt/wherecountriesstand/deploy
   nohup docker compose run --rm -T app wcs bootstrap > /var/log/wcs-bootstrap.log 2>&1 &
   tail -f /var/log/wcs-bootstrap.log     # Ctrl-C stops watching, not the load
   ```
   Rehearsed from an empty volume in October 2026 (4 CPUs), it took 2 hours 10 minutes:

   | Step | Time |
   |---|---|
   | Voting data, Security Council corpus (510 MB), General Debate corpus (71 MB): download and load | 1 minute |
   | The UN's own records since the corpora: 778 documents (Security Council from 2024, General Debate from 2024, and the General Debate website for sessions whose records are not out) | 1 hour 35 minutes |
   | Rights of reply in past General Debates (2000-2023) | 32 minutes |
   | Keyword index | 1 minute |

   Meaning search is then left to the scheduled updates, an hour a run, so it covers everything within about a day
   and a half (`--embed-hours N` does N hours of it at the end of the first load instead). It skips whatever is
   already done, so if it stops, run it again. Keyword search works as soon as the index is built; meaning search
   fills in as it is embedded.
7. **Check.** Visit the site, ask a question, and run `bash /opt/hansardgpt/deploy/check.sh`, which lists every site
   on the server. The site takes about 20 seconds to start, and the first few searches after a start take a few
   seconds while the index is read from disk; then most take well under a second.

## Running it

Commands below are run in `/opt/wherecountriesstand/deploy`. Any `wcs` command runs in a one-off container:
`docker compose run --rm -T app wcs <command>` (for example `wcs usage`).

* **Updates** run twice a day (`wcs-update.timer`, 5.41am and 5.41pm UTC, clear of the other sites' updates;
  `docs/OPERATIONS.md`): new votes, new and corrected Security Council records, the General Debate, the index, and an
  hour of meaning search for passages still waiting. `journalctl -u wcs-update.service -n 50` shows the last run; a
  report that needs attention says so in `/data/logs/update-*.json`.
* **New code:** merge it into `main` on GitHub, then `bash /opt/wherecountriesstand/deploy/update.sh`.
* **Memory:** the container is limited to 4 GB (`mem_limit` in `docker-compose.yml`), so a runaway update stops
  itself rather than slowing the other sites.
* **Spending:** every briefing's cost is recorded in `/data/state.db` (table `usage`). When a cap is reached the
  site carries on showing sources, and says why.
* **Backups:** everything but the state database can be rebuilt with `wcs bootstrap`. That database (spending,
  saved briefings, reports) is copied every night (`wcs-backup.timer`) to `/opt/wherecountriesstand/deploy/backups`,
  the last 14 kept. The cached UN PDFs (`/data/raw/undocs/`) save a day of downloading if the server is lost.
* **The privacy notice** (`web/site/privacy.html`) says what the site keeps: no requests logged (`wcs serve` turns
  the access log off, and the front door keeps none), briefings cached for seven days (`answer_cache_hours`), notes
  of failed briefings for 90 days, and a daily visitor code in place of IP addresses. It names Cloudflare, which
  carries the site's visits. Keep it true when changing any of these, or the hosting.
* **Search engines and link previews:** `/robots.txt`, `/sitemap.xml` and the pages' link-preview tags use
  `https://` and `DOMAIN` from `.env` (or `WCS_SITE_URL`, or `[site] url` in `config/settings.toml`). Crawlers are
  kept out of `/api/`, so one that runs the page's script cannot ask for briefings.
* **Visitors' addresses:** the front door passes each visitor's own address (from Cloudflare) in
  `X-Forwarded-For`, which `wcs serve --behind-proxy` trusts. The container's port is reachable only over the
  server's Docker networks, never from outside, so nobody else can set it.

## If something goes wrong

* **The build fails with "429 Too Many Requests" from Docker Hub** (its limit on anonymous
  downloads, met in the rehearsal): fetch the same Python image from Google's mirror, then build again:
  `docker pull mirror.gcr.io/library/python:3.12.15-slim && docker tag mirror.gcr.io/library/python:3.12.15-slim python:3.12.15-slim`.
* **The first load stops** (a closed connection, a restart): run `wcs bootstrap` again; it skips what is done.

## On your own computer

```bash
docker compose up -d --build                  # http://localhost:8000
docker compose exec app wcs bootstrap
```

Or without Docker: `uv sync --extra embed`, then `uv run wcs bootstrap` and `uv run wcs serve`.
