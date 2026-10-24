# Decisions

Product decisions made by the owner. They override anything else in this repo. Add new decisions
here once they're made.

| # | Topic | Decision |
|---|---|---|
| 1 | Name and address | **Where Countries Stand**, a separate website with its own domain (not a section or subdomain of Where They Stand). A project of the Where They Stand Foundation. |
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
