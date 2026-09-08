"""
Unit coverage for per-site content language.

Every tenant is one site with its own language: successifier.com writes
English, successifier.se and supportifier.se write Swedish. These tests pin
the pieces that carry that language from ``user_sites.settings`` into what the
models actually produce:

* ``shared.language`` — the prompt directive, the human-readable name, the
  localized headings the renderer emits itself, and a slugifier that keeps
  Nordic letters readable.
* ``TenantConfig.language`` — the country-code TLD fallback, which is what
  makes a freshly added ``.se`` site write Swedish before anyone has touched
  the language field.
* ``BrandVoice.neutral`` — the fallback for a site with no scraped voice. It
  must not carry another brand's messaging.
"""

import pytest

from shared.language import (
    language_instruction,
    language_name,
    normalize,
    slugify,
    structural_labels,
)


# ── normalize / language_name ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("sv", "sv"),
        ("SV", "sv"),
        ("sv-SE", "sv"),
        ("sv_SE", "sv"),
        ("  ", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_normalize_language_code(value, expected):
    assert normalize(value) == expected


def test_language_name_uses_readable_names():
    # A prompt saying "Swedish" is far more reliable than one saying "sv".
    assert language_name("sv") == "Swedish"
    assert language_name("sv-SE") == "Swedish"
    assert language_name("da") == "Danish"


def test_language_name_falls_back_to_the_code():
    # Unknown code still tells the model what to target.
    assert language_name("xx") == "xx"


# ── language_instruction ─────────────────────────────────────────────────────


def test_english_gets_no_directive():
    # The models already default to English; an extra directive would only
    # dilute the rest of the prompt.
    assert language_instruction("en") == ""
    assert language_instruction("") == ""


def test_non_english_directive_names_the_language():
    directive = language_instruction("sv")
    assert "Swedish" in directive
    assert "(sv)" in directive
    # It must cover the fields that used to leak English through even when the
    # body itself came out translated.
    for field in ("meta description", "image alt text", "Headings"):
        assert field in directive


# ── structural_labels ────────────────────────────────────────────────────────


def test_structural_labels_are_localized():
    sv = structural_labels("sv")
    assert sv["faq"] == "Vanliga frågor"
    assert sv["toc"] == "Innehåll"


def test_norwegian_aliases_share_one_label_set():
    assert structural_labels("no") == structural_labels("nb")
    assert structural_labels("nn") == structural_labels("nb")


def test_unknown_language_falls_back_to_english_labels():
    assert structural_labels("zz") == structural_labels("en")


# ── slugify ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,expected",
    [
        # The regression this exists for: the old slugifier deleted å/ä/ö
        # outright, so "kundnöjdheten" became "kundnjdheten".
        ("Så förbättrar du kundnöjdheten", "sa-forbattrar-du-kundnojdheten"),
        ("Blåbærsyltetøy og æbleskiver", "blabaersyltetoy-og-aebleskiver"),
        ("Größe und Straße", "grosse-und-strasse"),
        ("Ça va très bien", "ca-va-tres-bien"),
        ("Multiple   spaces", "multiple-spaces"),
        ("Trailing punctuation!!!", "trailing-punctuation"),
    ],
)
def test_slugify_transliterates_instead_of_dropping(title, expected):
    assert slugify(title) == expected


def test_slugify_has_a_fallback_for_empty_input():
    assert slugify("") == "article"
    # A title made entirely of characters that do not survive transliteration
    # must still produce a usable slug rather than an empty one.
    assert slugify("!!!") == "article"


def test_slugify_respects_max_length_without_a_trailing_hyphen():
    slug = slugify("word " * 40, max_length=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")


# ── TenantConfig.language ────────────────────────────────────────────────────


def _tenant_config(settings_blob):
    from shared.tenant import TenantConfig

    return TenantConfig("site-id", settings_blob)


def test_explicit_content_language_wins():
    assert _tenant_config({"content_language": "sv", "domain": "example.com"}).language == "sv"


def test_language_is_inferred_from_a_country_code_tld():
    # This is what makes a freshly added supportifier.se write Swedish before
    # anyone has opened the language selector.
    assert _tenant_config({"domain": "supportifier.se"}).language == "sv"
    assert _tenant_config({"domain": "successifier.se"}).language == "sv"


def test_com_domain_defaults_to_english():
    assert _tenant_config({"domain": "successifier.com"}).language == "en"


def test_no_domain_and_no_setting_defaults_to_english():
    assert _tenant_config({}).language == "en"


# ── Brand voice fallback ─────────────────────────────────────────────────────


def test_neutral_voice_carries_no_other_brands_claims():
    """A site with no scraped voice must not inherit Successifier's.

    The previous fallback was ``BrandVoice.for_tenant("default")``, which is
    the Successifier profile — so supportifier.se articles were written with
    Successifier's messaging pillars and proof points.
    """
    from agents.brand_voice import BrandVoice

    voice = BrandVoice.neutral("some-other-site-id")
    assert voice.voice["messaging_pillars"] == []
    assert voice.voice["proof_points"] == {}
    assert voice.voice["target_persona"] == {}

    prompt = voice.get_system_prompt(content_type="blog", brand_name="Supportifier")
    assert "Successifier" not in prompt
    assert "$79/month" not in prompt
    assert "14-day free trial" not in prompt
    assert "Supportifier" in prompt


def test_neutral_voice_keeps_the_brand_independent_craft_rules():
    from agents.brand_voice import BrandVoice

    voice = BrandVoice.neutral("some-other-site-id")
    # The AI-tell avoid list is about writing quality, not about any one brand.
    assert "delve" in voice.voice["vocabulary"]["avoid"]
    assert voice.voice["tone"]["do"]
