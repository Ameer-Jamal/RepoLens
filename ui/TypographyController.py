from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QToolButton


class TypographyController(QObject):
    """Manages monospace typography, font scaling, and persistence for diff views."""

    fontSizeChanged = pyqtSignal(int)

    MIN_FONT_SIZE = 10
    MAX_FONT_SIZE = 22
    DEFAULT_FONT_SIZE = 13

    PREFERRED_FONTS = [
        "JetBrains Mono",
        "Fira Code",
        "Cascadia Code",
        "SF Mono",
        "Menlo",
        "Consolas",
        "Source Code Pro",
        "Courier New",
    ]

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._font_family = self._detect_best_monospace_font()
        self._font_size = self._load_initial_font_size()

    def _load_initial_font_size(self) -> int:
        if self.config_manager and hasattr(self.config_manager, "get_diff_font_size"):
            try:
                return int(self.config_manager.get_diff_font_size())
            except (ValueError, TypeError):
                pass
        return self.DEFAULT_FONT_SIZE

    def _detect_best_monospace_font(self) -> str:
        db = QFontDatabase()
        families = set(db.families())
        for preferred in self.PREFERRED_FONTS:
            if preferred in families:
                return preferred
        return QFontDatabase.systemFont(QFontDatabase.FixedFont).family()

    @property
    def font_family(self) -> str:
        return self._font_family

    @property
    def font_size(self) -> int:
        return self._font_size

    def get_font(self, weight=QFont.Normal) -> QFont:
        font = QFont(self._font_family, self._font_size)
        font.setStyleHint(QFont.Monospace)
        font.setWeight(weight)
        return font

    def set_font_size(self, size: int) -> None:
        clamped = max(self.MIN_FONT_SIZE, min(self.MAX_FONT_SIZE, size))
        if clamped != self._font_size:
            self._font_size = clamped
            if self.config_manager and hasattr(self.config_manager, "set_diff_font_size"):
                self.config_manager.set_diff_font_size(clamped)
            self.fontSizeChanged.emit(clamped)

    def zoom_in(self) -> None:
        self.set_font_size(self._font_size + 1)

    def zoom_out(self) -> None:
        self.set_font_size(self._font_size - 1)

    def reset_zoom(self) -> None:
        self.set_font_size(self.DEFAULT_FONT_SIZE)

    def create_toolbar_widget(self, parent=None) -> QWidget:
        container = QWidget(parent)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        btn_style = (
            "QToolButton {"
            "  background-color: #262a33;"
            "  color: #e2e8f0;"
            "  border: 1px solid #3b4252;"
            "  border-radius: 4px;"
            "  font-weight: bold;"
            "  padding: 3px 8px;"
            "  min-width: 20px;"
            "}"
            "QToolButton:hover { background-color: #353b49; border-color: #4c566a; }"
            "QToolButton:pressed { background-color: #1e222a; }"
        )

        zoom_out_btn = QToolButton(container)
        zoom_out_btn.setText("A-")
        zoom_out_btn.setToolTip("Decrease diff font size (Ctrl + -)")
        zoom_out_btn.setStyleSheet(btn_style)
        zoom_out_btn.clicked.connect(self.zoom_out)

        size_label = QLabel(f"{self._font_size}px", container)
        size_label.setStyleSheet(
            "color: #94a3b8; font-size: 11px; font-weight: 600; padding: 0 4px;"
        )

        zoom_in_btn = QToolButton(container)
        zoom_in_btn.setText("A+")
        zoom_in_btn.setToolTip("Increase diff font size (Ctrl + +)")
        zoom_in_btn.setStyleSheet(btn_style)
        zoom_in_btn.clicked.connect(self.zoom_in)

        self.fontSizeChanged.connect(lambda s: size_label.setText(f"{s}px"))

        layout.addWidget(zoom_out_btn)
        layout.addWidget(size_label)
        layout.addWidget(zoom_in_btn)
        return container
