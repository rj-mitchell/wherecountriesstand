-- Going back to the site's code as it was before the shared package (wts-core), without losing what was asked,
-- sent or decided since. docs/OPERATIONS.md, "Going back, and coming forward again", says when and how to run this.
--
-- VALID ONLY UNTIL 1 FEBRUARY 2027. On that day the site drops its old tables (src/wcs/state.py,
-- DROP_OLD_TABLES_ON), and this file, which writes them again, has nothing to write to: it fails and changes
-- nothing. Do not run it after that day.
--
-- Since the move, spending, the answer cache, saved briefings, the notes of failed briefings, reports and
-- statements are kept in the package's tables (wts_spending, wts_answer_cache, wts_briefings, wts_failures,
-- wts_reports, wts_statements, wts_statement_log, wts_kv). The site's old tables (usage, answer_cache, briefings,
-- failures, reports, statements, kv) were left in place on the day of the move, and the old code reads only those.
-- Each night since, what the site has promised to delete has been deleted from them too (a sender's address, a
-- report's contact, old notes of failed briefings, old visitor secrets, and the visitor's code and time of day on
-- spending rows of days gone by). This writes the old tables again from the package's, in one transaction, so the
-- old code finds everything the site still keeps: what it had, and what has come in since. Then it forgets the
-- move (below).
--
--   docker compose stop app
--   docker compose run --rm --no-deps -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.db'); con.executescript(sys.stdin.read())" < undo-wts-core.sql
--   (then deploy the code from before the move, kept in the archived repository rj-mitchell/wherecountriesstand_old)
--
-- Run it BEFORE the old code is started, and once: a second run stops at its first step ("CHECK constraint
-- failed") and changes nothing.
--
-- WHAT THE OLD CODE IS GIVEN BACK. What the old tables can't hold is folded into what they can: a statement the
-- site removed for breaking the rules is "withdrawn" there (with its reason), and one that lapsed is "declined";
-- how a report ended (resolved or dismissed) and what was said of it publicly are not kept; an item hidden while a
-- concern is decided is shown again (the old code has no way to hide one: withdraw the briefing if it must stay
-- down); the record of each briefing stays in the package's tables, unread. And what the package has forgotten
-- stays forgotten. A spending row of a day gone by comes back with its day, its kind, its model, its token counts
-- and its cost, all exact, so every day's and month's total and both caps are what they were; but with no
-- visitor's code (the old column is empty) and with its day's midnight as its time (2026-11-15T00:00:00+00:00):
-- which visitor a call of an earlier day was for, and at what time of day it was made, is known to nothing any
-- more. (The site has no signed-in accounts, so there is no account to lose.) Today's rows go back whole, code
-- and time. The old code counts a visitor's briefings for today only, and reads no other day's code or time.
--
-- WHAT THIS DISCARDS. Anything the OLD code has written to the old tables since the move. It can have written
-- there only if it was started after the move without this script being run first; the new code then refuses to
-- start, and says so. If the old code's rows are the ones to keep, run deploy/redo-wts-core.sql instead, and read
-- what that one discards.
--
-- IT ENDS BY FORGETTING THE MOVE: the package's copies of these rows are emptied, with what it keeps in summary
-- of them (wts_account_months, and the month rows of wts_visitor_days: left behind, a second move would bring the
-- same spending back beside them), and the move's mark is removed. The old tables are then the whole truth again,
-- and the old code can run for as long as need be. When the new code is next started it makes the move afresh
-- from the old tables, with whatever the old code has taken in meanwhile. Kept as they are: the record of each
-- briefing, the weekly figures, the whole site's count of visitors by the day, and the list of hidden items (an
-- item hidden on the new code is hidden again when the new code next starts).
BEGIN IMMEDIATE;

-- Only on a database that has been moved, and only once. Without the move's mark the package's tables are empty
-- or were never made, and this script would write the old tables afresh from nothing: the INSERT below then
-- fails ("CHECK constraint failed"), and nothing is changed.
CREATE TEMP TABLE the_move_was_made (yes INTEGER NOT NULL CHECK (yes = 1));
INSERT INTO the_move_was_made SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state';
DROP TABLE the_move_was_made;

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

-- Forgetting the move (the same statements as deploy/redo-wts-core.sql; tests/test_import.py keeps them the same).
DELETE FROM wts_spending;
DELETE FROM wts_account_months;
DELETE FROM wts_visitor_days WHERE who != '*';
DELETE FROM wts_answer_cache;
DELETE FROM wts_failures;
DELETE FROM wts_briefings;
DELETE FROM wts_reports;
DELETE FROM wts_statement_log;
DELETE FROM wts_statements;
DELETE FROM wts_kv WHERE key LIKE 'visitor_salt:%';
DELETE FROM wts_schema WHERE name = 'import:wcs-state';

COMMIT;
