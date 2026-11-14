-- Going back to the site's code as it was before the shared package (wts-core), without losing what was asked,
-- sent or decided since. docs/OPERATIONS.md, "The state database", says when and how to run this.
--
-- Since the move, spending, the answer cache, saved briefings, the notes of failed briefings, reports and
-- statements are kept in the package's tables (wts_spending, wts_answer_cache, wts_briefings, wts_failures,
-- wts_reports, wts_statements, wts_statement_log, wts_kv). The site's old tables (usage, answer_cache, briefings,
-- failures, reports, statements, kv) were left exactly as they were on the day of the move, and the old code reads
-- only those. This writes the old tables again from the package's, in one transaction, so the old code finds
-- everything: what it had, and what has come in since.
--
--   docker compose stop app
--   docker compose run --rm -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.db'); con.executescript(sys.stdin.read())" < undo-wts-core.sql
--
-- What the old tables can't hold is folded into what they can: a statement the site removed for breaking the rules
-- is "withdrawn" there (with its reason), and one that lapsed is "declined"; how a report ended (resolved or
-- dismissed) and what was said of it publicly are not kept; an item hidden while a concern is decided is shown
-- again (the old code has no way to hide one: withdraw the briefing if it must stay down). A visitor's code that
-- was forgotten after its day stays forgotten. The package's tables are not changed, so the move can be made again
-- later: delete the row 'import:wcs-state' from wts_schema first, and empty the wts_ tables named above, or the
-- old rows would be brought across twice.
BEGIN IMMEDIATE;

DELETE FROM usage;
INSERT INTO usage (id, visitor, at, kind, model, input_tokens, output_tokens, cache_read_tokens, cost_usd,
                   cache_write_tokens)
SELECT id, nullif(visitor, ''), at, kind, model, input_tokens, output_tokens, cache_read_tokens, cost_usd,
       cache_write_tokens
FROM wts_spending;

DELETE FROM kv WHERE key LIKE 'visitor_salt:%';
INSERT INTO kv (key, value) SELECT key, value FROM wts_kv WHERE key LIKE 'visitor_salt:%';

DELETE FROM answer_cache;
INSERT INTO answer_cache (key, created_at, events) SELECT key, created_at, events FROM wts_answer_cache;

DELETE FROM failures;
INSERT INTO failures (id, at, kind, question, error) SELECT id, at, kind, question, error FROM wts_failures;

DELETE FROM briefings;
INSERT INTO briefings (id, at, question, options, events, withdrawn_at, withdrawn_reason)
SELECT id, created_at, question, options, events, withdrawn_at, withdrawn_reason FROM wts_briefings;

DELETE FROM reports;
INSERT INTO reports (id, at, kind, page, message, contact, closed_at)
SELECT id, at, coalesce(kind, 'error'), coalesce(page, ''), message, coalesce(contact, ''), closed_at
FROM wts_reports;

DELETE FROM statements;
INSERT INTO statements (id, at, page, country, name, role, text, link, email, status, shown_on, ended_at, reason,
                        log)
SELECT s.id, s.at, s.item, s.about, s.name, s.role, s.text, coalesce(s.link, ''), coalesce(s.email, ''),
       CASE s.status WHEN 'removed' THEN 'withdrawn' ELSE s.status END,
       -- the old code shows the date of the words now shown: the later of publishing and the last update
       CASE WHEN s.published_at IS NULL THEN NULL
            ELSE substr(max(s.published_at, coalesce(s.updated_at, '')), 1, 10) END,
       s.closed_at, s.reason,
       coalesce((SELECT json_group_array(json(entry)) FROM (
                   SELECT CASE WHEN l.note IS NULL THEN json_object('at', l.at, 'action', l.action)
                               ELSE json_object('at', l.at, 'action', l.action, 'note', l.note) END AS entry
                   FROM wts_statement_log l WHERE l.statement = s.id ORDER BY l.id)), '[]')
FROM wts_statements s;

COMMIT;
