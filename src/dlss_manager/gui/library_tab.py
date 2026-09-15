from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..components import KEY_TO_COMPONENT
from ..library import entry_path
from ..manager import Manager


def _format_size(num_bytes: int) -> str:
    mb = num_bytes / 1_048_576
    return f"{mb:.1f} MB" if mb >= 0.1 else f"{num_bytes} B"


def _format_timestamp(iso: str) -> str:
    # Stored as datetime.isoformat() (UTC, with microseconds) -- trim for display.
    return iso.split(".")[0].replace("T", " ") + " UTC"


class LibraryTab(QWidget):
    """Local DLL library: everything picked up by scans plus whatever the
    user drags in or imports by hand."""

    def __init__(self, manager: Manager, set_status: Callable[[str], None]):
        super().__init__()
        self.manager = manager
        self.set_status = set_status
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)

        hint = QLabel("Перетащите .dll файлы сюда, чтобы добавить их в библиотеку, или используйте кнопку ниже.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        actions_row = QHBoxLayout()
        import_btn = QPushButton("Импортировать DLL...")
        import_btn.clicked.connect(self._on_import_clicked)
        actions_row.addWidget(import_btn)
        actions_row.addStretch(1)
        root.addLayout(actions_row)

        self.table = self._build_table()
        root.addWidget(self.table)

        self.refresh()

    # -- layout -----------------------------------------------------------

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Компонент", "Версия", "Хэш", "Размер", "Источник", "Добавлен", ""])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setAcceptDrops(True)
        return table

    # -- data ---------------------------------------------------------------

    def refresh(self) -> None:
        table = self.table
        table.setRowCount(0)

        for row in self.manager.all_library_files():
            r = table.rowCount()
            table.insertRow(r)
            component = KEY_TO_COMPONENT.get(row["component_key"])
            table.setItem(r, 0, QTableWidgetItem(component.display_name if component else row["component_key"]))
            table.setItem(r, 1, QTableWidgetItem(row["version"] or "?"))
            table.setItem(r, 2, QTableWidgetItem(row["sha256"][:12]))

            path = entry_path(row)
            size_text = _format_size(path.stat().st_size) if path.is_file() else "?"
            table.setItem(r, 3, QTableWidgetItem(size_text))

            table.setItem(r, 4, QTableWidgetItem(row["source"]))
            table.setItem(r, 5, QTableWidgetItem(_format_timestamp(row["added_at"])))

            remove_btn = QPushButton("Удалить")
            remove_btn.clicked.connect(lambda _c=False, file_id=row["id"]: self._on_remove(file_id))
            table.setCellWidget(r, 6, remove_btn)

    # -- drag & drop --------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        dlls = [p for p in paths if p.suffix.lower() == ".dll"]
        if not dlls:
            QMessageBox.warning(self, "Не .dll", "Перетащите файлы с расширением .dll.")
            return
        event.acceptProposedAction()
        self.import_paths(dlls)

    # -- actions ------------------------------------------------------------

    def _on_import_clicked(self) -> None:
        paths_str, _ = QFileDialog.getOpenFileNames(
            self, "Импортировать DLL", str(Path.home()), "DLL files (*.dll)"
        )
        if not paths_str:
            return
        self.import_paths([Path(p) for p in paths_str])

    def import_paths(self, paths: list[Path]) -> None:
        entries, errors = self.manager.import_dlls(paths)

        added = sum(1 for e in entries if e.is_new)
        already = len(entries) - added
        parts = []
        if added:
            parts.append(f"добавлено {added}")
        if already:
            parts.append(f"уже было {already}")
        if errors:
            parts.append(f"ошибок {len(errors)}")
        self.set_status("Импорт: " + ", ".join(parts) if parts else "Ничего не импортировано")

        if errors:
            details = "\n".join(f"{p.name}: {msg}" for p, msg in errors)
            QMessageBox.warning(self, "Не всё удалось импортировать", details)

        self.refresh()

    def _on_remove(self, file_id: int) -> None:
        if QMessageBox.question(
            self, "Удалить из библиотеки", "Удалить этот файл из локальной библиотеки?"
        ) != QMessageBox.Yes:
            return
        try:
            self.manager.remove_library_file(file_id)
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.set_status(f"Удалено из библиотеки: #{file_id}")
        self.refresh()
