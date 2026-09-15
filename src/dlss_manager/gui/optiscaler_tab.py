from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
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

from ..manager import Manager
from ..optiscaler import PROXY_DLL_CHOICES, DEFAULT_PROXY_DLL, ExtractionError, OptiScalerError

LATEST_LABEL = "Последняя"


class OptiScalerTab(QWidget):
    def __init__(self, manager: Manager, set_status: Callable[[str], None]):
        super().__init__()
        self.manager = manager
        self.set_status = set_status
        self._releases_loaded = False

        root = QVBoxLayout(self)

        form = QGroupBox("Установка OptiScaler")
        form_layout = QVBoxLayout(form)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Игра:"))
        self.game_combo = QComboBox()
        self.game_combo.currentIndexChanged.connect(self._on_game_changed)
        row1.addWidget(self.game_combo, 1)
        form_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Папка установки:"))
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        row2.addWidget(self.target_combo, 1)
        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self._on_browse)
        row2.addWidget(browse_btn)
        form_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Proxy DLL:"))
        self.proxy_combo = QComboBox()
        self.proxy_combo.addItems(PROXY_DLL_CHOICES)
        self.proxy_combo.setCurrentText(DEFAULT_PROXY_DLL)
        row3.addWidget(self.proxy_combo)

        row3.addWidget(QLabel("Версия:"))
        self.version_combo = QComboBox()
        self.version_combo.addItem(LATEST_LABEL)
        row3.addWidget(self.version_combo)

        reload_versions_btn = QPushButton("Обновить список версий")
        reload_versions_btn.clicked.connect(self._load_releases)
        row3.addWidget(reload_versions_btn)
        form_layout.addLayout(row3)

        row4 = QHBoxLayout()
        install_btn = QPushButton("Установить / переустановить")
        install_btn.clicked.connect(self._on_install)
        row4.addWidget(install_btn)
        row4.addStretch(1)
        form_layout.addLayout(row4)

        root.addWidget(form)

        actions_row = QHBoxLayout()
        check_btn = QPushButton("Проверить обновления")
        check_btn.clicked.connect(self._on_check_updates)
        actions_row.addWidget(check_btn)
        actions_row.addStretch(1)
        root.addLayout(actions_row)

        self.installs_table = self._build_installs_table()
        root.addWidget(self.installs_table)

        self.refresh()

    # -- layout -----------------------------------------------------------

    def _build_installs_table(self) -> QTableWidget:
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Игра", "Версия", "Proxy", "Папка", "Статус", "", ""])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        return table

    # -- data ---------------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_game_combo()
        self._refresh_installs_table()

    def _refresh_game_combo(self) -> None:
        current = self.game_combo.currentData()
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        for game in self.manager.db.all_games():
            self.game_combo.addItem(game["name"], game["app_id"])
        if current is not None:
            idx = self.game_combo.findData(current)
            if idx >= 0:
                self.game_combo.setCurrentIndex(idx)
        self.game_combo.blockSignals(False)
        self._on_game_changed()

    def _on_game_changed(self) -> None:
        app_id = self.game_combo.currentData()
        self.target_combo.clear()
        if app_id is None:
            return
        try:
            targets = self.manager.suggest_optiscaler_targets(app_id)
        except ValueError:
            targets = []
        for t in targets:
            self.target_combo.addItem(str(t))

    def _refresh_installs_table(self, status_by_id: dict[int, str] | None = None) -> None:
        table = self.installs_table
        table.setRowCount(0)
        status_by_id = status_by_id or {}

        for row in self.manager.db.active_optiscaler_installs():
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(row["game_name"]))
            table.setItem(r, 1, QTableWidgetItem(row["version"]))
            table.setItem(r, 2, QTableWidgetItem(row["proxy_filename"]))
            table.setItem(r, 3, QTableWidgetItem(row["target_dir"]))
            table.setItem(r, 4, QTableWidgetItem(status_by_id.get(row["id"], "?")))

            update_btn = QPushButton("Обновить")
            update_btn.clicked.connect(lambda _c=False, install_id=row["id"]: self._on_update(install_id))
            table.setCellWidget(r, 5, update_btn)

            remove_btn = QPushButton("Удалить")
            remove_btn.clicked.connect(lambda _c=False, install_id=row["id"]: self._on_uninstall(install_id))
            table.setCellWidget(r, 6, remove_btn)

    # -- actions ------------------------------------------------------------

    def _on_browse(self) -> None:
        app_id = self.game_combo.currentData()
        start_dir = self.target_combo.currentText() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Папка установки OptiScaler", start_dir)
        if path:
            self.target_combo.setEditText(path)
        _ = app_id  # kept for clarity that this is scoped to the selected game

    def _load_releases(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            releases = self.manager.optiscaler_releases(limit=20)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка", f"Не удалось получить список релизов:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        current = self.version_combo.currentText()
        self.version_combo.clear()
        self.version_combo.addItem(LATEST_LABEL)
        for r in releases:
            self.version_combo.addItem(r.tag)
        idx = self.version_combo.findText(current)
        if idx >= 0:
            self.version_combo.setCurrentIndex(idx)
        self._releases_loaded = True

    def _selected_release(self):
        tag = self.version_combo.currentText()
        if tag == LATEST_LABEL or not tag:
            return self.manager.latest_optiscaler_release()
        for r in self.manager.optiscaler_releases(limit=30):
            if r.tag == tag:
                return r
        raise OptiScalerError(f"release {tag!r} not found")

    def _on_install(self) -> None:
        app_id = self.game_combo.currentData()
        if app_id is None:
            QMessageBox.warning(self, "Нет игры", "Сначала просканируйте Steam и выберите игру.")
            return
        target_text = self.target_combo.currentText().strip()
        if not target_text:
            QMessageBox.warning(self, "Нет папки", "Укажите папку установки.")
            return
        target_dir = Path(target_text)
        proxy = self.proxy_combo.currentText()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.set_status("Скачивание и установка OptiScaler...")
        try:
            release = self._selected_release()
            try:
                result = self.manager.install_optiscaler(app_id, target_dir, proxy_filename=proxy, release=release)
            except FileExistsError:
                QApplication.restoreOverrideCursor()
                if QMessageBox.question(
                    self,
                    "Файл уже существует",
                    f"{target_dir / proxy} уже существует (возможно, другой враппер).\n"
                    f"Заменить (оригинал будет сохранён в бэкапе)?",
                ) != QMessageBox.Yes:
                    self.set_status("Установка отменена")
                    return
                QApplication.setOverrideCursor(Qt.WaitCursor)
                result = self.manager.install_optiscaler(
                    app_id, target_dir, proxy_filename=proxy, release=release, overwrite_conflict=True
                )
        except (OptiScalerError, ExtractionError, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка установки", str(e))
            self.set_status("Ошибка установки OptiScaler")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.set_status(f"OptiScaler {release.tag} установлен в {result.target_dir}")
        self.refresh()

    def _on_update(self, install_id: int) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            release = self.manager.latest_optiscaler_release()
            result = self.manager.update_optiscaler(install_id, release=release)
        except (OptiScalerError, ExtractionError, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка обновления", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.set_status(f"Обновлено до {release.tag}: {result.target_dir}")
        self.refresh()

    def _on_uninstall(self, install_id: int) -> None:
        if QMessageBox.question(self, "Удалить OptiScaler", "Удалить установленные файлы OptiScaler?") != QMessageBox.Yes:
            return
        try:
            self.manager.uninstall_optiscaler(install_id)
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.set_status(f"OptiScaler (#{install_id}) удалён")
        self.refresh()

    def _on_check_updates(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pending = self.manager.check_optiscaler_updates()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка", f"Не удалось проверить обновления:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        pending_ids = {row["id"]: latest.tag for row, latest in pending}
        status_by_id = {}
        for row in self.manager.db.active_optiscaler_installs():
            if row["id"] in pending_ids:
                status_by_id[row["id"]] = f"доступно {pending_ids[row['id']]}"
            else:
                status_by_id[row["id"]] = "актуально"
        self._refresh_installs_table(status_by_id)
        self.set_status(f"Проверено: доступно обновлений — {len(pending)}")
