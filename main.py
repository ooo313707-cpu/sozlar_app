"""
main.py
-------
Ilovani ishga tushiruvchi asosiy fayl.

Ishga tushirish: terminalda shu papkada turib -> python main.py
"""

import sys

from PySide6.QtWidgets import QApplication

from app import database as db
from app.ui import theme
from app.ui.main_window import MainWindow


def main():
    # Birinchi ishga tushganda: avtomatik zaxira (backups/ papkasi) va
    # kerak bo'lsa bazani yangi tuzilmaga o'tkazish (so'zlar saqlanib qoladi).
    db.init_db()

    # Saqlangan rang mavzusi (oyna yaratilishidan OLDIN qo'llanadi)
    theme.apply_palette(db.get_setting("accent_color", theme.DEFAULT_PALETTE))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.global_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
