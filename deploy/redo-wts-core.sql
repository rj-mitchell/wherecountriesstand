-- Making the move onto the shared package (wts-core) again from the site's old tables, when the code from before
-- the move has written to the database since it was moved. docs/OPERATIONS.md, "Going back, and coming forward
-- again", says when and how to run this.
--
-- VALID ONLY UNTIL 1 FEBRUARY 2027, like deploy/undo-wts-core.sql: after that day the old tables are gone, and
-- this stops at its first step ("no such table") and changes nothing.
--
-- When it is needed. The move is made once, and marked. If the old code is then started on the database without
-- deploy/undo-wts-core.sql having been run first, what it takes in goes to the old tables only. The new code,
-- deployed again, would never see it (a briefing saved then would answer "no briefing at this address", and its
-- cost would not count towards the caps), so it refuses to start: "the site's old tables hold rows that are newer
-- than the import that copied them". This script forgets the move and touches nothing else, so that the next
-- start of the new code brings every row of the old tables across afresh.
--
--   docker compose stop app
--   docker compose run --rm --no-deps -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.db'); con.executescript(sys.stdin.read())" < redo-wts-core.sql
--   docker compose up -d
--
-- WHAT THIS DISCARDS. Everything the NEW code wrote to its own tables between the move and the day the old code
-- was started again, which the old tables do not hold: spending rows (so that day's and that month's totals fall
-- by their cost), saved briefings (their addresses then answer "There is no briefing at this address"), cached
-- answers, notes of failed briefings, reports and statements sent or decided on the new code, and the log of what
-- was done to each statement. If the new code never answered a reader after the move, that is nothing. If the new
-- code's rows are the ones to keep, run deploy/undo-wts-core.sql instead, which carries them to the old tables and
-- discards what the old code wrote since the move.
--
-- Kept as they are: the old tables, the record of each briefing, the weekly figures, the whole site's count of
-- visitors by the day, and the list of hidden items (an item hidden on the new code is hidden again when it
-- starts).
BEGIN IMMEDIATE;

-- Only while the old tables are there. From 1 February 2027 the nightly `wcs tidy` drops them, and then there is
-- nothing to make the move again from: the move forgotten would leave the site with no spending, no saved briefings,
-- no reports and no statements at all. Without either of them the SELECT below fails ("no such table"), and nothing
-- is changed. These are the tables the import needs (ALWAYS_THERE in src/wcs/state.py). The others are not asked
-- for: a database put back from an early backup lacks some of them, and the import does without them.
SELECT count(*) FROM usage;
SELECT count(*) FROM briefings;

-- Only on a database that has been moved, and only once. Without the move's mark there is no move to forget: the
-- INSERT below then fails ("CHECK constraint failed"), and nothing is changed.
CREATE TEMP TABLE the_move_was_made (yes INTEGER NOT NULL CHECK (yes = 1));
INSERT INTO the_move_was_made SELECT count(*) FROM wts_schema WHERE name = 'import:wcs-state';
DROP TABLE the_move_was_made;

-- Forgetting the move (the same statements as deploy/undo-wts-core.sql ends with; tests/test_import.py keeps them the
-- same).
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
