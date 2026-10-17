"""Window rounding mask + overlay scrollbar pill (no reserved strip)."""
import pytest

from ui.main_window import RoundedWindow
from ui.m3_scroll_area import M3ScrollArea
import utils.twinsock as tw


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


@pytest.fixture
def main_win(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    win = RoundedWindow()
    yield win
    try:
        win.close()
        win.deleteLater()
    except Exception:
        pass


def _tall_scroll(qapp):
    from PySide6.QtWidgets import QWidget
    scroll = M3ScrollArea()
    content = QWidget()
    content.setFixedSize(200, 1000)
    scroll.setWidget(content)
    scroll.resize(200, 400)
    scroll.show()
    qapp.processEvents()
    scroll._sync_pill()
    return scroll


def test_pill_visible_when_overflowing(qapp):
    scroll = _tall_scroll(qapp)
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > bar.minimum()
    assert scroll._pill.isVisible() is True
    vp = scroll.viewport().geometry()
    assert scroll._pill.x() + scroll._pill.width() <= vp.left() + vp.width()
    assert scroll._pill.y() >= 12
    assert scroll._pill.y() + scroll._pill.height() <= vp.top() + vp.height() - 12 + 1


def test_pill_hidden_when_content_fits(qapp):
    from PySide6.QtWidgets import QWidget
    scroll = M3ScrollArea()
    content = QWidget()
    content.setFixedSize(200, 100)
    scroll.setWidget(content)
    scroll.resize(200, 400)
    scroll.show()
    qapp.processEvents()
    scroll._sync_pill()
    assert scroll._pill.isVisible() is False


def test_pill_follows_value_and_drags(qapp):
    scroll = _tall_scroll(qapp)
    bar = scroll.verticalScrollBar()
    top_y = scroll._pill.y()

    bar.setValue(bar.maximum())
    qapp.processEvents()
    assert scroll._pill.y() > top_y

    m = scroll._pill_track()
    assert m is not None
    top, _track_h, _pill_h, vmin, vmax = m
    scroll._pill_drag_to(top)
    assert bar.value() == vmin
    scroll._pill_drag_to(top + 10 ** 6)
    assert bar.value() == vmax


def test_native_bar_takes_no_space(qapp):
    from PySide6.QtCore import Qt
    scroll = _tall_scroll(qapp)
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    # Viewport spans the full area width: no reserved strip at the edge.
    assert scroll.viewport().geometry().width() >= scroll.width() - 2


def test_container_mask_applied(main_win, qapp):
    win = main_win
    win.show()
    qapp.processEvents()
    win._layout_overlays()
    assert win.container.mask().isEmpty() is False
