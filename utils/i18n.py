"""Two-language UI strings (Russian/English).

Language is stored as settings["language"] ("ru" or "en", default "ru")
and applied at startup before widgets are built; changing it in Settings
takes effect after restart, like the render backend.
"""
from . import i18n_en, i18n_ru

SUPPORTED_LANGUAGES = ("ru", "en")
DEFAULT_LANGUAGE = "ru"

_catalogs = {"ru": i18n_ru.STRINGS, "en": i18n_en.STRINGS}
_current = DEFAULT_LANGUAGE


def set_language(lang) -> str:
    """Normalize and activate a language id; unknown values fall back to default."""
    global _current
    norm = str(lang or "").strip().lower()
    _current = norm if norm in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return _current


def get_language() -> str:
    return _current


def tr(key: str) -> str:
    """Translated string for the active language (Russian fallback, then the key)."""
    table = _catalogs.get(_current, {})
    hit = table.get(key)
    if isinstance(hit, str):
        return hit
    fallback = _catalogs[DEFAULT_LANGUAGE].get(key)
    if isinstance(fallback, str):
        return fallback
    return key


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
    table = _catalogs.get(_current, {}).get(key)
    if not isinstance(table, dict):
        table = _catalogs[DEFAULT_LANGUAGE].get(key, {})
    if not isinstance(table, dict):
        return key
    form = _pick_form(n)
    hit = table.get(form) or table.get("other") or table.get("many")
    return hit if isinstance(hit, str) else key
