from typing import Optional, Callable
from PySide6.QtWidgets import QScrollArea
from PySide6.QtCore import QPoint

from utils.server_model import server_identity
from .server_item import ServerItem
from .group_header_card import GroupHeaderCard
from .virtual_server_group import VirtualGroup


class WidgetPool:
    """Pool of detached server item and group card widgets for reuse during refresh."""

    def __init__(self, items: list[ServerItem], cards: list[GroupHeaderCard]):
        self.pool_by_addr = {id(it.server): it for it in items}
        self.pool_by_key: dict[str, list[ServerItem]] = {}
        for it in items:
            self.pool_by_key.setdefault(server_identity(it.server), []).append(it)
        self.card_pool: dict[str, list[GroupHeaderCard]] = {}
        for card in cards:
            self.card_pool.setdefault(card.key, []).append(card)

    def take_item(self, srv) -> Optional[ServerItem]:
        it = self.pool_by_addr.pop(id(srv), None)
        if it is not None:
            lst = self.pool_by_key.get(server_identity(it.server))
            if lst:
                try:
                    lst.remove(it)
                except ValueError:
                    pass
            return it
        lst = self.pool_by_key.get(server_identity(srv))
        while lst:
            cand = lst.pop(0)
            if self.pool_by_addr.pop(id(cand.server), None) is not None:
                return cand
        return None

    def take_card(self, gkey: str) -> Optional[GroupHeaderCard]:
        lst = self.card_pool.get(gkey)
        return lst.pop(0) if lst else None


def create_or_update_group_card(group: dict, card: Optional[GroupHeaderCard], theme, panel) -> GroupHeaderCard:
    """Create a new GroupHeaderCard or update an existing detached one."""
    gkey = group.get("key", "")
    is_pinned = bool(group.get("is_pinned", False))
    if card is None:
        card = GroupHeaderCard(
            key=gkey,
            title=group.get("title", ""),
            icon=group.get("icon", "🌐"),
            subtitle=group.get("subtitle", ""),
            is_direct=group.get("is_direct", False),
            is_pinned=is_pinned,
            theme=theme,
        )
        card.collapseToggled.connect(panel.groupCollapseToggled.emit)
        card.pingGroupRequested.connect(panel.groupPingRequested.emit)
        card.refreshRequested.connect(panel.groupRefreshRequested.emit)
        card.editRequested.connect(panel.groupEditRequested.emit)
        if hasattr(panel, "groupPinToggled"):
            card.pinToggled.connect(panel.groupPinToggled.emit)
        if hasattr(panel, "groupDeleteRequested"):
            card.deleteRequested.connect(panel.groupDeleteRequested.emit)
    else:
        card.key = gkey
        card.update_group(
            group.get("title", ""),
            group.get("icon", "🌐"),
            group.get("subtitle", ""),
            bool(group.get("is_direct", False)),
            is_pinned=is_pinned,
        )
    return card


class ServerListVirtualizer:
    """Manages virtual scrolling, viewport calculation, and item recycling for ServerListPanel."""

    def __init__(self, panel):
        self.panel = panel
        self.connected_scroll_area: Optional[QScrollArea] = None
        self.virtual_groups: list[VirtualGroup] = []
        self.item_pool: list[ServerItem] = []

    def get_scroll_area(self) -> Optional[QScrollArea]:
        p = self.panel.parentWidget()
        while p is not None:
            if isinstance(p, QScrollArea):
                return p
            p = p.parentWidget()
        return None

    def ensure_scroll_connected(self, on_scroll: Callable):
        sa = self.get_scroll_area()
        if sa is not None and sa != self.connected_scroll_area:
            if self.connected_scroll_area is not None:
                try:
                    self.connected_scroll_area.verticalScrollBar().valueChanged.disconnect(on_scroll)
                except Exception:
                    pass
            self.connected_scroll_area = sa
            sa.verticalScrollBar().valueChanged.connect(on_scroll)

    def calculate_viewport_range(self) -> tuple[int, int]:
        sa = self.get_scroll_area()
        if sa is None or not self.panel.isVisible():
            return 0, 1500
        viewport = sa.viewport()
        top_in_panel = self.panel.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        visible_top = max(0, -top_in_panel)
        visible_bottom = visible_top + viewport.height()
        return visible_top, visible_bottom

    def pop_item(self, srv) -> ServerItem:
        if self.item_pool:
            return self.item_pool.pop()
        return ServerItem(
            srv.name, srv, self.panel.theme,
            ping_display_mode=self.panel._ping_display_mode,
            show_flags=self.panel._show_flags
        )

    def sync_group_widgets(self, vg: VirtualGroup, new_first: int, new_last: int, take_fn: Optional[Callable] = None):
        layout = self.panel.layout()
        old_items = list(vg.items)
        vg.items.clear()
        for it in old_items:
            self.panel._button_group.removeButton(it.radio)
            layout.removeWidget(it)
            it.hide()
            self.item_pool.append(it)

        vg.update_range(new_first, new_last)

        bot_idx = layout.indexOf(vg.bottom_spacer)
        idx_in_layout = bot_idx if bot_idx >= 0 else layout.count()

        for srv_idx in range(new_first, new_last):
            srv = vg.servers[srv_idx]
            flat_idx = vg.flat_indices.get(id(srv), -1)
            item = take_fn(srv) if take_fn else self.pop_item(srv)
            if item is None:
                item = self.pop_item(srv)
            self.panel._setup_virtual_item(item, srv, flat_idx)
            layout.insertWidget(idx_in_layout, item)
            item.show()
            vg.items.append(item)
            idx_in_layout += 1

    def update_virtual_window(self, take_fn: Optional[Callable] = None):
        if not self.virtual_groups:
            return
        visible_top, visible_bottom = self.calculate_viewport_range()
        curr_y = 0
        for vg in self.virtual_groups:
            if not vg.is_expanded or not vg.servers:
                self.sync_group_widgets(vg, 0, 0, take_fn)
                continue
            card_h = (vg.card.height() or 56) if vg.card else 0
            card_spacing = 6 if vg.card else 0
            group_y = curr_y + card_h + card_spacing
            total_h = vg.total_height()
            curr_y = group_y + total_h + 6

            if group_y >= visible_bottom:
                self.sync_group_widgets(vg, 0, 0, take_fn)
            elif group_y + total_h <= visible_top:
                self.sync_group_widgets(vg, len(vg.servers), len(vg.servers), take_fn)
            else:
                rel_top = max(0, visible_top - group_y)
                rel_bottom = min(total_h, visible_bottom - group_y)
                first = max(0, int(rel_top // vg.ROW_STEP) - 4)
                last = min(len(vg.servers), int(rel_bottom // vg.ROW_STEP) + 5)
                self.sync_group_widgets(vg, first, last, take_fn)
        self.panel._server_items = [it for vg in self.virtual_groups for it in vg.items]

    def clear_spacers(self):
        layout = self.panel.layout()
        for vg in self.virtual_groups:
            if vg.top_spacer.parent() is not None:
                layout.removeWidget(vg.top_spacer)
                vg.top_spacer.deleteLater()
            if vg.bottom_spacer.parent() is not None:
                layout.removeWidget(vg.bottom_spacer)
                vg.bottom_spacer.deleteLater()
        self.virtual_groups.clear()
