from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGraphicsOpacityEffect, QButtonGroup, QLabel,
    QSpacerItem, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, Slot, QPropertyAnimation, QThreadPool, QPoint

try:
    import shiboken6
except ImportError:
    shiboken6 = None
from .server_item import ServerItem
from .group_header_card import GroupHeaderCard
from .virtual_server_group import VirtualGroup
from .server_list_virtualizer import ServerListVirtualizer, create_or_update_group_card, WidgetPool
from utils.ping import AsyncBatchPingJob
from utils.server_model import server_identity
from utils.i18n import tr
from utils.icons import set_label_icon


class ServerListPanel(QWidget):
    """Flat group-aware server list (Android RecyclerView equivalent).

    Rendered inside ScreenMain's single scroll area, so the hero above scrolls
    away exactly like the Android AppBarLayout. No action bar and no internal
    search bar: those live in the sticky header (search) and the group cards
    (per-subscription actions).

    """
    serverSelected = Signal(int)
    serverDeleted = Signal(object)
    groupCollapseToggled = Signal(str)
    groupPingRequested = Signal(str)
    groupRefreshRequested = Signal(str)
    groupEditRequested = Signal(str)
    pingResultReceived = Signal(object, float)
    pingFinished = Signal(str)

    def __init__(self, theme, parent=None, ping_display_mode: int = 0, show_flags: bool = False):
        super().__init__(parent)
        self.theme = theme
        self._ping_display_mode = int(ping_display_mode)
        self._show_flags = bool(show_flags)
        self._server_items = []
        self._group_cards = []
        self._virtualizer = ServerListVirtualizer(self)
        self._selected_index: int = -1
        self._selected_key: str | None = None
        self._fade_out_cb = None
        self._query = ""
        self._last_servers = []
        self._last_groups = None
        self._last_connected_key = None
        self._ping_pending: set = set()
        self._setup_ui()
        self.apply_theme(theme)

    @property
    def _virtual_groups(self):
        return self._virtualizer.virtual_groups

    @_virtual_groups.setter
    def _virtual_groups(self, val):
        self._virtualizer.virtual_groups = val

    @property
    def _item_pool(self):
        return self._virtualizer.item_pool

    @_item_pool.setter
    def _item_pool(self, val):
        self._virtualizer.item_pool = val

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._bottom_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self._opacity_effect = self._new_opacity_effect()
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(200)
        self._fade_anim.finished.connect(self._on_fade_anim_finished)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.buttonToggled.connect(self._on_button_toggled)
        self.pingResultReceived.connect(self._apply_ping_result)

        self._empty_widget = QWidget(self)
        self._empty_widget.setObjectName("emptyServerList")
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setContentsMargins(20, 6, 20, 36)
        empty_layout.setSpacing(0)
        empty_layout.setAlignment(Qt.AlignCenter)

        self._empty_icon = QLabel(self._empty_widget)
        self._empty_icon.setAlignment(Qt.AlignCenter)
        self._empty_icon.setFixedSize(48, 48)
        empty_layout.addWidget(self._empty_icon, 0, Qt.AlignCenter)
        empty_layout.addSpacing(20)

        self._empty_title = QLabel(self._empty_widget)
        self._empty_title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self._empty_title, 0, Qt.AlignCenter)
        empty_layout.addSpacing(8)

        self._empty_hint = QLabel(self._empty_widget)
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setWordWrap(True)
        empty_layout.addWidget(self._empty_hint, 0, Qt.AlignCenter)

        self._empty_widget.hide()

    def _new_opacity_effect(self):
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(1.0)
        return eff

    def _on_fade_anim_finished(self):
        if self._fade_anim.endValue() >= 1.0:
            # Fully opaque again: drop the effect (Qt deletes it) so list
            # repaints go straight to the backing store - a persistent
            # opacity effect leaves ghost rows after a rebuild.
            if self.graphicsEffect() is not None:
                self.setGraphicsEffect(None)
                self._opacity_effect = self._new_opacity_effect()
                self._fade_anim.setTargetObject(self._opacity_effect)
            return
        cb = self._fade_out_cb
        self._fade_out_cb = None
        if cb:
            cb()

    def _on_button_toggled(self, button, checked):
        if checked:
            idx = self._button_group.id(button)
            if idx >= 0:
                self._selected_index = idx
                if 0 <= idx < len(self._last_servers):
                    self._selected_key = server_identity(self._last_servers[idx])
                self.serverSelected.emit(idx)

    def refresh(self, servers, connected_server_key=None, groups=None):
        """Rebuild the list, reusing existing rows and cards when possible.

        ``groups`` (optional) is a list of dicts describing subscription group
        cards: key, title, icon, subtitle, is_direct, collapsed, servers.
        The flattened display order must match ``servers`` (index identity is
        what ``serverSelected``/``get_selected_index`` report).

        Rows are matched to the new data by stable server identity and rebound
        in place (ServerItem.update_server): only genuinely new rows are
        created and only vanished ones deleted. A rebuild therefore never
        holds the old list and the new list at once (no ~2x memory peak) and
        avoids re-rendering icons/styles for unchanged rows.
        """
        self._last_servers = list(servers)
        self._last_groups = groups
        self._last_connected_key = connected_server_key

        layout = self.layout()
        self.setUpdatesEnabled(False)
        self._button_group.blockSignals(True)
        try:
            for b in self._button_group.buttons():
                self._button_group.removeButton(b)
            layout.removeItem(self._bottom_spacer)
            self._virtualizer.clear_spacers()
            # Detach everything first WITHOUT deleting: reusable widgets are
            # re-added below in the new order, so peak memory stays ~1x.
            detached_items: list = []
            detached_cards: list = []
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if isinstance(w, ServerItem):
                    detached_items.append(w)
                elif isinstance(w, GroupHeaderCard):
                    detached_cards.append(w)
                elif w is self._empty_widget:
                    pass
                elif w is not None:
                    w.deleteLater()
                else:
                    if item is not getattr(self, "_bottom_spacer", None):
                        if shiboken6 is not None:
                            try:
                                if shiboken6.isValid(item):
                                    shiboken6.delete(item)
                            except Exception:
                                pass
                    del item

            pool = WidgetPool(detached_items, detached_cards)
            self._server_items = []
            self._group_cards = []

            flat_index = {id(s): i for i, s in enumerate(servers)}
            query = self._query.strip().lower()

            total_visible = 0
            if groups:
                for g in groups:
                    exp = (not bool(g.get("collapsed", False))) or bool(query)
                    if exp:
                        total_visible += len(self._filter_group(g.get("servers", []), query))
            else:
                total_visible = len(self._filter_group(servers, query))

            is_virtual = total_visible > 30
            if is_virtual:
                self._ensure_scroll_connected()

            if groups:
                for group in groups:
                    group_servers = group.get("servers", [])
                    visible = self._filter_group(group_servers, query)
                    if query and not visible:
                        continue

                    gkey = group.get("key", "")
                    card = create_or_update_group_card(group, pool.take_card(gkey), self.theme, self)
                    expanded = (not bool(group.get("collapsed", False))) or bool(query)
                    card.set_expanded(expanded)
                    layout.addWidget(card)
                    self._group_cards.append(card)

                    if not expanded:
                        continue

                    if is_virtual:
                        vg = VirtualGroup(gkey, visible, flat_index, is_expanded=expanded)
                        vg.card = card
                        layout.addWidget(vg.top_spacer)
                        layout.addWidget(vg.bottom_spacer)
                        self._virtual_groups.append(vg)
                    else:
                        for srv in visible:
                            self._place_server_item(srv, connected_server_key, flat_index, pool.take_item)
            else:
                visible = self._filter_group(servers, query)
                if is_virtual:
                    vg = VirtualGroup("", visible, flat_index, is_expanded=True)
                    layout.addWidget(vg.top_spacer)
                    layout.addWidget(vg.bottom_spacer)
                    self._virtual_groups.append(vg)
                else:
                    for srv in visible:
                        self._place_server_item(srv, connected_server_key, flat_index, pool.take_item)

            if is_virtual:
                self._update_virtual_window_sync(pool.take_item)

            # Deletion is driven by the detached widgets, not by the match
            # pools: a row can be indexed under both its key and its address,
            # so pooling leftovers would delete it twice. Stopping the ping
            # timer keeps it from ticking until deleteLater() takes effect.
            placed_items = {id(it) for it in self._server_items}
            for it in detached_items:
                if id(it) in placed_items:
                    continue
                it.stop_ping_animation()
                if total_visible > 30 and len(self._item_pool) < 25:
                    it.hide()
                    self._item_pool.append(it)
                else:
                    it.deleteLater()
            placed_cards = {id(card) for card in self._group_cards}
            for card in detached_cards:
                if id(card) not in placed_cards:
                    card.deleteLater()

            if not self._server_items and not self._group_cards:
                self._update_empty_view()
                layout.addWidget(self._empty_widget)
                self._empty_widget.show()
            else:
                self._empty_widget.hide()

            layout.addItem(self._bottom_spacer)
            self._filter_current(query)
        finally:
            self._button_group.blockSignals(False)
            self.setUpdatesEnabled(True)

    def _filter_group(self, servers, query: str):
        if not query:
            return list(servers)
        out = []
        for s in servers:
            name = (getattr(s, "name", "") or "").lower()
            host = (getattr(s, "host", "") or "").lower()
            proto = getattr(s, "protocol", None)
            proto_label = (getattr(proto, "value", "") or "").lower()
            flag = (getattr(s, "flag", "") or "").lower()
            if query in name or query in host or query in proto_label or query in flag:
                out.append(s)
        return out

    def _place_server_item(self, server, connected_server_key, flat_index, take_fn):
        """Add a row for ``server``, reusing a detached row when available."""
        item = take_fn(server)
        if item is None:
            item = ServerItem(server.name, server, self.theme, ping_display_mode=self._ping_display_mode,
                              show_flags=self._show_flags)
        else:
            # A rebound row must not keep the previous row's checked state;
            # the connected marker / selection restore below decide it.
            item.radio.setChecked(False)
            item.update_server(server.name, server)
        self._wire_delete_button(item, server)
        idx = flat_index.get(id(server), -1)
        if idx >= 0:
            self._button_group.addButton(item.radio, idx)
        self.layout().addWidget(item)
        self._server_items.append(item)
        last_ms = getattr(server, "_last_ping", None)
        if last_ms is not None:
            item.set_ping(last_ms)
        elif id(server) in self._ping_pending:
            item.start_ping_animation()
        is_selected = False
        if connected_server_key and getattr(server, "key", None) == connected_server_key:
            is_selected = True
        elif idx >= 0 and self._selected_index == idx:
            is_selected = True
        elif self._selected_key and server_identity(server) == self._selected_key:
            is_selected = True
        if is_selected:
            item.radio.setChecked(True)

    def _wire_delete_button(self, item, server):
        """Point the row's delete action at ``server`` (reconnect on reuse)."""
        try:
            if item._delete_handler is not None:
                item.delete_button.clicked.disconnect(item._delete_handler)
        except (AttributeError, RuntimeError, TypeError):
            pass
        item._delete_handler = lambda checked=False, srv=server: self._emit_deleted(srv)
        item.delete_button.clicked.connect(item._delete_handler)

    def _ensure_scroll_connected(self):
        self._virtualizer.ensure_scroll_connected(self._on_viewport_scrolled)

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_scroll_connected()
        self._on_viewport_scrolled()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._on_viewport_scrolled()

    def _update_virtual_window_sync(self, take_fn=None):
        self._virtualizer.update_virtual_window(take_fn)

    def _sync_virtual_group_widgets(self, vg, new_first, new_last, take_fn=None):
        self._virtualizer.sync_group_widgets(vg, new_first, new_last, take_fn)

    def _on_viewport_scrolled(self, _val=None):
        if not self._virtualizer.virtual_groups:
            return
        self.setUpdatesEnabled(False)
        self._button_group.blockSignals(True)
        try:
            self._virtualizer.update_virtual_window()
        finally:
            self._button_group.blockSignals(False)
            self.setUpdatesEnabled(True)

    def _setup_virtual_item(self, item, srv, flat_idx):
        item.radio.setChecked(False)
        item.update_server(srv.name, srv, show_flags=self._show_flags)
        self._wire_delete_button(item, srv)
        if flat_idx >= 0:
            self._button_group.addButton(item.radio, flat_idx)
        last_ms = getattr(srv, "_last_ping", None)
        if last_ms is not None:
            item.set_ping(last_ms)
        elif id(srv) in self._ping_pending:
            item.start_ping_animation()
        is_selected = False
        if self._last_connected_key and getattr(srv, "key", None) == self._last_connected_key:
            is_selected = True
        elif flat_idx >= 0 and self._selected_index == flat_idx:
            is_selected = True
        elif self._selected_key and server_identity(srv) == self._selected_key:
            is_selected = True
        if is_selected:
            item.radio.setChecked(True)

    def set_ping_display_mode(self, mode: int):
        self._ping_display_mode = int(mode)
        for item in self._server_items:
            item.set_ping_display_mode(self._ping_display_mode)
        for item in self._item_pool:
            item.set_ping_display_mode(self._ping_display_mode)

    def set_show_flags(self, show: bool):
        """Show or hide country flags in all rows (mobile showFlags parity)."""
        self._show_flags = bool(show)
        for item in self._server_items:
            item.set_show_flags(self._show_flags)
        for item in self._item_pool:
            item.set_show_flags(self._show_flags)

    def _emit_deleted(self, server):
        # The row owns its server object: emit it directly instead of a
        # flat-list index. The flat list mixes manual and subscription
        # servers (and depends on filter/collapse state), while deletion
        # targets the manual list — an index cannot identify the row.
        self.serverDeleted.emit(server)

    def set_query(self, text: str):
        """Search from the sticky header; searching implicitly expands groups."""
        text = text or ""
        if text == self._query:
            return
        self._query = text
        self.refresh(self._last_servers, self._last_connected_key, self._last_groups)



    def _filter_current(self, query: str):
        for item in self._server_items:
            if not query:
                item.setVisible(True)
                continue
            name = (item.radio.text() or "").lower()
            host = (getattr(item.server, "host", "") or "").lower()
            item.setVisible(query in name or query in host)

    @Slot(object, float)
    def _apply_ping_result(self, server_id: int, ms: float):
        self._ping_pending.discard(server_id)
        val = ms if (ms is not None and ms >= 0) else -1.0
        for item in self._server_items:
            if id(item.server) == server_id:
                try:
                    setattr(item.server, "_last_ping", val)
                    item.set_ping(val)
                except RuntimeError:
                    pass
                return
        # Row not visible (collapsed group or active filter): keep the
        # result on the model so expanding/clearing the filter shows it.
        for srv in self._last_servers:
            if id(srv) == server_id:
                try:
                    setattr(srv, "_last_ping", val)
                except (AttributeError, TypeError):
                    pass
                break

    def ping_all(self, method, socks5_port, servers=None, group_key: str = "",
                 timeout: float | None = None, url: str | None = None):
        """Ping ``servers`` in the global thread pool (never blocks the UI).

        Starts the per-row busy animation up front; each result stops its
        row's animation via :meth:`_apply_ping_result`. Emits
        :attr:`pingFinished` with ``group_key`` once every probe reported.
        """
        targets = servers if servers is not None else [item.server for item in self._server_items]
        if not targets:
            return
        target_ids = {id(s) for s in targets}
        self._ping_pending.update(target_ids)
        for item in self._server_items:
            if id(item.server) in target_ids:
                item.start_ping_animation()
        target_keys = [id(s) for s in targets]
        total = len(target_keys)
        done = [0]

        def _on_result(index, ms, _keys=target_keys):
            if not (0 <= index < len(_keys)):
                return
            key = _keys[index]
            try:
                self.pingResultReceived.emit(key, float(ms if ms is not None else -1.0))
            finally:
                done[0] += 1
                if done[0] >= total:
                    self.pingFinished.emit(group_key or "")

        job = AsyncBatchPingJob(
            targets,
            callback=_on_result,
            method=method,
            socks5_port=socks5_port,
            timeout=timeout,
            url=url,
        )
        QThreadPool.globalInstance().start(job)

    def _is_index_visible(self, idx: int) -> bool:
        if not (0 <= idx < len(self._last_servers)):
            return False
        srv = self._last_servers[idx]
        if self._last_groups:
            query = self._query.strip().lower()
            for g in self._last_groups:
                expanded = (not bool(g.get("collapsed", False))) or bool(query)
                if any(id(s) == id(srv) for s in g.get("servers", [])):
                    return expanded
        return True

    def get_selected_index(self):
        btn = self._button_group.checkedButton()
        if btn:
            return self._button_group.id(btn)
        if self._selected_index >= 0 and self._is_index_visible(self._selected_index):
            return self._selected_index
        return -1

    def set_selected_index(self, idx: int) -> bool:
        """Check the radio for flat ``idx`` without emitting ``serverSelected``.

        Returns True when a matching visible button was checked. Used to
        restore the selection after :meth:`refresh` rebuilds the rows
        (collapse toggle, subscription update, ...).
        """
        self._selected_index = idx
        if 0 <= idx < len(self._last_servers):
            self._selected_key = server_identity(self._last_servers[idx])
        btn = self._button_group.button(idx)
        if btn is None:
            return 0 <= idx < len(self._last_servers)
        self._button_group.blockSignals(True)
        try:
            btn.setChecked(True)
        finally:
            self._button_group.blockSignals(False)
        return True

    def fade_out(self, on_done=None):
        self._fade_anim.stop()
        if self.graphicsEffect() is not self._opacity_effect:
            self.setGraphicsEffect(self._opacity_effect)
        self._fade_out_cb = on_done
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()



    def apply_theme(self, theme):
        self.theme = theme
        self._update_empty_view()
        for card in self._group_cards:
            card.apply_theme(theme)
        for item in self._server_items:
            item.apply_theme(theme)
        for item in self._item_pool:
            item.apply_theme(theme)

    def _update_empty_view(self):
        query = self._query.strip()
        if query:
            self._empty_title.setText(tr("servers.search_empty"))
            self._empty_hint.setText(tr("servers.search_empty_hint"))
            icon_name = "search"
            fallback = "🔍"
        else:
            self._empty_title.setText(tr("servers.empty_title"))
            self._empty_hint.setText(tr("servers.empty_hint"))
            icon_name = "tab_servers"
            fallback = "🌐"

        theme = self.theme
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")
        self._empty_title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {text_hex}; background: transparent;"
        )
        self._empty_hint.setStyleSheet(
            f"font-size: 13px; color: {muted_hex}; background: transparent;"
        )
        set_label_icon(self._empty_icon, icon_name, muted_hex, 44, fallback_text=fallback)
