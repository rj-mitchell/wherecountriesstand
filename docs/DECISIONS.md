# Decisions

Product decisions made by the owner. They override anything else in this repo. Add new decisions
here once they're made.

| # | Topic | Decision |
|---|---|---|
| 1 | Name and address | **Where Countries Stand**, a separate website with its own domain (not a section or subdomain of the Parliament site, Where MPs Stand). A project of the charity Where They Stand, on its international shelf with Where Lives Stand. |
| 2 | Code | A new repo. Copy the Where They Stand engine and adapt it. Pull out shared code into a common package later, once both sites work and the shared parts are obvious. |
| 3 | First version | General Debate, Security Council debates, General Assembly votes and group memberships, all from **1992**. General Debate and votes back to 1946 as an archive. Briefings default to the **last 10 years**, with an obvious control to widen the range. |
| 4 | Keeping current | The academic corpora for the back catalogue. Our own loader for UN verbatim records from 2024, checked against the corpora on overlapping years. |
| 5 | Groups | Countries are grouped by membership **on the date** (blocs, regions, income groups, Council membership), as Where They Stand uses party and role on the date. Before 1971, "China" is the Republic of China. |
| 6 | Country names | The UN's own names and status throughout (Member State, observer State, non-member), stated as a policy on the site. |
| 7 | Neutral wording | Where They Stand's rules apply. Also not used in the site's own voice: "regime" (say "government"), "rogue state", "the West" and similar labels. "Condemned" is allowed when describing a formal statement. Group statements are attributed to the group, never to each member individually. |
| 8 | Licence | Code AGPL-3.0, as Where They Stand. Briefing text **CC BY-NC 4.0**, because of the UN's non-commercial terms. |
| 9 | Hosting and budget | Start on the existing Where They Stand server if there's room; otherwise a second server of about £40–70 a month. A separate AI spending cap, about $50 a month to start. |
| 10 | Testing | A test set of about 50 questions from the start, including sparse ones where "No records found" is the right answer. |
| 11 | Look | The look of the UN's printed Official Records, in a **single column** (not the records' two columns), with the UN's web palette and typeface for controls, and no UN name, emblem or wordmark. See [`DESIGN.md`](DESIGN.md). |
| 12 | The General Debate website | Until a session's official records are out (about a year after the debate), statements come from the UN's General Debate website (gadebate.un.org). Its browser check blocks automated clients, so the update job presents itself as a browser, reading only what robots.txt allows, politely (October 2026). Texts from it are labelled as not yet the official record, and are replaced by it. |
| 13 | Rights of reply | Rights of reply in the General Debate are included as records of their own ("General debate: right of reply"), never mixed with the statements. Points of order are left out. Added from the UN's records from 1993 (the corpus has none; earlier records are unreadable scans). |
| 14 | Briefing length | Two kinds of briefing, as on Where MPs Stand: **Summary** (the default: 24 sources, about 300–450 words) and **Comprehensive** (40 sources, about 700–1,300 words, adding how positions changed over time and the main points of agreement and contention). The choice is shown openly beside the question box. |
| 15 | China and the Group of 77 | China is **not** counted as a member of the Group of 77: China says it is not a member, only a supporter, though the Group lists it and it contributes to the Group's funds. The site describes a country's memberships as the country itself states them. The Group's joint statements, made "on behalf of the Group of 77 and China", are attributed to the Group (decision 7). (October 2026) |
