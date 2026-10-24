# Where Countries Stand

**Sourced briefings on what countries have said, and how they have voted, at the United Nations.**

Where Countries Stand will apply the approach of [Where They Stand](https://wheretheystand.uk) to the
UN. Ask a question such as "How have African Union members spoken about Security Council reform
since 2015?" and get a short, balanced briefing that groups the positions taken. Every point cites
the verbatim UN record, with document symbol and page, and speeches given in other languages are
marked as UN translations. If the record holds nothing relevant, the site says "No records found."

It is a project of the Where They Stand Foundation, a charitable incorporated organisation being
registered in England and Wales.

> Where Countries Stand is independent. It is not affiliated with or endorsed by the United Nations,
> and it does not use the UN's name or emblem.

## Status

Planning, plus a design prototype. Nothing runs yet.

| File | What it is |
|---|---|
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Product decisions made so far. They override everything else. |
| [`docs/DATA.md`](docs/DATA.md) | Data sources: coverage, size, licence and how to get them |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The look: the UN's printed Official Records, without the UN's name or emblem |
| [`web/prototype/`](web/prototype/) | Static design prototype. Open `index.html` or `briefing.html` in a browser. Screenshots are in [`docs/img/`](docs/img/). |

## Next steps

1. Copy the Where They Stand engine into this repo and strip out the Parliament-specific parts
   (members, seats, divisions).
2. Loaders for the General Debate corpus, the Security Council corpus and the General Assembly
   voting data.
3. Country and group reference tables, with membership by date.
4. Adapt the retrieval filters, the grouping in briefings, and the neutral-wording rules.
5. Write the test question set.
6. Build the web app in the prototype's design.
