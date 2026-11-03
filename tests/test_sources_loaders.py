import io
import tarfile
from datetime import date

from wcs.reference.countries import countries
from wcs.sources import ungd, unsc
from wcs.sources.speech import in_national_capacity, language, on_behalf_of, split_attribution


def test_attribution_and_language():
    a, body = split_attribution("Mr. Delattre (France) (spoke in French): Our military action was justified.")
    assert a == "Mr. Delattre (France) (spoke in French)"
    assert body == "Our military action was justified."
    assert language(a) == "French"
    assert language("Mr. Omran (interpretation from Arabic)") == "Arabic"
    assert language("Mr. X (spoke in Prench)") == "French"   # recognition error in a scanned record
    assert language("Ms. Sison (United States of America)") is None


def test_group_statements():
    assert on_behalf_of("I have the honour to speak on behalf of the Group of 77 and China. We") == \
        "Group of 77 and China"
    assert on_behalf_of("I speak on behalf of the European Union and its member States.") == "European Union"
    assert on_behalf_of("I wish, on behalf of my delegation, to congratulate you") is None
    assert on_behalf_of("On behalf of the Government of Kenya, I thank you") is None
    assert on_behalf_of("I should like, on behalf of the Council, to welcome") is None
    assert on_behalf_of("I should like, on behalf of the Council, to welcome", chair=True) == "Security Council"
    assert on_behalf_of("I have the honour to speak on behalf of the Nordic countries: Finland, Iceland.") == \
        "Nordic countries"
    assert on_behalf_of("It is a privilege for me to deliver this statement on behalf of the States parties to the "
                        "Treaty on the Non-Proliferation of Nuclear Weapons (NPT) that") == \
        "States parties to the Treaty on the Non-Proliferation of Nuclear Weapons"
    assert on_behalf_of("On behalf of the African Group, I thank you.") == "Group of African States"
    # someone else's statement, mentioned
    assert on_behalf_of("We support the statement made by Ambassador Olhaye of Djibouti, which he delivered on "
                        "behalf of the Group, and") is None
    # a group named earlier in the meeting, not here
    assert on_behalf_of("I have the honour to speak on behalf of the Group.") == "a group not named in this statement"


def test_national_capacity():
    assert in_national_capacity("I shall now make a statement in my capacity as the representative of the United "
                                "Kingdom.")
    assert not in_national_capacity("I now give the floor to the representative of Chile.")


ROW = {"date": "2015-11-20", "document_symbol": "S/PV.7565", "topic": "Threats to international peace",
       "agenda_item1": "Thematic", "speaker": "Mr. Delattre", "country_org": "France", "participanttype": "Mentioned",
       "role": "NA"}


def test_security_council_record():
    r = unsc.record_for("UNSC_2015_SPV.7565_spch0004.txt",
                        "Mr. Delattre (France) (spoke in French): Our military action is self-defence.",
                        ROW, countries(), 1994, "https://undocs.org/en/")
    assert r["record_id"] == "sc:S/PV.7565:0004"
    assert r["country_code"] == "FRA" and r["entity_kind"] == "member_state"
    assert r["participant"] == "member" and r["language"] == "French"
    assert r["meeting"] == "7565th meeting" and r["url"] == "https://undocs.org/en/S/PV.7565"
    assert r["text"] == "Our military action is self-defence." and not r["ocr"]


def test_presiding_chair_is_not_the_country():
    row = {**ROW, "speaker": "Mr. Rycroft", "country_org": "United Kingdom Of Great Britain And Northern Ireland",
           "participanttype": "The President"}
    chair = unsc.record_for("UNSC_2015_SPV.7565_spch0001.txt", "The President: I now give the floor to France.",
                            row, countries(), 1994, "")
    assert chair["entity_kind"] == "chair" and chair["country_code"] is None
    national = unsc.record_for("UNSC_2015_SPV.7565_spch0020.txt", "The President: I shall now make a statement in "
                               "my capacity as the representative of the United Kingdom. We welcome it.",
                               row, countries(), 1994, "")
    assert national["country_code"] == "GBR" and national["entity_kind"] == "member_state"


def test_organisations_and_officials():
    org = unsc.record_for("UNSC_2015_SPV.7565_spch0030.txt", "Mr. X: The Union supports this.",
                          {**ROW, "country_org": "European Union", "participanttype": "Guest"}, countries(), 1994, "")
    assert org["entity_kind"] == "organisation" and org["country_code"] is None and org["participant"] == "guest"
    un = unsc.record_for("UNSC_2015_SPV.7565_spch0002.txt", "Mr. Y: I brief the Council.",
                         {**ROW, "country_org": "UN", "participanttype": "Guest"}, countries(), 1994, "")
    assert un["entity_kind"] == "un"


def _tar(path, files):
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_general_debate_loader(settings):
    d = ungd.raw_dir(settings)
    d.mkdir(parents=True)
    _tar(d / "UNGDC_1946-2025.tar.gz", {
        "TXT/Session 78 - 2023/GBR_78_2023.txt": "Climate finance must reach those most at risk.",
        "TXT/Session 78 - 2023/EU_78_2023.txt": "The European Union stands ready.",
        "TXT/Session 20 - 1965/CHN_20_1965.txt": "We speak for China.",
        "TXT/readme.txt": "not a statement",
    })
    recs = {r["record_id"]: r for r in ungd.iter_records(settings, countries())}
    assert set(recs) == {"gd:78:GBR", "gd:78:EU", "gd:20:CHN"}
    gbr = recs["gd:78:GBR"]
    assert gbr["date_precision"] == "session" and gbr["date"] == date(2023, 9, 1)
    assert gbr["meeting"] == "General Debate, 78th session" and gbr["body"] == "General Assembly"
    assert recs["gd:78:EU"]["entity_kind"] == "organisation" and recs["gd:78:EU"]["country_code"] is None
    assert recs["gd:20:CHN"]["country_label"] == "China (Republic of China)" and recs["gd:20:CHN"]["ocr"]
    only = list(ungd.iter_records(settings, countries(), years={1965}))
    assert [r["record_id"] for r in only] == ["gd:20:CHN"]


def test_videoconference_records():
    row = {**ROW, "date": "2020-07-24", "document_symbol": "S/2020/751", "country_org": "Costa Rica",
           "participanttype": "Guest"}
    r = unsc.record_for("UNSC_2020_S_2020_751_spch0024.txt", "Costa Rica expresses its appreciation.", row,
                        countries(), 1994, "https://undocs.org/en/")
    assert r["record_id"] == "sc:S/2020/751:0024" and r["symbol"] == "S/2020/751"
    assert r["meeting"] == "videoconference (recorded in S/2020/751)" and r["country_code"] == "CRI"
    assert unsc.symbol_of("S_2020_751") == "S/2020/751" and unsc.symbol_of("SPV.7565") == "S/PV.7565"
    assert unsc.symbol_of("S/PV.9220Resumption1") == "S/PV.9220 (Resumption 1)"
    assert unsc.symbol_of("SPV.9220Resumption1") == "S/PV.9220 (Resumption 1)"
    assert unsc.meeting_label("S/PV.9220 (Resumption 1)") == "9220th meeting (resumption 1)"
    assert unsc.symbol_url("https://undocs.org/en/", "S/PV.9220 (Resumption 1)") == \
        "https://undocs.org/en/S/PV.9220(Resumption1)"


def test_wrapped_lines_are_joined():
    from wcs.sources.speech import normalise_text

    t = "I wish at\r\nthe outset to speak on behalf of the Pacific\r\nIslands Forum. The Secretary-\r\nGeneral said\r\n\r\nNext."
    assert normalise_text(t) == ("I wish at the outset to speak on behalf of the Pacific Islands Forum. The "
                                 "Secretary-General said\n\nNext.")
    assert on_behalf_of(normalise_text(t)) == "Pacific Islands Forum"


def test_corpus_paragraph_numbers_are_removed():
    from wcs.sources.speech import normalise_text
    from wcs.sources.ungd import PARAGRAPH_NUMBER

    raw = "1.\t Mr. President, I congratulate you.\r81.\tThis session matters. 139.\tWe realize that wars began in 1945.\tNo."
    text = normalise_text(PARAGRAPH_NUMBER.sub("\n\n", raw).strip())
    assert text == "Mr. President, I congratulate you.\n\nThis session matters.\n\nWe realize that wars began in 1945.\tNo."


def test_general_debate_greetings_are_not_group_statements():
    from wcs.sources.speech import general_debate_behalf

    assert general_debate_behalf("On behalf of the African Union, the United Republic of Tanzania and in my own name, "
                                 "I congratulate you, Sir.") is None
    assert general_debate_behalf("I am honoured to address you on behalf of the Council of Ministers of Bosnia and "
                                 "Herzegovina.") is None
    assert general_debate_behalf("It gives me great pleasure to address the General Assembly on behalf of the Group "
                                 "of 77 and China, currently chaired by my country.") == "Group of 77 and China"


def test_group_names_are_made_one():
    from wcs.sources.speech import canonical_group as cg

    assert cg("Movement of Non-Aligned Countries") == cg("Non-Aligned Movement") == "Non-Aligned Movement"
    assert cg("Arab Group") == cg("States members of the Arab Group") == "Group of Arab States"
    assert cg("Pacific") == cg("Pacific Islands Forum member countries Australia") == "Pacific Islands Forum"
    assert cg("Nordic countries Denmark") == "Nordic countries"
    assert cg("European Union and the countries") == cg("Italian Presidency of the Council of the European Union") \
        == "European Union"
    assert cg("European Union and the Group of Friends of Children and Armed Conflict").startswith("European Union and")
    assert cg("Council of Ministers of Bosnia and Herzegovina") is None
    assert cg("Administrator of the United Nations Development Programme") is None
    assert cg("Group of Friends of Haiti") == "Group of Friends of Haiti"
