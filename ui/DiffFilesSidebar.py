from __future__ import annotations

import os
from typing import Any, Optional

from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QStyledItemDelegate,
    QStyle,
)


class FileItemDelegate(QStyledItemDelegate):
    """Custom painter for file list items with status badge, change pill, and comment count."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.font_size = 12

    def paint(self, painter: QPainter, option, index):
        data = index.data(Qt.UserRole) or {}
        path = data.get("path", "")
        change_type = (data.get("change_type") or "M").upper()
        additions = data.get("additions", 0)
        deletions = data.get("deletions", 0)
        comment_count = data.get("comment_count", 0)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(4, 2, -4, -2)

        is_selected = bool(option.state & QStyle.State_Selected)
        is_hovered = bool(option.state & QStyle.State_MouseOver)

        # Background
        if is_selected:
            bg_color = QColor("#1e3a5f")
            border_color = QColor("#3b82f6")
        elif is_hovered:
            bg_color = QColor("#222733")
            border_color = QColor("#334155")
        else:
            bg_color = QColor("#181b22")
            border_color = QColor("#222732")

        painter.setBrush(bg_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(rect, 6, 6)

        x = rect.left() + 8
        y = rect.top()

        # Status badge pill (M, A, D, R)
        badge_colors = {
            "A": (QColor("#064e3b"), QColor("#34d399")),  # green
            "D": (QColor("#4c0519"), QColor("#fb7185")),  # red
            "R": (QColor("#1e1b4b"), QColor("#818cf8")),  # indigo
            "M": (QColor("#172554"), QColor("#93c5fd")),  # blue
        }
        badge_bg, badge_fg = badge_colors.get(change_type, (QColor("#334155"), QColor("#94a3b8")))

        badge_w = 18
        badge_h = 18
        badge_y = y + (rect.height() - badge_h) // 2
        badge_rect = rect.adjusted(6, (rect.height() - badge_h) // 2, 0, 0)
        badge_rect.setWidth(badge_w)
        badge_rect.setHeight(badge_h)

        painter.setBrush(badge_bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_rect, 4, 4)

        font = painter.font()
        font.setPointSize(max(9, self.font_size - 3))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(badge_fg)
        painter.drawText(badge_rect, Qt.AlignCenter, change_type)

        x = badge_rect.right() + 8

        # Right side stats: comment count and diff numbers
        right_x = rect.right() - 8
        if comment_count > 0:
            comm_text = f"💬 {comment_count}"
            comm_w = painter.fontMetrics().horizontalAdvance(comm_text) + 8
            comm_rect = rect.adjusted(0, 0, -8, 0)
            comm_rect.setLeft(right_x - comm_w)
            comm_rect.setRight(right_x)
            comm_rect.setTop(badge_y)
            comm_rect.setHeight(badge_h)

            painter.setBrush(QColor("#312e81"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(comm_rect, 4, 4)
            painter.setPen(QColor("#a5b4fc"))
            painter.drawText(comm_rect, Qt.AlignCenter, comm_text)
            right_x -= (comm_w + 6)

        # Diff stats (+N -N)
        if additions > 0 or deletions > 0:
            stats_font = QFont(font)
            stats_font.setPointSize(max(9, self.font_size - 3))
            stats_font.setBold(False)
            painter.setFont(stats_font)

            if deletions > 0:
                del_str = f"-{deletions}"
                del_w = painter.fontMetrics().horizontalAdvance(del_str)
                right_x -= del_w
                painter.setPen(QColor("#f87171"))
                painter.drawText(right_x, y, del_w, rect.height(), Qt.AlignVCenter | Qt.AlignRight, del_str)
                right_x -= 4

            if additions > 0:
                add_str = f"+{additions}"
                add_w = painter.fontMetrics().horizontalAdvance(add_str)
                right_x -= add_w
                painter.setPen(QColor("#4ade80"))
                painter.drawText(right_x, y, add_w, rect.height(), Qt.AlignVCenter | Qt.AlignRight, add_str)
                right_x -= 8

        # File name / path in middle
        text_width = max(20, right_x - x)
        filename = os.path.basename(path)
        dirname = os.path.dirname(path)

        path_font = QFont(font)
        path_font.setPointSize(max(10, self.font_size - 2))
        path_font.setBold(is_selected)
        painter.setFont(path_font)

        # Draw filename bold, dir muted
        painter.setPen(QColor("#f1f5f9") if is_selected else QColor("#cbd5e1"))
        fn_w = painter.fontMetrics().horizontalAdvance(filename)

        if dirname:
            full_str = f"{filename} ({dirname})"
            elided = painter.fontMetrics().elidedText(full_str, Qt.ElideMiddle, text_width)
            painter.drawText(x, y, text_width, rect.height(), Qt.AlignVCenter | Qt.AlignLeft, elided)
        else:
            elided = painter.fontMetrics().elidedText(filename, Qt.ElideRight, text_width)
            painter.drawText(x, y, text_width, rect.height(), Qt.AlignVCenter | Qt.AlignLeft, elided)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, max(38, self.font_size + 26))


class DiffFilesSidebar(QWidget):
    """Sidebar widget that lists changed files with quick filtering and navigation."""

    fileSelected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list[dict[str, Any]] = []
        self._font_size = 12

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header bar
        header_layout = QHBoxLayout()
        self.title_label = QLabel("CHANGED FILES", self)
        self.title_label.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 11px; letter-spacing: 0.5px;")

        self.stats_label = QLabel("", self)
        self.stats_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")

        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.stats_label)
        layout.addLayout(header_layout)

        # Filter input
        self.filter_input = QLineEdit(self)
        self.filter_input.setPlaceholderText("Filter files...")
        self.filter_input.setStyleSheet(
            "QLineEdit {"
            "  background-color: #1a1d24;"
            "  border: 1px solid #2e3440;"
            "  border-radius: 6px;"
            "  color: #e2e8f0;"
            "  padding: 5px 8px;"
            "  font-size: 11px;"
            "}"
            "QLineEdit:focus { border-color: #3b82f6; }"
        )
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input)

        # Files list
        self.list_widget = QListWidget(self)
        self.list_widget.setStyleSheet(
            "QListWidget {"
            "  background-color: #12141a;"
            "  border: 1px solid #222732;"
            "  border-radius: 6px;"
            "  padding: 4px;"
            "}"
            "QListWidget::item { border: none; margin-bottom: 2px; }"
        )
        self.list_widget.setItemDelegate(FileItemDelegate(self.list_widget))
        self.list_widget.setSpacing(3)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def set_files(self, files: list[dict[str, Any]]) -> None:
        self._files = list(files)
        total_add = sum(f.get("additions", 0) for f in files)
        total_del = sum(f.get("deletions", 0) for f in files)
        self.title_label.setText(f"CHANGED FILES ({len(files)})")
        self.stats_label.setText(f"+{total_add}  -{total_del}")
        self._populate_list(self._files)

    def select_file(self, file_path: str) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            data = item.data(Qt.UserRole) or {}
            if data.get("path") == file_path:
                self.list_widget.setCurrentItem(item)
                break

    def _populate_list(self, files: list[dict[str, Any]]) -> None:
        self.list_widget.clear()
        for f in files:
            item = QListWidgetItem(f.get("path") or "")
            item.setData(Qt.UserRole, f)
            item.setToolTip(f.get("path") or "")
            item.setSizeHint(QSize(200, max(38, self._font_size + 26)))
            self.list_widget.addItem(item)

    def set_font_size(self, size: int):
        self._font_size = size
        self.list_widget.itemDelegate().font_size = size
        self.title_label.setStyleSheet(f"color: #94a3b8; font-weight: 700; font-size: {max(11, size - 2)}px;")
        self.stats_label.setStyleSheet(f"color: #94a3b8; font-size: {max(10, size - 2)}px;")
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setSizeHint(QSize(200, max(38, size + 26)))
        self.list_widget.doItemsLayout()
        self.list_widget.viewport().update()

    def _apply_filter(self, query: str) -> None:
        query = (query or "").strip().lower()
        if not query:
            self._populate_list(self._files)
            return

        filtered = [
            f for f in self._files
            if query in f.get("path", "").lower()
        ]
        self._populate_list(filtered)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        path = data.get("path", "")
        if path:
            self.fileSelected.emit(path)
