# Putting the site on a server

Where Countries Stand runs as three containers on one server: the site and its API (`app`), a scheduled
`updater`, and Caddy, which gets and renews the HTTPS certificate. Decision 9 (`DECISIONS.md`): the existing Where
MPs Stand server if it has room, or a second server.

## What it needs

| | Minimum | Comfortable |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU (meaning search is built on the CPU: about 26 passages a second on 4) |
| Memory | 4 GB | 8 GB |
| Disk | 15 GB | 30 GB |

The data comes to about 5 GB once loaded: the downloaded corpora and UN records (1.2 GB, kept so nothing need be
downloaded again), the stored speeches (150 MB), the keyword index (1.1 GB) and, once built, meaning search
(about 1 GB more for roughly 490,000 passages).

Also needed: a domain name, Docker with the compose plugin, and an Anthropic API key for written briefings (without
one, the site shows the sources only).

## Steps

1. **DNS.** Point the domain's A (and AAAA) record, and `www`'s, at the server.
2. **Code and settings.**
   ```bash
   git clone https://github.com/rj-mitchell/wherecountriesstand /opt/wherecountriesstand
   cd /opt/wherecountriesstand/deploy
   cp .env.example .env    # set DOMAIN and WCS_ANTHROPIC_API_KEY
   ```
   Spending caps are in `config/settings.toml`, `[usage]`: $3 a day and $50 a month to start (decision 9), 40 new
   briefings per visitor per day, 20 requests a minute.
3. **Start.**
   ```bash
   docker compose up -d --build
   ```
   The site is up at once (with no data yet, it finds nothing). Caddy gets the certificate on the first visit.
4. **The General Debate corpus** is downloaded by the first load (next step), which fills in the guestbook form
   Harvard Dataverse asks for with `WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL` from `.env`. Without them the first
   load carries on without the corpus; add them and run `docker compose exec app wcs fetch-gd` and
   `wcs ingest-gd` later, or download the two files by hand from https://doi.org/10.7910/DVN/0TJX8Y and
   `docker compose cp` them into `/data/raw/ungd/`.
   (Create the folder first if needed: `docker compose exec app mkdir -p /data/raw/ungd`.)
5. **First load.**
   ```bash
   docker compose exec app wcs bootstrap --embed-hours 6
   ```
   In order: the voting data, the Security Council corpus (a 510 MB download), the General Debate corpus if it is
   there, the UN's own records since the corpora (about 750 Security Council meetings and the General Debate:
   several hours, mostly reading PDFs), rights of reply since 2000, the keyword index, then up to 6 hours of
   meaning search. It skips whatever is already done, so if it stops, run it again. Run it inside `tmux` or with
   `nohup` so it survives a closed connection. Keyword search works as soon as the index is built; meaning search
   fills in as it is embedded, and the updater carries on with it.
6. **Check.** Visit the site, ask a question, and look at `docker compose logs app`. The updater's reports are in
   the data volume (`docker compose exec app ls /data/logs`).

## Running it

* **Updates** happen twice a day in the `updater` container (`docs/OPERATIONS.md`): new votes, new and corrected
  Security Council records, the General Debate, the index, and an hour of meaning search for passages still
  waiting. A report that needs attention says so in `/data/logs/update-*.json`.
* **New code:** `git pull && docker compose up -d --build`.
* **Spending:** every briefing's cost is recorded in `/data/state.db` (table `usage`). When a cap is reached the
  site carries on showing sources, and says why.
* **Backups:** the data can all be rebuilt with `wcs bootstrap`, so a backup is a convenience, not a necessity. The
  part that can't be rebuilt is the spend record (`/data/state.db`); copy it off now and then if you want the
  history. The cached UN PDFs (`/data/raw/undocs/`) save a day of downloading if the server is lost.
* **The privacy notice** (`web/site/privacy.html`) says what the site keeps: no requests logged (`wcs serve` turns
  the access log off, and Caddy keeps none), briefings cached for seven days (`answer_cache_hours`), notes of failed
  briefings for 90 days, and a daily visitor code in place of IP addresses. Keep it true when changing any of these,
  or the hosting; putting the site behind Cloudflare means adding Cloudflare to it, as Where MPs Stand's does.
* **Search engines and link previews:** `/robots.txt`, `/sitemap.xml` and the pages' link-preview tags use
  `https://` and `DOMAIN` from `.env` (or `WCS_SITE_URL`, or `[site] url` in `config/settings.toml`). Crawlers are
  kept out of `/api/`, so one that runs the page's script cannot ask for briefings.
* **Behind Cloudflare:** Caddy then sees Cloudflare's addresses, not visitors', and every visitor would share one
  rate limit. Either leave the site unproxied (DNS only), or add Cloudflare's ranges as trusted proxies in the
  Caddyfile and use `{client_ip}` in place of `{remote_host}` (as Where MPs Stand's `deploy/Caddyfile` does).

## On your own computer

```bash
docker compose up -d --build                  # http://localhost:8000
docker compose exec app wcs bootstrap
```

Or without Docker: `uv sync --extra embed`, then `uv run wcs bootstrap` and `uv run wcs serve`.
