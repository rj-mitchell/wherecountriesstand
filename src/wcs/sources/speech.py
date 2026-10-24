"""Reading a speech's opening: who spoke, in what language, and on whose behalf.

A verbatim record opens each speech with the speaker's attribution, for example
``Mr. Delattre (France) (spoke in French):``. Statements made on behalf of a group ("I have the honour to speak on
behalf of the Group of 77 and China") are the group's, not the speaker's country's alone (docs/DECISIONS.md, 7), so
the group is recorded with the speech and briefings attribute it to the group.
"""

from __future__ import annotations

import re

# "(spoke in French)"; records of the early 1990s write "(interpretation from French)"
LANGUAGE = re.compile(r"\((?:spoke in|interpretation from) ([A-Z][a-z]+)\)")
# The attribution ends at the first colon in the opening, before the speech itself begins.
_ATTRIBUTION_MAX = 250

# "on behalf of the X": X is a group when it names one (not "my delegation" or "the Government of X"). A group's
# name may run on in lower case ("the Nordic countries", "the States parties to the Treaty ...").
_BEHALF = re.compile(r"\b[Oo]n behalf of\s+(?:the\s+)?(?P<group>[A-Z][\w’'-]*(?:[\s\-](?:and|of|the|for|on|in|to|members|"
                     r"member|countries|parties|states|associated|group|friends|[A-Z0-9][\w’'-]*)){0,12})")
_GROUP_WORDS = re.compile(r"\b(Group|Groups|Union|Movement|countries|Countries|States|Community|Organization|"
                          r"Organisation|Council|Alliance|League|Forum|members|Members|Caucus|Friends|Coalition|"
                          r"Network|Nations|CARICOM|ASEAN|SICA|MERCOSUR|CELAC|BRICS|LDCs|SIDS|G-?77|"
                          r"Benelux|Nordic|Baltic|Visegrad|Pacific)\b")
_NOT_GROUP = re.compile(r"^(Government|Governments|people|peoples|Secretary|President|Prime|Minister|delegation|"
                        r"my|our|his|her|their|country|Republic|Kingdom|State of|Mr|Mrs|Ms|Ambassador|Chair|Chairperson|Chairman|"
                        r"Special|Under|Head|Office|Department|Mission|United Nations)\b")
# A group referred to by a bare word ("on behalf of the Group") was named earlier in the meeting, not here.
_BARE = {"Group", "Movement", "Organization", "Organisation", "States", "Union", "Forum", "Members", "Coalition",
         "Alliance", "Community", "Countries"}
UNNAMED_GROUP = "a group not named in this statement"
# The speaker is speaking for the group: "I have the honour to speak on behalf of ...", "We deliver this statement
# on behalf of ...", or a sentence that opens "On behalf of the X, ...". Not a mention of someone else's statement
# ("the statement he delivered on behalf of the Group").
_SPEAKING = re.compile(r"\b(?:speak|speaking|deliver|delivering|make|making|read|reading|address|addressing|"
                       r"take the floor|taking the floor|present|presenting|statement|honour|pleasure|privilege)\b",
                       re.I)
_FIRST_PERSON = re.compile(r"\b(?:I|we|We|me|my delegation|My delegation)\b")
_THIRD_PARTY = re.compile(r"\b(?:he|she|they|his|her|was|were|delivered by|made by|by the representative)\b", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_OPENING = 700


def normalise_text(text: str) -> str:
    """Paragraphs separated by one blank line, each on one line: the corpora keep the printed records' line wraps
    (and Windows line endings), which break sentences and names in two."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = []
    for para in re.split(r"\n[ \t]*\n", text):
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        joined = lines[0]
        for ln in lines[1:]:
            # a word broken at the end of a line ("Secretary-" / "General") joins without a space
            joined += ln if joined.endswith("-") and not joined.endswith(" -") else " " + ln
        paras.append(joined)
    return "\n\n".join(paras)


def split_attribution(text: str) -> tuple[str | None, str]:
    """(attribution, speech) for a speech that opens with "Name (Country) (spoke in X): ...", else (None, text)."""
    head = text[:_ATTRIBUTION_MAX]
    i = head.find(":")
    if i <= 0 or len(head[:i].split()) > 25 or "\n" in head[:i].strip():
        return None, text
    return head[:i].strip(), text[i + 1:].strip()


def language(attribution: str | None) -> str | None:
    """The language spoken, when the record notes it ("spoke in French"); English is not noted as a translation."""
    m = LANGUAGE.search(attribution or "")
    if not m:
        return None
    name = _known_language(m.group(1))
    return None if name in (None, "English") else name


OFFICIAL_LANGUAGES = ("Arabic", "Chinese", "English", "French", "Russian", "Spanish")
OTHER_LANGUAGES = ("German", "Italian", "Japanese", "Portuguese", "Turkish", "Ukrainian", "Persian", "Hebrew",
                   "Indonesian", "Korean", "Dutch", "Swedish", "Greek", "Polish")


def _known_language(word: str) -> str | None:
    """The language a (possibly mis-recognised) word names: scanned records give "Prench" or "Snaniah"."""
    from difflib import get_close_matches

    if word in OFFICIAL_LANGUAGES or word in OTHER_LANGUAGES:
        return word
    found = get_close_matches(word, OFFICIAL_LANGUAGES, n=1, cutoff=0.6) or \
        get_close_matches(word, OTHER_LANGUAGES, n=1, cutoff=0.8)
    return found[0] if found else None


def _clean_group(g: str) -> str:
    g = re.sub(r"\s+", " ", g).strip(" .,'’")
    # trailing words that only start the next clause ("the League of Arab States on the ...")
    while (shorter := re.sub(r"\s+(?:and|of|the|for|on|in|to|I|We)$", "", g)) != g:
        g = shorter
    return g


def on_behalf_of(speech: str, chair: bool = False) -> str | None:
    """The group a statement is made on behalf of, read from its opening; None for a national statement.

    ``chair``: the speaker is presiding, so "on behalf of the Council" (a presidential statement, or remarks for the
    members) is the Security Council's.
    """
    for sentence in _SENTENCES.split(speech[:_OPENING]):
        for m in _BEHALF.finditer(sentence):
            before = sentence[:m.start()]
            opens = not before.strip()
            g = _clean_group(m.group("group"))
            if re.fullmatch(r"(?:members of )?(?:the )?(?:Security )?Council(?: members)?", g, re.I):
                # the presiding chair speaking for the Council, in any words
                if chair and (opens or _FIRST_PERSON.search(before)):
                    return "Security Council"
                continue
            if not opens and not (_FIRST_PERSON.search(before) and _SPEAKING.search(before)):
                continue
            if not opens and _THIRD_PARTY.search(before) and not re.search(r"\b(?:I|me)\b", before):
                continue
            if _NOT_GROUP.match(g):
                continue
            if g in _BARE:
                return UNNAMED_GROUP
            if _GROUP_WORDS.search(g):
                return g
    return None


def in_national_capacity(speech: str) -> bool:
    """Whether a presiding speaker says they now speak for their own country."""
    return bool(re.search(r"in (?:my|its) (?:national )?capacity as (?:the )?(?:representative|President) of|"
                          r"in my national capacity", speech[:1500], re.I))
