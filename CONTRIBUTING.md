# Contributing

How to set up the code, the checks to run before a pull request, and the style to follow. Something wrong in a
briefing or elsewhere on the site is quickest reported through the
[corrections page](https://wherecountriesstand.com/corrections.html). Issues and pull requests on GitHub are also
welcome.

## Setting up

Setting up needs [uv](https://docs.astral.sh/uv/) and Python 3.12 or later.

```bash
uv sync --all-extras
uv run wcs serve          # http://127.0.0.1:8000, with no data until some is loaded (README, "Quick start")
```

## Before opening a pull request

Run the checks CI runs on the code:

```bash
uv sync --frozen
uv run ruff check src tests scripts
uv run pytest
```

The tests run offline, with the model faked. After a change to the pages or the stylesheet, also run the accessibility
check against a running site (`node scripts/a11y.mjs http://127.0.0.1:8000`, with Node 22, Playwright and
axe-core installed). A change to how questions are read or evidence is found should be scored with
`uv run wcs evaluate` before and after.

Some tests compare the site with recorded outputs in `tests/golden/`. If a change to what readers see is
intended, update the recording or list the change in the test, and say so in the pull request.

## Style

- Write in plain British English, in sentence case, with dates in the form 6 November 2026.
- Headings name the thing they cover, in sentence case. Don't use em dashes.
- Wording shared across the Where They Stand sites follows the family glossary (`family/GLOSSARY.md` in the
  charity's private repository). The site's own wording rules are
  [decision 7](docs/DECISIONS.md#7-neutral-wording).
- Don't edit `web/site/family/`; it is a copy of the family kit
  ([docs/DEPLOY.md](docs/DEPLOY.md#pages-and-the-family-kit)).
- Behaviour shared with the sister sites belongs in [wts-core](https://github.com/rj-mitchell/wts-core).
- Code is checked with ruff (`pyproject.toml`). Keep comments short and about the code as it is.
- A product decision goes in [docs/DECISIONS.md](docs/DECISIONS.md).

## Commit messages

Write a short summary line in plain words, then, if needed, a few sentences on what changed and why.

## Licence

By contributing you agree that your code is licensed under the AGPL-3.0, as set out in the
[README](README.md#licence).
