from datetime import date

from wcs.reference.countries import countries
from wcs.reference.roles import classify


def test_resolves_the_labels_records_use():
    c = countries()
    assert c.resolve("United Kingdom Of Great Britain And Northern Ireland") == "GBR"
    assert c.resolve("Cote D'ivoire") == "CIV"
    assert c.resolve("Slovak Republic") == "SVK"
    assert c.resolve("Bolivarian Republic of Venezuela") == "VEN"
    assert c.resolve("Venezuela (Bolivarian Republic Of)") == "VEN"
    assert c.resolve("RUSSIAN FEDERATION") == "RUS"
    assert c.resolve("GER") == "DEU"


def test_organisations_are_not_countries():
    c = countries()
    assert c.resolve("European Union") is None
    assert c.resolve("UN") is None
    assert c.kind(None) == "organisation"


def test_status_follows_the_un():
    c = countries()
    assert c.kind("PSE") == "observer_state"
    assert c.kind("VAT") == "observer_state"
    assert c.kind("SUN") == "former_member_state"
    assert c.kind("XKX") == "other_state"
    assert sum(1 for x in c.by_code.values() if x.kind == "member_state") == 193


def test_dated_names():
    c = countries()
    assert c.name_on("CHN", date(1965, 1, 1)) == "China (Republic of China)"
    assert c.name_on("CHN", date(1972, 1, 1)) == "China"
    assert c.name_on("DEU", date(1985, 1, 1)) == "Federal Republic of Germany"
    assert c.name_on("DEU", date(1991, 1, 1)) == "Germany"
    assert c.name_on("TUR", date(2020, 1, 1)) == "Türkiye"


def test_groups_on_a_date():
    c = countries()
    assert "eu" in c.groups_on("GBR", date(2019, 6, 1))
    assert "eu" not in c.groups_on("GBR", date(2021, 6, 1))
    assert "au" not in c.groups_on("MAR", date(2015, 1, 1))
    assert "au" in c.groups_on("MAR", date(2018, 1, 1))
    assert "p5" in c.groups_on("SUN", date(1990, 1, 1))
    assert c.groups_on("SUN", date(1995, 1, 1)) == []
    assert "p5" in c.groups_on("RUS", date(1995, 1, 1))
    assert "nato" in c.groups_on("FIN", date(2024, 1, 1)) and "nato" not in c.groups_on("FIN", date(2022, 1, 1))
    assert "sids" in c.groups_on("FJI", date(2020, 1, 1))


def test_group_sizes_today():
    c = countries()
    assert len(c.members_of("eu", date(2026, 1, 1))) == 27
    assert len(c.members_of("nato", date(2026, 1, 1))) == 32
    assert len(c.members_of("au", date(2026, 1, 1))) == 54
    assert len(c.members_of("p5", date(2026, 1, 1))) == 5


def test_roles():
    assert classify("President of the Republic") == "head"
    assert classify("Prime Minister") == "head"
    assert classify("Deputy Prime Minister and Minister for Foreign Affairs") == "minister"
    assert classify("Vice-President") == "minister"
    assert classify("Minister for Foreign Affairs") == "minister"
    assert classify("Permanent Representative to the United Nations") == "representative"
    assert classify("President of the General Assembly") == ""
    assert classify(None) == ""


def test_g77_nam_and_oic_on_the_date():
    c = countries()
    on = date(2026, 10, 25)
    assert (len(c.members_of("g77", on)), len(c.members_of("nam", on)), len(c.members_of("oic", on))) == (133, 121, 57)
    assert "nam" in c.groups_on("MLT", date(2003, 6, 1)) and "nam" not in c.groups_on("MLT", date(2005, 6, 1))
    assert "oic" not in c.groups_on("SYR", date(2015, 1, 1)) and "oic" in c.groups_on("SYR", date(2025, 6, 1))
    assert "g77" not in c.groups_on("AZE", date(2018, 1, 1)) and "g77" in c.groups_on("AZE", date(2020, 1, 1))
    assert "g77" in c.groups_on("MEX", date(1990, 1, 1)) and "g77" not in c.groups_on("MEX", date(2000, 1, 1))
    assert "nam" in c.groups_on("PSE", on) and "g77" in c.groups_on("PSE", on)
    # China says it is not a member (docs/DECISIONS.md, 15), on any date
    assert not any("g77" in c.groups_on("CHN", date(y, 1, 1)) for y in (1965, 1980, 2000, 2026))
    assert c.label("g77") == "Group of 77 members"


def test_un_regional_groups_income_and_au_suspensions():
    c = countries()
    on = date(2026, 10, 25)
    sizes = [len(c.members_of(f"ungroup:{g}", on)) for g in
             ("african", "asia_pacific", "eastern_european", "latin_american_caribbean", "western_european_other")]
    assert sizes == [54, 55, 23, 33, 29]            # Türkiye is in two, as the UN lists it
    assert "ungroup:western_european_other" not in c.groups_on("ISR", date(1999, 1, 1))
    assert "ungroup:western_european_other" in c.groups_on("ISR", date(2001, 1, 1))
    assert "income:low" in c.groups_on("IND", date(2005, 1, 1)) and "income:lower_middle" in c.groups_on("IND", on)
    assert "income:middle" in c.groups_on("IND", on) and "income:high" in c.groups_on("GBR", date(1995, 1, 1))
    assert "au_suspended" in c.groups_on("MLI", date(2023, 1, 1)) and "au" in c.groups_on("MLI", date(2023, 1, 1))
    assert "au_suspended" not in c.groups_on("EGY", date(2015, 1, 1))
