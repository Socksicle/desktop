"""UI strings for Russian, English, Chinese, and Hebrew.

Language is stored as settings["language"] ("ru", "en", "zh" or "he", default "ru")
and applied at startup before widgets are built; changing it in Settings
takes effect after restart, like the render backend.
"""
from . import i18n_en, i18n_he, i18n_ru, i18n_zh

SUPPORTED_LANGUAGES = ("ru", "en", "zh", "he")
DEFAULT_LANGUAGE = "ru"

_catalogs = {
    "ru": i18n_ru.STRINGS,
    "en": i18n_en.STRINGS,
    "zh": i18n_zh.STRINGS,
    "he": i18n_he.STRINGS,
}
_current = DEFAULT_LANGUAGE


def set_language(lang) -> str:
    """Normalize and activate a language id; unknown values fall back to default."""
    global _current
    norm = str(lang or "").strip().lower()
    _current = norm if norm in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return _current


def get_language() -> str:
    return _current


def _lookup(key: str):
    """Raw catalog value: active language first, then fallbacks (zh/he → en → ru)."""
    langs = [_current]
    if _current in ("zh", "he"):
        # Untranslated strings are better shown in English than in Russian.
        langs.append("en")
    if _current != DEFAULT_LANGUAGE:
        langs.append(DEFAULT_LANGUAGE)
    for lang in langs:
        value = _catalogs.get(lang, {}).get(key)
        if value is not None:
            return value
    return None


def tr(key: str) -> str:
    """Translated string for the active language (zh/he falls back to English, then Russian)."""
    value = _lookup(key)
    return value if isinstance(value, str) else key


def _pick_form(n: int) -> str:
    if _current == "ru":
        n = abs(int(n))
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if abs(int(n)) == 1 else "other"


def trn(key: str, n: int) -> str:
    """Plural template for count ``n`` (catalog value is a form table)."""
    table = _lookup(key)
    if not isinstance(table, dict):
        return key
    form = _pick_form(n)
    hit = table.get(form) or table.get("other") or table.get("many")
    return hit if isinstance(hit, str) else key
