"""Four-language shell: the EN/ES/PT/HT catalogs must carry identical
key sets, and every catalog must label demo data as demo."""

import json
import os

I18N_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "i18n",
)
LANGS = ("en", "es", "pt", "ht")


def _load(lang):
    with open(os.path.join(I18N_DIR, f"{lang}.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def test_catalogs_have_identical_key_sets():
    catalogs = {lang: _load(lang) for lang in LANGS}
    reference = set(catalogs["en"])
    assert reference, "english catalog must not be empty"
    for lang in LANGS:
        assert set(catalogs[lang]) == reference, (
            f"{lang} catalog keys differ from en: "
            f"missing={reference - set(catalogs[lang])}, "
            f"extra={set(catalogs[lang]) - reference}"
        )


def test_catalogs_are_fully_translated():
    for lang in LANGS:
        for key, value in _load(lang).items():
            assert isinstance(value, str) and value.strip(), (lang, key)


def test_every_catalog_labels_demo_and_all_statuses():
    for lang in LANGS:
        catalog = _load(lang)
        assert catalog["banner_demo"].strip()
        for status in ("demo_simulated", "configured", "connected",
                       "verified", "operational", "degraded",
                       "disconnected", "failed"):
            assert catalog[f"status_{status}"].strip(), (lang, status)
