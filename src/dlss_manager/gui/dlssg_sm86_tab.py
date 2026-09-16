from __future__ import annotations

import sys
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

from ..dlssg_sm86 import (
    DEFAULT_PROXY_DLL,
    DEFAULT_RUNTIME_BUILD,
    PROXY_DLL_CHOICES,
    RUNTIME_BUILDS,
    DlssgSm86Error,
)
from ..manager import Manager
from ..optiscaler import wine_dll_override_hint

LATEST_LABEL = "Последняя"

NOTE_TEXT = (
    "Неофициальный инструмент (sdli1995/dlssg_for_sm86), не связан с OptiScaler. Разблокирует "
    "DLSS Frame Generation на RTX 20/30 (SM75/SM86), которые официально её не поддерживают -- "
    "через proxy-DLL вокруг немодифицированного рантайма NVIDIA.\n"
    "В отличие от релизов OptiScaler, у GitHub нет опубликованного хэша для сверки скачанного "
    "архива -- слабее по проверяемости, хотя релизные DLL подписаны (self-signed, отпечаток "
    "сертификата указан в README проекта).\n"
    "Не использовать в мультиплеере -- патчинг рантайма NVIDIA в процессе игры, риск бана."
)


class DlssgSm86Tab(QWidget):
    def __init__(self, manager: Manager, set_status: Callable[[str], None]):
        super().__init__()
        self.manager = manager
        self.set_status = set_status

        root = QVBoxLayout(self)

        form = QGroupBox("Установка dlssg_for_sm86 (DLSS-G unlock для RTX 20/30)")
        form_layout = QVBoxLayout(form)

        note_label = QLabel(f"⚠ {NOTE_TEXT}")
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #b35c00;")
        form_layout.addWidget(note_label)

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

        row3.addWidget(QLabel("Рантайм:"))
        self.runtime_combo = QComboBox()
        self.runtime_combo.addItems(list(RUNTIME_BUILDS))
        self.runtime_combo.setCurrentText(DEFAULT_RUNTIME_BUILD)
        row3.addWidget(self.runtime_combo)

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
        table.setHorizontalHeaderLabels(["Игра", "Версия", "Рантайм", "Proxy", "Папка", "Статус", ""])
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
            targets = self.manager.suggest_dlssg_sm86_targets(app_id)
        except ValueError:
            targets = []
        for t in targets:
            self.target_combo.addItem(str(t))

    def _refresh_installs_table(self, status_by_id: dict[int, str] | None = None) -> None:
        table = self.installs_table
        table.setRowCount(0)
        status_by_id = status_by_id or {}

        for row in self.manager.db.active_dlssg_sm86_installs():
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(row["game_name"]))
            table.setItem(r, 1, QTableWidgetItem(row["version"]))
            table.setItem(r, 2, QTableWidgetItem(row["runtime_build"]))
            table.setItem(r, 3, QTableWidgetItem(row["proxy_filename"]))
            table.setItem(r, 4, QTableWidgetItem(row["target_dir"]))
            table.setItem(r, 5, QTableWidgetItem(status_by_id.get(row["id"], "?")))

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            update_btn = QPushButton("Обновить")
            update_btn.clicked.connect(lambda _c=False, install_id=row["id"]: self._on_update(install_id))
            actions_layout.addWidget(update_btn)
            remove_btn = QPushButton("Удалить")
            remove_btn.clicked.connect(lambda _c=False, install_id=row["id"]: self._on_uninstall(install_id))
            actions_layout.addWidget(remove_btn)
            table.setCellWidget(r, 6, actions)

    # -- actions ------------------------------------------------------------

    def _on_browse(self) -> None:
        start_dir = self.target_combo.currentText() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Папка установки dlssg_for_sm86", start_dir)
        if path:
            self.target_combo.setEditText(path)

    def _load_releases(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            releases = self.manager.dlssg_sm86_releases(limit=20)
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

    def _selected_release(self):
        tag = self.version_combo.currentText()
        if tag == LATEST_LABEL or not tag:
            return self.manager.latest_dlssg_sm86_release()
        for r in self.manager.dlssg_sm86_releases(limit=30):
            if r.tag == tag:
                return r
        raise DlssgSm86Error(f"release {tag!r} not found")

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
        runtime = self.runtime_combo.currentText()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.set_status("Скачивание и установка dlssg_for_sm86...")
        try:
            release = self._selected_release()
            try:
                result = self.manager.install_dlssg_sm86(
                    app_id, target_dir, proxy_filename=proxy, runtime_build=runtime, release=release
                )
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
                result = self.manager.install_dlssg_sm86(
                    app_id,
                    target_dir,
                    proxy_filename=proxy,
                    runtime_build=runtime,
                    release=release,
                    overwrite_conflict=True,
                )
        except (DlssgSm86Error, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка установки", str(e))
            self.set_status("Ошибка установки dlssg_for_sm86")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.set_status(
            f"dlssg_for_sm86 {release.tag} (рантайм {result.runtime_build}) установлен в {result.target_dir}"
        )
        self.refresh()
        if sys.platform.startswith("linux"):
            self._show_launch_option_hint(result.proxy_filename)

    def _show_launch_option_hint(self, proxy_filename: str) -> None:
        override = wine_dll_override_hint(proxy_filename)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Linux/Proton: параметр запуска")
        box.setText(
            f"Чтобы Proton точно использовал этот файл, а не свой встроенный {proxy_filename}, "
            "добавь в свойствах игры в Steam -> Launch Options:"
        )
        box.setInformativeText(override)
        copy_btn = box.addButton("Копировать", QMessageBox.ActionRole)
        box.addButton("Закрыть", QMessageBox.AcceptRole)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(override))
        box.exec()

    def _on_update(self, install_id: int) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.manager.update_dlssg_sm86(install_id)
        except (DlssgSm86Error, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка обновления", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.set_status(f"Обновлено: {result.target_dir}")
        self.refresh()

    def _on_uninstall(self, install_id: int) -> None:
        if QMessageBox.question(
            self, "Удалить dlssg_for_sm86", "Удалить установленные файлы dlssg_for_sm86?"
        ) != QMessageBox.Yes:
            return
        try:
            self.manager.uninstall_dlssg_sm86(install_id)
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.set_status(f"dlssg_for_sm86 (#{install_id}) удалён")
        self.refresh()

    def _on_check_updates(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pending = self.manager.check_dlssg_sm86_updates()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка", f"Не удалось проверить обновления:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        pending_ids = {row["id"]: latest.tag for row, latest in pending}
        status_by_id = {}
        for row in self.manager.db.active_dlssg_sm86_installs():
            if row["id"] in pending_ids:
                status_by_id[row["id"]] = f"доступно {pending_ids[row['id']]}"
            else:
                status_by_id[row["id"]] = "актуально"
        self._refresh_installs_table(status_by_id)
        self.set_status(f"Проверено: доступно обновлений — {len(pending)}")
