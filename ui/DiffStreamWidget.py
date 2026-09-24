from __future__ import annotations

import html
import os
from difflib import SequenceMatcher
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
    QCheckBox,
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
    addCommentRequested = pyqtSignal(str, int, str)

    def __init__(
        self,
        diff_file: DiffFile,
        comments: list[dict[str, Any]],
        typography: TypographyController,
        parent=None,
        ignore_whitespace: bool = False,
        hide_blank_lines: bool = False,
    ):
        super().__init__(parent)
        self.diff_file = diff_file
        self.comments = comments
        self.typography = typography
        self.is_collapsed = False
        self.ignore_whitespace = ignore_whitespace
        self.hide_blank_lines = hide_blank_lines
        self._ignored_line_ids: set[int] = set()
        self._find_whitespace_only_changes()
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
            "M": ("#172e55", "#93c5fd"),
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
        self.path_lbl.setStyleSheet(
            f"color: #f8fafc; font-weight: 600; font-size: 12px; font-family: '{self.typography.font_family}';"
        )
        header_layout.addWidget(self.path_lbl)

        header_layout.addStretch()

        self.viewed_checkbox = QCheckBox("Viewed", self.header_frame)
        self.viewed_checkbox.setToolTip("Mark this file viewed and collapse it")
        self.viewed_checkbox.toggled.connect(self._set_viewed)
        header_layout.addWidget(self.viewed_checkbox)

        copy_diff_btn = QPushButton("Copy diff", self.header_frame)
        copy_diff_btn.clicked.connect(self._copy_diff)
        header_layout.addWidget(copy_diff_btn)

        copy_path_btn = QPushButton("Copy file path", self.header_frame)
        copy_path_btn.clicked.connect(self._copy_path)
        header_layout.addWidget(copy_path_btn)

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
        self.content_layout.setAlignment(Qt.AlignTop)

        self._build_diff_content()
        self._filter_notice = QLabel("Whitespace-only changes hidden", self.content_container)
        self._filter_notice.setStyleSheet("color: #94a3b8; font-style: italic; padding: 8px 12px;")
        self.content_layout.addWidget(self._filter_notice)
        self._update_filter_notice()
        self.card_layout.addWidget(self.content_container)

    def toggle_collapse(self):
        self.is_collapsed = not self.is_collapsed
        self.content_container.setVisible(not self.is_collapsed)
        self.collapse_btn.setText("▶" if self.is_collapsed else "▼")

    def _copy_path(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.diff_file.display_path)

    def _copy_diff(self):
        if self.diff_file.raw_lines:
            QApplication.clipboard().setText("\n".join(self.diff_file.raw_lines) + "\n")
            return
        lines = [f"diff --git a/{self.diff_file.old_path} b/{self.diff_file.new_path}",
                 "--- " + (self.diff_file.old_path if self.diff_file.old_path == "/dev/null" else f"a/{self.diff_file.old_path}"),
                 "+++ " + (self.diff_file.new_path if self.diff_file.new_path == "/dev/null" else f"b/{self.diff_file.new_path}")]
        for hunk in self.diff_file.hunks:
            lines.append(hunk.header)
            for line in hunk.lines:
                prefix = {"add": "+", "del": "-", "context": " "}.get(line.line_type, " ")
                lines.append(prefix + line.content)
        QApplication.clipboard().setText("\n".join(lines) + "\n")

    def _set_viewed(self, checked: bool):
        if self.is_collapsed != checked:
            self.toggle_collapse()

    def _find_whitespace_only_changes(self):
        self._ignored_line_ids.clear()
        if not self.ignore_whitespace:
            return
        for hunk in self.diff_file.hunks:
            old_lines = [line for line in hunk.lines if line.line_type != "add"]
            new_lines = [line for line in hunk.lines if line.line_type != "del"]
            normalize = lambda line: "".join(line.content.split())
            old = [normalize(line) for line in old_lines]
            new = [normalize(line) for line in new_lines]
            for match in SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
                for offset in range(match.size):
                    old_line = old_lines[match.a + offset]
                    new_line = new_lines[match.b + offset]
                    if old_line.line_type == "del":
                        self._ignored_line_ids.add(id(old_line))
                    if new_line.line_type == "add":
                        self._ignored_line_ids.add(id(new_line))
            for line in hunk.lines:
                if line.line_type in ("add", "del") and not line.content.strip():
                    self._ignored_line_ids.add(id(line))

    def _is_line_visible(self, line: DiffLine) -> bool:
        return id(line) not in self._ignored_line_ids and not (self.hide_blank_lines and not line.content.strip())

    def _update_filter_notice(self):
        changed_lines = [line for hunk in self.diff_file.hunks for line in hunk.lines
                         if line.line_type in ("add", "del")]
        self._filter_notice.setVisible(self.ignore_whitespace and bool(changed_lines) and not any(
            self._is_line_visible(line) for line in changed_lines
        ))

    def set_diff_options(self, ignore_whitespace: bool, hide_blank_lines: bool):
        self.ignore_whitespace = ignore_whitespace
        self.hide_blank_lines = hide_blank_lines
        self._find_whitespace_only_changes()
        self.update_typography()
        self._update_filter_notice()

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
            for thread in self.comments:
                c_widget = PRCommentCardWidget(thread, self.content_container, typography=self.typography)
                c_widget.replySubmitted.connect(self.replySubmitted.emit)
                c_widget.resolveToggled.connect(self.resolveToggled.emit)
                self._comment_widgets.append(c_widget)
                self.content_layout.addWidget(c_widget)
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
                        c_widget = PRCommentCardWidget(thread, self.content_container, typography=self.typography)
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
            c_widget = PRCommentCardWidget(thread, self.content_container, typography=self.typography)
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
        tb.setOpenLinks(False)
        tb.setReadOnly(True)
        tb.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tb.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tb.setStyleSheet("QTextBrowser { border: none; background-color: #12141a; }")
        tb.document().setDocumentMargin(0)

        tb._diff_lines = lines
        tb._diff_header = hunk_header
        tb._diff_lexer = lexer
        tb._diff_formatter = formatter
        tb.setHtml(self._render_lines_html(lines, hunk_header, lexer, formatter))
        tb.anchorClicked.connect(self._on_comment_link)

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
        for line in lines:
            if not self._is_line_visible(line):
                continue
            old_str = f"{line.old_line_num:>4}" if line.old_line_num is not None else "    "
            new_str = f"{line.new_line_num:>4}" if line.new_line_num is not None else "    "

            if line.line_type == "add":
                sign = "+"
                row_class = "line-add"
                code_bg = "#174331"
                gutter_fg = "#86efac"
            elif line.line_type == "del":
                sign = "-"
                row_class = "line-del"
                code_bg = "#51212c"
                gutter_fg = "#fda4af"
            else:
                sign = "&nbsp;"
                row_class = "line-context"
                code_bg = "#171d29"
                gutter_fg = "#718096"

            target_line = line.old_line_num if line.line_type == "del" else line.new_line_num
            side = "LEFT" if line.line_type == "del" else "RIGHT"
            comment_link = (
                f'<a href="comment:{side}:{target_line}" '
                f'style="color:#bfdbfe; text-decoration:none; font-weight:bold;">[+]</a>'
                if target_line is not None else ""
            )

            # Syntax highlight line content
            try:
                hl_code = highlight(line.content, lexer, formatter).rstrip("\n")
            except Exception:
                hl_code = html.escape(line.content)

            rows.append(
                f'<pre class="{row_class}" style="margin:0; padding:2px 6px; background-color:{code_bg}; '
                f"color:#f1f5f9; font-family:'{font_family}'; font-size:{font_size}px;\">"
                f'{comment_link or " "} '
                f'<span style="color:{gutter_fg};">{old_str} {new_str} {sign}</span> '
                f'&nbsp;&nbsp;<span class="code-text">{hl_code}</span></pre>'
            )

        table_rows = "".join(rows)
        return (
            f"<html><head><style>body {{ background-color:#12141a; color:#f1f5f9; "
            f"margin:0; padding:0; font-family:'{font_family}', monospace; "
            f"font-size:{font_size}px; }} {pygments_css}</style></head>"
            f"<body>{table_rows}</body></html>"
        )

    def update_typography(self):
        self.path_lbl.setStyleSheet(
            f"color: #f8fafc; font-weight: 600; font-size: {self.typography.font_size}px; font-family: '{self.typography.font_family}';"
        )
        for tb in self._text_browsers:
            tb.setHtml(self._render_lines_html(
                tb._diff_lines, tb._diff_header, tb._diff_lexer, tb._diff_formatter,
            ))
            tb.setFixedHeight(max(24, int(tb.document().size().height()) + 12))
            tb.setVisible(any(self._is_line_visible(line) for line in tb._diff_lines))

    def _on_comment_link(self, url: QUrl):
        parts = url.toString().split(":")
        if len(parts) == 3 and parts[0] == "comment":
            try:
                self.addCommentRequested.emit(self.diff_file.display_path, int(parts[2]), parts[1])
            except ValueError:
                return

    def set_comments_visible(self, visible: bool):
        for card in self._comment_widgets:
            card.setVisible(visible)


class DiffStreamWidget(QWidget):
    """Main scrollable stream containing syntax-colored diff cards for all modified files."""

    replySubmitted = pyqtSignal(str, str)
    resolveToggled = pyqtSignal(str, bool)
    addCommentRequested = pyqtSignal(str, int, str)

    def __init__(self, typography: TypographyController, parent=None):
        super().__init__(parent)
        self.typography = typography
        self._file_cards: dict[str, DiffFileCard] = {}
        self._general_comment_widgets: list[QWidget] = []
        self._orphan_comment_widgets: list[QWidget] = []
        self._general_comments_visible = True
        self._code_comments_visible = True
        self._ignore_whitespace = False
        self._hide_blank_lines = False
        self._viewed_paths: set[str] = set()

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
        self._general_comment_widgets.clear()
        self._orphan_comment_widgets.clear()
        # Remove widgets from stream_layout
        while self.stream_layout.count() > 0:
            item = self.stream_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        self.stream_layout.addStretch()

    def reset_viewed(self):
        self._viewed_paths.clear()

    def set_ignore_whitespace(self, enabled: bool):
        self._ignore_whitespace = enabled
        for card in self._file_cards.values():
            card.set_diff_options(enabled, self._hide_blank_lines)

    def set_hide_blank_lines(self, enabled: bool):
        self._hide_blank_lines = enabled
        for card in self._file_cards.values():
            card.set_diff_options(self._ignore_whitespace, enabled)

    def set_diff_content(
        self,
        diff_text: str,
        comments_by_file: dict[str, list[dict[str, Any]]],
        general_comments: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Parse unified diff, populate cards, and return list of file summaries for sidebar."""
        self.clear()
        files = parse_unified_diff(diff_text)
        file_summaries: list[dict[str, Any]] = []

        def add_comments(title: str, threads: list[dict[str, Any]], *, general: bool) -> None:
            if not threads:
                return
            target = self._general_comment_widgets if general else self._orphan_comment_widgets
            visible = self._general_comments_visible if general else self._code_comments_visible
            heading = QLabel(title, self.scroll_widget)
            heading.setStyleSheet("color: #cbd5e1; font-size: 13px; font-weight: 700; padding: 8px;")
            self.stream_layout.insertWidget(self.stream_layout.count() - 1, heading)
            target.append(heading)
            heading.setVisible(visible)
            for thread in threads:
                card = PRCommentCardWidget(thread, self.scroll_widget, typography=self.typography)
                card.replySubmitted.connect(self.replySubmitted.emit)
                card.resolveToggled.connect(self.resolveToggled.emit)
                self.stream_layout.insertWidget(self.stream_layout.count() - 1, card)
                target.append(card)
                card.setVisible(visible)

        add_comments("General review comments", general_comments or [], general=True)

        if not files:
            empty_lbl = QLabel("No text diff available for this pull request.", self.scroll_widget)
            empty_lbl.setStyleSheet("color: #64748b; font-size: 13px; padding: 20px; font-style: italic;")
            self.stream_layout.insertWidget(self.stream_layout.count() - 1, empty_lbl)
            for path, threads in comments_by_file.items():
                add_comments(path, threads, general=False)
            return []

        for f in files:
            file_comments = comments_by_file.get(f.display_path, [])
            # Also check if comments were keyed under filename alone
            if not file_comments:
                file_comments = comments_by_file.get(os.path.basename(f.display_path), [])

            card = DiffFileCard(f, file_comments, self.typography, self.scroll_widget,
                                ignore_whitespace=self._ignore_whitespace,
                                hide_blank_lines=self._hide_blank_lines)
            card.replySubmitted.connect(self.replySubmitted.emit)
            card.resolveToggled.connect(self.resolveToggled.emit)
            card.addCommentRequested.connect(self.addCommentRequested.emit)
            card.set_comments_visible(self._code_comments_visible)
            card.viewed_checkbox.toggled.connect(
                lambda checked, path=f.display_path: self._viewed_paths.add(path) if checked else self._viewed_paths.discard(path)
            )
            card.viewed_checkbox.setChecked(f.display_path in self._viewed_paths)

            self._file_cards[f.display_path] = card
            self.stream_layout.insertWidget(self.stream_layout.count() - 1, card)

            file_summaries.append({
                "path": f.display_path,
                "change_type": f.change_type,
                "additions": f.additions,
                "deletions": f.deletions,
                "comment_count": len(file_comments),
            })

        displayed_paths = {f.display_path for f in files}
        for path, threads in comments_by_file.items():
            if path not in displayed_paths and not any(os.path.basename(p) == path for p in displayed_paths):
                add_comments(path, threads, general=False)

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
        for widget in self._general_comment_widgets + self._orphan_comment_widgets:
            if isinstance(widget, QLabel):
                widget.setStyleSheet(f"color: #cbd5e1; font-size: {size}px; font-weight: 700; padding: 8px;")

    def set_general_comments_visible(self, visible: bool):
        self._general_comments_visible = visible
        for widget in self._general_comment_widgets:
            widget.setVisible(visible)

    def set_code_comments_visible(self, visible: bool):
        self._code_comments_visible = visible
        for card in self._file_cards.values():
            card.set_comments_visible(visible)
        for widget in self._orphan_comment_widgets:
            widget.setVisible(visible)
