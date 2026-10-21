from typing import Optional
from PySide6.QtWidgets import QWidget


class VirtualGroup:
    """Manages virtualized window geometry and spacers for a group of server items."""

    ROW_HEIGHT = 48
    SPACING = 6
    ROW_STEP = ROW_HEIGHT + SPACING

    def __init__(self, key: str, servers: list, flat_indices: dict, is_expanded: bool = True):
        self.key = key
        self.servers = list(servers)
        self.flat_indices = flat_indices
        self.is_expanded = is_expanded
        self.card = None
        self.first_idx = 0
        self.last_idx = 0
        self.items: list = []
        self.top_spacer = QWidget()
        self.top_spacer.setFixedHeight(0)
        self.top_spacer.hide()
        self.bottom_spacer = QWidget()
        self.bottom_spacer.setFixedHeight(0)
        self.bottom_spacer.hide()

    def total_count(self) -> int:
        return len(self.servers) if self.is_expanded else 0

    def total_height(self) -> int:
        if not self.is_expanded:
            return 0
        return len(self.servers) * self.ROW_STEP

    def update_range(self, first: int, last: int):
        first = max(0, min(len(self.servers), first))
        last = max(first, min(len(self.servers), last))
        self.first_idx = first
        self.last_idx = last

        top_h = first * self.ROW_STEP
        self.top_spacer.setFixedHeight(top_h)
        self.top_spacer.setVisible(top_h > 0)

        bot_h = (len(self.servers) - last) * self.ROW_STEP
        self.bottom_spacer.setFixedHeight(bot_h)
        self.bottom_spacer.setVisible(bot_h > 0)
