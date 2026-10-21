from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPixmap

from utils.window_utils import configure_window, find_overlay_target
from utils.i18n import tr


def show_server_qr_dialog(server, theme, parent):
    """Display a centered modal / overlay QR code dialog for a server."""
    if not server:
        return
    import qrcode

    qr_img = qrcode.make(server.key)
    img = qr_img.toqimage()
    target = find_overlay_target(parent)
    is_overlay = target is not None

    class _QrOverlayDialog(QDialog):
        def paintEvent(self, event):
            if is_overlay:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(0, 0, 0, 140))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(self.rect(), 32, 32)
            else:
                super().paintEvent(event)

        def mousePressEvent(self, event):
            if is_overlay and event.button() == Qt.LeftButton:
                if hasattr(self, "_card") and not self._card.geometry().contains(event.position().toPoint()):
                    self.reject()
                    event.accept()
                    return
            super().mousePressEvent(event)

    d = _QrOverlayDialog(target if is_overlay else parent)
    if is_overlay:
        d.setWindowFlags(Qt.Widget)
        d.setAttribute(Qt.WA_TranslucentBackground, True)
        d.setGeometry(target.rect())
    else:
        d.setWindowFlags(Qt.Dialog)
        configure_window(d)

    container = QFrame(d)
    d._card = container
    container.setStyleSheet(f"""
        QFrame {{
            background-color: {theme.surface_container_high};
            border-radius: 28px;
            border: 1px solid rgba(255, 255, 255, 0.12);
            outline: none;
        }}
    """)
    lay = QVBoxLayout(container)
    lay.setContentsMargins(24, 24, 24, 24)

    title = QLabel(server.name if server else tr("item.qr_title"))
    title.setStyleSheet(f"color: {theme.on_surface}; font-weight: bold; font-size: 16px; margin-bottom: 8px; border: none;")
    title.setAlignment(Qt.AlignCenter)
    lay.addWidget(title)

    lbl = QLabel()
    lbl.setPixmap(QPixmap.fromImage(img).scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    lbl.setAlignment(Qt.AlignCenter)
    lay.addWidget(lbl)

    close_btn = QPushButton(tr("dlg.close"))
    close_btn.setFocusPolicy(Qt.NoFocus)
    close_btn.setStyleSheet(theme.get_button_style("tonal"))
    close_btn.clicked.connect(d.accept)
    lay.addWidget(close_btn)

    d_lay = QVBoxLayout(d)
    if is_overlay:
        d_lay.setAlignment(Qt.AlignCenter)
        d_lay.setContentsMargins(16, 16, 16, 16)
    else:
        d_lay.setContentsMargins(12, 12, 12, 12)
    d_lay.addWidget(container)
    d.exec()
