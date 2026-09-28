from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import KEY_TO_COMPONENT
from ..manager import Manager
from ..pe_version import version_sort_key
from .dlssg_sm86_tab import DlssgSm86Tab
from .library_tab import LibraryTab
from .optiscaler_tab import OptiScalerTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DLSS Manager")
        self.resize(980, 600)

        self.manager = Manager()
        self.setAcceptDrops(True)

        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_toolbar())

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.games_table = self._build_games_table()
        self.tabs.addTab(self.games_table, "Игры")

        self.history_table = self._build_history_table()
        self.tabs.addTab(self.history_table, "История")

        status_cb = lambda msg: self.statusBar().showMessage(msg, 5000)

        self.library_tab = LibraryTab(self.manager, status_cb)
        self.tabs.addTab(self.library_tab, "Библиотека")

        self.optiscaler_tab = OptiScalerTab(self.manager, status_cb)
        self.tabs.addTab(self.optiscaler_tab, "OptiScaler")

        self.dlssg_sm86_tab = DlssgSm86Tab(self.manager, status_cb)
        self.tabs.addTab(self.dlssg_sm86_tab, "DLSS-G unlock")

        self.statusBar().showMessage("Готово")

        self.refresh()

    def _build_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")

        import_action = file_menu.addAction("Импортировать DLL...")
        import_action.triggered.connect(self.on_import)

        scan_action = file_menu.addAction("Сканировать Steam")
        scan_action.triggered.connect(self.on_scan)

        file_menu.addSeparator()
        exit_action = file_menu.addAction("Выход")
        exit_action.triggered.connect(self.close)

    # -- layout -----------------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        btn_scan = QPushButton("Сканировать Steam")
        btn_scan.clicked.connect(self.on_scan)
        bar.addWidget(btn_scan)

        btn_import = QPushButton("Импортировать DLL...")
        btn_import.clicked.connect(self.on_import)
        bar.addWidget(btn_import)

        btn_clean_logs = QPushButton("Очистить логи враппера")
        btn_clean_logs.clicked.connect(self.on_clean_logs)
        bar.addWidget(btn_clean_logs)

        btn_clean_lib = QPushButton("Почистить библиотеку")
        btn_clean_lib.clicked.connect(self.on_clean_library)
        bar.addWidget(btn_clean_lib)

        btn_rollback_all = QPushButton("Откатить всё")
        btn_rollback_all.clicked.connect(self.on_rollback_all)
        bar.addWidget(btn_rollback_all)

        bar.addStretch(1)
        return bar

    def _build_games_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Игра", "Компонент", "Установлена", "Доступные версии", ""])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        table.setColumnWidth(3, 220)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        return table

    def _build_history_table(self) -> QTableWidget:
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Игра", "Компонент", "Было", "Стало", "Когда", ""])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        return table

    # -- data refresh -------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_games_table()
        self._refresh_history_table()
        if hasattr(self, "library_tab"):
            self.library_tab.refresh()
        if hasattr(self, "optiscaler_tab"):
            self.optiscaler_tab.refresh()
        if hasattr(self, "dlssg_sm86_tab"):
            self.dlssg_sm86_tab.refresh()

    def _refresh_games_table(self) -> None:
        table = self.games_table
        table.setRowCount(0)

        for game in self.manager.db.all_games():
            for comp_row in self.manager.db.components_for_game(game["app_id"]):
                row_idx = table.rowCount()
                table.insertRow(row_idx)

                component = KEY_TO_COMPONENT[comp_row["component_key"]]

                table.setItem(row_idx, 0, QTableWidgetItem(game["name"]))
                table.setItem(row_idx, 1, QTableWidgetItem(component.display_name))
                table.setItem(row_idx, 2, QTableWidgetItem(comp_row["version"] or "?"))

                lib_rows = sorted(
                    self.manager.library_versions(component.key),
                    key=lambda r: version_sort_key(r["version"] or "0"),
                    reverse=True,
                )
                combo = QComboBox()
                for lr in lib_rows:
                    label = lr["version"] or "(unknown)"
                    if lr["sha256"] == comp_row["sha256"]:
                        label += "  [установлена]"
                    combo.addItem(label, lr["id"])
                table.setCellWidget(row_idx, 3, combo)

                apply_btn = QPushButton("Применить")
                apply_btn.clicked.connect(
                    lambda _checked=False, app_id=game["app_id"], comp_key=component.key, cb=combo: self.on_apply(
                        app_id, comp_key, cb
                    )
                )
                table.setCellWidget(row_idx, 4, apply_btn)

    def _refresh_history_table(self) -> None:
        table = self.history_table
        table.setRowCount(0)

        for change in self.manager.db.active_changes():
            row_idx = table.rowCount()
            table.insertRow(row_idx)
            table.setItem(row_idx, 0, QTableWidgetItem(change["game_name"]))
            component = KEY_TO_COMPONENT.get(change["component_key"])
            table.setItem(row_idx, 1, QTableWidgetItem(component.display_name if component else change["component_key"]))
            table.setItem(row_idx, 2, QTableWidgetItem(change["previous_version"] or "?"))
            table.setItem(row_idx, 3, QTableWidgetItem(change["new_version"] or "?"))
            table.setItem(row_idx, 4, QTableWidgetItem(change["applied_at"]))

            rollback_btn = QPushButton("Откатить")
            rollback_btn.clicked.connect(
                lambda _checked=False, change_id=change["id"]: self.on_rollback_one(change_id)
            )
            table.setCellWidget(row_idx, 5, rollback_btn)

    # -- actions ------------------------------------------------------------

    def on_scan(self) -> None:
        self.setEnabled(False)
        self.statusBar().showMessage("Сканирование Steam-библиотек...")
        try:
            games = self.manager.scan_all()
            self.statusBar().showMessage(f"Просканировано игр: {len(games)}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сканирования", str(e))
        finally:
            self.setEnabled(True)
            self.refresh()

    def on_import(self) -> None:
        paths_str, _ = QFileDialog.getOpenFileNames(
            self, "Импортировать DLL", str(Path.home()), "DLL files (*.dll)"
        )
        if not paths_str:
            return
        self.library_tab.import_paths([Path(p) for p in paths_str])
        self.tabs.setCurrentWidget(self.library_tab)

    # -- drag & drop ----------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        dropped = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        dlls = [p for p in dropped if p.suffix.lower() == ".dll"]
        if not dlls:
            QMessageBox.warning(self, "Не .dll", "Перетащите файлы с расширением .dll.")
            return
        event.acceptProposedAction()
        self.library_tab.import_paths(dlls)
        self.tabs.setCurrentWidget(self.library_tab)

    def on_apply(self, app_id: str, component_key: str, combo: QComboBox) -> None:
        lib_id = combo.currentData()
        if lib_id is None:
            QMessageBox.warning(self, "Нет версии", "В библиотеке нет версий для этого компонента.")
            return
        try:
            result = self.manager.apply_version(app_id, component_key, lib_id)
        except (ValueError, FileNotFoundError) as e:
            QMessageBox.critical(self, "Не удалось применить", str(e))
            return
        self.statusBar().showMessage(
            f"Применено: {result.previous_version} -> {result.new_version} (бэкап сохранён)", 5000
        )
        self.refresh()

    def on_rollback_one(self, change_id: int) -> None:
        try:
            self.manager.rollback_change(change_id)
        except (ValueError, FileNotFoundError) as e:
            QMessageBox.critical(self, "Не удалось откатить", str(e))
            return
        self.statusBar().showMessage(f"Изменение #{change_id} откачено", 5000)
        self.refresh()

    def on_rollback_all(self) -> None:
        if QMessageBox.question(
            self,
            "Откатить всё",
            "Вернуть все изменённые этим приложением файлы к исходному состоянию?",
        ) != QMessageBox.Yes:
            return
        n = self.manager.rollback_all()
        self.statusBar().showMessage(f"Восстановлено файлов: {n}", 5000)
        self.refresh()

    def on_clean_logs(self) -> None:
        artifacts = self.manager.find_wrapper_artifacts()
        if not artifacts:
            QMessageBox.information(self, "Чистка логов", "Файлы враппера не найдены.")
            return
        preview = "\n".join(str(p) for p in artifacts[:25])
        if len(artifacts) > 25:
            preview += f"\n... и ещё {len(artifacts) - 25}"
        if QMessageBox.question(
            self,
            "Чистка логов",
            f"Удалить {len(artifacts)} файл(ов)?\n\n{preview}",
        ) != QMessageBox.Yes:
            return
        self.manager.clean_wrapper_artifacts(dry_run=False)
        self.statusBar().showMessage(f"Удалено файлов: {len(artifacts)}", 5000)

    def on_clean_library(self) -> None:
        notes = self.manager.dedupe_library()
        if not notes:
            QMessageBox.information(self, "Библиотека", "Библиотека уже чистая.")
            return
        QMessageBox.information(self, "Библиотека", "\n".join(notes))
        self.refresh()

    def closeEvent(self, event) -> None:
        self.manager.close()
        super().closeEvent(event)
