from __future__ import annotations

import re

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


class SearchableComboBox(QComboBox):
    """Combo box with a keyboard searchable popup and stable item data."""

    def __init__(self, parent=None, *, search_placeholder="Search..."):
        super().__init__(parent)
        self._popup = QFrame(self, Qt.Popup | Qt.FramelessWindowHint)
        self._popup.setObjectName("searchPopup")
        layout = QVBoxLayout(self._popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.search_input = QLineEdit(self._popup)
        self.search_input.setPlaceholderText(search_placeholder)
        self.search_input.textChanged.connect(self._filter_items)
        self.search_input.installEventFilter(self)
        layout.addWidget(self.search_input)
        self.search_list = QListWidget(self._popup)
        self.search_list.itemClicked.connect(self._choose_item)
        self.search_list.itemActivated.connect(self._choose_item)
        self.search_list.installEventFilter(self)
        layout.addWidget(self.search_list)
        self._popup.setStyleSheet(
            "QFrame#searchPopup { background: #161821; border: 1px solid #3b82f6; border-radius: 7px; }"
            "QLineEdit { background: #11151e; color: #f1f5f9; border: 1px solid #334155; padding: 7px; }"
            "QListWidget { background: #11151e; color: #e2e8f0; border: 1px solid #273244; }"
            "QListWidget::item { padding: 7px; }"
            "QListWidget::item:selected { background: #1e40af; color: white; }"
        )

    def showPopup(self):
        self.search_input.clear()
        self._filter_items("")
        position = self.mapToGlobal(QPoint(0, self.height()))
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        width = min(max(self.width(), 620), geometry.width())
        height = min(360, geometry.height())
        x = max(geometry.left(), min(position.x(), geometry.right() - width + 1))
        y = position.y() if position.y() + height <= geometry.bottom() else self.mapToGlobal(QPoint(0, 0)).y() - height
        y = max(geometry.top(), y)
        self._popup.resize(width, height)
        self._popup.move(x, y)
        self._popup.show()
        self.search_input.setFocus()

    def hidePopup(self):
        self._popup.hide()

    def _filter_items(self, query: str):
        terms = re.findall(r"[\w-]+", query.casefold())
        matches = []
        for index in range(self.count()):
            if self.itemData(index) is None:
                continue
            label = self.itemText(index)
            search_text = (label + " " + str(self.itemData(index, Qt.UserRole + 1) or "")).casefold()
            if all(term in search_text for term in terms):
                score = 0
                if terms:
                    score += 0 if search_text.startswith(terms[0]) else 1
                    score += sum(search_text.find(term) for term in terms)
                matches.append((score, index, label))
        matches.sort(key=lambda item: (item[0], item[1]))
        self.search_list.clear()
        for _, index, label in matches:
            item = QListWidgetItem(label, self.search_list)
            item.setData(Qt.UserRole, index)
        if matches:
            self.search_list.setCurrentRow(0)
        else:
            item = QListWidgetItem("No matching items", self.search_list)
            item.setFlags(Qt.NoItemFlags)

    def _choose_item(self, item):
        index = item.data(Qt.UserRole)
        if isinstance(index, int):
            self.setCurrentIndex(index)
            self._popup.hide()

    def eventFilter(self, source, event):
        if source in (self.search_input, self.search_list) and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._popup.hide()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                item = self.search_list.currentItem()
                if item:
                    self._choose_item(item)
                return True
            if source is self.search_input and event.key() == Qt.Key_Down:
                self.search_list.setFocus()
                return True
            if source is self.search_list and event.text() and event.text().isprintable():
                self.search_input.setFocus()
                self.search_input.insert(event.text())
                return True
        return super().eventFilter(source, event)
