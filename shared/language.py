"""
Per-site content language helpers.

Every tenant is one *site*, and a site has its own language: successifier.com
writes English, successifier.se and supportifier.se write Swedish. The language
lives in ``user_sites.settings.content_language`` and is exposed as
``TenantConfig.language`` (which also infers it from a country-code TLD when the
field was never filled in).

This module holds the pieces every generation path needs to honour that:

* :func:`language_name` — the human-readable name to put in an LLM prompt.
  A prompt saying "Write everything in Swedish" is far more reliable than one
  saying "sv".
* :func:`language_instruction` — the standard directive block, so the idea
  generator, the article writer and the social writer all phrase it the same way.
* :data:`STRUCTURAL_LABELS` — localized headings for the parts of an article the
  code assembles rather than the model (table of contents, key takeaways, FAQ).
  Without these a Swedish article ends up with English section headings.
* :func:`slugify` — a URL slug that transliterates Nordic and accented letters
  instead of deleting them, so "Så förbättrar du kundnöjdheten" becomes
  ``sa-forbattrar-du-kundnojdheten`` rather than ``s-frbttrar-du-kundnjdheten``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict

# ISO-639-1 → human-readable name for prompts. Unlisted codes fall back to the
# code itself, which still tells the model what to target.
LANGUAGE_NAMES: Dict[str, str] = {
    "sv": "Swedish",
    "nb": "Norwegian (Bokmål)",
    "nn": "Norwegian (Nynorsk)",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "is": "Icelandic",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "pl": "Polish",
    "cs": "Czech",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    "en": "English",
}


def normalize(code: str) -> str:
    """Lowercase an ISO-639-1 code, tolerating ``sv-SE`` style tags."""
    code = (code or "").strip().lower()
    if not code:
        return "en"
    return code.split("-", 1)[0].split("_", 1)[0]


def language_name(code: str) -> str:
    """Human-readable language name for use inside an LLM prompt."""
    code = normalize(code)
    return LANGUAGE_NAMES.get(code, code)


def language_instruction(code: str) -> str:
    """The standard 'write in this language' directive.

    Returns an empty string for English so English prompts stay unchanged
    (the models already default to English, and an extra directive would only
    dilute the rest of the prompt).
    """
    code = normalize(code)
    if code == "en":
        return ""
    name = language_name(code)
    return (
        f"LANGUAGE: Write EVERYTHING in {name} ({code}). Headings, body text, "
        f"meta title, meta description, keywords, image alt text, questions and "
        f"answers must all be in {name}. Do not mix in English sentences, and do "
        f"not translate the output afterwards — compose it in {name} from the "
        f"start so it reads natively rather than like a translation."
    )


# Headings for the parts of an article assembled in code rather than written by
# the model. English is the fallback for any language not listed.
STRUCTURAL_LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "toc": "Table of Contents",
        "takeaways": "Key Takeaways",
        "faq": "Frequently Asked Questions",
        "point": "Point",
        "details": "Details",
    },
    "sv": {
        "toc": "Innehåll",
        "takeaways": "Viktigaste punkterna",
        "faq": "Vanliga frågor",
        "point": "Punkt",
        "details": "Detaljer",
    },
    "nb": {
        "toc": "Innhold",
        "takeaways": "Viktigste punkter",
        "faq": "Ofte stilte spørsmål",
        "point": "Punkt",
        "details": "Detaljer",
    },
    "da": {
        "toc": "Indhold",
        "takeaways": "Vigtigste pointer",
        "faq": "Ofte stillede spørgsmål",
        "point": "Pointe",
        "details": "Detaljer",
    },
    "fi": {
        "toc": "Sisällys",
        "takeaways": "Tärkeimmät kohdat",
        "faq": "Usein kysytyt kysymykset",
        "point": "Kohta",
        "details": "Tiedot",
    },
    "de": {
        "toc": "Inhaltsverzeichnis",
        "takeaways": "Die wichtigsten Punkte",
        "faq": "Häufig gestellte Fragen",
        "point": "Punkt",
        "details": "Details",
    },
    "fr": {
        "toc": "Sommaire",
        "takeaways": "Points clés",
        "faq": "Questions fréquentes",
        "point": "Point",
        "details": "Détails",
    },
    "es": {
        "toc": "Índice",
        "takeaways": "Puntos clave",
        "faq": "Preguntas frecuentes",
        "point": "Punto",
        "details": "Detalles",
    },
    "nl": {
        "toc": "Inhoudsopgave",
        "takeaways": "Belangrijkste punten",
        "faq": "Veelgestelde vragen",
        "point": "Punt",
        "details": "Details",
    },
    "it": {
        "toc": "Indice",
        "takeaways": "Punti chiave",
        "faq": "Domande frequenti",
        "point": "Punto",
        "details": "Dettagli",
    },
    "pt": {
        "toc": "Índice",
        "takeaways": "Pontos principais",
        "faq": "Perguntas frequentes",
        "point": "Ponto",
        "details": "Detalhes",
    },
}

# "no" is an alias for Norwegian Bokmål throughout the product.
STRUCTURAL_LABELS["no"] = STRUCTURAL_LABELS["nb"]
STRUCTURAL_LABELS["nn"] = STRUCTURAL_LABELS["nb"]


def structural_labels(code: str) -> Dict[str, str]:
    """Localized headings for code-assembled article sections."""
    return STRUCTURAL_LABELS.get(normalize(code), STRUCTURAL_LABELS["en"])


# Letters that must become a specific ASCII pair rather than whatever NFKD
# decomposition would leave behind (ø and đ have no combining-mark form, and
# German/Nordic convention expands ß and æ to two letters).
_SLUG_TRANSLITERATIONS = {
    "ß": "ss",
    "æ": "ae",
    "ø": "o",
    "œ": "oe",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ł": "l",
}


def slugify(text: str, *, max_length: int = 80, fallback: str = "article") -> str:
    """URL slug that keeps non-ASCII letters readable instead of dropping them.

    ``å ä ö`` become ``a a o`` and ``ø`` becomes ``o``, so Swedish, Norwegian and
    Danish titles produce pronounceable slugs. Stripping the letters outright
    (the previous behaviour) turned "kundnöjdhet" into "kundnjdhet".
    """
    text = (text or "").lower()
    for source, replacement in _SLUG_TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    # NFKD splits "ä" into "a" + combining diaeresis; dropping the combining
    # marks leaves the base letter.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s-]", "", text).strip()
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_length].strip("-") or fallback
