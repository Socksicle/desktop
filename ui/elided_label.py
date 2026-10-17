from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtCore import Qt, QSize


class ElidedLabel(QLabel):
    """Single-line label that elides long text and never widens its layout.

    QLabel's minimumSizeHint() is the full text width, which lets long
    subscription names push the whole scroll content wider than the window.
    The horizontal size policy is Ignored so the label takes whatever the
    layout offers and crops the text with an ellipsis instead.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text or ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextFormat(Qt.PlainText)
        self.setMinimumWidth(0)
        super().setText(self._elided())

    def setText(self, text: str):
        self._full_text = text or ""
        super().setText(self._elided())

    def text(self) -> str:
        return self._full_text

    def full_text(self) -> str:
        return self._full_text

    def _elided(self) -> str:
        if not self._full_text:
            return ""
        width = self.width()
        if width <= 0:
            return self._full_text
        return self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        elided = self._elided()
        if elided != super().text():
            super().setText(elided)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setWidth(0)
        return hint
