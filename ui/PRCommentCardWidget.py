from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QFrame,
)


class PRCommentCardWidget(QFrame):
    """Inline review comment thread card with reply and resolution controls."""

    replySubmitted = pyqtSignal(str, str)   # (parent_comment_id, reply_body)
    resolveToggled = pyqtSignal(str, bool)  # (comment_id, unresolve)

    def __init__(self, thread_data: dict[str, Any], parent=None, typography=None):
        super().__init__(parent)
        self.thread_data = thread_data
        self.typography = typography
        self._font_size = typography.font_size if typography is not None else 13
        self._reply_labels: list[tuple[QLabel, QLabel, QLabel]] = []
        root = thread_data.get("root_comment") or thread_data
        self._is_resolved = bool(thread_data.get("resolved", thread_data.get("is_resolved", False)))
        self.root_comment_id = str(root.get("id") or thread_data.get("comment_id") or "")

        self.setFrameShape(QFrame.StyledPanel)
        self._update_card_style()

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 8, 10, 8)
        self.main_layout.setSpacing(6)

        # Header: Avatar + Author + Time + Status Badge
        header = QHBoxLayout()
        header.setSpacing(8)

        author_name = root.get("author_display_name") or root.get("author_display") or root.get("author") or "Developer"
        initial = (author_name[0] if author_name else "D").upper()

        self.avatar_label = QLabel(initial, self)
        self.avatar_label.setAlignment(Qt.AlignCenter)
        self.avatar_label.setFixedSize(22, 22)
        self.avatar_label.setStyleSheet(
            "background-color: #3b82f6; color: white; font-weight: bold; font-size: 11px; border-radius: 11px;"
        )

        self.author_label = QLabel(author_name, self)
        self.author_label.setStyleSheet("color: #f1f5f9; font-weight: 600; font-size: 12px;")

        time_str = str(root.get("created_at") or root.get("created_on") or "")[:16].replace("T", " ")
        self.time_label = QLabel(time_str, self)
        self.time_label.setStyleSheet("color: #64748b; font-size: 11px;")

        self.status_badge = QLabel(self)
        self._update_status_badge()

        header.addWidget(self.avatar_label)
        header.addWidget(self.author_label)
        header.addWidget(self.time_label)
        header.addStretch()
        header.addWidget(self.status_badge)
        self.main_layout.addLayout(header)

        # Body
        body_text = root.get("body") or ""
        self.body_label = QLabel(body_text, self)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_label.setStyleSheet("color: #e2e8f0; font-size: 12px; line-height: 1.4; padding: 2px 0;")
        self.main_layout.addWidget(self.body_label)

        # Nested Replies
        replies = thread_data.get("replies") or []
        for reply in replies:
            self._add_reply_widget(reply)

        # Actions Row: Reply Button + Resolve/Reopen Button
        self.action_layout = QHBoxLayout()
        self.action_layout.setSpacing(8)

        self.reply_btn = QPushButton("↩ Reply", self)
        self.reply_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: transparent;"
            "  color: #60a5fa;"
            "  border: none;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "  padding: 2px 6px;"
            "}"
            "QPushButton:hover { color: #93c5fd; text-decoration: underline; }"
        )
        self.reply_btn.clicked.connect(self._toggle_reply_box)

        self.resolve_btn = QPushButton(self)
        self._update_resolve_button()
        self.resolve_btn.clicked.connect(self._on_resolve_clicked)

        self.action_layout.addWidget(self.reply_btn)
        self.action_layout.addWidget(self.resolve_btn)
        self.action_layout.addStretch()
        self.main_layout.addLayout(self.action_layout)

        # Inline Reply Input Box (Initially hidden)
        self.reply_container = QWidget(self)
        reply_box_layout = QVBoxLayout(self.reply_container)
        reply_box_layout.setContentsMargins(0, 4, 0, 0)
        reply_box_layout.setSpacing(8)

        self.reply_input = QTextEdit(self.reply_container)
        self.reply_input.setPlaceholderText("Write a reply... (Keep it natural and direct)")
        self.reply_input.setFixedHeight(55)
        self.reply_input.setStyleSheet(
            "QTextEdit {"
            "  background-color: #12141a;"
            "  border: 1px solid #334155;"
            "  border-radius: 4px;"
            "  color: #f1f5f9;"
            "  font-size: 12px;"
            "  padding: 4px;"
            "}"
            "QTextEdit:focus { border-color: #3b82f6; }"
        )
        reply_box_layout.addWidget(self.reply_input)

        reply_buttons = QHBoxLayout()
        reply_buttons.setSpacing(12)
        reply_buttons.addStretch()

        self.cancel_reply_btn = QPushButton("Cancel", self.reply_container)
        self.cancel_reply_btn.setStyleSheet(
            "QPushButton { background-color: #262a33; color: #cbd5e1; border: 1px solid #475569; border-radius: 4px; padding: 6px 12px; min-width: 72px; font-size: 11px; }"
            "QPushButton:hover { background-color: #333a46; }"
        )
        self.cancel_reply_btn.clicked.connect(self._toggle_reply_box)

        self.submit_reply_btn = QPushButton("Post Reply", self.reply_container)
        self.submit_reply_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; border: none; border-radius: 4px; padding: 6px 12px; min-width: 88px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        self.submit_reply_btn.clicked.connect(self._on_submit_reply)

        reply_buttons.addWidget(self.cancel_reply_btn)
        reply_buttons.addWidget(self.submit_reply_btn)
        reply_box_layout.addLayout(reply_buttons)

        self.reply_container.setVisible(False)
        self.main_layout.addWidget(self.reply_container)
        if typography is not None:
            typography.fontSizeChanged.connect(self.set_font_size)
            self.set_font_size(typography.font_size)

    def _update_card_style(self):
        if self._is_resolved:
            self.setStyleSheet(
                "PRCommentCardWidget {"
                "  background-color: #111822;"
                "  border: 1px solid #1e293b;"
                "  border-left: 3px solid #10b981;"
                "  border-radius: 6px;"
                "  margin: 4px 10px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "PRCommentCardWidget {"
                "  background-color: #181d26;"
                "  border: 1px solid #2e3848;"
                "  border-left: 3px solid #f59e0b;"
                "  border-radius: 6px;"
                "  margin: 4px 10px;"
                "}"
            )

    def _update_status_badge(self):
        if self._is_resolved:
            self.status_badge.setText("Resolved")
            self.status_badge.setStyleSheet(
                "background-color: #064e3b; color: #34d399; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
            )
        else:
            self.status_badge.setText("Unresolved")
            self.status_badge.setStyleSheet(
                "background-color: #451a03; color: #fbbf24; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
            )

    def _update_resolve_button(self):
        button_size = max(10, self._font_size - 2)
        if self._is_resolved:
            self.resolve_btn.setText("Reopen Thread")
            self.resolve_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 3px 8px; font-size: {button_size}px; }}"
                "QPushButton:hover { background-color: #272f3d; color: #e2e8f0; }"
            )
        else:
            self.resolve_btn.setText("✓ Resolve Thread")
            self.resolve_btn.setStyleSheet(
                f"QPushButton {{ background-color: #064e3b; color: #34d399; border: 1px solid #059669; border-radius: 4px; padding: 3px 8px; font-size: {button_size}px; font-weight: 600; }}"
                "QPushButton:hover { background-color: #047857; }"
            )

    def _add_reply_widget(self, reply: dict[str, Any]):
        reply_box = QFrame(self)
        reply_box.setStyleSheet("background-color: #12151c; border-radius: 4px; border: 1px solid #1f2430; padding: 4px;")
        layout = QVBoxLayout(reply_box)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        r_author = reply.get("author_display_name") or reply.get("author_display") or reply.get("author") or "Developer"
        r_time = str(reply.get("created_at") or reply.get("created_on") or "")[:16].replace("T", " ")

        r_header = QHBoxLayout()
        r_author_lbl = QLabel(r_author, reply_box)
        r_author_lbl.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 11px;")
        r_time_lbl = QLabel(r_time, reply_box)
        r_time_lbl.setStyleSheet("color: #64748b; font-size: 10px;")
        r_header.addWidget(r_author_lbl)
        r_header.addWidget(r_time_lbl)
        r_header.addStretch()
        layout.addLayout(r_header)

        r_body = QLabel(reply.get("body") or "", reply_box)
        r_body.setWordWrap(True)
        r_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        r_body.setStyleSheet("color: #e2e8f0; font-size: 11px;")
        layout.addWidget(r_body)

        self._reply_labels.append((r_author_lbl, r_time_lbl, r_body))

        self.main_layout.addWidget(reply_box)

    def _toggle_reply_box(self):
        self.reply_container.setVisible(not self.reply_container.isVisible())
        if self.reply_container.isVisible():
            self.reply_input.setFocus()

    def _on_submit_reply(self):
        text = self.reply_input.toPlainText().strip()
        if text:
            self.replySubmitted.emit(self.root_comment_id, text)
            self.reply_input.clear()
            self.reply_container.setVisible(False)

    def _on_resolve_clicked(self):
        new_unresolve = self._is_resolved
        self.resolveToggled.emit(self.root_comment_id, new_unresolve)

    def update_resolution_state(self, resolved: bool):
        self._is_resolved = resolved
        self._update_card_style()
        self._update_status_badge()
        self._update_resolve_button()

    def set_font_size(self, size: int):
        self._font_size = size
        small = max(10, size - 2)
        tiny = max(9, size - 3)
        self.author_label.setStyleSheet(f"color: #f1f5f9; font-weight: 600; font-size: {small}px;")
        self.time_label.setStyleSheet(f"color: #94a3b8; font-size: {tiny}px;")
        self.body_label.setStyleSheet(f"color: #e2e8f0; font-size: {size}px; padding: 2px 0;")
        self.reply_input.setStyleSheet(
            f"QTextEdit {{ background-color: #12141a; border: 1px solid #334155; border-radius: 4px; "
            f"color: #f1f5f9; font-size: {size}px; padding: 6px; }}"
            "QTextEdit:focus { border-color: #3b82f6; }"
        )
        self.reply_input.setFixedHeight(max(70, size * 4))
        self.reply_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: #93c5fd; border: none; "
            f"font-size: {small}px; font-weight: 600; padding: 4px 8px; }}"
            "QPushButton:hover { color: #bfdbfe; }"
        )
        self.cancel_reply_btn.setStyleSheet(
            f"QPushButton {{ background: #262a33; color: #cbd5e1; border: 1px solid #475569; "
            f"border-radius: 4px; padding: 6px 12px; min-width: 72px; font-size: {small}px; }}"
        )
        self.submit_reply_btn.setStyleSheet(
            f"QPushButton {{ background: #2563eb; color: white; border: none; border-radius: 4px; "
            f"padding: 6px 12px; min-width: 88px; font-weight: 600; font-size: {small}px; }}"
        )
        self._update_resolve_button()
        for author, time_label, body in self._reply_labels:
            author.setStyleSheet(f"color: #cbd5e1; font-weight: 600; font-size: {small}px;")
            time_label.setStyleSheet(f"color: #94a3b8; font-size: {tiny}px;")
            body.setStyleSheet(f"color: #e2e8f0; font-size: {size}px;")
