from PySide6.QtCore import QEasingCurve, QObject, Property, QPropertyAnimation, Signal
from PySide6.QtGui import QColor

THEME_LERP_MS = 300

# Stylesheet passes are the expensive part of a theme switch: applying
# every animation tick drops frames on low-end machines, so intermediate
# frames are decimated and only every FRAME_STRIDE-th is painted. The
# finished handler always applies the exact target palette, so skipped
# frames never affect the end state.
FRAME_STRIDE = 2

THEME_COLOR_KEYS = (
    "primary",
    "on_primary",
    "primary_container",
    "on_primary_container",
    "secondary",
    "on_secondary",
    "secondary_container",
    "on_secondary_container",
    "tertiary",
    "on_tertiary",
    "tertiary_container",
    "on_tertiary_container",
    "surface",
    "surface_dim",
    "surface_bright",
    "surface_container_lowest",
    "surface_container_low",
    "surface_container",
    "surface_container_high",
    "surface_container_highest",
    "surface_variant",
    "on_surface",
    "on_surface_variant",
    "outline",
    "outline_variant",
    "error",
    "on_error",
    "error_container",
    "on_error_container",
    "success",
    "on_success",
    "success_container",
    "warning",
    "on_warning",
    "warning_container",
)


def lerp_color_hex(a_hex: str, b_hex: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca, cb = QColor(a_hex), QColor(b_hex)
    if not ca.isValid() or not cb.isValid():
        return b_hex
    r = int(round(ca.red() + (cb.red() - ca.red()) * t))
    g = int(round(ca.green() + (cb.green() - ca.green()) * t))
    b = int(round(ca.blue() + (cb.blue() - ca.blue()) * t))
    return QColor(r, g, b).name()


def theme_tokens(theme) -> dict:
    return {
        k: v for k in THEME_COLOR_KEYS
        if isinstance((v := getattr(theme, k, None)), str)
    }


def apply_theme_tokens(theme, tokens: dict) -> None:
    for k, v in tokens.items():
        if k in THEME_COLOR_KEYS:
            setattr(theme, k, v)


def lerp_theme_tokens(start: dict, end: dict, t: float) -> dict:
    out = {}
    for k in set(start) | set(end):
        if k in start and k in end:
            out[k] = lerp_color_hex(start[k], end[k], t)
        elif k in end:
            out[k] = end[k]
        else:
            out[k] = start[k]
    return out


class ThemeColorTransition(QObject):
    frame = Signal(object)
    finished = Signal()

    def __init__(self, parent=None, duration_ms: int = THEME_LERP_MS):
        super().__init__(parent)
        self._duration_ms = duration_ms
        self._start: dict = {}
        self._end: dict = {}
        self._blend = 0.0
        self._frame_no = 0
        self._anim = QPropertyAnimation(self, b"blend", self)
        self._anim.setDuration(duration_ms)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.OutCubic))
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.finished.connect(self.finished)

    def _get_blend(self) -> float:
        return self._blend

    def _set_blend(self, value: float):
        self._blend = float(value)
        if not self._end:
            return
        self._frame_no += 1
        if self._frame_no % FRAME_STRIDE == 0 or self._blend >= 1.0:
            self.frame.emit(lerp_theme_tokens(self._start, self._end, self._blend))

    blend = Property(float, _get_blend, _set_blend)

    def start(self, start_tokens: dict, end_tokens: dict) -> None:
        self._anim.stop()
        self._start = dict(start_tokens)
        self._end = dict(end_tokens)
        self._frame_no = 0
        self._anim.setDuration(self._duration_ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._blend = 0.0
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    def is_running(self) -> bool:
        return self._anim.state() == QPropertyAnimation.Running

    def current(self) -> dict:
        return lerp_theme_tokens(self._start, self._end, self._blend)

    def finish_now(self) -> None:
        if not self._end:
            return
        self._anim.stop()
        self._blend = 1.0
        self.frame.emit(dict(self._end))
        self.finished.emit()
