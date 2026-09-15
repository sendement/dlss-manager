from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def run_gui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("DLSS Manager")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
