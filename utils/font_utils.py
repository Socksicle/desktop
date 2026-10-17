"""Font management and emoji font loader for Socksicle (Windows only)."""
import logging
import sys
from pathlib import Path
from PySide6.QtGui import QFontDatabase, QFont
from .platform_utils import get_app_dir

log = logging.getLogger("font_utils")

_EMOJI_FONT_FAMILY = None


def init_app_fonts() -> str | None:
    """Load bundled Noto Color Emoji font on Windows and configure font fallbacks.

    On Linux/other platforms, system fonts provide native emoji support, so
    no font bundling or substitutions are applied.
    """
    global _EMOJI_FONT_FAMILY
    if sys.platform != "win32":
        return None

    if _EMOJI_FONT_FAMILY is not None:
        return _EMOJI_FONT_FAMILY

    font_names = ["NotoColorEmoji.ttf", "NotoColorEmoji_WindowsCompatible.ttf", "NotoColorEmoji-Regular.ttf"]
    for name in font_names:
        candidate_paths = [
            get_app_dir() / "assets" / "fonts" / name,
            Path(__file__).resolve().parent.parent / "assets" / "fonts" / name,
            get_app_dir() / "ui" / name,
            Path(__file__).resolve().parent.parent / "ui" / name,
        ]
        font_path = next((p for p in candidate_paths if p.exists()), None)
        if font_path is not None:
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    _EMOJI_FONT_FAMILY = families[0]
                    for base in (
                        "Segoe UI", "Arial", "Roboto", "Ubuntu",
                        "Helvetica Neue", "Cantarell", "DejaVu Sans", "system-ui"
                    ):
                        QFont.insertSubstitutions(base, [_EMOJI_FONT_FAMILY, "Segoe UI Emoji", "Apple Color Emoji"])
                    log.info("Loaded emoji font for Windows: %s (%s)", _EMOJI_FONT_FAMILY, font_path.name)
                    return _EMOJI_FONT_FAMILY
    log.debug("No bundled Noto Color Emoji font found on Windows")
    return None



