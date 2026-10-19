from PySide6.QtWidgets import QScrollArea, QFrame, QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QBrush


def get_m3_scrollbar_stylesheet(theme=None) -> str:
    """Return sleek Material 3 scrollbar QSS with a slim 5px pill handle."""
    return """
        QScrollArea {
            background: transparent;
            border: none;
            outline: none;
        }
        QScrollArea > QWidget > QWidget {
            background: transparent;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 5px;
            margin: 4px 1px 4px 0px;
            border: none;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.22);
            border-radius: 2px;
            min-height: 36px;
            border: none;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.42);
        }
        QScrollBar::handle:vertical:pressed {
            background: rgba(255, 255, 255, 0.65);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
            background: transparent;
            border: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            height: 0px;
            background: transparent;
            border: none;
        }
    """


def apply_m3_scrollbar(widget, theme=None):
    """Apply Material 3 slim scrollbar styling to a widget (e.g. QTextEdit or QScrollArea)."""
    qss = get_m3_scrollbar_stylesheet(theme)
    widget.setStyleSheet(qss)
    if hasattr(widget, "verticalScrollBar") and widget.verticalScrollBar():
        widget.verticalScrollBar().setStyleSheet(qss)
    if hasattr(widget, "horizontalScrollBar") and widget.horizontalScrollBar():
        widget.horizontalScrollBar().setStyleSheet(qss)


_PILL_WIDTH = 5
_PILL_MIN_H = 36
_PILL_INSET_X = 3
_PILL_INSET_Y = 12


class _OverlayPill(QWidget):
    """Floating scrollbar thumb drawn above the content.

    The native QScrollBar is hidden (it would reserve its own strip along
    the window edge and break the rounded window corners), while this pill
    reserves no layout space: the viewport spans the full width underneath.
    Dragging the pill scrolls exactly like a native handle.
    """

    def __init__(self, area):
        super().__init__(area)
        self._area = area
        self._hovered = False
        self._pressed = False
        self._drag_grab = 0.0
        self.setFixedWidth(_PILL_WIDTH)
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self._pressed:
            alpha = 0.65
        elif self._hovered:
            alpha = 0.42
        else:
            alpha = 0.22
        p.setBrush(QBrush(QColor(255, 255, 255, int(alpha * 255))))
        r = self.width() / 2.0
        p.drawRoundedRect(0, 0, self.width(), self.height(), r, r)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._drag_grab = event.position().y()
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed:
            self._area._pill_drag_to(self.y() + event.position().y() - self._drag_grab)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self._area._scroll_by_event(event)
        event.accept()


class M3ScrollArea(QScrollArea):
    """Material 3 scroll area with overlay pill thumb and customizable wheel direction."""

    def __init__(self, parent=None, invert_scroll: bool = False):
        super().__init__(parent)
        self._invert_scroll = bool(invert_scroll)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The native vertical bar is kept functional but hidden: a visible
        # bar reserves its own strip at the window edge (and square ends
        # poking out of the rounded corners). The overlay pill below
        # replaces it 1-to-1 without taking layout space.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pill = _OverlayPill(self)
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(lambda _mn, _mx: self._sync_pill())
        bar.valueChanged.connect(lambda _v: self._sync_pill())
        self._wheel_anim = QPropertyAnimation(bar, b"value", self)
        self._wheel_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._wheel_target = None
        self.apply_theme()

    def set_invert_scroll(self, invert: bool):
        self._invert_scroll = bool(invert)

    def apply_theme(self, theme=None):
        qss = get_m3_scrollbar_stylesheet(theme)
        self.setStyleSheet(qss)
        self.verticalScrollBar().setStyleSheet(qss)
        self.horizontalScrollBar().setStyleSheet(qss)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_pill()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_pill()

    def wheelEvent(self, event):
        self._scroll_by_event(event)
        event.accept()

    def _scroll_by_event(self, event):
        bar = self.verticalScrollBar()
        if not bar or bar.maximum() <= bar.minimum():
            return

        if not event.pixelDelta().isNull() and event.phase() in (
                Qt.ScrollBegin, Qt.ScrollUpdate, Qt.ScrollEnd):
            # Real touchpad gesture: raw pixels, no animation. Never key
            # mouse wheels off pixelDelta: on Wayland it is synthesized as
            # angleDelta/8 (e.g. 15px per 120° notch) and scrolls crawl.
            step = event.pixelDelta().y()
            if step == 0:
                return
            self._stop_wheel_scroll()
            if self._invert_scroll:
                bar.setValue(bar.value() + step)
            else:
                bar.setValue(bar.value() - step)
            return

        # Mouse wheel: angleDelta is authoritative (Qt docs). One notch
        # (120°) scrolls ~1/6 of the viewport (min 100px).
        notches = event.angleDelta().y() / 120.0
        if notches == 0:
            return
        notch_px = max(bar.pageStep() / 6.0, 100.0)
        step = notches * notch_px
        # Wheel down (negative notches) increases the value, like the old
        # manual path (`value - step`); invert flips the direction.
        delta = step if self._invert_scroll else -step

        base = self._wheel_target if self._wheel_target is not None else bar.value()
        target = max(bar.minimum(), min(bar.maximum(), round(base + delta)))
        self._wheel_target = target
        self._wheel_anim.stop()
        self._wheel_anim.setDuration(140)
        self._wheel_anim.setStartValue(bar.value())
        self._wheel_anim.setEndValue(target)
        self._wheel_anim.start()

    def _stop_wheel_scroll(self):
        self._wheel_target = None
        self._wheel_anim.stop()

    def _pill_track(self):
        """Return (top, track_h, pill_h, vmin, vmax) or None when no scroll."""
        bar = self.verticalScrollBar()
        vmin, vmax = bar.minimum(), bar.maximum()
        if vmax <= vmin:
            return None
        vp = self.viewport().geometry()
        top = vp.top() + _PILL_INSET_Y
        track_h = vp.height() - 2 * _PILL_INSET_Y
        if track_h <= 0:
            return None
        span = vmax - vmin
        page = max(1, bar.pageStep())
        pill_h = max(_PILL_MIN_H, track_h * page / (span + page))
        pill_h = min(pill_h, track_h)
        return top, track_h, pill_h, vmin, vmax

    def _sync_pill(self):
        m = self._pill_track()
        if m is None:
            self._pill.hide()
            return
        top, track_h, pill_h, vmin, vmax = m
        bar = self.verticalScrollBar()
        frac = (bar.value() - vmin) / (vmax - vmin)
        y = top + frac * (track_h - pill_h)
        vp = self.viewport().geometry()
        x = vp.left() + vp.width() - _PILL_INSET_X - _PILL_WIDTH
        self._pill.setGeometry(int(x), int(y), _PILL_WIDTH, int(pill_h))
        if not self._pill.isVisible():
            self._pill.show()
        else:
            self._pill.update()

    def _pill_drag_to(self, pill_top_y: float):
        self._stop_wheel_scroll()
        m = self._pill_track()
        if m is None:
            return
        top, track_h, pill_h, vmin, vmax = m
        travel = track_h - pill_h
        frac = 0.0 if travel <= 0 else (pill_top_y - top) / travel
        frac = max(0.0, min(1.0, frac))
        self.verticalScrollBar().setValue(vmin + round(frac * (vmax - vmin)))
