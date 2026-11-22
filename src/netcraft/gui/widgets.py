"""Reusable, keyboard-accessible Qt layout and display primitives."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import override

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)


def label(text: str = "", role: str = "", *, wrap: bool = False) -> QLabel:
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(wrap)
    if role:
        value.setObjectName(role)
    return value


def button(text: str, *, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setAccessibleName(text.replace("&", ""))
    if primary:
        widget.setObjectName("primary")
    return widget


def line(name: str, placeholder: str = "", value: str = "") -> QLineEdit:
    widget = QLineEdit(value)
    widget.setPlaceholderText(placeholder)
    widget.setAccessibleName(name)
    widget.setClearButtonEnabled(True)
    widget.setMaxLength(8192)
    return widget


def combo(name: str, entries: Iterable[tuple[str, object]]) -> QComboBox:
    widget = QComboBox()
    widget.setAccessibleName(name)
    for text, value in entries:
        widget.addItem(text, value)
    return widget


def pick(widget: QComboBox, value: object) -> None:
    index = widget.findData(value)
    if index >= 0:
        widget.setCurrentIndex(index)


def form_row(form: QFormLayout, caption: str, field: QWidget) -> None:
    title = label(caption)
    title.setBuddy(field)
    form.addRow(title, field)


def form_layout() -> QFormLayout:
    form = QFormLayout()
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(9)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def controls(*widgets: QWidget, stretch: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for widget in widgets:
        row.addWidget(widget)
    if stretch:
        row.addStretch(1)
    return row


class Card(QFrame):
    def __init__(self, heading: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 13, 16, 14)
        self.body.setSpacing(9)
        if heading:
            self.body.addWidget(label(heading, "panelTitle"))


class Page(QScrollArea):
    def __init__(self, title: str, description: str = "") -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.surface = QWidget()
        self.content = QVBoxLayout(self.surface)
        self.content.setContentsMargins(23, 19, 23, 20)
        self.content.setSpacing(13)
        self.content.addWidget(label(title, "pageHeading"))
        if description:
            self.content.addWidget(label(description, "muted", wrap=True))
        self.setWidget(self.surface)
        self.setAccessibleName(title)


class Metrics(QWidget):
    def __init__(self, names: tuple[str, ...], *, columns: int = 4) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.values: dict[str, QLabel] = {}
        for index, name in enumerate(names):
            tile = Card()
            title = label(name, "muted", wrap=True)
            value = label("—", "value", wrap=True)
            value.setAccessibleName(name)
            tile.body.addWidget(title)
            tile.body.addWidget(value)
            grid.addWidget(tile, index // columns, index % columns)
            self.values[name] = value
        for index in range(columns):
            grid.setColumnStretch(index, 1)

    def show_values(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            self.values[name].setText(value)


def latency(ns: int | float | None, unit: str = "ms") -> str:
    if ns is None:
        return "—"
    scale = 1_000_000 if unit == "ms" else 1000
    return f"{ns / scale:,.3f} {unit}"


def timestamp(at: datetime | None) -> str:
    return at.strftime("%Y-%m-%d %H:%M:%S UTC") if at is not None else "—"


def rate(value: object) -> str:
    return f"{value:.1%}" if isinstance(value, (float, int)) else "—"


def choose_destination(parent: QWidget, extension: str, suggested: str) -> tuple[Path, bool] | None:
    filename, _ = QFileDialog.getSaveFileName(
        parent,
        "Choose export destination",
        suggested,
        f"{extension.upper()} (*.{extension})",
        options=QFileDialog.Option.DontConfirmOverwrite,
    )
    if not filename:
        return None
    path = Path(filename)
    if not path.suffix:
        path = path.with_suffix(f".{extension}")
    if path.suffix.lower() != f".{extension}":
        QMessageBox.warning(parent, "File type", f"Choose a .{extension} filename for this format.")
        return None
    exists = path.exists()
    if (
        exists
        and QMessageBox.question(
            parent,
            "Replace existing export?",
            "Replace this existing file with the new export?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        != QMessageBox.StandardButton.Yes
    ):
        return None
    return path, exists


@dataclass(frozen=True, slots=True)
class TableRow:
    key: str
    cells: tuple[str, ...]
    sort_keys: tuple[str | int | float, ...] = ()


_ROOT_INDEX = QModelIndex()


class Rows(QAbstractTableModel):
    def __init__(self, headers: tuple[str, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self.headers = headers
        self.rows: tuple[TableRow, ...] = ()

    def change(self, rows: Iterable[TableRow]) -> None:
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.rows)

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.headers)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> object:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return row.cells[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return (row.sort_keys or row.cells)[index.column()]
        return None

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None


class Table(QTableView):
    def __init__(self, name: str, headers: tuple[str, ...], *, height: int = 190) -> None:
        super().__init__()
        self.setAccessibleName(name)
        self.rows = Rows(headers, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.rows)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setMinimumHeight(height)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def selected_key(self) -> str | None:
        selected = self.selectionModel().selectedRows()
        if not selected:
            return None
        return self.rows.rows[self.proxy.mapToSource(selected[0]).row()].key
