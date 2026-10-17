"""SVG icon set for the PC client, converted from the Android drawables.

White paint is stored as ``currentColor`` so icons can be tinted to the
active theme at runtime. Only UI-chrome icons live here: server country
flags stay emoji.
"""

from collections import OrderedDict
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, QRectF, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer

from utils.platform_utils import get_app_dir

log = logging.getLogger(__name__)

_ICONS_DIR = get_app_dir() / "assets" / "icons"

# Rendered tinted pixmaps are tiny (a 20px icon is ~6 KB @2x) and the app
# shows only a handful of distinct icon/color/size combos, so 64 entries
# (~0.5 MB worst case) is plenty. Stale accent colors are dropped on theme
# switch via clear_cache() instead of lingering until eviction.
_PIXMAP_CACHE_MAX = 64
_pixmap_cache: OrderedDict[tuple[str, str, int], QPixmap] = OrderedDict()





def _tinted_svg(name: str, color_hex: str) -> bytes | None:
    try:
        raw = (_ICONS_DIR / f"{name}.svg").read_bytes()
    except OSError as e:
        log.warning("Icon '%s' not found: %s", name, e)
        return None
    try:
        return raw.replace(b"currentColor", color_hex.encode("ascii"))
    except Exception as e:
        log.warning("Failed to tint icon '%s': %s", name, e)
        return None


def pixmap(name: str, color_hex: str, size: int) -> QPixmap | None:
    """Render ``name`` in ``color_hex`` at ``size`` px (with @2x for HiDPI)."""
    key = (name, color_hex, size)
    if key in _pixmap_cache:
        cached = _pixmap_cache[key]
        if cached is not None and not cached.isNull():
            _pixmap_cache.move_to_end(key)
            return cached
        else:
            del _pixmap_cache[key]

    data = _tinted_svg(name, color_hex)
    if data is None:
        return None
    try:
        renderer = QSvgRenderer(QByteArray(data))
        if not renderer.isValid():
            log.warning("Invalid SVG icon '%s'", name)
            return None
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        painter = None
        try:
            from PySide6.QtGui import QPainter

            painter = QPainter(pm)
            renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
        finally:
            if painter is not None:
                painter.end()
        if pm.isNull():
            return None
        pm.setDevicePixelRatio(2.0)
        if len(_pixmap_cache) >= _PIXMAP_CACHE_MAX:
            _pixmap_cache.popitem(last=False)
        _pixmap_cache[key] = pm
        return pm
    except Exception as e:
        log.warning("Failed to render icon '%s': %s", name, e)
        return None


def icon(name: str, color_hex: str, size: int = 20) -> QIcon | None:
    """Build a QIcon for a QPushButton/QAction (1x + @2x pixmaps)."""
    pm = pixmap(name, color_hex, size)
    if pm is None or pm.isNull():
        return None
    try:
        # The pixmap carries devicePixelRatio 2.0, so QIcon stays crisp on
        # HiDPI while Qt downscales it on 1x screens.
        result = QIcon()
        result.addPixmap(pm, QIcon.Normal, QIcon.Off)
        return result
    except Exception as e:
        log.warning("Failed to build QIcon '%s': %s", name, e)
        return None


def set_button_icon(button, name: str, color_hex: str, size: int,
                    fallback_text: str = "") -> None:
    """Put a tinted SVG on a QPushButton, or ``fallback_text`` if missing."""
    try:
        ic = icon(name, color_hex, size)
        if ic is not None and not ic.isNull():
            button.setIcon(ic)
            button.setIconSize(QSize(size, size))
            button.setText("")
            return
    except Exception as e:
        log.warning("set_button_icon('%s') failed: %s", name, e)
    button.setText(fallback_text)


def set_label_icon(label, name: str, color_hex: str, size: int,
                   fallback_text: str = "") -> None:
    """Put a tinted SVG pixmap on a QLabel, or ``fallback_text`` if missing."""
    try:
        pm = pixmap(name, color_hex, size)
        if pm is not None and not pm.isNull():
            label.setPixmap(pm)
            label.setText("")
            return
    except Exception as e:
        log.warning("set_label_icon('%s') failed: %s", name, e)
    label.setPixmap(QPixmap())
    label.setText(fallback_text)


def clear_cache() -> None:
    """Drop rendered pixmaps (theme switch, tests)."""
    _pixmap_cache.clear()
