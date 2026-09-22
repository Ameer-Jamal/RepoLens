from __future__ import annotations

import html
import os
from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtGui import QColor, QFont, QCursor, QTextDocument
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QTextBrowser,
    QToolButton,
    QApplication,
)
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename, TextLexer, DiffLexer

from ui.PRCommentCardWidget import PRCommentCardWidget
from ui.TypographyController import TypographyController
from ui.diff_parser import DiffFile, DiffHunk, DiffLine, parse_unified_diff


class DiffFileCard(QFrame):
    """Visual card for a single modified file in the PR diff stream."""

    replySubmitted = pyqtSignal(str, str)
    resolveToggled = pyqtSignal(str, bool)

    def __init__(
        self,
        diff_file: DiffFile,
        comments: list[dict[str, Any]],
        typography: TypographyController,
        parent=None,
    ):
        super().__init__(parent)
        self.diff_file = diff_file
        self.comments = comments
        self.typography = typography
        self.is_collapsed = False
        self._comment_widgets: list[PRCommentCardWidget] = []
        self._text_browsers: list[QTextBrowser] = []

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "DiffFileCard {"
            "  background-color: #16181f;"
            "  border: 1px solid #242b38;"
            "  border-radius: 8px;"
            "  margin-bottom: 12px;"
            "}"
        )

        self.card_layout = QVBoxLayout(self)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)

        # Header Bar
        self.header_frame = QFrame(self)
        self.header_frame.setStyleSheet(
            "QFrame {"
            "  background-color: #1a1e28;"
            "  border-bottom: 1px solid #242b38;"
            "  border-top-left-radius: 8px;"
            "  border-top-right-radius: 8px;"
            "  padding: 6px 10px;"
            "}"
        )
        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(8)

        # Collapse Button
        self.collapse_btn = QToolButton(self.header_frame)
        self.collapse_btn.setText("▼")
        self.collapse_btn.setStyleSheet(
            "QToolButton { color: #94a3b8; font-size: 10px; border: none; background: transparent; }"
            "QToolButton:hover { color: #f1f5f9; }"
        )
        self.collapse_btn.clicked.connect(self.toggle_collapse)
        header_layout.addWidget(self.collapse_btn)

        # Status badge (M, A, D, R)
        badge_colors = {
            "A": ("#064e3b", "#34d399"),
            "D": ("#4c0519", "#fb7185"),
            "R": ("#1e1b4b", "#818cf8"),
            "M": ("#451a03", "#fbbf24"),
        }
        bg_col, fg_col = badge_colors.get(self.diff_file.change_type, ("#334155", "#94a3b8"))
        status_lbl = QLabel(self.diff_file.change_type, self.header_frame)
        status_lbl.setFixedSize(20, 20)
        status_lbl.setAlignment(Qt.AlignCenter)
        status_lbl.setStyleSheet(
            f"background-color: {bg_col}; color: {fg_col}; font-weight: bold; font-size: 10px; border-radius: 4px;"
        )
        header_layout.addWidget(status_lbl)

        # File path
        self.path_lbl = QLabel(self.diff_file.display_path, self.header_frame)
        self.path_lbl.setStyleSheet("color: #f8fafc; font-weight: 600; font-size: 12px; font-family: monospace;")
        header_layout.addWidget(self.path_lbl)

        # Copy path button
        copy_btn = QToolButton(self.header_frame)
        copy_btn.setText("📋")
        copy_btn.setToolTip("Copy file path")
        copy_btn.setStyleSheet("QToolButton { border: none; background: transparent; padding: 2px; } QToolButton:hover { background: #262b37; border-radius: 3px; }")
        copy_btn.clicked.connect(self._copy_path)
        header_layout.addWidget(copy_btn)

        header_layout.addStretch()

        # Diff stats pill
        if self.diff_file.additions > 0 or self.diff_file.deletions > 0:
            stats_lbl = QLabel(f"+{self.diff_file.additions}  -{self.diff_file.deletions}", self.header_frame)
            stats_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; padding-right: 6px;")
            header_layout.addWidget(stats_lbl)

        # Comment count badge if any
        if self.comments:
            comm_badge = QLabel(f"💬 {len(self.comments)}", self.header_frame)
            comm_badge.setStyleSheet(
                "background-color: #312e81; color: #c7d2fe; font-size: 10px; font-weight: bold; border-radius: 4px; padding: 2px 6px;"
            )
            header_layout.addWidget(comm_badge)

        self.card_layout.addWidget(self.header_frame)

        # Content Area Container
        self.content_container = QWidget(self)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        self._build_diff_content()
        self.card_layout.addWidget(self.content_container)

    def toggle_collapse(self):
        self.is_collapsed = not self.is_collapsed
        self.content_container.setVisible(not self.is_collapsed)
        self.collapse_btn.setText("▶" if self.is_collapsed else "▼")

    def _copy_path(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.diff_file.display_path)

    def _get_lexer(self):
        try:
            return get_lexer_for_filename(self.diff_file.display_path, stripnl=False)
        except Exception:
            return TextLexer(stripnl=False)

    def _build_diff_content(self):
        lexer = self._get_lexer()
        formatter = HtmlFormatter(nowrap=True, style="monokai")

        # Map comments by line number
        comments_by_line: dict[int, list[dict[str, Any]]] = {}
        file_level_comments: list[dict[str, Any]] = []

        for c in self.comments:
            # Check line or inline context
            line_num = c.get("line") or (c.get("inline") or {}).get("to_line") or (c.get("inline") or {}).get("from_line")
            if line_num is not None:
                try:
                    line_int = int(line_num)
                    comments_by_line.setdefault(line_int, []).append(c)
                except (ValueError, TypeError):
                    file_level_comments.append(c)
            else:
                file_level_comments.append(c)

        # If no hunks (e.g. binary file or empty diff)
        if not self.diff_file.hunks:
            no_diff_lbl = QLabel("No text changes (binary file or empty diff).", self.content_container)
            no_diff_lbl.setStyleSheet("color: #64748b; font-style: italic; padding: 12px;")
            self.content_layout.addWidget(no_diff_lbl)
            return

        # Render each hunk
        for hunk in self.diff_file.hunks:
            # Accumulate lines up to any line that has an inline comment
            current_chunk_lines: list[DiffLine] = []

            for line in hunk.lines:
                current_chunk_lines.append(line)
                target_line = line.new_line_num if line.new_line_num is not None else line.old_line_num

                if target_line is not None and target_line in comments_by_line:
                    # Flush current lines to a text browser
                    self._add_lines_browser(current_chunk_lines, hunk.header, lexer, formatter)
                    current_chunk_lines = []

                    # Add comment card widgets for this line
                    for thread in comments_by_line.pop(target_line):
                        c_widget = PRCommentCardWidget(thread, self.content_container)
                        c_widget.replySubmitted.connect(self.replySubmitted.emit)
                        c_widget.resolveToggled.connect(self.resolveToggled.emit)
                        self._comment_widgets.append(c_widget)
                        self.content_layout.addWidget(c_widget)

            if current_chunk_lines:
                self._add_lines_browser(current_chunk_lines, hunk.header, lexer, formatter)

        # Any remaining comments (e.g. unmapped line numbers or file-level)
        remaining_comments = file_level_comments + [
            c for thread_list in comments_by_line.values() for c in thread_list
        ]
        for thread in remaining_comments:
            c_widget = PRCommentCardWidget(thread, self.content_container)
            c_widget.replySubmitted.connect(self.replySubmitted.emit)
            c_widget.resolveToggled.connect(self.resolveToggled.emit)
            self._comment_widgets.append(c_widget)
            self.content_layout.addWidget(c_widget)

    def _add_lines_browser(
        self,
        lines: list[DiffLine],
        hunk_header: str,
        lexer,
        formatter,
    ):
        if not lines:
            return

        tb = QTextBrowser(self.content_container)
        tb.setOpenExternalLinks(False)
        tb.setReadOnly(True)
        tb.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tb.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tb.setStyleSheet("QTextBrowser { border: none; background-color: #12141a; }")

        html_content = self._render_lines_html(lines, hunk_header, lexer, formatter)
        tb.setHtml(html_content)

        # Auto-adjust height to document size
        doc_height = int(tb.document().size().height())
        tb.setFixedHeight(max(24, doc_height + 12))
        tb.document().documentLayout().documentSizeChanged.connect(
            lambda s: tb.setFixedHeight(max(24, int(s.height()) + 12))
        )

        self._text_browsers.append(tb)
        self.content_layout.addWidget(tb)

    def _render_lines_html(
        self,
        lines: list[DiffLine],
        hunk_header: str,
        lexer,
        formatter,
    ) -> str:
        font_family = self.typography.font_family
        font_size = self.typography.font_size
        pygments_css = formatter.get_style_defs(".code-text")

        rows = []
        for l in lines:
            old_str = str(l.old_line_num) if l.old_line_num is not None else ""
            new_str = str(l.new_line_num) if l.new_line_num is not None else ""

            if l.line_type == "add":
                sign = "+"
                row_class = "line-add"
                code_bg = "#11271e"
                gutter_fg = "#34d399"
            elif l.line_type == "del":
                sign = "-"
                row_class = "line-del"
                code_bg = "#30151c"
                gutter_fg = "#f87171"
            else:
                sign = "&nbsp;"
                row_class = "line-context"
                code_bg = "#141720"
                gutter_fg = "#475569"

            # Syntax highlight line content
            try:
                hl_code = highlight(l.content, lexer, formatter).rstrip("\n")
            except Exception:
                hl_code = html.escape(l.content)

            rows.append(
                f'<tr class="{row_class}" style="background-color: {code_bg};">'
                f'<td class="gutter" style="color: {gutter_fg}; text-align: right; width: 38px; padding: 1px 4px; user-select: none;">{old_str}</td>'
                f'<td class="gutter" style="color: {gutter_fg}; text-align: right; width: 38px; padding: 1px 4px; user-select: none;">{new_str}</td>'
                f'<td class="sign" style="color: {gutter_fg}; text-align: center; width: 14px; font-weight: bold; padding: 1px 2px; user-select: none;">{sign}</td>'
                f'<td class="code-cell" style="padding: 1px 6px; white-space: pre;"><span class="code-text">{hl_code}</span></td>'
                f'</tr>'
            )

        table_rows = "".join(rows)
        return f"""
        <html>
        <head>
        <style>
          body {{
            background-color: #12141a;
            color: #f1f5f9;
            margin: 0;
            padding: 0;
            font-family: '{font_family}', monospace;
            font-size: {font_size}px;
          }}
          table {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
          }}
          td {{
            vertical-align: top;
            font-family: inherit;
            font-size: inherit;
            line-height: 1.35;
          }}
          .gutter {{
            font-size: {max(9, font_size - 2)}px;
            border-right: 1px solid #222734;
          }}
          .sign {{
            border-right: 1px solid #222734;
          }}
          {pygments_css}
        </style>
        </head>
        <body>
          <table>
            {table_rows}
          </table>
        </body>
        </html>
        """

    def update_typography(self):
        # Refresh all text browsers with new font size
        lexer = self._get_lexer()
        formatter = HtmlFormatter(nowrap=True, style="monokai")
        # In modern Qt, simply re-rendering the HTML updates size smoothly
        for tb in self._text_browsers:
            doc = tb.document()
            f = self.typography.get_font()
            doc.setDefaultFont(f)
            tb.setFont(f)


class DiffStreamWidget(QWidget):
    """Main scrollable stream containing syntax-colored diff cards for all modified files."""

    replySubmitted = pyqtSignal(str, str)
    resolveToggled = pyqtSignal(str, bool)

    def __init__(self, typography: TypographyController, parent=None):
        super().__init__(parent)
        self.typography = typography
        self._file_cards: dict[str, DiffFileCard] = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll Area
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #0f1117; }"
            "QScrollBar:vertical { background-color: #161821; width: 10px; }"
            "QScrollBar::handle:vertical { background-color: #333a4a; border-radius: 5px; }"
            "QScrollBar::handle:vertical:hover { background-color: #475569; }"
        )

        self.scroll_widget = QWidget(self.scroll_area)
        self.scroll_widget.setStyleSheet("background-color: #0f1117;")
        self.stream_layout = QVBoxLayout(self.scroll_widget)
        self.stream_layout.setContentsMargins(12, 12, 12, 12)
        self.stream_layout.setSpacing(12)
        self.stream_layout.addStretch()

        self.scroll_area.setWidget(self.scroll_widget)
        main_layout.addWidget(self.scroll_area)

        self.typography.fontSizeChanged.connect(self._on_font_size_changed)

    def clear(self):
        self._file_cards.clear()
        # Remove widgets from stream_layout
        while self.stream_layout.count() > 0:
            item = self.stream_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.stream_layout.addStretch()

    def set_diff_content(
        self,
        diff_text: str,
        comments_by_file: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Parse unified diff, populate cards, and return list of file summaries for sidebar."""
        self.clear()
        files = parse_unified_diff(diff_text)
        file_summaries: list[dict[str, Any]] = []

        if not files:
            empty_lbl = QLabel("No changes in this pull request.", self.scroll_widget)
            empty_lbl.setStyleSheet("color: #64748b; font-size: 13px; padding: 20px; font-style: italic;")
            self.stream_layout.insertWidget(0, empty_lbl)
            return []

        for f in files:
            file_comments = comments_by_file.get(f.display_path, [])
            # Also check if comments were keyed under filename alone
            if not file_comments:
                file_comments = comments_by_file.get(os.path.basename(f.display_path), [])

            card = DiffFileCard(f, file_comments, self.typography, self.scroll_widget)
            card.replySubmitted.connect(self.replySubmitted.emit)
            card.resolveToggled.connect(self.resolveToggled.emit)

            self._file_cards[f.display_path] = card
            self.stream_layout.insertWidget(self.stream_layout.count() - 1, card)

            file_summaries.append({
                "path": f.display_path,
                "change_type": f.change_type,
                "additions": f.additions,
                "deletions": f.deletions,
                "comment_count": len(file_comments),
            })

        return file_summaries

    def scroll_to_file(self, file_path: str):
        card = self._file_cards.get(file_path)
        if not card:
            # Try matching basename
            for p, c in self._file_cards.items():
                if os.path.basename(p) == os.path.basename(file_path):
                    card = c
                    break

        if card:
            if card.is_collapsed:
                card.toggle_collapse()
            self.scroll_area.ensureWidgetVisible(card, 0, 40)

    def collapse_all(self):
        for card in self._file_cards.values():
            if not card.is_collapsed:
                card.toggle_collapse()

    def expand_all(self):
        for card in self._file_cards.values():
            if card.is_collapsed:
                card.toggle_collapse()

    def _on_font_size_changed(self, size: int):
        for card in self._file_cards.values():
            card.update_typography()
