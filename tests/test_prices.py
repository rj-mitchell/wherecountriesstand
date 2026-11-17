"""Strict prices: every model the site calls has prices of its own, or the site does not start.

The independent review of the adoption found that a model with no table under ``[usage.models]`` was costed at the
base prices, which are the briefing model's: a dearer model named in the server's settings would have had its
spending undercounted, and the daily and monthly caps would have bitten late. The site now asks the briefing desk
for strict prices (``require_prices_for``): the model that writes briefings and the model that reads questions
must each match a table. What a known model costs is unchanged (tests/test_parity.py holds 55 recorded costs).
"""

from __future__ import annotations

import pytest
from wts_core.ports import Usage
from wts_core.settings import SettingsError
from wts_core.usage import PriceTable

from wcs.api.app import create_app, models_called
from wcs.config import load_settings
from wcs.state import open_state

MILLION = Usage.from_mapping({"input_tokens": 1_000_000, "output_tokens": 1_000_000})
OFFLINE = {"embedding.backend": "hashing", "embedding.dim": 64, "live.gd_bundled": ""}


def test_every_model_the_settings_name_has_a_table_and_the_briefing_models_is_the_base_prices(settings):
    usage = settings.usage
    assert models_called(settings) == ("claude-sonnet-5-5", "claude-haiku-5-5")
    assert set(usage["models"]) == set(models_called(settings))
    # the briefing model was costed at the base prices until it had a table: the table is the same figures
    assert usage["models"]["claude-sonnet-5-5"] == {k: usage[k] for k in (
        "price_input_per_mtok", "price_output_per_mtok", "price_cache_read_per_mtok")}
    prices = PriceTable.from_settings(usage)
    prices.require(models_called(settings), base_counts=False)
    assert prices.cost(MILLION, "claude-sonnet-5-5") == pytest.approx(12.0)
    assert prices.cost(MILLION, "claude-haiku-5-5") == pytest.approx(0.6)
    # a dated name is the same model
    assert prices.cost(MILLION, "claude-sonnet-5-5-20260929") == pytest.approx(12.0)
    prices.require(["claude-haiku-5-5-20261001"], base_counts=False)


def test_a_longer_name_takes_the_longest_table_it_begins_with_and_a_variant_needs_a_table_of_its_own(settings):
    """wts-core 0.3.0, where a model name takes the price table of the longest name it begins with: a variant
    whose name begins with a model already in the settings is costed at its own prices once it has a table of its
    own, wherever that table stands in the files. (Until 0.3.0 the first table that matched was used, so the
    variant was costed at the shorter name's prices whatever table it was given; this test held that fault, and
    docs/DEPLOY.md warned of it.) What strict prices still do not catch, on purpose: a variant with no table of
    its own matches the shorter name's and is costed there, since a name must stand for every dated id that
    begins with it. docs/DEPLOY.md says to give a variant its table; this holds the note to what the package
    does."""
    from wcs.config import REPO_ROOT

    usage = {**settings.usage, "models": {**settings.usage["models"],
                                          "claude-sonnet-5-5-larger": {"price_input_per_mtok": 9.0}}}
    prices = PriceTable.from_settings(usage)
    prices.require(["claude-sonnet-5-5-larger"], base_counts=False)
    # its own table: 9.0 in, and the base price out, which its table does not give (it was 12.0: the first match)
    assert prices.cost(MILLION, "claude-sonnet-5-5-larger") == pytest.approx(19.0)
    assert prices.cost(MILLION, "claude-sonnet-5-5-larger-20270101") == pytest.approx(19.0)  # and its dated ids
    # the shorter name and its dated ids are costed as they were
    assert prices.cost(MILLION, "claude-sonnet-5-5") == pytest.approx(12.0)
    assert prices.cost(MILLION, "claude-sonnet-5-5-20260929") == pytest.approx(12.0)
    # the order of the tables no longer matters: the variant's first, and each is still costed at its own
    turned = PriceTable.from_settings({**settings.usage, "models": {
        "claude-sonnet-5-5-larger": {"price_input_per_mtok": 9.0}, **settings.usage["models"]}})
    assert turned.cost(MILLION, "claude-sonnet-5-5-larger") == pytest.approx(19.0)
    assert turned.cost(MILLION, "claude-sonnet-5-5") == pytest.approx(12.0)
    # a variant with no table of its own is not refused: it takes the table of the name it begins with
    plain = PriceTable.from_settings(settings.usage)
    plain.require(["claude-sonnet-5-5-larger"], base_counts=False)
    assert plain.cost(MILLION, "claude-sonnet-5-5-larger") == pytest.approx(12.0)
    notes = " ".join((REPO_ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8").split())
    assert "the one with the longest name is used" in notes and "Give such a variant its table" in notes
    assert "The first table that matches" not in notes  # the warning of the old fault is gone


@pytest.mark.parametrize("which", ["model", "query_parse_model"])
def test_the_site_does_not_start_with_a_model_that_has_no_prices(settings, which):
    settings.raw["answer"] = {**settings.raw["answer"], which: "another-model-1"}
    with pytest.raises(SettingsError) as refused:
        create_app(settings, state=open_state(settings))
    said = str(refused.value)
    assert "no price for another-model-1" in said and '[usage.models."<name>"]' in said
    assert "known: claude-sonnet-5-5, claude-haiku-5-5" in said and "settings.toml" in said


def test_with_no_model_for_reading_questions_the_briefing_model_reads_them_and_needs_no_second_table(settings):
    settings.raw["answer"] = {**settings.raw["answer"], "query_parse_model": ""}
    assert models_called(settings) == ("claude-sonnet-5-5", "claude-sonnet-5-5")
    create_app(settings, state=open_state(settings))


def test_the_servers_own_settings_can_name_another_model_with_its_prices(tmp_path, monkeypatch):
    """`<data>/settings.local.toml` is laid over the repository's settings two levels deep (wts-core 0.2.1): a
    table there adds a model's prices and replaces none of the repository's. The model alone, with no prices, stops
    the site, and the error names the local file as well."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WCS_DATA_DIR", str(data))
    local = data / "settings.local.toml"

    local.write_text('[answer]\nmodel = "another-model-1"\n', encoding="utf-8")
    settings = load_settings(**OFFLINE)
    with pytest.raises(SettingsError) as refused:
        create_app(settings, state=open_state(settings))
    assert "no price for another-model-1" in str(refused.value) and "settings.local.toml" in str(refused.value)

    local.write_text('[answer]\nmodel = "another-model-1"\n\n[usage.models."another-model-1"]\n'
                     'price_input_per_mtok = 5.0\nprice_output_per_mtok = 25.0\nprice_cache_read_per_mtok = 0.5\n',
                     encoding="utf-8")
    settings = load_settings(**OFFLINE)
    assert list(settings.usage["models"]) == ["claude-sonnet-5-5", "claude-haiku-5-5", "another-model-1"]
    app = create_app(settings, state=open_state(settings))
    prices = app.state.parts.prices
    assert prices.cost(MILLION, "another-model-1") == pytest.approx(30.0)
    assert prices.cost(MILLION, "claude-haiku-5-5") == pytest.approx(0.6)  # the repository's tables are still there
