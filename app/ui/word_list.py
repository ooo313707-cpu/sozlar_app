"""
word_list.py
------------
Barcha qo'shilgan so'zlarni jadval ko'rinishida ko'rsatadi, qidirish,
bo'lim bo'yicha filtrlash, tahrirlash va o'chirish imkoniyati bilan.
v1.2: bo'limcha raqami ustuni qo'shildi.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QComboBox,
    QDialog, QTextEdit, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

from .. import database as db
from . import theme


class EditWordDialog(QDialog):
    """So'zni tahrirlash uchun kichik oyna - mavjud ma'lumotlar bilan
    oldindan to'ldirilgan holda ochiladi."""

    def __init__(self, parent, word_row):
        super().__init__(parent)
        self.word_id = word_row["id"]
        self.setWindowTitle("So'zni tahrirlash")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Ingliz so'zi:"))
        self.word_input = QLineEdit(word_row["word"])
        layout.addWidget(self.word_input)

        layout.addWidget(QLabel("Tarjima:"))
        self.translation_input = QLineEdit(word_row["translation"])
        layout.addWidget(self.translation_input)

        layout.addWidget(QLabel("Misol jumla:"))
        self.example_input = QTextEdit(word_row["example"] or "")
        self.example_input.setMaximumHeight(70)
        layout.addWidget(self.example_input)

        layout.addWidget(QLabel("Bo'lim (o'zgartirsangiz, so'z yangi bo'limning oxirgi bo'limchasiga tushadi):"))
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        existing = ["Umumiy"] + [c for c in db.get_categories() if c != "Umumiy"]
        self.category_input.addItems(existing)
        self.category_input.setCurrentText(word_row["category"] or "Umumiy")
        layout.addWidget(self.category_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("💾 Saqlash")
        buttons.button(QDialogButtonBox.Cancel).setText("Bekor qilish")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        word = self.word_input.text().strip()
        translation = self.translation_input.text().strip()
        if not word or not translation:
            QMessageBox.warning(self, "Xato", "So'z va tarjima bo'sh bo'lmasligi kerak.")
            return
        try:
            db.update_word(
                self.word_id, word, translation,
                self.example_input.toPlainText().strip(),
                self.category_input.currentText().strip() or "Umumiy",
            )
        except Exception as e:
            QMessageBox.warning(self, "Xato", f"Saqlashda xatolik: {e}")
            return
        self.accept()


class WordListPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 30)
        layout.setSpacing(14)

        title = QLabel("Barcha so'zlar")
        title.setFont(QFont(theme.FONT_FAMILY, 22, QFont.Bold))
        layout.addWidget(title)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Qidirish...")
        self.search_input.textChanged.connect(self.load_words)
        search_row.addWidget(self.search_input, stretch=1)

        self.category_filter = QComboBox()
        self.category_filter.setMinimumWidth(160)
        self.category_filter.currentTextChanged.connect(self.load_words)
        search_row.addWidget(self.category_filter)

        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["So'z", "Tarjima", "Bo'lim", "Bo'limcha", "Holat", "Eslab qolish", "", ""]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in range(2, 8):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table)

    def refresh(self):
        self.search_input.clear()

        current = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItems(["Barchasi"] + db.get_categories())
        self.category_filter.setCurrentText(current or "Barchasi")
        self.category_filter.blockSignals(False)

        self.load_words()

    def load_words(self):
        search = self.search_input.text().strip()
        category = self.category_filter.currentText()
        words = db.get_all_words(search, category)
        leech = db.get_leech_ids()           # 4+ marta Again olgan "qiyin" so'zlar
        self.table.setRowCount(len(words))
        for row_idx, w in enumerate(words):
            self.table.setItem(row_idx, 0, QTableWidgetItem(w["word"]))
            self.table.setItem(row_idx, 1, QTableWidgetItem(w["translation"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(w["category"] or "Umumiy"))
            self.table.setItem(row_idx, 3, QTableWidgetItem(f"{w['pack'] or 1}-bo'limcha"))
            label = db.word_label(w) + ("  ⚠️ qiyin" if w["id"] in leech else "")
            self.table.setItem(row_idx, 4, QTableWidgetItem(label))

            # Eslab qolish foizi + rang (qizil = yomon ... yashil = yaxshi)
            pct = db.retrievability_percent(w)
            pct_item = QTableWidgetItem("—" if pct is None else f"{pct}%")
            pct_item.setForeground(QColor(theme.retention_color(pct)))
            pct_item.setData(Qt.UserRole, -1 if pct is None else pct)
            self.table.setItem(row_idx, 5, pct_item)

            edit_btn = QPushButton("✏️")
            edit_btn.setFixedWidth(36)
            edit_btn.clicked.connect(lambda checked=False, wid=w["id"]: self.edit_word(wid))
            self.table.setCellWidget(row_idx, 6, edit_btn)

            delete_btn = QPushButton("🗑")
            delete_btn.setFixedWidth(36)
            delete_btn.clicked.connect(lambda checked=False, wid=w["id"]: self.delete_word(wid))
            self.table.setCellWidget(row_idx, 7, delete_btn)

    def edit_word(self, word_id: int):
        word_row = db.get_word(word_id)
        if word_row is None:
            return
        dialog = EditWordDialog(self, word_row)
        if dialog.exec() == QDialog.Accepted:
            self.load_words()

    def delete_word(self, word_id: int):
        reply = QMessageBox.question(
            self, "O'chirish", "Bu so'zni butunlay o'chirmoqchimisiz?",
        )
        if reply == QMessageBox.Yes:
            db.delete_word(word_id)
            self.load_words()
