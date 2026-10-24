"""Classify a speaker's post: head of state or government, minister, or representative.

A statement by a head of state or government, or a foreign minister, speaks for a government more plainly than a
delegate's, so ranking gives it a little more weight (``rank.composite``). The post comes from the source where it
is given (the General Debate's list of speakers); Security Council records usually give only a name and country,
which classifies as nothing and carries no extra weight.
"""

from __future__ import annotations

import re

HEAD = re.compile(
    r"\b(president|prime minister|premier|king|queen|emir|amir|sultan|prince|grand duke|chancellor|"
    r"head of (state|government)|chair(man|person)? of the (presidential council|presidency|council of ministers|"
    r"transitional)|taoiseach|captain regent|governor-general|sovereign)\b", re.I)
# Deputies and vice-presidents are ministers here, not heads: they speak for a government without leading it.
DEPUTY = re.compile(r"\b(vice|deputy)\b", re.I)
MINISTER = re.compile(r"\b(minister|secretary of state|foreign secretary|state counsellor|secretary for foreign)\b",
                      re.I)
REPRESENTATIVE = re.compile(r"\b(ambassador|permanent representative|representative|delegation|charg[eé] d'affaires|"
                            r"envoy|observer)\b", re.I)


def classify(post: str | None) -> str:
    """'head', 'minister', 'representative', or '' when the post is not given or not recognised."""
    if not post:
        return ""
    if HEAD.search(post) and not DEPUTY.search(post) and not re.search(r"president of the general assembly", post, re.I):
        return "head"
    if MINISTER.search(post) or (HEAD.search(post) and DEPUTY.search(post)):
        return "minister"
    if REPRESENTATIVE.search(post):
        return "representative"
    return ""


ROLE_LABEL = {
    "head": "head of state or government",
    "minister": "minister",
    "representative": "representative",
}
