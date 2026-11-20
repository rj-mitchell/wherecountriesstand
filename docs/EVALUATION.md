# Evaluation

How Where Countries Stand is measured against its test set, and what the runs found. The set is
`eval/questions.toml`: 55 questions, each with the reading a careful researcher would give it and, for some, what
the evidence must contain. How to run the commands on the server is in
[OPERATIONS.md](OPERATIONS.md#test-runs-with-briefings-written).

- `wcs evaluate` scores how each question is read and what evidence is found. It writes no briefing and costs
  nothing.
- `wcs eval run` also has each briefing written and checked, and reports what the checks found, the spread of
  sources, the time and the cost. It is held to `[eval.thresholds]` in `config/settings.toml`. A run of the whole
  set that meets every threshold is noted as a pass in `passed.jsonl`, beside the runs.

A run never starts without a limit: `[eval] max_cost_usd` in `config/settings.toml` ($10), or `--max-cost`. Once
its spending reaches the limit it stops cleanly between questions: the rest are marked as not asked, the run is
saved, and it does not pass. It says what it spent as it ends, and the run file keeps it (`cost_usd`).

## The first baseline on the real archive (19 November 2026)

The owner ran the whole test set on the server on 19 November 2026, in two runs with the same configuration,
`599ae09b95da`. The first stopped at its limit after 33 questions; the second asked the other 22:

```bash
wcs eval run --max-cost 5.5 --record-fixtures /data/eval-fixtures   # the first 33
wcs eval run --only <id> --only <id> …                              # the other 22, each named
```

The runs are `/data/eval/runs/20261119-230110-eval-599ae09b95da.json` and
`/data/eval/runs/20261119-232422-eval-599ae09b95da.json` on the server's data volume. The first kept its
briefings in `/data/eval-fixtures/`, for `wcs eval replay`.

| Figure | First run | Second run | Together |
|---|---|---|---|
| Questions asked | 33 | 22 | 55 of 55 |
| Briefings written | 33 | 15 | 48 |
| Answers of "no records" | 0 | 7 | 7 |
| Errors | 0 | 0 | 0 |
| Grounded ratio (mean) | 0.918 | 0.91 | |
| Citations | 499, none not found in their source | 268, none not found | 767, none not found |
| Unsupported sentences | 49; 69.7% of briefings have at least one | 18; 73.3% of briefings | 67 |
| Quotations | 417; 4 not found word for word | 200; all word for word | 617; 4 not found word for word |
| Loaded words | 2; 6.1% of briefings | 2; 13.3% of briefings | in 4 briefings |
| Sources for a briefing (mean) | 23.06, from 3.39 groups; 58% from the largest group | 23.93, from 4.67 groups; 40.8% from the largest | |
| Time to the first word | median 4,061 ms; 95th percentile 13,082 ms | median 4,007 ms; 95th percentile 6,898 ms | |
| Time to the end | median 12,681 ms; 95th percentile 20,850 ms | median 13,238 ms; 95th percentile 15,994 ms | |
| Cost | $5.502, or $0.1667 a question | $2.895 | $8.40 |
| Tokens | 2,480,157 in and 59,569 out; 173,952 read from the cache and 5,436 written to it | 1,325,746 in and 29,865 out | |
| Met every expectation | 97% of questions | 95.5% of questions | |

No question ended in an error, and every citation was found in its source.

Neither run passes. The first stopped at its limit, and 4 of its 417 quotations were not found word for word. The
threshold for those (`max_unverified_quotations`) is 0 on purpose, so a run does not pass whenever even one
quotation is not word for word. The checks flag each such quotation to the reader, so none is shown as exact. The
second found every quotation word for word, but a run of part of the set is never noted as a pass.

The seven answers of "no records" are the six questions named `none-…`, which ask about something the record
does not hold, and `vatican-vote`. The Holy See is an observer at the UN and does not vote, which the test set accepts.

The whole set cost $8.40, about 15 cents a question: about twice a question on Where MPs Stand. The models and
prices are the same. The difference is what the model reads: about 75,000 tokens of sources a question here,
against about 42,000 there, because UN statements are long. Sending only the relevant part of each long statement
is the first candidate for the weekly improvement routine, to be judged against this baseline. The limit for a run
is now $10, so that the whole set fits in one run with a margin.

These two runs are what later runs are compared against, and so is the weekly routine of improving the site
(wts-core's [docs/IMPROVING.md](https://github.com/rj-mitchell/wts-core/blob/main/docs/IMPROVING.md)). On the
server:

```bash
wcs eval compare /data/eval/runs/20261119-230110-eval-599ae09b95da.json /data/eval/runs/<the new run>.json
```
