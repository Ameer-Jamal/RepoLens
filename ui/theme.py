"""Shared desktop colors derived from the PR Review tab."""

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication, QWidget


APP_STYLESHEET = """
QWidget { color: #e2e8f0; }
QTabWidget::pane { background: #0f1117; border: 1px solid #273244; }
QTabBar::tab { background: #1a1e28; color: #94a3b8; border: 1px solid #273244;
               padding: 9px 14px; margin-right: 3px; border-top-left-radius: 5px;
               border-top-right-radius: 5px; }
QTabBar::tab:selected { background: #263349; color: #f8fafc; border-bottom-color: #3b82f6; }
QTabBar::tab:hover:!selected { background: #232b3a; color: #e2e8f0; }
QGroupBox { background: #161b25; border: 1px solid #2b3546; border-radius: 7px;
            margin-top: 12px; padding-top: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #cbd5e1; }
QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget, QComboBox {
    background: #171c27; color: #f1f5f9; border: 1px solid #334155;
    border-radius: 5px; padding: 5px; selection-background-color: #2563eb;
    selection-color: white;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QComboBox QAbstractItemView { background: #171c27; color: #f1f5f9;
                              selection-background-color: #2563eb; }
QPushButton, QToolButton { background: #273244; color: #e2e8f0; border: 1px solid #3a4b62;
                           border-radius: 5px; padding: 6px 10px; }
QPushButton:hover, QToolButton:hover { background: #33435b; border-color: #60a5fa; }
QPushButton:pressed, QToolButton:pressed { background: #1e293b; }
QPushButton:disabled, QToolButton:disabled { background: #1c2431; color: #64748b;
                                            border-color: #293345; }
QRadioButton, QCheckBox { color: #cbd5e1; spacing: 6px; }
QScrollArea { background: #0f1117; border: none; }
QProgressBar { background: #1e293b; color: #f1f5f9; border: 1px solid #334155;
               border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
QToolTip { background: #1e293b; color: #f1f5f9; border: 1px solid #475569; }
"""


THEME_COLORS = {
    "Classic": {},
    "Midnight": {},
    "Aurora": {
        "#0f1117": "#130f20", "#161821": "#1d172c", "#161b25": "#211a31",
        "#171c27": "#231c34", "#12141a": "#171223", "#1a1e28": "#281f3a",
        "#1e2433": "#302545", "#273244": "#382a50", "#334155": "#604775",
        "#3b82f6": "#b47aff", "#2563eb": "#824fd0", "#1e3a5f": "#473067",
        "#60a5fa": "#d0a5ff", "#93c5fd": "#d8b7ff",
    },
    "Forest": {
        "#0f1117": "#0d1917", "#161821": "#14241f", "#161b25": "#172a24",
        "#171c27": "#1b3029", "#12141a": "#10211c", "#1a1e28": "#20362d",
        "#1e2433": "#254035", "#273244": "#2b493d", "#334155": "#446858",
        "#3b82f6": "#4fd1a2", "#2563eb": "#278f70", "#1e3a5f": "#285b47",
        "#60a5fa": "#8be9c0", "#93c5fd": "#a1edce",
    },
}


def _recolor(style: str, theme: str) -> str:
    import re
    colors = THEME_COLORS.get(theme, {})
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda match: colors.get(match.group().lower(), match.group()), style)


def apply_theme(widget, theme="Midnight"):
    if theme not in THEME_COLORS:
        theme = "Midnight"
    colors = THEME_COLORS[theme]
    def color(value):
        return QColor(colors.get(value, value))
    palette = widget.style().standardPalette() if theme == "Classic" else widget.palette()
    if theme != "Classic":
        palette.setColor(QPalette.Window, color("#0f1117"))
        palette.setColor(QPalette.WindowText, color("#e2e8f0"))
        palette.setColor(QPalette.Base, color("#171c27"))
        palette.setColor(QPalette.AlternateBase, color("#202938"))
        palette.setColor(QPalette.Text, color("#f1f5f9"))
        palette.setColor(QPalette.Button, color("#273244"))
        palette.setColor(QPalette.ButtonText, color("#e2e8f0"))
        palette.setColor(QPalette.Highlight, color("#2563eb"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    widget.setPalette(palette)
    if isinstance(widget, QApplication):
        widget.setStyleSheet("" if theme == "Classic" else _recolor(APP_STYLESHEET, theme))
        children = widget.allWidgets()
    else:
        children = [widget] + widget.findChildren(QWidget)
    for child in children:
        style = child.styleSheet()
        if not style:
            continue
        previous = getattr(child, "_theme_applied_style", None)
        if previous != style:
            child._theme_base_style = style
        base = getattr(child, "_theme_base_style", style)
        updated = base if theme == "Classic" else _recolor(base, theme)
        child._theme_applied_style = updated
        if style != updated:
            child.setStyleSheet(updated)
