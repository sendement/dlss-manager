from __future__ import annotations

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow


def _install_excepthook() -> None:
    """A Qt slot that raises just prints to stderr -- invisible when launched
    from a desktop entry, so the user sees a button that "does nothing".
    Show it instead."""
    default_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        default_hook(exc_type, exc, tb)
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Неожиданная ошибка")
        box.setText(f"{exc_type.__name__}: {exc}")
        box.setDetailedText("".join(traceback.format_exception(exc_type, exc, tb)))
        box.exec()

    sys.excepthook = hook


def run_gui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("DLSS Manager")
    _install_excepthook()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
