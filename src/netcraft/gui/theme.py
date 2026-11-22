"""Shared Qt palette, styles and original line icons."""

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True, slots=True)
class Theme:
    background: str
    sidebar: str
    panel: str
    field: str
    border: str
    text: str
    muted: str
    cyan: str
    primary: str
    primary_text: str
    hover: str
    selected: str
    success: str
    warning: str
    failure: str


DARK = Theme(
    "#0b1422",
    "#0e1928",
    "#111e30",
    "#0c1727",
    "#263950",
    "#ebf3fe",
    "#a5b8d0",
    "#61dbee",
    "#5bd4ec",
    "#06212e",
    "#182d46",
    "#173753",
    "#6be0b3",
    "#ffcc80",
    "#ff9099",
)
LIGHT = Theme(
    "#edf3fa",
    "#e2ebf6",
    "#ffffff",
    "#f5f8fc",
    "#b8c9dc",
    "#14283f",
    "#435a75",
    "#00677c",
    "#09667e",
    "#ffffff",
    "#d9e9f7",
    "#c6e7f4",
    "#006848",
    "#804200",
    "#a12536",
)


def apply_theme(app: QApplication, theme: Theme) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, theme.background),
        (QPalette.ColorRole.Base, theme.field),
        (QPalette.ColorRole.AlternateBase, theme.panel),
        (QPalette.ColorRole.WindowText, theme.text),
        (QPalette.ColorRole.Text, theme.text),
        (QPalette.ColorRole.Button, theme.panel),
        (QPalette.ColorRole.ButtonText, theme.text),
        (QPalette.ColorRole.Highlight, theme.selected),
        (QPalette.ColorRole.HighlightedText, theme.text),
        (QPalette.ColorRole.PlaceholderText, theme.muted),
        (QPalette.ColorRole.ToolTipBase, theme.panel),
        (QPalette.ColorRole.ToolTipText, theme.text),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{
            color: {theme.text}; background: {theme.background};
            selection-background-color: {theme.selected};
        }}
        QFrame#sidebar, QFrame#toolbar, QStatusBar {{ background: {theme.sidebar}; }}
        QFrame#sidebar {{ border-right: 1px solid {theme.border}; }}
        QFrame#toolbar {{ border-bottom: 1px solid {theme.border}; }}
        QStatusBar {{ border-top: 1px solid {theme.border}; }}
        QFrame#card {{ background: {theme.panel}; border: 1px solid {theme.border};
            border-radius: 10px; }}
        QLabel, QCheckBox {{ background: transparent; }}
        QLabel#brand {{ font-size: 21px; font-weight: 750; }}
        QLabel#pageTitle {{ font-size: 21px; font-weight: 690; }}
        QLabel#pageHeading {{ font-size: 28px; font-weight: 710; }}
        QLabel#panelTitle {{ font-size: 15px; font-weight: 680; }}
        QLabel#value {{ font-size: 23px; font-weight: 690; color: {theme.text}; }}
        QLabel#muted {{ color: {theme.muted}; }}
        QLabel#accent {{ color: {theme.cyan}; font-weight: 650; }}
        QLabel[tone="good"] {{ color: {theme.success}; }}
        QLabel[tone="warn"] {{ color: {theme.warning}; }}
        QLabel[tone="error"] {{ color: {theme.failure}; }}
        QPushButton, QToolButton {{
            min-height: 32px; padding: 1px 11px; background: {theme.panel};
            border: 1px solid {theme.border}; border-radius: 6px; font-weight: 580;
        }}
        QPushButton:hover, QToolButton:hover {{ background: {theme.hover}; }}
        QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
        QAbstractSpinBox:focus, QTableView:focus, QCheckBox:focus {{
            border: 1px solid {theme.cyan};
        }}
        QPushButton#primary {{ background: {theme.primary}; color: {theme.primary_text};
            border-color: {theme.primary}; }}
        QPushButton#primary:hover {{ border-color: {theme.primary_text}; }}
        QPushButton#nav {{ background: transparent; border: 1px solid transparent;
            text-align: left; padding: 2px 8px; }}
        QPushButton#nav:hover {{ background: {theme.hover}; }}
        QPushButton#nav:checked {{ background: {theme.selected}; border-color: {theme.border}; }}
        QPushButton#nav:focus {{ border-color: {theme.cyan}; }}
        QPushButton:disabled, QPushButton#primary:disabled, QToolButton:disabled {{
            color: {theme.muted}; background: {theme.field}; border-color: {theme.border};
        }}
        QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit {{
            background: {theme.field}; color: {theme.text}; border: 1px solid {theme.border};
            border-radius: 6px; min-height: 31px; padding-left: 7px; padding-right: 7px;
        }}
        QComboBox QAbstractItemView {{ background: {theme.panel}; color: {theme.text}; }}
        QTableView {{ background: {theme.field}; alternate-background-color: {theme.panel};
            border: 1px solid {theme.border}; border-radius: 6px; gridline-color: {theme.border}; }}
        QHeaderView::section {{ background: {theme.panel}; color: {theme.muted}; font-weight: 650;
            padding: 5px 7px; border: 0px; border-bottom: 1px solid {theme.border}; }}
        QTableView::item {{ padding: 3px 6px; }}
        QTableView::item:selected {{ background: {theme.selected}; color: {theme.text}; }}
        QScrollArea {{ border: 0px; }}
        QToolTip {{ background: {theme.panel}; color: {theme.text};
            border: 1px solid {theme.border}; padding: 5px; }}
    """)


def tone(widget: QWidget, value: str) -> None:
    widget.setProperty("tone", value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# Stroke paths on a 24 by 24 grid. These icons are drawn by NetCraft.
_PATHS = {
    "Overview": (
        ((3, 12), (12, 4), (21, 12)),
        ((5, 11), (5, 20), (19, 20), (19, 11)),
        ((10, 20), (10, 15), (14, 15), (14, 20)),
    ),
    "Diagnostics": (((3, 12), (7, 12), (10, 5), (14, 19), (17, 12), (21, 12)),),
    "Live Monitor": (
        ((3, 5), (21, 5), (21, 16), (3, 16), (3, 5)),
        ((8, 20), (16, 20)),
        ((12, 16), (12, 20)),
    ),
    "History": (
        ((12, 7), (12, 12), (15, 14)),
        ((3, 11), (3, 5), (7, 5)),
        ((3, 11), (5, 7), (9, 4), (14, 4), (19, 7), (21, 12), (19, 17), (15, 20), (9, 20), (5, 17)),
    ),
    "Analytics": (((4, 4), (4, 20), (21, 20)), ((7, 16), (11, 11), (14, 13), (20, 6))),
    "Export": (
        ((12, 3), (12, 15)),
        ((8, 11), (12, 15), (16, 11)),
        ((4, 15), (4, 20), (20, 20), (20, 15)),
    ),
    "Settings": (
        ((4, 6), (20, 6)),
        ((4, 12), (20, 12)),
        ((4, 18), (20, 18)),
        ((8, 4), (8, 8)),
        ((15, 10), (15, 14)),
        ((10, 16), (10, 20)),
    ),
    "Menu": (((5, 7), (19, 7)), ((5, 12), (19, 12)), ((5, 17), (19, 17))),
}


def icon(name: str, color: str) -> QIcon:
    image = QPixmap(48, 48)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for path in _PATHS[name]:
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in path]))
    painter.end()
    return QIcon(image)
